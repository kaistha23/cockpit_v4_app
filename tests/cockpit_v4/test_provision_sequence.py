"""REAL PROCESS · REAL DATABASE/RUNNER · REAL HTTP · REPRODUCTION.

The Mac's exact sequence, both halves, against a release that really is not
there and then really is.

Every other test in this suite runs against the demonstration release, which
is published in this checkout. This one provisions its OWN release into its
OWN namespace, so "the release is absent" is a fact about the filesystem
rather than a fixture pretending. It then runs the documented V4 provisioning
command as a subprocess -- the same command the failure message hands an
operator -- and asserts the same endpoints now work.

Slow by nature: it builds a small twenty-quarter book. Marked so it can be
deselected, and it never touches the demonstration release or the V3
namespace.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import readiness as ready_mod
from backend.cockpit_v4 import routes
from backend.cockpit_v4 import states as st

ROOT = Path(__file__).resolve().parents[2]
P = "/api/v1/cockpit-v4"

#: Its own id and its own namespace. Neither is the demonstration release,
#: and neither is anything V3 can see.
PROBE_RELEASE = "v4-provision-sequence-probe"
PROBE_NAMESPACE = "cockpit_v4_provision_probe"


@pytest.fixture(scope="module")
def probe_namespace():
    """A namespace of our own, removed afterwards whatever happens."""
    import shutil

    from backend.config import settings

    target = Path(settings.analytics_dir).parent / PROBE_NAMESPACE
    if target.exists():
        shutil.rmtree(target)
    try:
        yield target
    finally:
        if target.exists():
            shutil.rmtree(target)


def _cfg(v4_config):
    return replace(v4_config, release_id=PROBE_RELEASE)


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


import contextlib


@contextlib.contextmanager
def _in_namespace(namespace: str):
    """Read and write the probe's lake, not the demonstration one.

    `settings` is a frozen dataclass built once at import, so the
    environment variable the SUBPROCESS reads has no effect in this process.
    `store.namespace` is the function every V3 path actually calls, so that
    is what is swapped -- and put back, whatever happens, because every other
    test in the suite reads the demonstration release through it.
    """
    from backend.cockpit_agentic import store as v3_store
    from backend.cockpit_v4 import release as rel
    from backend.cockpit_v4 import service

    previous = v3_store.namespace
    v3_store.namespace = lambda: namespace
    service.reset_caches()
    rel.forget()
    try:
        yield
    finally:
        v3_store.namespace = previous
        service.reset_caches()
        rel.forget()


def _build_runtime(cfg, namespace: str):
    """Exactly what the application does, in this namespace."""
    from backend.cockpit_v4 import service
    from backend.cockpit_v4.service import PreflightFailed, load_release

    with _in_namespace(namespace):
        try:
            catalog, coverage, summary = load_release(cfg)
        except PreflightFailed as exc:
            return None, f"{exc.code}: {exc}"
    return service.Runtime(
        cfg=cfg, capability=None, provider=None, catalog=catalog,
        coverage=coverage, release_summary=summary), ""


@pytest.mark.slow
def test_the_whole_sequence(probe_namespace, store_db, v4_config):
    cfg = _cfg(v4_config)

    # ---- before: the release genuinely is not there -------------------
    assert not probe_namespace.exists()
    runtime, preflight_error = _build_runtime(cfg, PROBE_NAMESPACE)
    assert runtime is None, "the probe release must not already exist"
    assert preflight_error.startswith(st.DATA_UNAVAILABLE)
    assert "seed_release.py" in preflight_error

    client = _app(store_db, None, cfg, preflight_error=preflight_error)

    # health tells the truth
    caps = client.get("/api/v1/health").json()["capabilities"]
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

    # attention is typed, not a traceback.
    #
    # The dashboard reads its own DOMAIN release rather than the runtime's
    # pinned one, so an unprovisioned RUNTIME no longer makes it unavailable:
    # a published corporate book can be looked at while the runtime that
    # would ANSWER questions about it is not ready. The refusal being tested
    # is the domain's own, so the domain is the thing to unpublish.
    from backend.cockpit_v4 import domains as dom_mod

    published = dict(dom_mod.DEFAULT_RELEASES)
    dom_mod.DEFAULT_RELEASES[dom_mod.CORPORATE] = "v4-corporate-absent"
    try:
        attention = client.get(f"{P}/attention")
        assert attention.status_code == 503
        assert "AttributeError" not in attention.text
        assert attention.json()["detail"]["error_code"] == st.DATA_UNAVAILABLE
        assert "seed_domains.py" in (
            attention.json()["detail"]["provision_command"])
    finally:
        dom_mod.DEFAULT_RELEASES.update(published)

    # a run is refused, and nothing is left behind to wait forever
    run = client.post(f"{P}/runs", json={"question": "Total EAD by sector?"})
    assert run.status_code == 503, run.text
    with store_db._connect() as conn:  # noqa: SLF001
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0

    # ---- provision, with the command the failure message names --------
    command = [sys.executable, "scripts/cockpit_v4/seed_release.py",
               "--release", PROBE_RELEASE, "--namespace", PROBE_NAMESPACE,
               "--borrowers", "20", "--facilities", "40"]
    environment = {**os.environ, "COCKPIT_AGENTIC_V3": "true"}
    result = subprocess.run(command, cwd=str(ROOT), env=environment,
                            capture_output=True, text=True, timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SAR million" in result.stdout
    assert "quarters:  20" in result.stdout
    assert "evidence not rewritten" in result.stdout, (
        "a probe release must not be published in the docs as the "
        "demonstration book")

    # ---- after: the same endpoints work -------------------------------
    runtime, preflight_error = _build_runtime(cfg, PROBE_NAMESPACE)
    assert runtime is not None, preflight_error
    assert runtime.catalog.reporting_currency == "SAR"
    assert runtime.catalog.amount_scale == "million"

    client = _app(store_db, runtime, cfg)
    caps = client.get("/api/v1/health").json()["capabilities"]
    assert caps[ready_mod.RELEASE_READY] is True
    assert caps[ready_mod.ATTENTION_READY] is True

    from backend.cockpit_v4 import release as rel

    with _in_namespace(PROBE_NAMESPACE):
        header = rel.header(release_id=PROBE_RELEASE,
                            catalog=runtime.catalog,
                            release_summary=runtime.release_summary)
        assert header.country == "Saudi Arabia"
        assert header.reporting_currency == "SAR"
        assert header.amount_scale == "million"
        assert len(header.release_fingerprint) == 64

        # The dashboard answers from the DOMAIN release, which is a
        # different thing from the runtime's pinned analytical release --
        # provisioning the latter is what this sequence is about, and the
        # former is provisioned by seed_domains.py.
        from backend.cockpit_v4 import domains as dom_mod

        attention = client.get(f"{P}/attention")
        assert attention.status_code == 200, attention.text
        assert attention.json()["release_id"] == (
            dom_mod.DEFAULT_RELEASES[dom_mod.DEFAULT_DOMAIN])


@pytest.mark.slow
def test_seeding_twice_does_not_rebuild_a_published_release(probe_namespace):
    """Immutability, against the real command rather than its source."""
    command = [sys.executable, "scripts/cockpit_v4/seed_release.py",
               "--release", PROBE_RELEASE, "--namespace", PROBE_NAMESPACE,
               "--borrowers", "20", "--facilities", "40"]
    environment = {**os.environ, "COCKPIT_AGENTIC_V3": "true"}
    first = subprocess.run(command, cwd=str(ROOT), env=environment,
                           capture_output=True, text=True, timeout=900)
    assert first.returncode == 0, first.stdout + first.stderr

    from backend.cockpit_v4 import release as rel

    with _in_namespace(PROBE_NAMESPACE):
        before = rel.fingerprint(PROBE_RELEASE)
        second = subprocess.run(command, cwd=str(ROOT), env=environment,
                                capture_output=True, text=True, timeout=900)
        assert second.returncode == 0
        assert "already exists" in second.stdout
        assert "immutable" in second.stdout
        rel.forget(PROBE_RELEASE)
        assert rel.fingerprint(PROBE_RELEASE) == before
