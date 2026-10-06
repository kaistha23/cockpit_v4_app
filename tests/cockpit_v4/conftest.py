"""
Fixtures for the V4 tests, and the scripted provider they use.

The provider here is a MODEL MOCK and every test that uses it is labelled as
such. It proves the application's half of the contract: that a given model
action produces the right validation, the right execution, the right events
and the right terminal state. It proves nothing about answer quality, and no
test in this suite claims it does.

What it does NOT mock: the SQL runner, the catalog, the release, the store,
the ledger or the event stream. Those are real, so "real SQL ran" is a fact
these tests establish even while the model is scripted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

RELEASE_ID = os.environ.get("COCKPIT_V4_TEST_RELEASE", "v4-saudi-20q-v1")
os.environ.setdefault("COCKPIT_AGENTIC_V3_NAMESPACE", "cockpit_v4")


@pytest.fixture(autouse=True)
def _no_limits_profile(monkeypatch):
    """A developer's .env must not change what these tests measure.

    `backend/config.py` runs `load_dotenv` at import, so a local
    `COCKPIT_V4_LIMITS_PROFILE=local` would otherwise widen every analytical
    limit the suite asserts on.
    """
    monkeypatch.delenv("COCKPIT_V4_LIMITS_PROFILE", raising=False)


# ---- the scripted provider --------------------------------------------

class ScriptedResult:
    """One `converse` return, shaped like the real adapter's."""

    def __init__(self, *, tool_calls=None, text="", stop_reason="tool_use",
                 input_tokens=1200, output_tokens=300, model="mock-analyst",
                 request_id="req-mock") -> None:
        self.tool_calls = list(tool_calls or [])
        self.text = text
        self.stop_reason = stop_reason
        self.assistant_blocks = (
            [{"type": "tool_use", "id": c["id"], "name": c["name"],
              "input": c["input"]} for c in self.tool_calls]
            or ([{"type": "text", "text": text}] if text else []))
        self.model = model
        self.request_id = request_id
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.duration_ms = 5


class ScriptedProvider:
    """Returns queued turns in order. Records what it was actually sent.

    `sent` is the evidence for the "no whole catalogue" and "original wording
    preserved" assertions: those are checked against the bytes that would have
    gone to a provider, not against a summary of them.
    """

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.sent: list[dict[str, Any]] = []
        self.count_calls = 0

    def count_tokens(self, *, system=None, messages=None, tools=None,
                     model="") -> int:
        self.count_calls += 1
        import json

        payload = json.dumps({"s": system, "m": messages, "t": tools},
                             default=str)
        return int(len(payload) / 3.5) + 1

    def converse(self, *, system, messages, tools=None, max_tokens=4096,
                 model="", purpose="", role="", timeout=0.0,
                 allow_retry=True, effort="", tool_choice=None,
                 output_config=None, temperature=None) -> Any:
        assert allow_retry is False, (
            "the SDK must not retry behind the ledger's back")
        # `tool_choice` and `output_config` are recorded, not ignored: the
        # snapshot tests assert what a turn was actually allowed to be, and
        # a double that dropped them would let the run go back to sending
        # unconstrained turns without a single test noticing. Effort lives
        # inside `output_config` -- the bare `effort` keyword above is
        # accepted and unused, and asserting on it would be asserting on
        # nothing. Thinking is spent from the output allowance, so what is
        # in `output_config` is part of how much room an answer had.
        self.sent.append({"system": system, "messages": [dict(m) for m in
                                                         messages],
                          "tools": tools, "max_tokens": max_tokens,
                          "model": model, "purpose": purpose,
                          "timeout": timeout, "tool_choice": tool_choice,
                          "output_config": output_config})
        if not self.script:
            raise AssertionError("the scripted provider ran out of turns")
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if callable(nxt):
            try:
                nxt = nxt(messages)
            except Exception as exc:  # surface a TEST bug as a test bug
                import json as _json
                body = messages[-1].get("content")
                raise AssertionError(
                    f"the scripted turn could not build its response "
                    f"({type(exc).__name__}: {exc}). The tool result it read "
                    f"was: {_json.dumps(body, default=str)[:1500]}") from exc
        if isinstance(nxt, Exception):
            # A turn may RETURN the failure it wants to simulate, which lets
            # a timed turn consume its latency before failing.
            raise nxt
        return nxt

    # -- assertions the tests reuse --------------------------------------

    def last_input_text(self) -> str:
        import json

        last = self.sent[-1]
        return json.dumps({"system": last["system"],
                           "messages": last["messages"]}, default=str,
                          ensure_ascii=False)

    def first_input_text(self) -> str:
        import json

        first = self.sent[0]
        return json.dumps({"system": first["system"],
                           "messages": first["messages"]}, default=str,
                          ensure_ascii=False)


def tool_call(name: str, arguments: dict[str, Any], call_id: str = "tu-1"
              ) -> dict[str, Any]:
    return {"id": call_id, "name": name, "input": arguments}


def intent(mode: str = "PRODUCT_HELP", owner: str = "COCKPIT", *,
           understood: str = "what this is", language: str = "en",
           ambiguities=(), excluded=(), rationale: str = "direct",
           resolved=(), mappings=()) -> dict:
    return {"query_mode": mode, "owner": owner,
            "understood_request": understood, "response_language": language,
            "blocking_ambiguities": list(ambiguities),
            "resolved_assumptions": list(resolved),
            "canonical_mappings": list(mappings),
            "excluded_parts": list(excluded),
            "public_rationale": rationale}


def final(**overrides) -> dict[str, Any]:
    body = {"intent": intent(), "disposition": "answer",
            "narrative": "CreditProbe Cockpit is the corporate credit "
                         "analysis workspace.",
            "coverage": [], "numeric_claims": [], "evidence_refs": [],
            "tables": [], "charts": [], "limitations": [],
            "suggested_questions": [], "clarification_question": "",
            "clarification_options": [], "referral_owner": "",
            "referral_reason": ""}
    body.update(overrides)
    return body


# ---- fixtures ----------------------------------------------------------

@pytest.fixture(scope="session")
def release_id() -> str:
    from backend.cockpit_agentic import store

    try:
        store.read_manifest(RELEASE_ID)
    except store.ReleaseNotFound:
        pytest.skip(f"release {RELEASE_ID} is not published in this runtime; "
                    f"run scripts/cockpit_v4/seed_release.py")
    return RELEASE_ID


@pytest.fixture
def store_db(tmp_path):
    from backend.cockpit_v4.run_store import RunStore

    return RunStore(tmp_path / "state.sqlite3")


@pytest.fixture
def capability():
    from backend.cockpit_v4.capability import Capability, PriceCard

    return Capability(
        provider="anthropic", model_id="mock-analyst", sdk_version="test",
        context_tokens=200_000, max_output_tokens=128_000,
        supports_tools=True, supports_token_counting=True,
        price=PriceCard(input_usd_per_mtok=5.0, output_usd_per_mtok=25.0,
                        cache_write_usd_per_mtok=6.25,
                        cache_read_usd_per_mtok=0.5),
        source="test fixture", verified_at="2026-09-10T00:00:00Z",
        live_verified=True)


@pytest.fixture
def v4_config(tmp_path, release_id):
    from backend.cockpit_v4.config import V4Config

    return V4Config(
        enabled=True, provider="anthropic", reasoning_model="mock-analyst",
        runtime_dir=tmp_path / "runtime",
        state_database=str(tmp_path / "state.sqlite3"),
        release_id=release_id, api_port=8414, ui_port=5414,
        local_demo_auth=True, price_card_path=str(tmp_path / "prices.json"),
        memory_enabled=False, memory_model="", default_mode="standard",
        heartbeat_seconds=5.0, lease_heartbeat_seconds=2.0,
        lease_stale_seconds=10.0, supervisor_poll_seconds=2.0,
        credential_present=True, missing=())


@pytest.fixture
def runtime(v4_config, capability, release_id):
    """A real catalog and a real release; only the model is scripted."""
    from backend.cockpit_v4.service import Runtime, load_release

    catalog, _, summary = load_release(v4_config)
    return Runtime(cfg=v4_config, capability=capability, provider=None,
                   catalog=catalog, coverage=None, release_summary=summary)


@pytest.fixture
def make_run(store_db, release_id):
    def _make(question: str, *, mode: str = "standard",
              tenant: str = "demo-tenant", principal: str = "u1"):
        thread_id = store_db.create_thread(tenant_id=tenant,
                                           principal_id=principal)
        record, _ = store_db.accept_run(
            thread_id=thread_id, tenant_id=tenant, principal_id=principal,
            question=question, mode=mode, release_id=release_id,
            ui_filters={}, idempotency_key="", body_digest="",
            startup_sha="testsha", deadline_at="")
        return record
    return _make


@pytest.fixture
def drive(store_db, runtime, make_run):
    """Run one scripted conversation end to end through the real worker."""
    def _drive(question: str, script: list[Any], *, mode: str = "standard",
               record=None):
        from backend.cockpit_v4.worker import Worker

        provider = ScriptedProvider(script)
        runtime.provider = provider
        record = record or make_run(question, mode=mode)
        worker = Worker(store=store_db, runtime=runtime)
        outcome = worker.execute(record)
        return outcome, provider, record
    return _drive


@pytest.fixture
def session(runtime, release_id):
    """A real read-only DuckDB session over the pinned release."""
    from backend.cockpit_agentic import scope as v3_scope
    from backend.cockpit_agentic import sql as v3_sql
    from backend.cockpit_v4.service import _Principal

    scope = v3_scope.for_principal(
        _Principal({"tenant": "demo-tenant", "id": "u"}),
        dataset_release_id=release_id)
    return v3_sql.open_session(scope=scope, catalog=runtime.catalog)


@pytest.fixture
def service(session, store_db, runtime, release_id):
    """The real execution service: validates, binds and runs for real."""
    from backend.cockpit_v4.config import STANDARD_LIMITS
    from backend.cockpit_v4.execute_tool import ExecutionService

    return ExecutionService(
        session=session, scope=None, catalog=runtime.catalog, store=store_db,
        run_id="r-exec", tenant_id="demo-tenant", release_id=release_id,
        limits=STANDARD_LIMITS)


@pytest.fixture
def ledger_factory(store_db, capability):
    """A real ledger over a real store, for budget arithmetic."""
    from dataclasses import replace

    from backend.cockpit_v4.budgets import Ledger
    from backend.cockpit_v4.config import STANDARD_LIMITS

    def _make(**overrides):
        limits = replace(STANDARD_LIMITS, **overrides)
        run_id = "r-budget"
        return Ledger(limits=limits, capability=capability, store=store_db,
                      run_id=run_id)
    return _make


@pytest.fixture
def catalog_service(runtime, release_id):
    """The real catalog tool over the pinned release, with no session."""
    from backend.cockpit_agentic import scope as v3_scope
    from backend.cockpit_v4.catalog_tool import CatalogService
    from backend.cockpit_v4.service import _Principal

    scope = v3_scope.for_principal(
        _Principal({"tenant": "demo-tenant", "id": "u"}),
        dataset_release_id=release_id)
    return CatalogService(catalog=runtime.catalog, scope=scope)
