"""
The standalone V4 application. Its own process, its own port, its own state.

Why standalone rather than a router bolted onto the product app
---------------------------------------------------------------
Mounting V4 into the existing FastAPI application would put V4 code in the
process that serves V3, EWS, What-if and everything else -- and a V4 defect
would then take those down. The whole isolation requirement is that a V4
build cannot disturb a running instance, so V4 runs as its own ASGI app on
its own port and shares nothing but read-only data files.

`routes.router` is additive and CAN be included in the product app by a
deployment that wants that later. Nothing here requires it.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import routes
from backend.cockpit_v4.run_store import RunStore
from backend.cockpit_v4.service import PreflightFailed, build_runtime
from backend.cockpit_v4.supervisor import Supervisor
from backend.cockpit_v4.worker import Worker

logger = logging.getLogger(__name__)

#: The demo principal a loopback synthetic-only profile may issue. It is
#: server-controlled: nothing in a request can name a different tenant, and
#: the tenant it DOES get is the one the pinned synthetic release actually
#: contains -- a demo principal scoped to a tenant the release has no rows
#: for reads as "the portfolio is empty" rather than "you are looking at the
#: wrong release".
#:
#: It carries NO `name`. "Local UAT" is the name of a profile, not of the
#: person at the keyboard, and a landing page that greets a deployment label
#: as if it were a colleague is worse than one that greets nobody. The label
#: travels as `profile_label`, which the operator view shows and the greeting
#: never reads.
#: The local demo profile carries `administrator`, because local UAT is
#: where the governance record is READ. On a real deployment the roles come
#: from the authenticated user and nothing here grants any.
DEMO_PRINCIPAL = {"id": "v4-local-demo", "tenant": "demo", "name": "",
                  "profile_label": "Local UAT", "demo": True,
                  "roles": ("administrator",)}


def demo_tenant(runtime: Any, cfg: config_mod.V4Config) -> str:
    """The synthetic release's own tenant, or the documented fallback."""
    tenants = (getattr(runtime, "release_summary", None) or {}).get("tenants")
    if isinstance(tenants, (list, tuple)) and tenants:
        return str(tenants[0])
    return str(DEMO_PRINCIPAL["tenant"])


def startup_sha() -> str:
    """The SHA the PROCESS started from, read once.

    Not re-read later: a checkout that moves while the process runs would
    otherwise make the trace claim code that is not what is executing.
    """
    cached = os.environ.get("COCKPIT_V4_STARTUP_SHA", "").strip()
    if cached:
        return cached
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[2]), timeout=5)
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _demo_resolver(cfg: config_mod.V4Config, runtime: Any = None) -> Any:
    tenant = demo_tenant(runtime, cfg)

    def resolve(request: Request) -> dict[str, Any] | None:
        if not cfg.local_demo_auth:
            return None
        client = request.client.host if request.client else ""
        # Loopback only. A demo principal that works off-host is not a demo
        # profile, it is an unauthenticated deployment.
        if client not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return None
        return {**DEMO_PRINCIPAL, "tenant": tenant}
    return resolve


def _session_resolver() -> Any:
    """Reuse the product's authenticated user when it is importable."""
    def resolve(request: Request) -> dict[str, Any] | None:
        user = getattr(request.state, "user", None)
        if user is None:
            return None
        return {"id": str(getattr(user, "id", "") or getattr(user, "email", "")),
                "tenant": str(getattr(user, "tenant_id", "") or "default"),
                "name": str(getattr(user, "name", "") or ""),
                # WHAT THIS PERSON MAY SEE, carried from the product's own
                # user rather than assumed. The principal carried only an
                # id and a tenant, so there was nothing to gate the SQL in
                # a governance record on; an absent `roles` grants nothing,
                # which is the right failure direction.
                "roles": tuple(
                    str(role) for role in (getattr(user, "roles", ()) or ()))}
    return resolve


def create_app(cfg: config_mod.V4Config | None = None, *,
               provider: Any = None, verify_model: bool = True,
               start_workers: bool = True) -> FastAPI:
    cfg = cfg or config_mod.load()
    sha = startup_sha()

    app = FastAPI(title="CreditProbe Cockpit V4", version="4.1",
                  docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[f"http://127.0.0.1:{cfg.ui_port}",
                       f"http://localhost:{cfg.ui_port}"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    store = RunStore(cfg.state_database)
    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    for directory in (cfg.artifacts_dir, cfg.logs_dir, cfg.pids_dir):
        directory.mkdir(parents=True, exist_ok=True)

    runtime = None
    preflight_error = ""
    try:
        runtime = build_runtime(cfg, provider=provider,
                               verify_model=verify_model)
    except PreflightFailed as exc:
        # The app still starts, so /diagnostics can SAY what is missing.
        # It just cannot accept a run that would need the missing thing.
        preflight_error = f"{exc.code}: {exc}"
        logger.warning("V4 preflight incomplete: %s", preflight_error)

    # There is NO stand-in runtime.
    #
    # A placeholder carrying only a config used to be installed here when
    # preflight failed, so that /diagnostics could still answer. It did far
    # more than that: every consumer then HAD a runtime, so nothing refused
    # anything. The attention route called a method the real runtime has and
    # the placeholder did not and returned a traceback; run creation had no
    # reason to object and answered 202 for work that nothing would ever do,
    # because the worker check looked at the REAL runtime and correctly saw
    # none. A stand-in that is present but cannot work is worse than an
    # absence, because an absence is checkable.
    #
    # So the runtime is the real one or it is None, and `readiness` answers
    # what that means for each capability.
    resolver = (_demo_resolver(cfg, runtime) if cfg.local_demo_auth
                else _session_resolver())
    routes.install(store=store, runtime=runtime, cfg=cfg,
                   preflight_error=preflight_error,
                   principal_resolver=resolver, startup_sha=sha)
    app.include_router(routes.router)
    # The shell's status indicator polls /api/v1/health. Serving
    # it here is what stops the header reporting the whole
    # backend as offline when it is pointed at V4.
    app.include_router(routes.compat_router)

    app.state.cockpit_v4 = {
        "config": cfg, "store": store, "runtime": runtime,
        "startup_sha": sha, "preflight_error": preflight_error}

    if start_workers and runtime is not None:
        worker = Worker(store=store, runtime=runtime)
        supervisor = Supervisor(store=store,
                                poll_seconds=cfg.supervisor_poll_seconds,
                                lease_stale_seconds=cfg.lease_stale_seconds)
        threading.Thread(target=worker.serve_forever, daemon=True,
                         name="cockpit-v4-worker").start()
        threading.Thread(target=supervisor.serve_forever, daemon=True,
                         name="cockpit-v4-supervisor").start()
        app.state.cockpit_v4["worker"] = worker
        app.state.cockpit_v4["supervisor"] = supervisor

    # §41: which books this process opened, printed at startup, every time.
    # Which release, from which bytes, at which scale, over how many periods
    # -- for EACH book. A deployment serving one book and not the other is
    # not a thing to discover afterwards from the numbers on the screen.
    _announce_books()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        # `ok` says the PROCESS is answering. What the process can do is a
        # separate question with six separate answers, and a shell that reads
        # only `ok` used to show a green badge over a runtime that could not
        # accept a single analytical question.
        from backend.cockpit_v4 import readiness as ready_mod

        state = ready_mod.assess(runtime, cfg=cfg,
                                 preflight_error=preflight_error)
        return {"ok": True, "service": "cockpit-v4", "startup_sha": sha,
                "preflight_error": preflight_error,
                "capabilities": state.to_dict(),
                "ready": state.ready,
                "api_port": cfg.api_port}

    return app


def _announce_books() -> None:
    """One line per book, at startup. Never one line for "the release"."""
    try:
        from backend.cockpit_v4 import domain_resolver as resolver
        from backend.cockpit_v4 import domains as dom_mod

        available = resolver.availability()
    except Exception as exc:  # noqa: BLE001 - announcing must never fail boot
        logger.warning("V4 could not enumerate its books: %s", exc)
        return

    for status in available.statuses:
        label = dom_mod.LABELS[status.domain_id]
        if not status.ready or status.scope is None:
            logger.warning(
                "V4 book %s: NOT AVAILABLE (%s). Nothing was substituted "
                "for it. Publish with: %s",
                label, status.reason or "no published release",
                resolver.provision_command(status.domain_id))
            continue
        scope = status.scope
        askable = "askable" if resolver.analysis_supported(
            status.domain_id) else "browse only"
        logger.info(
            "V4 book %s: release %s fingerprint %s, %s %s, %s %s periods "
            "to %s, %d relations, %s",
            label, scope.release_id, scope.release_fingerprint[:16],
            scope.currency, scope.amount_scale, len(scope.periods),
            scope.reporting_frequency, scope.latest_period,
            len(scope.relations), askable)


__all__ = ["DEMO_PRINCIPAL", "create_app", "startup_sha"]
