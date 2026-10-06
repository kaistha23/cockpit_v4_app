"""
Assembly and preflight: what must be true before a run may cost anything.

Order matters here. Credential, model, capability and price are checked
BEFORE the release is opened, and the release is opened before any context is
built, and context is built before the first generation is dispatched. A run
that fails at any of those stages fails with the check's own name -- so an
operator is sent to the setting that is actually wrong, rather than to a
generic provider error three stages later.

Nothing in this module answers a question. If a check fails, the run stops
with that reason; there is no deterministic substitute and no V3 fallback.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

from backend.cockpit_agentic import calendar as v3_calendar
from backend.cockpit_agentic import catalog as v3_catalog
from backend.cockpit_agentic import scope as v3_scope
from backend.cockpit_agentic import store as v3_store
from backend.cockpit_v4 import DOMAIN
from backend.cockpit_v4 import capability as cap_mod
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import precision as prec
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.config import V4Config, limits_for

logger = logging.getLogger(__name__)

_CATALOG_CACHE: dict[str, Any] = {}
_COVERAGE_CACHE: dict[str, Any] = {}
_LOCK = threading.RLock()


class PreflightFailed(RuntimeError):
    """A named check failed before anything could cost money."""

    def __init__(self, code: str, message: str, *,
                 variables: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.variables = variables


@dataclass
class Runtime:
    """Everything one run needs, assembled once and verified."""

    cfg: V4Config
    capability: cap_mod.Capability
    provider: Any
    catalog: Any
    coverage: Any
    release_summary: dict[str, Any]

    def scope_for(self, principal: dict[str, Any]) -> Any:
        """The effective read scope, derived server-side from the principal.

        Reuses V3's `for_principal`, so the intersection of the Cockpit's
        capability scope with this principal's grants is computed in exactly
        one place for both builds. Nothing in a request or a model response
        can widen it.
        """
        return v3_scope.for_principal(_Principal(principal),
                                      dataset_release_id=self.cfg.release_id)


class _Principal:
    """Adapts V4's principal dict to the attribute shape V3 scope expects."""

    def __init__(self, principal: dict[str, Any]) -> None:
        self.tenant_id = str(principal.get("tenant") or "")
        self.id = str(principal.get("id") or "")
        self.cockpit_relations = tuple(principal.get("relations") or ())


def credential() -> str:
    """The one Cockpit credential. No fallback, ever."""
    value = os.environ.get(config_mod.CREDENTIAL_VAR, "").strip()
    if not value:
        raise PreflightFailed(
            st.PROVIDER_CREDENTIAL_MISSING,
            f"{config_mod.CREDENTIAL_VAR} is not set in this runtime. The "
            f"Cockpit does not fall back to another module's credential.",
            variables=(config_mod.CREDENTIAL_VAR,))
    return value


def credential_status(cfg: V4Config | None = None) -> str:
    """Whether the selected provider has the credentials it requires.

    oMLX installations may intentionally run without a bearer key, so an
    empty OMLX_API_KEY is not a configuration failure. Reachability is still
    established by model discovery/calls, never assumed from this status.
    """
    cfg = cfg or config_mod.load()
    if cfg.provider == "omlx":
        return "PRESENT"
    return ("PRESENT" if os.environ.get(config_mod.CREDENTIAL_VAR, "").strip()
            else "MISSING")


def resolve_provider(cfg: V4Config) -> Any:
    """Build the provider client bound to the Cockpit's own key."""
    if cfg.provider == "omlx":
        from backend.config import settings
        from backend.llm.omlx_provider import OMLXProvider
        return OMLXProvider(api_key=settings.omlx_api_key,
                            model=cfg.reasoning_model,
                            base_url=settings.omlx_url)
    key = credential()
    if cfg.provider != "anthropic":
        raise PreflightFailed(
            st.MODEL_CONFIGURATION_MISSING,
            f"AI_PROVIDER={cfg.provider!r} is not supported by this build. "
            f"The installed adapter is 'anthropic'.",
            variables=("AI_PROVIDER",))
    from backend.llm.anthropic_provider import AnthropicProvider

    try:
        return AnthropicProvider(api_key=key)
    except TypeError:
        # Older adapter signature: construct then bind the key explicitly,
        # rather than letting the SDK pick up an ambient ANTHROPIC_API_KEY.
        provider = AnthropicProvider()
        setattr(provider, "api_key", key)
        return provider


def load_capability(cfg: V4Config, provider: Any = None,
                    *, verify: bool = True) -> cap_mod.Capability:
    if not cfg.reasoning_model:
        raise PreflightFailed(
            st.MODEL_CONFIGURATION_MISSING,
            f"{config_mod.REASONING_MODEL_VAR} is not set. V4 does not "
            f"default a model, inherit AI_MODEL or resolve 'latest'.",
            variables=(config_mod.REASONING_MODEL_VAR,))
    if not cfg.price_card_path:
        raise PreflightFailed(
            st.CAPABILITY_UNVERIFIED,
            "COCKPIT_V4_PRICE_CARD is not set, so no paid request can be "
            "reserved against a verified price.",
            variables=("COCKPIT_V4_PRICE_CARD",))
    try:
        capability = cap_mod.load_price_card(
            cfg.price_card_path, model_id=cfg.reasoning_model,
            provider=cfg.provider)
    except cap_mod.CapabilityUnverified as exc:
        raise PreflightFailed(st.CAPABILITY_UNVERIFIED, str(exc)) from exc
    if verify and provider is not None:
        try:
            capability = cap_mod.verify_live(capability, provider)
        except cap_mod.CapabilityUnverified as exc:
            raise PreflightFailed(st.CAPABILITY_UNVERIFIED, str(exc)) from exc
    return capability


def load_release(cfg: V4Config) -> tuple[Any, Any, dict[str, Any]]:
    """Open the pinned release READ-ONLY and validate its manifest.

    A release that has gone away is reported as gone. Substituting the latest
    one would answer a question about one book with another book's numbers,
    and nothing in the answer would say so.
    """
    if not cfg.release_id:
        raise PreflightFailed(
            st.DATA_UNAVAILABLE,
            "COCKPIT_V4_RELEASE_ID is not set, so no authorized release is "
            "pinned.", variables=("COCKPIT_V4_RELEASE_ID",))
    with _LOCK:
        cached = _CATALOG_CACHE.get(cfg.release_id)
        if cached is not None:
            return cached[0], _COVERAGE_CACHE.get(cfg.release_id), cached[1]
    try:
        manifest = v3_store.read_manifest(cfg.release_id)
        calendar = v3_store.load_calendar(cfg.release_id)
    except v3_store.ReleaseNotFound as exc:
        # V3's own message tells the reader to run V3's build script. That
        # instruction is wrong for V4: V4 publishes into its OWN namespace,
        # localizes the book to Saudi Arabia after it is built, and never
        # touches a V3 release. Forwarding V3's sentence sent an operator to
        # the wrong tool, so V4 says what V4 needs instead.
        raise PreflightFailed(
            st.DATA_UNAVAILABLE,
            f"release {cfg.release_id!r} is not published in this runtime. "
            f"Nothing was substituted for it. Provision it with:\n"
            f"    {provision_command(cfg.release_id)}",
            variables=("COCKPIT_V4_RELEASE_ID",)) from exc

    summary = {"dataset_release_id": cfg.release_id, "domain_id": DOMAIN,
               **{k: manifest.get(k) for k in
                  ("origin", "data_version", "not_client_data",
                   "reporting_currency", "amount_scale", "tenants",
                   # Where the book is. Carried so the execution header can
                   # state the country rather than infer one from the
                   # currency -- SAR is Saudi, but a currency is not a
                   # country and reading one off the other is how a header
                   # starts asserting things nobody published.
                   "geography", "geography_name", "localization",
                   "amounts_converted")
                  if k in manifest}}
    currency, scale = denomination(cfg.release_id, manifest)
    catalog = v3_catalog.build(
        dataset_release_id=cfg.release_id, calendar=calendar,
        tenant_id=str((manifest.get("tenants") or [""])[0]),
        reporting_currency=currency, amount_scale=scale)
    summary["reporting_currency"] = currency
    summary["amount_scale"] = scale
    with _LOCK:
        _CATALOG_CACHE[cfg.release_id] = (catalog, summary)
    return catalog, _COVERAGE_CACHE.get(cfg.release_id), summary


def provision_command(release_id: str) -> str:
    """The ONE V4-native command that publishes a release.

    Named in one place because it appears in a preflight failure, in the
    launcher's terminal output and in the diagnostics document, and an
    operator who is handed three different instructions tries all three.

    It is V4's own seeder, not V3's builder: it writes only into the V4
    namespace, localizes the book to Saudi Arabia after the shared generator
    has produced it, converts no amount, and refuses to overwrite a release
    that is already published.
    """
    return (f"python3 scripts/cockpit_v4/seed_release.py "
            f"--release {release_id}")


def denomination(release_id: str,
                 manifest: dict[str, Any]) -> tuple[str, str]:
    """What this release is denominated in. Asked, never assumed.

    The selected release decides its own currency and scale. There is no V4
    default of any nationality here, in either direction:

      1. The manifest, when the release declares one. A release published by
         the V4 seeder records its own currency, so this is the normal path
         and it is explicit.

      2. Otherwise the release's OWN DATA. Every relation carries a
         `reporting_currency` column, so a release that never declared a
         currency can still be asked what it holds rather than told what it
         must be.

      3. Otherwise nothing. An empty pair means "this release does not say",
         which is a fact a caller can render honestly. Inventing a currency
         here is exactly the defect this function exists to remove: a
         hard-coded fallback silently relabelled a release whose data said
         something else, and the relabelling was invisible because the
         manifest was merely silent rather than wrong.

    The scale is read from the producer that built the release when the
    manifest does not carry one. Asking the generator what it generated is
    not a default; it is the release's provenance.
    """
    declared = str(manifest.get("reporting_currency") or "").strip()
    scale = str(manifest.get("amount_scale") or "").strip()
    if declared and scale:
        return declared, scale

    observed, observed_scale = _denomination_from_data(release_id)
    return (declared or observed, scale or observed_scale)


def _denomination_from_data(release_id: str) -> tuple[str, str]:
    """Read the currency the release actually holds, and its producer's scale."""
    currency = ""
    try:
        import pandas as pd

        path = v3_store.relation_path(release_id, "cockpit_facility_quarter")
        frame = pd.read_parquet(path, columns=["reporting_currency"])
        values = [str(v) for v in frame["reporting_currency"].dropna().unique()]
        if len(values) == 1:
            currency = values[0]
        elif values:
            # More than one reporting currency in a reporting-currency column
            # is a data problem, not something to pick a winner from.
            logger.warning("release %s reports %d reporting currencies: %s",
                           release_id, len(values), sorted(values)[:5])
    except Exception:                                         # noqa: BLE001
        logger.info("release %s: could not read a reporting currency from its "
                    "data", release_id)
    scale = ""
    try:
        from backend.cockpit_agentic import generate as v3_generate

        scale = str(getattr(v3_generate, "AMOUNT_SCALE", "") or "")
    except Exception:                                         # noqa: BLE001
        pass
    return currency, scale


def coverage_for(cfg: V4Config) -> Any:
    """The measured coverage profile, computed once per release.

    Missingness comes from here, not from ten preview rows.
    """
    with _LOCK:
        cached = _COVERAGE_CACHE.get(cfg.release_id)
    if cached is not None:
        return cached
    try:
        from backend.cockpit_agentic import generate as v3_generate
        from backend.cockpit_agentic import profile as v3_profile

        manifest = v3_store.read_manifest(cfg.release_id)
        calendar = v3_store.load_calendar(cfg.release_id)
        frames = {relation: v3_store.read_relation(cfg.release_id, relation)
                  for relation in manifest["relations"]}
        release = v3_generate.Release(dataset_release_id=cfg.release_id,
                                      calendar=calendar, frames=frames)
        profile = v3_profile.profile_release(release)
    except Exception as exc:  # noqa: BLE001
        logger.info("V4 coverage profile unavailable for %s: %s",
                    cfg.release_id, exc)
        return None
    with _LOCK:
        _COVERAGE_CACHE[cfg.release_id] = profile
    return profile


def build_runtime(cfg: V4Config | None = None, *, provider: Any = None,
                  verify_model: bool = True) -> Runtime:
    """Run every preflight check, in the order an operator should read them."""
    cfg = cfg or config_mod.load()
    if not cfg.enabled:
        raise PreflightFailed(
            st.MODEL_CONFIGURATION_MISSING,
            "COCKPIT_AGENTIC_V4 is not enabled in this runtime.",
            variables=("COCKPIT_AGENTIC_V4",))
    if provider is None:
        provider = resolve_provider(cfg)
    capability = load_capability(cfg, provider, verify=verify_model)
    catalog, _, summary = load_release(cfg)
    coverage = coverage_for(cfg)
    return Runtime(cfg=cfg, capability=capability, provider=provider,
                   catalog=catalog, coverage=coverage,
                   release_summary=summary)


def diagnostics(cfg: V4Config | None = None, *,
                startup_sha: str = "") -> dict[str, Any]:
    """Per-capability readiness. Three separate checks, not one green badge.

    `ready_for_product_help` needs a credential, a model and a verified
    price. `ready_for_sql_analysis` additionally needs the release to open.
    `ready_for_python_analysis` additionally needs an isolated runner. A build
    with no Python jail is honest about it rather than showing all-green and
    failing on the first Python step.
    """
    cfg = cfg or config_mod.load()
    report: dict[str, Any] = {
        "route": "cockpit_v4", "enabled": cfg.enabled,
        "startup_sha": startup_sha,
        "credential": credential_status(cfg),
        "settings": cfg.to_dict(),
        "checks": {},
    }
    checks = report["checks"]

    checks["credential"] = {"ok": credential_status(cfg) == "PRESENT",
                            "variable": ("OMLX_API_KEY (optional)" if cfg.provider == "omlx"
                                         else config_mod.CREDENTIAL_VAR)}
    try:
        capability = load_capability(cfg, None, verify=False)
        checks["model_and_price"] = {"ok": True,
                                     "capability": capability.to_dict()}
    except PreflightFailed as exc:
        checks["model_and_price"] = {"ok": False, "reason": str(exc),
                                     "code": exc.code}
    try:
        catalog, _, summary = load_release(cfg)
        from backend.cockpit_v4 import release as release_mod

        header = release_mod.header(
            release_id=cfg.release_id, catalog=catalog,
            release_summary=summary)
        checks["release"] = {
            "ok": True, "release": summary,
            # Printed at startup, every time. Release selection is not a
            # thing to discover afterwards from the numbers on the screen:
            # which release, from which bytes, in which currency, at which
            # scale, for which country.
            "header": header.to_dict(),
            "denominated": header.denominated,
            "relations": len(catalog.relations()),
            "quarters": len(getattr(catalog.calendar, "slots", ()) or ())}
    except PreflightFailed as exc:
        # What is selected, and why it will not open. Never a substitution:
        # a runtime configured for the Saudi release does not quietly serve
        # a different one, because the only thing worse than no numbers is
        # someone else's numbers under your release's name.
        checks["release"] = {
            "ok": False, "reason": str(exc), "code": exc.code,
            "header": {"release_id": cfg.release_id,
                       "release_fingerprint": "", "published": False,
                       "readable": False},
            "remedy": provision_command(cfg.release_id),
            "substituted": False}

    # Each book, separately. One dashboard being green says nothing about
    # the other, and a single "release" row would let an unopenable Retail
    # release hide behind a healthy Corporate one.
    checks["domains"] = _domain_checks()

    from backend.cockpit_v4 import pyrunner

    runner = pyrunner.probe()
    checks["python_runner"] = runner

    try:
        from backend.cockpit_v4.run_store import RunStore

        RunStore(cfg.state_database)
        checks["state_database"] = {
            "ok": True, "path": config_mod.redact_database_url(
                cfg.state_database)}
    except Exception as exc:  # noqa: BLE001
        checks["state_database"] = {"ok": False, "reason": str(exc)[:200]}

    base_ok = (checks["credential"]["ok"]
               and checks["model_and_price"].get("ok")
               and checks["state_database"].get("ok") and cfg.enabled)
    report["ready_for_product_help"] = bool(base_ok)
    report["ready_for_sql_analysis"] = bool(
        base_ok and checks["release"].get("ok"))
    # The dashboard reads a BOOK, not the runtime's configured release, and
    # costs no provider call. A deployment whose configured release is
    # missing can still serve every card on the Cockpit if a book is
    # published, and saying otherwise over a dashboard that is on screen is
    # how a reader learns to ignore the header.
    report["ready_for_attention"] = bool(
        checks["release"].get("ok")
        or any(b["browse_ready"] for b in checks["domains"]["books"]))
    report["ready_for_python_analysis"] = bool(
        base_ok and checks["release"].get("ok") and runner.get("available"))
    # The same six flags the health document serves, named identically, so a
    # reader comparing the two documents is comparing like with like.
    from backend.cockpit_v4 import readiness as ready_mod

    report["capabilities"] = {
        ready_mod.PROCESS_ALIVE: True,
        ready_mod.RELEASE_READY: bool(checks["release"].get("ok")),
        ready_mod.PRODUCT_HELP_READY: bool(base_ok),
        ready_mod.SQL_ANALYSIS_READY: report["ready_for_sql_analysis"],
        ready_mod.ATTENTION_READY: report["ready_for_attention"],
        ready_mod.PYTHON_ANALYSIS_READY: report["ready_for_python_analysis"],
    }
    if not checks["release"].get("ok"):
        report["preflight_error"] = (
            f"{checks['release'].get('code', '')}: "
            f"{checks['release'].get('reason', '')}".strip(": "))
    report["limits"] = {
        # The EFFECTIVE limits (constants plus the selected profile), and
        # the profile's name, so a slow-model deployment can be told apart
        # from a default one without reading its environment.
        "standard": config_mod.effective_limits("standard").__dict__,
        "deep": config_mod.effective_limits("deep").__dict__,
        "analytical_standard":
            config_mod.effective_limits("analytical_standard").__dict__,
        "analytical_deep":
            config_mod.effective_limits("analytical_deep").__dict__,
    }
    report["limits_profile"] = config_mod.active_profile()
    # §12. The whole allowance ladder, by name, so an operator reading a run
    # that stopped on time can see which family it was in and what that
    # family allows -- without reading the source, and without inferring it
    # from the number of seconds in an error message.
    from backend.cockpit_v4 import envelope as envelope_mod

    report["budget_policy"] = {
        "families": envelope_mod.policy(),
        "how_it_is_chosen": (
            "Before the first provider call, from the question, the mode and "
            "the thread: a conversation opened from an attention card, or "
            "one whose earlier turn ran an analysis, starts on the analysis "
            "allowance. The allowance only ever widens afterwards -- an "
            "analyst declaring DATA_ANALYSIS, or calling execute_analysis, "
            "still widens a turn that started on the product-help clock."),
    }
    askable = [d["domain_id"] for d in checks["domains"]["books"]
               if d["analysis_ready"]]
    report["analysis_domains"] = askable
    report["ready_for_sql_analysis"] = bool(
        base_ok and (checks["release"].get("ok") or askable))
    report["capabilities"][ready_mod.SQL_ANALYSIS_READY] = \
        report["ready_for_sql_analysis"]
    return report


def _domain_checks() -> dict[str, Any]:
    """What each book is, and whether it can be browsed and asked.

    Browsable and askable are reported separately because they fail
    separately: a release can be published and still refuse to materialize a
    session, and a reader offered the book deserves to know which of the two
    they have.
    """
    from backend.cockpit_v4 import domain_resolver as resolver
    from backend.cockpit_v4 import domains as dom_mod

    books: list[dict[str, Any]] = []
    try:
        available = resolver.availability()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)[:300], "books": []}

    for status in available.statuses:
        entry: dict[str, Any] = {
            "domain_id": status.domain_id,
            "domain_label": dom_mod.LABELS[status.domain_id],
            "release_id": status.release_id,
            "published": bool(status.ready),
            "browse_ready": bool(status.ready),
            "analysis_ready": False,
            "reason": status.reason,
            "provision_command": resolver.provision_command(
                status.domain_id),
            "substituted": False,
        }
        if status.ready and status.scope is not None:
            scope = status.scope
            entry.update({
                "release_fingerprint": scope.release_fingerprint,
                "reporting_currency": scope.currency,
                "amount_scale": scope.amount_scale,
                "reporting_frequency": scope.reporting_frequency,
                "reporting_periods": len(scope.periods),
                "latest_period": scope.latest_period,
                "relations": len(scope.relations),
            })
            try:
                entry["analysis_ready"] = resolver.analysis_supported(
                    status.domain_id)
            except Exception as exc:  # noqa: BLE001
                entry["reason"] = str(exc)[:300]
        books.append(entry)
    return {"ok": all(b["browse_ready"] for b in books) and bool(books),
            "books": books,
            "note": ("Each book is a different release with different bytes. "
                     "Neither is ever served in place of the other, and a "
                     "book that cannot be opened is reported rather than "
                     "substituted.")}


def reset_caches() -> None:
    with _LOCK:
        _CATALOG_CACHE.clear()
        _COVERAGE_CACHE.clear()


__all__ = ["PreflightFailed", "Runtime", "build_runtime", "coverage_for",
           "credential", "credential_status", "diagnostics", "limits_for",
           "load_capability", "load_release", "provision_command",
           "reset_caches", "resolve_provider"]
