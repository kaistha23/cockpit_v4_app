"""MODEL MOCK · REAL DATABASE/RUNNER · REAL HTTP · REPRODUCTION.

A runtime that cannot work must say so, not pretend.

The live sequence, from a Mac log
---------------------------------
    V4 preflight incomplete: DATA_UNAVAILABLE:
    release 'v4-saudi-20q-v1' is not published in this runtime.
    ...
    Application startup complete.
    Uvicorn running on http://127.0.0.1:8414
    POST /api/v1/cockpit-v4/runs -> 202
    GET  /api/v1/health          -> 200
    GET  /api/v1/cockpit-v4/attention -> 500
        AttributeError: '_Runtime' object has no attribute 'scope_for'

and on screen, for over a minute:

    Request accepted
    Understanding the request: not started

Four symptoms, one cause. When preflight failed the application substituted a
placeholder object carrying only a config and handed it to the routes as the
runtime. Every consumer then HAD a runtime, so nothing refused anything: the
attention route called a method the real runtime has and the placeholder did
not, run creation had no reason to object, and the worker was never started
because THAT check looked at the real runtime and correctly saw none.

A stand-in that is present but cannot work is worse than an absence, because
an absence is checkable.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.cockpit_v4 import readiness as ready_mod
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import states as st

P = "/api/v1/cockpit-v4"


def _app(store, runtime, cfg, preflight_error=""):
    app = FastAPI()
    routes.install(store=store, runtime=runtime, cfg=cfg,
                   preflight_error=preflight_error,
                   principal_resolver=lambda r: {"id": "u1",
                                                 "tenant": "demo-tenant"},
                   startup_sha="testsha")
    app.include_router(routes.router)
    app.include_router(routes.compat_router)
    return TestClient(app)


@pytest.fixture
def unprovisioned(store_db, v4_config):
    """The Mac's exact state: configured for a release that is not there."""
    from dataclasses import replace

    cfg = replace(v4_config, release_id="v4-saudi-20q-v1")
    return _app(store_db, None, cfg,
                preflight_error=("DATA_UNAVAILABLE: release "
                                 "'v4-saudi-20q-v1' is not published in this "
                                 "runtime."))


@pytest.fixture
def provisioned(store_db, runtime):
    return _app(store_db, runtime, runtime.cfg)


# ---- §5, §10: a run that cannot be processed is not accepted -----------

def test_a_run_is_refused_when_no_analysis_can_run(unprovisioned):
    """202 is a promise to do the work. Nothing that cannot keep it sends one."""
    response = unprovisioned.post(f"{P}/runs",
                                  json={"question": "Total EAD by sector?"})
    assert response.status_code == 503, (
        f"the API answered {response.status_code}; the live failure was a "
        f"202 for a run nothing would ever process")
    detail = response.json()["detail"]
    assert detail["error_code"] == st.DATA_UNAVAILABLE
    assert detail["capability"] in (ready_mod.PRODUCT_HELP_READY,
                                    ready_mod.SQL_ANALYSIS_READY)
    assert detail["readiness"][ready_mod.SQL_ANALYSIS_READY] is False


def test_a_refused_run_leaves_nothing_stuck(unprovisioned, store_db):
    """The screenshot: "Request accepted" for a minute and then forever."""
    unprovisioned.post(f"{P}/runs", json={"question": "Total EAD by sector?"})
    with store_db._connect() as conn:  # noqa: SLF001 - the store is the oracle
        rows = conn.execute("SELECT state FROM runs").fetchall()
    assert rows == [], (
        f"a run was persisted that nothing can process: {rows}")


# ---- §6, §7: attention is typed, never a traceback ---------------------

def test_attention_returns_a_typed_refusal_not_an_attribute_error(
        unprovisioned, monkeypatch):
    """THE live traceback, as an assertion.

    The dashboard no longer reads the runtime's pinned release: it opens its
    own DOMAIN release, so "unprovisioned" now means that domain's release is
    missing rather than the runtime's. The property under test is unchanged
    and is the one that matters -- a missing book is a typed refusal naming
    the command that publishes it, never an AttributeError and never an empty
    feed that reads as "your portfolio is fine".
    """
    from backend.cockpit_v4 import domains as dom_mod
    from backend.cockpit_v4 import lake as lake_mod

    monkeypatch.setitem(dom_mod.DEFAULT_RELEASES, dom_mod.CORPORATE,
                        "v4-saudi-corporate-not-published")
    response = unprovisioned.get(f"{P}/attention")
    assert response.status_code == 503, response.text
    assert "AttributeError" not in response.text
    detail = response.json()["detail"]
    assert detail["error_code"] == st.DATA_UNAVAILABLE
    assert detail["domain_id"] == dom_mod.CORPORATE
    assert "seed_domains.py" in detail["provision_command"], (
        "a refusal that does not say how to fix it makes the operator guess")
    assert "Nothing was substituted" in detail["message"]
    assert lake_mod is not None


def test_attention_works_against_the_real_runtime(provisioned, release_id):
    """§13. The regression that would have caught `scope_for` going missing.

    Run through the real FastAPI application against the real runtime object
    the application builds, not a stand-in that happens to expose whatever
    the test needed.
    """
    from backend.cockpit_v4 import domains as dom_mod

    response = provisioned.get(f"{P}/attention")
    assert response.status_code == 200, response.text
    feed = response.json()
    # Its own DOMAIN release, not the runtime's pinned one: the dashboard is
    # a property of the book being looked at rather than of the process.
    assert feed["domain_id"] == dom_mod.DEFAULT_DOMAIN
    assert feed["release_id"] == dom_mod.DEFAULT_RELEASES[dom_mod.CORPORATE]
    assert feed["release_id"] != release_id
    assert feed["segments_requiring_attention"]


# ---- §8: health tells the truth ---------------------------------------

def test_health_reports_capability_state_not_a_green_badge(unprovisioned):
    response = unprovisioned.get("/api/v1/health")
    assert response.status_code == 200, (
        "the health DOCUMENT is still served; what it says is the point")
    body = response.json()
    caps = body["capabilities"]
    assert caps[ready_mod.PROCESS_ALIVE] is True
    assert caps[ready_mod.RELEASE_READY] is False
    assert caps[ready_mod.SQL_ANALYSIS_READY] is False
    # The dashboard is computed from a BOOK, not from the release preflight
    # failed on, and it costs no provider call -- so a runtime that cannot
    # accept a question can still serve every card on the Cockpit. This is
    # true when a domain book is published and false when none is.
    from backend.cockpit_v4 import domain_resolver as resolver

    assert caps[ready_mod.ATTENTION_READY] is bool(
        resolver.availability().ready_domains)


def test_health_reports_everything_ready_when_it_is(provisioned):
    body = provisioned.get("/api/v1/health").json()
    caps = body["capabilities"]
    assert caps[ready_mod.RELEASE_READY] is True
    assert caps[ready_mod.SQL_ANALYSIS_READY] is True
    assert caps[ready_mod.ATTENTION_READY] is True


# ---- §9: diagnostics name the release and never substitute -------------

def test_diagnostics_say_which_release_and_why_it_will_not_open(v4_config):
    from dataclasses import replace

    from backend.cockpit_v4.service import diagnostics

    report = diagnostics(replace(v4_config, release_id="v4-not-published"))
    release = report["checks"]["release"]
    assert release["ok"] is False
    assert release["code"] == st.DATA_UNAVAILABLE
    assert release["header"]["release_id"] == "v4-not-published"
    assert release["header"]["published"] is False
    assert release["substituted"] is False, (
        "a runtime configured for one release must never serve another")
    assert "seed_release.py --release v4-not-published" in release["remedy"]
    assert report["capabilities"][ready_mod.SQL_ANALYSIS_READY] is False
    # The dashboard is computed from a BOOK, not from the release preflight
    # failed on, and it costs no provider call -- so a runtime that cannot
    # accept a question can still serve every card on the Cockpit. This is
    # true when a domain book is published and false when none is.
    from backend.cockpit_v4 import domain_resolver as resolver

    assert report["capabilities"][ready_mod.ATTENTION_READY] is bool(
        resolver.availability().ready_domains)


def test_diagnostics_state_the_saudi_release_when_it_is_there(v4_config,
                                                              release_id):
    from backend.cockpit_v4.service import diagnostics

    report = diagnostics(v4_config)
    header = report["checks"]["release"]["header"]
    assert header["release_id"] == release_id
    assert header["country"] == "Saudi Arabia"
    assert header["reporting_currency"] == "SAR"
    assert header["amount_scale"] == "million"
    assert len(header["release_fingerprint"]) == 64
    assert report["capabilities"][ready_mod.ATTENTION_READY] is True


# ---- §2: the remedy is V4's own, never V3's ---------------------------

def test_the_remedy_is_the_v4_seeder_and_not_the_v3_builder(v4_config):
    from dataclasses import replace

    from backend.cockpit_v4.service import PreflightFailed, load_release

    with pytest.raises(PreflightFailed) as raised:
        load_release(replace(v4_config, release_id="v4-not-published"))
    message = str(raised.value)
    assert "scripts/cockpit_v4/seed_release.py" in message
    assert "build_cockpit_agentic_v3" not in message, (
        "V3's own message sends a V4 operator to the wrong tool; V4 must not "
        "forward it")
    assert "Nothing was substituted" in message


# ---- §14: test and production runtimes must agree ---------------------

def test_no_test_installs_a_runtime_production_would_not_recognise():
    """The reason every test here passed while the Mac was broken.

    The API fixture installed a stand-in carrying only a config -- the same
    shape the application substituted on preflight failure. The suite and the
    broken runtime were fake in the SAME way, so the suite could not see it.
    """
    import pathlib

    tests = pathlib.Path(__file__).resolve().parent
    here = pathlib.Path(__file__).name
    offenders = []
    for path in sorted(tests.glob("test_*.py")):
        if path.name == here:
            # This module installs `None` on purpose, through a helper, to
            # reproduce the failing runtime. Scanning its own source would
            # only ever find the scanner.
            continue
        for line_no, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if "routes.install(store=" not in line:
                continue
            if "runtime=runtime" in line or "runtime=None" in line:
                continue
            offenders.append(f"{path.name}:{line_no} {line.strip()[:70]}")
    assert offenders == [], (
        "these install a runtime that is neither the real one nor an honest "
        "absence:\n" + "\n".join(offenders))


def test_the_readiness_contract_covers_the_real_runtime_interface(runtime):
    """Whatever `assess` reads, a real runtime must actually have."""
    state = ready_mod.assess(runtime, cfg=runtime.cfg)
    assert state[ready_mod.RELEASE_READY] is True
    assert callable(getattr(runtime, "scope_for", None)), (
        "every consumer derives the authorized scope through scope_for; a "
        "runtime without it cannot serve attention, analysis or saved work")
    assert runtime.scope_for({"id": "u1", "tenant": "demo-tenant"}) is not None


def test_an_absent_runtime_is_assessed_as_absent(v4_config):
    """No runtime means no analysis. It does not mean no dashboard.

    Four of the five capabilities need a runtime: they need a model, a
    price, a session or a Python jail. The dashboard needs a published book
    and nothing else, and reporting it unavailable over a page that is
    rendering it is how a reader learns to stop reading the header.
    """
    from backend.cockpit_v4 import domain_resolver as resolver

    state = ready_mod.assess(None, cfg=v4_config,
                             preflight_error="DATA_UNAVAILABLE: nope")
    assert state[ready_mod.PROCESS_ALIVE] is True
    for capability in (ready_mod.RELEASE_READY, ready_mod.PRODUCT_HELP_READY,
                       ready_mod.SQL_ANALYSIS_READY,
                       ready_mod.PYTHON_ANALYSIS_READY):
        assert state[capability] is False, capability
    assert state[ready_mod.ATTENTION_READY] is bool(
        resolver.availability().ready_domains)
    assert state.error_code == st.DATA_UNAVAILABLE
    assert "seed_release.py" in state.remedy


# ---- §10: an accepted run is a promise, and it is kept -----------------

def test_an_accepted_run_advances_beyond_accepted(provisioned, store_db,
                                                  runtime, release_id):
    """A run may never sit in ACCEPTED because preflight broke the worker.

    Two outcomes are legitimate: the run is processed, or it settles with a
    typed terminal failure. Staying ACCEPTED is neither, and it is what the
    live Mac showed for a minute and then for the rest of the afternoon.
    """
    from conftest import ScriptedProvider, ScriptedResult, final, intent, \
        tool_call
    from backend.cockpit_v4 import states as states_mod
    from backend.cockpit_v4.worker import Worker

    response = provisioned.post(f"{P}/runs", json={"question": "Who are you?"})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert store_db.get_run(run_id).state == states_mod.ACCEPTED

    # The worker the application starts alongside the API, run once.
    runtime.provider = ScriptedProvider([ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("PRODUCT_HELP", "COCKPIT"),
              narrative="The Cockpit answers credit questions."))])])
    # Exactly what `serve_forever` does for one iteration: claim, execute.
    worker = Worker(store=store_db, runtime=runtime)
    claimed = store_db.claim_next(worker.worker_id)
    assert claimed is not None, (
        "the worker claimed nothing, so the run would have waited forever")
    worker.execute(claimed)

    settled = store_db.get_run(run_id)
    assert settled.state != states_mod.ACCEPTED, (
        "the run is still ACCEPTED after the worker ran")
    assert settled.state in states_mod.TERMINAL_STATES, settled.state


def test_a_runtime_that_cannot_work_starts_no_worker(store_db, v4_config):
    """The other half: the worker is only started for a real runtime.

    That check was already right, which is exactly why the 202 was so bad --
    the API accepted work for a worker that correctly refused to exist.
    """
    import inspect

    from backend.cockpit_v4 import app as app_mod

    source = inspect.getsource(app_mod.create_app)
    assert "start_workers and runtime is not None" in source
    # And there is no longer a stand-in that could make `runtime` truthy.
    assert "class _Runtime" not in source, (
        "a placeholder runtime is what made every consumer think it had one")
