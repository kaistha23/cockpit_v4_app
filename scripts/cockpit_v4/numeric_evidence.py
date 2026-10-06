#!/usr/bin/env python3
"""
What M01 actually publishes: the canonical figure, and the one a reader sees.

The MODEL is a stub and this file says so. What is real: the Saudi release,
the catalogue, the DuckDB execution, the derivation arithmetic, the display
policy and the validator. So the numbers below are measurements of the
product's own pipeline; only the model's prose is scripted.

Usage:
    python3 scripts/cockpit_v4/numeric_evidence.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "cockpit_v4"))

OUT = ROOT / "docs" / "cockpit_v4" / "evidence" / "numeric_rendering.json"
RELEASE = os.environ.get("V4_RELEASE", "v4-saudi-20q-v1")
QUESTION = "What is total exposure at default by sector in the latest quarter?"


def main() -> int:
    import oracles
    from conftest import ScriptedProvider, ScriptedResult, final, intent, tool_call
    from test_mandatory_analytical_cases import EAD_FIELDS, EAD_SQL
    from test_vertical_slice import _execute_call
    from scripts.cockpit_v4.orchestration_evidence import _runtime

    from backend.cockpit_v4 import display as disp
    from backend.cockpit_v4 import release as release_mod
    from backend.cockpit_v4.run_store import RunStore
    from backend.cockpit_v4.worker import Worker

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        runtime = _runtime(tmp)
        store = RunStore(tmp / "state.sqlite3")
        quarter = oracles.latest_quarter(RELEASE)

        def _answer(messages):
            body = json.loads(messages[-1]["content"][0]["content"])
            step = body["steps"][0]
            column = next(c for c in step["columns"] if c != "sector_name")
            return ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                      narrative=("Total portfolio EAD in "
                                 f"{quarter} is {{{{claim.total_ead}}}}. The "
                                 "four largest sectors carry "
                                 "{{claim.top4_share}} of it."),
                      numeric_claims=[
                          {"claim_id": "total_ead", "unit": "SAR million",
                           "derivation": {"operation": "sum", "operands": [
                               {"artifact_id": step["artifact_id"],
                                "column_id": column,
                                "row_ids": list(step["row_ids"])}]}},
                          {"claim_id": "top4_share", "unit": "percent",
                           "derivation": {"operation": "percentage",
                                          "operands": [
                               {"artifact_id": step["artifact_id"],
                                "column_id": column,
                                "row_ids": step["row_ids"][:4]},
                               {"artifact_id": step["artifact_id"],
                                "column_id": column,
                                "row_ids": list(step["row_ids"])}]}}],
                      tables=[{"title": "EAD by sector",
                               "artifact_id": step["artifact_id"],
                               "columns": ["sector_name", column]}]))])

        thread_id = store.create_thread(tenant_id="demo-tenant",
                                        principal_id="u1")
        record, _ = store.accept_run(
            thread_id=thread_id, tenant_id="demo-tenant", principal_id="u1",
            question=QUESTION, mode="standard", release_id=RELEASE,
            ui_filters={}, idempotency_key="", body_digest="",
            startup_sha="evidence", deadline_at="")
        runtime.provider = ScriptedProvider([
            ScriptedResult(tool_calls=[_execute_call(
                EAD_SQL, purpose="Reported EAD by sector", grain="sector",
                units="SAR million", subquestions=["EAD by sector"],
                fields=EAD_FIELDS, quarter=quarter)]),
            _answer])
        outcome = Worker(store=store, runtime=runtime).execute(record)
        if outcome.state != "COMPLETED":
            print(f"M01 did not publish: {outcome.message}")
            return 1

        spend = store.get_run(record.run_id).budget
        body = outcome.response
        table = body["tables"][0]
        column = next(c for c in table["columns"] if c != "sector_name")
        expected = oracles.ead_by_sector(RELEASE, quarter)
        oracle_total = sum(Decimal(str(v)) for v in expected.values())
        top4 = sorted(expected.values(), reverse=True)[:4]
        oracle_share = (sum(Decimal(str(v)) for v in top4)
                        / oracle_total * 100)

        evidence = {
            "model": "STUB ANALYST. No paid provider call was made. The "
                     "release, the SQL, the arithmetic, the display policy "
                     "and the validator are all real.",
            "release": body["release"],
            "question": QUESTION,
            "quarter": quarter,
            "rendered_narrative": body["narrative"],
            "model_calls": spend["generation_attempts"][0],
            "answer_format_recoveries": spend["answer_format_recoveries"][0],
            "answer_corrections": spend["answer_corrections"][0],
            "claims": [
                {"claim_id": c["claim_id"], "unit": c["unit"],
                 "display_precision": c["display_precision"],
                 "analyst_sent_a_value": "decimal_value" in c}
                for c in body["numeric_claims"]],
            "total_ead": {
                "oracle_canonical": str(oracle_total),
                "published": body["narrative"].split("is ")[1].split(".")[0],
                "policy_rendering": disp.format_value(oracle_total,
                                                      "SAR million"),
            },
            "top4_share": {
                "oracle_canonical": str(oracle_share),
                "policy_rendering": disp.format_value(oracle_share,
                                                      "percent"),
            },
            "table": {
                "rendered_by": table["rendered_by"],
                "row_count": table["row_count"],
                "column_units": table["column_units"],
                "first_three_rows": [
                    {"sector": r["canonical"]["sector_name"],
                     "canonical": str(r["canonical"][column]),
                     "display": r["display"][column]}
                    for r in table["rows"][:3]],
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
