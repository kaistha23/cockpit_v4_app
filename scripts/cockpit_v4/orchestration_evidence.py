#!/usr/bin/env python3
"""
Measure what an analytical run costs, per model call.

The MODEL is a stub and every artefact this writes says so. What is real: the
published release, the catalogue, the context the server assembles, the tool
schemas it sends, the DuckDB execution and the ledger arithmetic. So the call
counts, the payload sizes, the field-definition counts and the split between
provider time and CreditProbe time are measurements. The call LATENCIES are
not -- a stub answers instantly -- and the output says so rather than letting
a reader mistake them for live figures.

Written for the questions a reader of the regression report will ask:
  * how many model calls does a simple analysis take, and what is each for?
  * how much context does each one carry, and where does that context sit?
  * how much smaller is the answer turn than the action turn?
  * how many field definitions does a seeded covenant thread receive?
  * of the wall-clock, how much was the provider and how much was us?

Usage:
    python3 scripts/cockpit_v4/orchestration_evidence.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "cockpit_v4"))

OUT = ROOT / "docs" / "cockpit_v4" / "evidence" / "orchestration.json"
RELEASE = os.environ.get("V4_RELEASE", "v4-saudi-20q-v1")


def _runtime(tmp: Path):
    from backend.cockpit_v4.capability import Capability, PriceCard
    from backend.cockpit_v4.config import V4Config
    from backend.cockpit_v4.service import Runtime, load_release

    cfg = V4Config(
        enabled=True, provider="anthropic", reasoning_model="stub-analyst",
        runtime_dir=tmp / "runtime", state_database=str(tmp / "state.sqlite3"),
        release_id=RELEASE, api_port=8414, ui_port=5414, local_demo_auth=True,
        price_card_path=str(tmp / "prices.json"), memory_enabled=False,
        memory_model="", default_mode="standard", heartbeat_seconds=5.0,
        lease_heartbeat_seconds=2.0, lease_stale_seconds=10.0,
        supervisor_poll_seconds=2.0, credential_present=True, missing=())
    capability = Capability(
        provider="anthropic", model_id="stub-analyst", sdk_version="evidence",
        context_tokens=200_000, max_output_tokens=128_000,
        supports_tools=True,
        supports_token_counting=False,
        price=PriceCard(input_usd_per_mtok=5.0, output_usd_per_mtok=25.0,
                        cache_write_usd_per_mtok=6.25,
                        cache_read_usd_per_mtok=0.5),
        source="evidence script", verified_at="2026-09-13T00:00:00Z",
        live_verified=False)
    catalog, _, summary = load_release(cfg)
    return Runtime(cfg=cfg, capability=capability, provider=None,
                   catalog=catalog, coverage=None, release_summary=summary)


def _drive(runtime, store, question, script, *, thread_id=""):
    from conftest import ScriptedProvider
    from backend.cockpit_v4.worker import Worker

    if not thread_id:
        thread_id = store.create_thread(tenant_id="demo-tenant",
                                        principal_id="u1")
    record, _ = store.accept_run(
        thread_id=thread_id, tenant_id="demo-tenant", principal_id="u1",
        question=question, mode="standard", release_id=RELEASE,
        ui_filters={}, idempotency_key="", body_digest="",
        startup_sha="evidence", deadline_at="")
    runtime.provider = ScriptedProvider(script)
    outcome = Worker(store=store, runtime=runtime).execute(record)
    return outcome, record, thread_id


def _calls(outcome) -> list[dict]:
    """The per-call row a reader wants, without the noise."""
    rows = []
    for call in outcome.call_report.get("calls", []):
        rows.append({
            "seq": call.get("seq"),
            "purpose": call.get("purpose"),
            "phase": call.get("phase"),
            "outcome": call.get("outcome"),
            "counted_input_tokens": call.get("counted_input_tokens"),
            "output_allowance_granted": (
                call.get("output_allowance") or {}).get("granted"),
            "tools_offered": len(call.get("tools_offered") or ()),
            "context_bytes": call.get("context_bytes"),
        })
    return rows


def _case_file_vs_relation_dump(runtime, sem) -> dict:  # noqa: ANN001
    """What the case file costs, against what it removes.

    Stated in bytes both ways rather than asserted, because the case file is
    not free: it makes the FIRST request bigger. The trade is only worth
    making if what it removes is bigger still -- and what it removes is a
    whole extra generation carrying the whole relation.
    """
    import json as _json

    from conftest import intent

    from backend.cockpit_agentic import scope as v3_scope
    from backend.cockpit_v4.catalog_tool import CatalogService
    from backend.cockpit_v4.contracts import parse_catalog
    from backend.cockpit_v4.service import _Principal

    scope = v3_scope.for_principal(
        _Principal({"tenant": "demo-tenant", "id": "u"}),
        dataset_release_id=RELEASE)
    service = CatalogService(catalog=runtime.catalog, scope=scope)
    dump = service.inspect(parse_catalog({
        "intent": intent("DATA_ANALYSIS", "COCKPIT",
                         understood="covenant fields",
                         rationale=("no covenant column is a canonical "
                                    "measure")),
        "query": "", "relation_ids": ["cockpit_covenant_quarter"],
        "field_ids": [], "detail": ["fields"], "reporting_periods": [],
        "sample_rows": 0, "cursor": ""}))
    case = sem.seed_field_packet(runtime.catalog, "covenant_breach_share")

    def _bytes(obj) -> int:  # noqa: ANN001
        return len(_json.dumps(obj, ensure_ascii=False,
                               default=str).encode("utf-8"))

    return {
        "relation_dump_bytes": _bytes(dump),
        "relation_dump_fields": len(dump.get("fields") or ()),
        "relation_dump_costs_an_extra_generation": True,
        "case_file_bytes": _bytes(case),
        "case_file_fields": len(case),
    }


def main() -> int:
    import oracles
    from conftest import ScriptedResult, tool_call
    from test_mandatory_analytical_cases import (
        COVENANT_FIELDS, COVENANT_SQL, EAD_FIELDS, EAD_SQL, _final_from)
    from test_vertical_slice import _execute_call

    from backend.cockpit_v4 import semantics as sem
    from backend.cockpit_v4.run_store import RunStore

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        runtime = _runtime(tmp)
        store = RunStore(tmp / "state.sqlite3")
        quarter = oracles.latest_quarter(RELEASE)

        # -- a simple analysis, end to end --
        simple, record, _ = _drive(runtime, store, (
            "What is total exposure at default by sector in the latest "
            "quarter?"), [
            ScriptedResult(tool_calls=[_execute_call(
                EAD_SQL, purpose="Reported EAD by sector", grain="sector",
                units="SAR million", subquestions=["EAD by sector"],
                fields=EAD_FIELDS, quarter=quarter)]),
            lambda m: _final_from(m, narrative="Exposure by sector.",
                                  subquestion="EAD by sector")])
        spend = store.get_run(record.run_id).budget

        # -- the seeded covenant thread --
        thread_id = store.create_thread(tenant_id="demo-tenant",
                                        principal_id="u1")
        store.set_thread_context(
            thread_id, tenant_id="demo-tenant", kind="attention_item", body={
                "segment": "Manufacturing",
                "segment_dimension": "sector_name",
                "reporting_quarter": "2026Q2",
                "comparison_quarter": "2026Q1",
                "metric": "covenant_breach_share",
                "metric_label": "Exposure with a covenant breach",
                "headline": "Manufacturing: more exposure sits under a "
                            "covenant breach"})
        seeded, seeded_record, _ = _drive(runtime, store, (
            "Which borrowers are in breach and on how many covenants?"), [
            ScriptedResult(tool_calls=[_execute_call(
                COVENANT_SQL, purpose="Borrowers in covenant breach",
                grain="borrower", units="count",
                subquestions=["Borrowers in breach"],
                fields=COVENANT_FIELDS, quarter=quarter)]),
            lambda m: _final_from(m, narrative="Borrowers in breach.",
                                  subquestion="Borrowers in breach")],
            thread_id=thread_id)
        seeded_spend = store.get_run(seeded_record.run_id).budget

        packet = {m: len(sem.seed_field_packet(runtime.catalog, m))
                  for m in sorted(sem.SEED_FIELDS)}
        covenant_columns = len(list(
            runtime.catalog.columns("cockpit_covenant_quarter")))
        trade = _case_file_vs_relation_dump(runtime, sem)

        action, answer = _calls(simple)[0], _calls(simple)[-1]
        evidence = {
            "model": "STUB ANALYST. No paid provider call was made. Call "
                     "latencies below are therefore not live figures; call "
                     "counts, payload sizes and field counts are.",
            "release_id": RELEASE,
            "simple_analysis": {
                "question": "total EAD by sector, latest quarter",
                "state": simple.state,
                "generations": spend["generation_attempts"][0],
                "catalog_calls": spend["catalog_calls"][0],
                "execution_submissions": spend["execution_submissions"][0],
                "calls": _calls(simple),
                "answer_turn_saving": {
                    "system_bytes": [action["context_bytes"]["system"],
                                     answer["context_bytes"]["system"]],
                    "tools_bytes": [action["context_bytes"]["tools"],
                                    answer["context_bytes"]["tools"]],
                    "tools_offered": [action["tools_offered"],
                                      answer["tools_offered"]],
                    "counted_input_tokens": [
                        action["counted_input_tokens"],
                        answer["counted_input_tokens"]],
                },
                "wall_clock_ms": {
                    "total": simple.call_report.get("elapsed_ms"),
                    "provider": simple.call_report.get("provider_ms"),
                    "creditprobe": simple.call_report.get("local_ms"),
                },
            },
            "seeded_covenant_thread": {
                "state": seeded.state,
                "generations": seeded_spend["generation_attempts"][0],
                "catalog_calls": seeded_spend["catalog_calls"][0],
                "execution_submissions":
                    seeded_spend["execution_submissions"][0],
                "calls": _calls(seeded),
            },
            "seeded_case_file": {
                "bound": sem.MAX_SEED_FIELDS,
                "field_definitions_by_indicator": packet,
                "covenant_relation_columns": covenant_columns,
                "note": ("Before the case file a covenant question had no "
                         "narrower way to ask than naming the relation, and "
                         "the catalogue answered with all of it."),
                "what_it_replaces": trade,
            },
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    print(f"\nevidence written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
