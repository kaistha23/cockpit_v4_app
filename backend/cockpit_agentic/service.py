"""
The entry point the API calls. Specification sections 7.1, 7.9 and 11.

One request, one budget, one thread update
------------------------------------------
`ask` resolves the scope server-side, opens or resumes the ledger, runs the
state machine, renders whatever it produced, and then — after the answer is
already available — makes the single bounded Sonnet call that updates the
rolling summary. Section 7.9 is explicit that the summary call failing must not
erase a completed answer, and here it cannot: the outcome is built before the
summary is attempted.

No fallback, anywhere
---------------------
There is no branch in this module that answers from anything but the Opus flow.
If the provider is missing, the catalogue does not fit, the budget runs out or
every attempt fails, the user gets a stop envelope that says so. Sections 17
and 18 forbid the alternative, and the alternative is what the previous
architecture did.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_agentic import (CATALOG_VERSION, DEEP, DOMAIN,
                                     MODES, STANDARD, default_mode,
                                     enabled)
from backend.cockpit_agentic import credential
from backend.cockpit_agentic import ledger as ledger_mod
from backend.cockpit_agentic import models as models_mod
from backend.cockpit_agentic import profile as profile_mod
from backend.cockpit_agentic import pysandbox
from backend.cockpit_agentic import registry, sonnet, sql, states, store, thread
from backend.cockpit_agentic.contracts import Exchange, ThreadSummary
from backend.cockpit_agentic.runtime import Outcome, Runtime

logger = logging.getLogger(__name__)

DEFAULT_RELEASE = "demo-20q-v1"

#: Coverage profiles, per release. Computed from the whole release, versioned
#: with it, and reused rather than recomputed per query (section 5).
_PROFILES: dict[str, Any] = {}

#: In-memory thread state, keyed by thread id. The seam where a deployment
#: binds `backend/services/threads.py` for durable storage.
_THREADS: dict[str, thread.ThreadState] = {}


class ReleaseUnavailable(RuntimeError):
    """The pinned data release cannot be read. Section 38."""

    status = "DATA_UNAVAILABLE"


class NotAvailable(RuntimeError):
    """The Cockpit Agentic runtime is off or unbuilt. Said, not worked around."""


def available(dataset_release_id: str = DEFAULT_RELEASE) -> bool:
    if not enabled():
        return False
    try:
        store.read_manifest(dataset_release_id)
    except store.ReleaseNotFound:
        return False
    return True


def coverage_for(dataset_release_id: str) -> Any:
    """The release's measured coverage profile, computed once."""
    cached = _PROFILES.get(dataset_release_id)
    if cached is not None:
        return cached
    from backend.cockpit_agentic import generate

    manifest = store.read_manifest(dataset_release_id)
    calendar = store.load_calendar(dataset_release_id)
    frames = {relation: store.read_relation(dataset_release_id, relation)
              for relation in manifest["relations"]}
    release = generate.Release(dataset_release_id=dataset_release_id,
                               calendar=calendar, frames=frames)
    profile = profile_mod.profile_release(release)
    _PROFILES[dataset_release_id] = profile
    return profile


def thread_key(*, thread_id: str, tenant_id: str, dataset_release_id: str,
               catalogue_version: str) -> str:
    """Section 37: a thread's identity is not its name.

    Two users of different tenants can pick the same thread id, and a thread
    carried across a data release or a catalogue version would be answering
    with prior results that no longer describe the same book. So the identity
    includes all four, and a mismatch simply addresses a different thread
    rather than reusing one that does not apply.
    """
    return "|".join([str(tenant_id), str(dataset_release_id),
                     str(catalogue_version), str(thread_id)])


def thread_for(thread_id: str, dataset_release_id: str, *,
               tenant_id: str = "", catalogue_version: str = "") -> \
        thread.ThreadState:
    key = thread_key(thread_id=thread_id, tenant_id=tenant_id,
                     dataset_release_id=dataset_release_id,
                     catalogue_version=catalogue_version)
    state = _THREADS.get(key)
    if state is None:
        state = thread.ThreadState(thread_id=thread_id,
                                   dataset_release_id=dataset_release_id,
                                   tenant_id=str(tenant_id),
                                   catalogue_version=str(catalogue_version))
        _THREADS[key] = state
    return state


@dataclass
class Answer:
    """What the API returns."""

    outcome: Outcome
    thread_id: str
    history: dict[str, Any] = field(default_factory=dict)
    summary_updated: bool = False
    #: What, if anything, had to be repaired in this thread's stored summary
    #: before it was used. Reported rather than absorbed: a thread that lost a
    #: settled definition is a fact about the answer above it.
    summary_repair: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        body = self.outcome.to_dict()
        body["thread_id"] = self.thread_id
        body["history"] = dict(self.history)
        body["summary_updated"] = self.summary_updated
        body["summary_repair"] = dict(self.summary_repair)
        return body


def ask(question: str, principal: Any, *, provider: Any = None,
        thread_id: str = "", mode: str = "",
        dataset_release_id: str = DEFAULT_RELEASE,
        ui_filters: dict[str, Any] | None = None,
        request_id: str = "", pairs: int = thread.DEFAULT_PAIRS,
        referenced_exchange_ids: list[str] | None = None) -> Answer:
    """Answer one Cockpit question, end to end."""
    if not enabled():
        raise NotAvailable(
            "Cockpit Agentic V3 is switched off in this runtime. Set "
            "COCKPIT_AGENTIC_V3=true to enable it.")
    # Section 38: the pinned release, or nothing. A release that has gone
    # away is reported as gone; switching to the latest one would answer a
    # question about one book with the numbers from another.
    try:
        calendar = store.load_calendar(dataset_release_id)
    except store.ReleaseNotFound as e:
        raise ReleaseUnavailable(
            f"{e} The request named this release and it is not readable. "
            f"Nothing was substituted for it: another release is a different "
            f"book, and answering from one while the question named the other "
            f"would be wrong in a way nobody could see.") from e

    chosen = str(mode or default_mode()).lower()
    if chosen not in MODES:
        chosen = STANDARD

    provider_error: Exception | None = None
    if provider is None:
        try:
            provider = _resolve_provider()
        except credential.ProviderCredentialMissing as e:
            # Not raised out of here. The request gets the same shape of
            # honest outcome every other configuration failure gets, and the
            # message names the variable rather than any value.
            provider_error, provider = e, None

    state = thread_for(
        thread_id or "cockpit-default", dataset_release_id,
        tenant_id=str(getattr(principal, "tenant_id", "") or ""),
        catalogue_version=CATALOG_VERSION)
    limits = ledger_mod.limits_for(chosen)
    request_scope = {
        "tenant_id": str(getattr(principal, "tenant_id", "") or ""),
        "dataset_release_id": dataset_release_id,
        **{k: v for k, v in (ui_filters or {}).items() if v},
    }
    summary, selection = state.context_for(
        limits=limits, pairs=pairs,
        referenced_ids=referenced_exchange_ids, scope=request_scope)

    runtime = Runtime(
        provider=provider, principal=principal,
        dataset_release_id=dataset_release_id,
        coverage=coverage_for(dataset_release_id), calendar=calendar,
        mode=chosen, request_id=request_id, provider_error=provider_error)

    outcome = runtime.run(question, ui_filters=ui_filters,
                          rolling_summary=summary,
                          recent_exchanges=selection.exchanges)

    # The answer exists from here on. Nothing below can take it away.
    summary_updated = False
    if outcome.exchange is not None:
        # Section 40: the scope travels with the exchange, so a later turn can
        # be told whether these numbers apply to it.
        outcome.exchange.scope = dict(request_scope)
        state.record(outcome.exchange)
        try:
            updated = sonnet.update_summary(state.summary, outcome.exchange,
                                            provider, runtime.ledger)
            state.apply_summary(updated)
            summary_updated = bool(
                updated.summary_through_exchange_id
                == outcome.exchange.exchange_id)
        except Exception as e:                              # noqa: BLE001
            logger.info("The Cockpit thread summary was not updated: %s", e)

    return Answer(outcome=outcome, thread_id=state.thread_id,
                  history=selection.to_dict(), summary_updated=summary_updated,
                  summary_repair=(state.last_repair.to_dict()
                                  if state.last_repair.occurred else {}))


def _resolve_provider() -> Any:
    """The Cockpit's provider, on the Cockpit's own credential.

    `COCKPIT_ANTHROPIC_API_KEY` and nothing else. Not `ANTHROPIC_API_KEY`,
    which in this deployment is the Claude Code agent's own; not the SDK's
    implicit discovery; not a legacy application setting. Missing raises here,
    before any request is assembled, and the runtime turns that into
    PROVIDER_CREDENTIAL_MISSING.

    No model is passed. The Cockpit's two roles are resolved strictly in
    `models.py` and supplied per call, so a default carried on the provider
    would be a fallback nobody chose -- exactly what the model-role hardening
    removed.
    """
    from backend.config import settings
    from backend.llm.anthropic_provider import AnthropicProvider

    if str(settings.ai_provider).lower() == "offline":
        # An explicitly offline deployment is a configuration, not a failure.
        # The Cockpit still answers nothing -- the runtime reports the
        # provider as unavailable rather than substituting an analysis.
        from backend.llm.base import NullProvider
        return NullProvider()
    return AnthropicProvider(api_key=credential.require())


def _preflight(body: dict[str, Any], dataset_release_id: str
               ) -> dict[str, Any]:
    """The commissioning checklist, in one place.

    Each entry is a STATE. Nothing here carries a credential, a prompt, or a
    row of data -- a preflight that leaked one of those would be the thing it
    exists to prevent.
    """
    from backend.config import settings

    models = body["cockpit_models"]
    sandbox = body["python_execution"]
    release = body.get("release") or {}
    prices = ledger_mod.prices_from_settings()
    counting = "provider_count_tokens" if body["provider"]["configured"] \
        else "local_conservative_estimate"

    return {
        "cockpit_agentic_v3": "enabled" if body["cockpit_agentic_v3"]
                              else "disabled",
        "provider": (settings.ai_provider or "").strip().lower() or "unset",
        "cockpit_anthropic_credential": body["provider"]["status"],
        "preprocess_model": models.get("preprocess_model") or "NOT SET",
        "reasoning_model": models.get("reasoning_model") or "NOT SET",
        "model_role_configuration": models.get("status"),
        "provider_connectivity": (
            "UNVERIFIED — no call has been made from this process"
            if body["provider"]["configured"] else
            "NOT ATTEMPTED — no credential"),
        "token_counting": (
            "provider counting, against the configured ids"
            if counting == "provider_count_tokens" else
            "local conservative estimate; never reported as a measurement"),
        "prompt_cache": ("one breakpoint after the invariant prefix; the "
                         "catalogue is inside it"),
        "spend_accounting": ("enforced" if prices.configured else
                             "UNKNOWN — no prices configured, so the spend "
                             "ceiling is not a control"),
        "python_sandbox": ("available — " + str(sandbox.get("strategy") or "")
                           if sandbox.get("available") else "unavailable"),
        "data_release": (dataset_release_id if body["available"]
                         else f"{dataset_release_id} — NOT READABLE"),
        "data_origin": release.get("origin") or "demo_only — no real source",
        "ready_for_commissioning": bool(
            body["cockpit_agentic_v3"]
            and body["available"]
            and body["provider"]["configured"]
            and models.get("configured")),
    }


def diagnostics(principal: Any = None,
                dataset_release_id: str = DEFAULT_RELEASE) -> dict[str, Any]:
    """What this runtime actually is, for the badge and the handoff.

    Includes which guardrails are running raised, so a demonstration cannot be
    read as having been obtained under the specification's own numbers.
    """
    from backend.config import settings
    from backend.llm import roles

    body: dict[str, Any] = {
        "cockpit_agentic_v3": enabled(),
        "domain_id": DOMAIN,
        "available": available(dataset_release_id),
        "dataset_release_id": dataset_release_id,
        "namespace": settings.cockpit_agentic_v3_namespace,
        "default_mode": default_mode(),
        "modes": list(MODES),
        "functionality_routes": registry.verify_routes(),
        "python_execution": pysandbox.probe().as_dict(),
        "cockpit_models": models_mod.status(),
    }
    for mode in MODES:
        body[f"{mode}_limits"] = ledger_mod.limits_for(mode).to_dict()
        body[f"{mode}_overrides_in_force"] = ledger_mod.overrides_in_force(mode)
    body["guardrails_note"] = (
        "Any limit listed under overrides_in_force is running raised by "
        "explicit deployment configuration. The five execution submissions and "
        "three analysis rounds have no override at any level.")
    body["model_roles"] = {
        role_name: roles.role(role_name).to_dict()
        for role_name in (sonnet.SONNET_ROLE, "cockpit_reasoning")}
    body["provider"] = {
        "name": (settings.ai_provider or "").strip().lower(),
        # PRESENT or MISSING, and never anything else about the value. Its
        # own `note` names the variable to set and is kept: the general note
        # below would otherwise overwrite the actionable one.
        **credential.report(),
        "behaviour": ("With no credential configured the Cockpit reports that "
                      "it cannot answer. It does not substitute a "
                      "deterministic analysis."),
    }
    # A single block an operator can read straight down before commissioning,
    # rather than assembling the answer from eight places. Every line is a
    # state, never a value: the credential in particular is PRESENT or
    # MISSING and nothing else.
    body["preflight"] = _preflight(body, dataset_release_id)
    body["cost_enforced"] = ledger_mod.prices_from_settings().configured
    if not body["cost_enforced"]:
        body["cost_note"] = (
            "No model price is configured, so spend is UNKNOWN and the "
            "spending ceiling is not a control.")
    try:
        manifest = store.read_manifest(dataset_release_id)
        body["release"] = {
            "reporting_quarters": manifest["calendar"]["reporting_slots"],
            "populated_quarters": manifest["calendar"]["populated_quarters"],
            "missing_quarters": manifest["calendar"]["missing_quarters"],
            "built_at": manifest["built_at"],
            "data_version": manifest["data_version"],
            "catalog_version": manifest["catalog_version"],
            "origin": manifest["origin"],
            "not_client_data": manifest["not_client_data"],
            "rows": {k: v["rows"] for k, v in manifest["relations"].items()},
        }
    except store.ReleaseNotFound as e:
        body["release"] = {"available": False, "reason": str(e)}
    return body


__all__ = ["Answer", "DEFAULT_RELEASE", "NotAvailable", "ask", "available",
           "coverage_for", "diagnostics", "thread_for"]
