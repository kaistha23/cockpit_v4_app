"""
What this runtime can actually do, in one place, truthfully.

The defect this exists for
--------------------------
A Mac runtime was started with the Saudi release unpublished. Preflight said
so:

    V4 preflight incomplete: DATA_UNAVAILABLE:
    release 'v4-saudi-20q-v1' is not published in this runtime.

and then the server started anyway, `GET /health` answered 200, the browser
posted a run, the API answered `202 Accepted`, and the run sat at "Request
accepted" for the rest of the afternoon. The attention endpoint answered a
raw 500 with an AttributeError in it.

All four came from one decision. When preflight failed, the application
substituted a placeholder object that carried a config and nothing else, and
handed it to the routes as the runtime. Every consumer then had a runtime --
so nothing refused anything. The routes called a method the real runtime has
and the placeholder did not; run creation had no reason to object; and the
worker was never started, because THAT check looked at the real runtime and
correctly saw none.

A stand-in that is present but cannot work is worse than an absence. An
absence is checkable.

So there is no stand-in. The runtime is the real one or it is `None`, and
this module answers, for a given runtime and config, which capabilities are
genuinely available. Every route that needs one asks here first, and a
capability that is not ready produces a TYPED refusal naming what is missing
and how to fix it -- never a 202 for work nothing will do, and never a
traceback.

Capability separation
---------------------
These are separate on purpose. Product Help answers from the product's own
knowledge and needs a model, not a portfolio. SQL analysis needs the release
open. Python analysis needs an isolated runner as well. The attention
dashboard needs the release and nothing else. A build missing one of them
should say which, rather than showing one green badge and failing at the
first question that happens to need the missing piece.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4 import states as st

#: The capability names, as they appear in `/health` and `/diagnostics`.
PROCESS_ALIVE = "process_alive"
RELEASE_READY = "release_ready"
PRODUCT_HELP_READY = "product_help_ready"
SQL_ANALYSIS_READY = "sql_analysis_ready"
ATTENTION_READY = "attention_ready"
PYTHON_ANALYSIS_READY = "python_analysis_ready"

CAPABILITIES = (PROCESS_ALIVE, RELEASE_READY, PRODUCT_HELP_READY,
                SQL_ANALYSIS_READY, ATTENTION_READY, PYTHON_ANALYSIS_READY)


@dataclass(frozen=True)
class Readiness:
    """One truthful answer per capability, and why each is what it is."""

    flags: dict[str, bool]
    reason: str = ""
    error_code: str = ""
    remedy: str = ""

    def __getitem__(self, name: str) -> bool:
        return bool(self.flags.get(name, False))

    @property
    def ready(self) -> bool:
        """Everything the product advertises. Not a substitute for asking."""
        return all(self.flags.get(name, False) for name in CAPABILITIES)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = dict(self.flags)
        if self.reason:
            body["reason"] = self.reason
        if self.error_code:
            body["error_code"] = self.error_code
        if self.remedy:
            body["remedy"] = self.remedy
        return body

    def require(self, capability: str) -> None:
        """Raise the typed refusal for a capability that is not ready.

        A 503 rather than a 500: nothing is broken, something is not
        provisioned, and the difference decides whether an operator goes
        looking for a bug or runs one command.
        """
        if self.flags.get(capability, False):
            return
        from fastapi import HTTPException

        detail = {
            "error_code": self.error_code or st.DATA_UNAVAILABLE,
            "message": self.reason or (
                f"{capability.replace('_', ' ')} is not available in this "
                f"runtime."),
            "capability": capability,
            "readiness": dict(self.flags),
        }
        if self.remedy:
            detail["remedy"] = self.remedy
        raise HTTPException(503, detail)


def _books_open() -> bool:
    """Whether at least one domain book can be browsed in this runtime."""
    try:
        from backend.cockpit_v4 import domain_resolver as resolver

        return bool(resolver.availability().ready_domains)
    except Exception:  # noqa: BLE001 - an unanswerable question is a no
        return False


def assess(runtime: Any, *, cfg: Any = None, preflight_error: str = "",
           python_runner_available: bool | None = None) -> Readiness:
    """What this runtime can do. Derived, never declared.

    `runtime is None` is the whole of the failing case: preflight did not
    produce one, so there is no catalogue, no capability card and no provider
    -- and therefore no analysis, no dashboard and no product help. Saying so
    is the fix.
    """
    # The dashboard needs a BOOK, not a model. A runtime with no verified
    # price card cannot accept a question and can still compute every card on
    # the Cockpit, because nothing on that page costs a provider call. The
    # header used to read "dashboard service not in this runtime" over a
    # dashboard that was on screen, which teaches a reader to ignore it.
    books_open = _books_open()

    if runtime is None:
        code, reason = _split(preflight_error)
        return Readiness(
            flags={PROCESS_ALIVE: True, RELEASE_READY: False,
                   PRODUCT_HELP_READY: False, SQL_ANALYSIS_READY: False,
                   # The one capability a failed preflight does NOT take
                   # away, when a book is published: the dashboard is
                   # computed from the release and costs no provider call.
                   ATTENTION_READY: books_open,
                   PYTHON_ANALYSIS_READY: False},
            reason=reason or ("This runtime did not complete preflight, so "
                              "it has no authorized release open."),
            error_code=code or st.DATA_UNAVAILABLE,
            remedy=_remedy(cfg))

    release_open = getattr(runtime, "catalog", None) is not None
    has_model = getattr(runtime, "capability", None) is not None
    if python_runner_available is None:
        python_runner_available = _runner_available()

    return Readiness(
        flags={
            PROCESS_ALIVE: True,
            RELEASE_READY: release_open,
            # Product Help answers from the product's own knowledge. It needs
            # a model; it does not need a portfolio.
            PRODUCT_HELP_READY: has_model,
            SQL_ANALYSIS_READY: has_model and release_open,
            ATTENTION_READY: release_open or books_open,
            PYTHON_ANALYSIS_READY: (has_model and release_open
                                    and bool(python_runner_available)),
        },
        reason="" if release_open else (
            "This runtime has no authorized release open."),
        error_code="" if release_open else st.DATA_UNAVAILABLE,
        remedy="" if release_open else _remedy(
            cfg or getattr(runtime, "cfg", None)))


def _runner_available() -> bool:
    try:
        from backend.cockpit_v4 import pyrunner

        return bool(pyrunner.probe().get("available"))
    except Exception:  # noqa: BLE001 - a probe that cannot run is a no
        return False


def _remedy(cfg: Any) -> str:
    release_id = str(getattr(cfg, "release_id", "") or "")
    if not release_id:
        return ""
    from backend.cockpit_v4.service import provision_command

    return provision_command(release_id)


def _split(preflight_error: str) -> tuple[str, str]:
    """`"CODE: message"` as the two things it is."""
    text = str(preflight_error or "").strip()
    if not text:
        return "", ""
    code, sep, message = text.partition(": ")
    if sep and code.isupper() and " " not in code:
        return code, message.strip()
    return "", text


__all__ = ["ATTENTION_READY", "CAPABILITIES", "PROCESS_ALIVE",
           "PRODUCT_HELP_READY", "PYTHON_ANALYSIS_READY", "RELEASE_READY",
           "Readiness", "SQL_ANALYSIS_READY", "assess"]
