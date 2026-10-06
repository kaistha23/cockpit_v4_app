"""
The shell-compatible health route, and why V4 serves one at all.

The application shell polls `GET /api/v1/health` and renders "Backend offline"
when that call fails. Point the shell at the V4 API without this route and the
header reports the whole backend as down while V4 is answering every request
it was asked to answer -- which is not a cosmetic problem: it is the status
indicator lying, and a user who is told the backend is offline stops trusting
the answer next to it.

So V4 answers the same contract, truthfully, about itself.

The headline `status` is the worst of the V4 SERVICE components only. The
legacy dashboard surfaces this runtime deliberately does not serve are
reported as `not_configured` and are excluded from the headline, exactly as
`backend/api/routers/health.py` already excludes "no AI provider configured"
from its own: a mode the product supports is not a fault in the service.
Letting an absent optional surface turn the light red would reintroduce the
misreport by a different route.
"""

from __future__ import annotations

from typing import Any

from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4.service import credential_status, diagnostics

APP_NAME = "CreditProbe Cockpit V4"
APP_VERSION = "4.1"
BUILD_PHASE = "Cockpit V4 — single-analyst runtime"

#: Components that decide the headline. An optional surface never does.
SERVICE_COMPONENTS: frozenset[str] = frozenset({
    "cockpit_v4_api", "cockpit_v4_analyst", "cockpit_v4_release",
    "cockpit_v4_state_store"})

_SEVERITY = {"ok": 0, "empty": 1, "not_configured": 1, "degraded": 2,
             "unavailable": 3}

#: The shell reads this to tell "V4 is reachable and this surface simply is
#: not part of it" apart from "the backend is down".
OPTIONAL_MARKER = {"optional": True, "runtime": "cockpit_v4"}


def _component(name: str, status: str, detail: str,
               data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail,
            "data": dict(data or {})}


def health_payload(cfg: config_mod.V4Config | None = None, *,
                   startup_sha: str = "",
                   capabilities: dict[str, Any] | None = None,
                   preflight_error: str = "") -> dict[str, Any]:
    """A `HealthResponse`-shaped answer describing the V4 runtime.

    `capabilities` describes the runtime that is actually INSTALLED. Without
    it this function re-derives readiness from the environment, which answers
    a subtly different question -- "could a runtime be built from this
    configuration" rather than "what can the one serving you do" -- and two
    health documents disagreeing about the same process is worse than either
    being wrong on its own.
    """
    cfg = cfg or config_mod.load()
    report = diagnostics(cfg, startup_sha=startup_sha)
    checks = report.get("checks", {})

    components: list[dict[str, Any]] = [
        _component(
            "cockpit_v4_api", "ok",
            f"The Cockpit V4 API is serving on port {cfg.api_port}.",
            {"api_port": cfg.api_port, "startup_sha": startup_sha,
             "route_prefix": "/api/v1/cockpit-v4"}),
    ]

    model_ok = bool(checks.get("model_and_price", {}).get("ok"))
    credential = credential_status(cfg)
    if model_ok and credential == "PRESENT":
        analyst_status, analyst_detail = "ok", (
            "The analyst model is configured and its price is verified.")
    elif credential != "PRESENT":
        analyst_status, analyst_detail = "not_configured", (
            f"The credential required by {cfg.provider} is not set in this "
            "runtime, so no question can be answered. The API itself is running.")
    else:
        analyst_status, analyst_detail = "not_configured", (
            str(checks.get("model_and_price", {}).get("reason") or
                "The analyst model or its price card is not verified."))
    components.append(_component(
        "cockpit_v4_analyst", analyst_status, analyst_detail,
        {"credential": credential,
         "ready_for_product_help": bool(
             report.get("ready_for_product_help"))}))

    release = checks.get("release", {})
    if release.get("ok"):
        components.append(_component(
            "cockpit_v4_release", "ok",
            f"Release {cfg.release_id} is readable: "
            f"{release.get('relations')} relations over "
            f"{release.get('quarters')} reporting quarters.",
            {"release_id": cfg.release_id,
             "ready_for_sql_analysis": bool(
                 report.get("ready_for_sql_analysis"))}))
    else:
        components.append(_component(
            "cockpit_v4_release", "unavailable",
            str(release.get("reason")
                or f"Release {cfg.release_id} is not readable."),
            {"release_id": cfg.release_id}))

    store = checks.get("state_database", {})
    components.append(_component(
        "cockpit_v4_state_store", "ok" if store.get("ok") else "unavailable",
        ("The durable run store is writable."
         if store.get("ok")
         else str(store.get("reason") or "The run store is not writable.")),
        {"path": store.get("path", "")}))

    runner = checks.get("python_runner", {})
    components.append(_component(
        "cockpit_v4_python_runner",
        "ok" if runner.get("available") else "not_configured",
        (runner.get("escape_self_test", "")
         if runner.get("available")
         else str(runner.get("reason")
                  or "Python analysis is not available in this runtime.")),
        {**OPTIONAL_MARKER,
         "ready_for_python_analysis": bool(
             report.get("ready_for_python_analysis"))}))

    # The surfaces the shell's landing page and dashboards use. They live in
    # the main CreditProbe backend and are NOT part of this runtime. Saying so
    # is the whole point: the shell can then show "reachable, without the
    # dashboard" instead of "offline".
    components.append(_component(
        "legacy_dashboard_api", "not_configured",
        "This runtime serves the Cockpit V4 API only. The landing-page "
        "widgets, briefing, threads and dashboard routes are served by the "
        "main CreditProbe backend, which is not part of this instance.",
        {**OPTIONAL_MARKER,
         "served_routes": ["/api/v1/cockpit-v4/*", "/api/v1/health",
                           "/health"],
         "absent_routes": ["/api/v1/ask/*", "/api/v1/briefing",
                           "/api/v1/threads", "/api/v1/catalog"]}))

    service = [c for c in components if c["name"] in SERVICE_COMPONENTS]
    worst = max((_SEVERITY.get(c["status"], 0) for c in service), default=0)
    overall = "ok" if worst <= 1 else (
        "degraded" if worst == 2 else "unavailable")

    return {
        "status": overall,
        "app": APP_NAME,
        "version": APP_VERSION,
        "environment": "local" if cfg.local_demo_auth else "server",
        "phase": BUILD_PHASE,
        "components": components,
        # Six separate answers, not one badge. A shell that reads only
        # `status` used to show green over a runtime with no release open,
        # which could not accept a single analytical question.
        "capabilities": (capabilities if capabilities is not None
                         else report.get("capabilities", {})),
        "preflight_error": (preflight_error
                            or report.get("preflight_error", "")),
    }


__all__ = ["APP_NAME", "APP_VERSION", "BUILD_PHASE", "OPTIONAL_MARKER",
           "SERVICE_COMPONENTS", "health_payload"]
