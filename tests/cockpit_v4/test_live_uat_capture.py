"""The live-UAT harness reads the field the store actually has.

UNIT · REPRODUCTION · REAL DATABASE. No model call, no paid provider call.

H-LIVE-01
---------
`capture()` read the published answer with

    answer = getattr(record, "response", None) or {}

and `RunRecord` has no `response`. The dataclass declares `final_response`
(`run_store.py:327`), the column is `final_response` (`:71`), `get_run`
populates `final_response` (`:675`) and the worker settles `outcome.response`
INTO `final_response` (`:717`). `response` is `Outcome`'s field name -- the
worker's in-memory result -- and a settled run is not read back from it.

Because `getattr` was given a default, the expression could only ever be
`{}`, silently. The first live UAT recorded a blank narrative, a blank
disposition, no claims, no charts and no clarification for twenty-eight paid
runs while every one of those answers sat in `runs.final_response` in the
same file.

H-LIVE-02
---------
`reconcile()` built its published values from

    claim.get("canonical", claim.get("published"))

and neither key exists on a published claim. `NumericClaim.to_dict`
(`contracts.py:940`) emits `claim_id`, `unit`, `evidence`,
`display_precision` and optionally `decimal_value` and `derivation`;
`orchestration` adds `display_value` (`:1638`). So every comparand was
`None`, and the first pass must not be described as independently reconciled.

It now recovers the governed number from the stored ARTIFACT -- through the
product's own `derivation` module, the same arithmetic the finalizer checked
the claim with -- and joins it to the oracle through the key and value
columns the matrix declares before the run. The claim id never enters.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.cockpit_v4 import states as st
from backend.cockpit_v4.run_store import RunStore

HARNESS = (Path(__file__).resolve().parents[2]
           / "scripts" / "cockpit_v4" / "live_uat.py")

TENANT = "t-uat"

#: One sector result, at the precision a query produces.
COLUMNS = ["sector", "ead_sar_mn", "ecl_sar_mn"]
ROWS = [
    {"sector": "Manufacturing", "ead_sar_mn": 218.9712345678,
     "ecl_sar_mn": 4.5120000001},
    {"sector": "Real Estate", "ead_sar_mn": 53.1100000002,
     "ecl_sar_mn": 1.2039999998},
    {"sector": "Construction", "ead_sar_mn": 91.4400000003,
     "ecl_sar_mn": 2.8810000004},
]
TRUTH = {row["sector"]: row["ead_sar_mn"] for row in ROWS}


@pytest.fixture(scope="module")
def uat():
    spec = importlib.util.spec_from_file_location("live_uat", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules["live_uat"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop("live_uat", None)


#: Everything a UAT grader reads off one turn. A blank in any of these is the
#: first-pass defect.
ANSWER = {
    "intent": {"query_mode": "DATA_ANALYSIS", "owner": "COCKPIT"},
    "disposition": "answer",
    "narrative": "Manufacturing carries SAR 219 million of EAD.",
    "canonical_mappings": ["exposure at default = ead_sar_mn"],
    "resolved_assumptions": ["this quarter = 2026Q2"],
    "blocking_ambiguities": [],
    "clarification_question": "Which exposure measure did you mean?",
    "clarification_options": ["ead_sar_mn", "drawn_balance"],
    "coverage": ["2026Q2"],
    "limitations": ["undrawn commitments are included"],
    "suggested_questions": ["And for Real Estate?"],
    "executed": True,
    "evidence_bound": True,
    "validation": {"ok": True, "problems": [], "warnings": ["one warning"],
                   "claims_checked": 1},
    "policy_context": {"pack_version": "2026.09-v1", "clauses": ["CP-1.1"]},
    "policy_citations": ["CP-1.1"],
}


def _settled(db: Path, *, answer: dict,
             claims: Any = None) -> tuple[RunStore, str, str]:
    """A real run in a real ledger, settled with a real final response.

    A settled run is TERMINAL -- `update_state` refuses a second write, as
    it should -- so a variant answer is built here and settled once.
    """
    store = RunStore(db)
    thread_id = store.create_thread(
        tenant_id=TENANT, principal_id="u1", domain_id="corporate",
        release_id="rel-1")
    record, _created = store.accept_run(
        thread_id=thread_id, tenant_id=TENANT, principal_id="u1",
        question="What is total exposure at default by sector this quarter?",
        mode="standard", release_id="rel-1", domain_id="corporate",
        release_fingerprint="fp-1", ui_filters={}, idempotency_key="",
        body_digest="", startup_sha="live-uat", deadline_at="")
    artifact_id = store.put_artifact(
        run_id=record.run_id, tenant_id=TENANT, kind="table",
        release_id="rel-1", scope={"release_fingerprint": "fp-1",
                                   "relations": ["corp_facility_quarter"]},
        columns=COLUMNS, rows=ROWS)
    body = dict(answer)
    body["numeric_claims"] = (claims(artifact_id) if callable(claims) else [{
        "claim_id": "manufacturing_ead", "unit": "SAR million",
        "evidence": {"artifact_id": artifact_id, "row_key": "r0",
                     "column_id": "ead_sar_mn"},
        "display_precision": 0,
        "display_value": "SAR 219 million"}])
    body["tables"] = [{"title": "EAD by sector", "artifact_id": artifact_id,
                       "columns": COLUMNS, "column_units": {},
                       "rows": [{"row_id": "r0"}, {"row_id": "r1"},
                                {"row_id": "r2"}]}]
    body["charts"] = [{"kind": "bar", "title": "EAD by sector",
                       "artifact_id": artifact_id,
                       "why_this_chart": "twelve comparable magnitudes",
                       "series_units": {"ead_sar_mn": "SAR million"},
                       "points": [{}, {}, {}]}]
    store.update_state(record.run_id, expect_version=record.version,
                       state=st.COMPLETED, final_response=body,
                       terminal=True)
    return store, record.run_id, artifact_id


# ---- H-LIVE-01 ---------------------------------------------------------

def test_a_run_record_has_no_response_attribute_at_all():
    """The premise. `getattr(record, "response", None)` cannot work here."""
    from backend.cockpit_v4.run_store import RunRecord

    fields = set(RunRecord.__dataclass_fields__)
    assert "final_response" in fields
    assert "response" not in fields, (
        "if RunRecord ever gains a `response`, this whole defect changes "
        "shape and the fix below has to be re-argued")


def test_capture_recovers_the_whole_published_answer(uat, tmp_path):
    store, run_id, artifact_id = _settled(tmp_path / "live.sqlite3",
                                          answer=ANSWER)
    record = store.get_run(run_id)
    assert not hasattr(record, "response")
    assert record.final_response, "the ledger holds the answer"

    taken = uat.capture(store, run_id, TENANT)

    assert taken["narrative"] == ANSWER["narrative"]
    assert taken["disposition"] == "answer"
    assert taken["clarification_question"] == ANSWER["clarification_question"]
    assert taken["clarification_options"] == ANSWER["clarification_options"]
    assert taken["canonical_mappings"] == ANSWER["canonical_mappings"]
    assert taken["resolved_assumptions"] == ANSWER["resolved_assumptions"]
    assert taken["limitations"] == ANSWER["limitations"]
    assert taken["suggested_questions"] == ANSWER["suggested_questions"]
    assert len(taken["numeric_claims"]) == 1
    assert taken["numeric_claims"][0]["claim_id"] == "manufacturing_ead"
    assert taken["chart_count"] == 1
    assert taken["charts"][0]["kind"] == "bar"
    assert taken["charts"][0]["points"] == 3
    assert taken["tables"][0]["rows"] == 3
    assert taken["tables"][0]["artifact_id"] == artifact_id
    # The grading fields the old capture dropped entirely.
    assert taken["intent"]["query_mode"] == "DATA_ANALYSIS"
    assert taken["executed"] is True
    assert taken["evidence_bound"] is True
    assert taken["validation"]["warnings"] == ["one warning"]
    assert taken["policy_citations"] == ["CP-1.1"]
    assert taken["policy_context"]["clauses"] == ["CP-1.1"]


def test_the_table_summary_stays_lightweight(uat, tmp_path):
    """Deliberate: the rows are in the artifact, at full precision.

    Copying them into the evidence file would double it and would make the
    harness the second place a governed number lives.
    """
    store, run_id, _ = _settled(tmp_path / "live.sqlite3", answer=ANSWER)
    table = uat.capture(store, run_id, TENANT)["tables"][0]
    assert set(table) == {"title", "artifact_id", "rows", "columns",
                          "column_units"}
    assert isinstance(table["rows"], int)


def test_that_guard_fails_against_the_old_response_field(uat, tmp_path,
                                                         monkeypatch):
    """The mutation check. Restore the old access; the guard must go red."""
    monkeypatch.setattr(
        uat, "published_answer",
        lambda record: getattr(record, "response", None) or {})
    with pytest.raises(AssertionError):
        test_capture_recovers_the_whole_published_answer(uat, tmp_path)


def test_an_unsettled_run_captures_as_empty_rather_than_guessing(uat,
                                                                 tmp_path):
    store = RunStore(tmp_path / "live.sqlite3")
    thread_id = store.create_thread(tenant_id=TENANT, principal_id="u1",
                                    domain_id="corporate", release_id="rel-1")
    record, _ = store.accept_run(
        thread_id=thread_id, tenant_id=TENANT, principal_id="u1",
        question="q", mode="standard", release_id="rel-1",
        domain_id="corporate", release_fingerprint="fp-1", ui_filters={},
        idempotency_key="", body_digest="", startup_sha="live-uat",
        deadline_at="")
    taken = uat.capture(store, record.run_id, TENANT)
    assert taken["narrative"] == ""
    assert taken["numeric_claims"] == []
    assert taken["state"] != st.COMPLETED, (
        "a blank answer must be readable as 'not settled' from the state, "
        "which is how the first pass should have looked wrong immediately")


# ---- H-LIVE-02 ---------------------------------------------------------

def _journey(uat, **kwargs):
    base = dict(jid="X01", domain_id="corporate",
                turns=["What is total exposure at default by sector "
                       "this quarter?"],
                coverage="test", key="sector", value="ead_sar_mn",
                tolerance=0.01, oracle=lambda: dict(TRUTH))
    base.update(kwargs)
    return uat.Journey(**base)


def test_a_correct_settled_answer_reconciles(uat, tmp_path):
    store, run_id, _ = _settled(tmp_path / "live.sqlite3", answer=ANSWER)
    taken = uat.capture(store, run_id, TENANT)
    report = uat.reconcile(_journey(uat), taken, store=store,
                           tenant_id=TENANT)

    assert report["has_oracle"] and report["checked"]
    assert report["ok"] is True
    assert sorted(report["rows_matched"]) == sorted(TRUTH)
    assert report["rows_mismatched"] == []
    assert report["rows_missing_from_the_answer"] == []
    assert report["claims_compared"] == 1
    claim = report["claims"][0]
    assert claim["oracle_key"] == "Manufacturing", (
        "the join came from the claim's own evidence row, not from its id")
    assert claim["ok"] is True


def test_the_comparand_is_the_artifact_and_not_the_rounded_display(uat,
                                                                   tmp_path):
    store, run_id, _ = _settled(tmp_path / "live.sqlite3", answer=ANSWER)
    taken = uat.capture(store, run_id, TENANT)
    report = uat.reconcile(_journey(uat), taken, store=store,
                           tenant_id=TENANT)
    claim = report["claims"][0]
    assert claim["display_value"] == "SAR 219 million"
    assert claim["canonical"] == "218.9712345678", (
        "the canonical value comes from the stored cell at full precision")
    assert claim["expected"] == "218.9712345678"
    # And a tolerance this tight would refuse the rounded figure, which is
    # the proof that parsing the display string would have been wrong.
    assert not uat._close("219", claim["expected"], 1e-9)


def test_a_deliberately_wrong_number_fails(uat, tmp_path):
    """The mutation check for the reconciliation itself."""
    store, run_id, _ = _settled(tmp_path / "live.sqlite3", answer=ANSWER)
    taken = uat.capture(store, run_id, TENANT)
    wrong = dict(TRUTH)
    wrong["Manufacturing"] = 999.0
    report = uat.reconcile(_journey(uat, oracle=lambda: wrong), taken,
                           store=store, tenant_id=TENANT)
    assert report["checked"] is True
    assert report["ok"] is False
    assert report["rows_mismatched"] == ["Manufacturing"]
    assert report["claims"][0]["ok"] is False


def test_a_key_the_answer_never_published_is_reported_missing(uat, tmp_path):
    store, run_id, _ = _settled(tmp_path / "live.sqlite3", answer=ANSWER)
    taken = uat.capture(store, run_id, TENANT)
    truth = dict(TRUTH)
    truth["Telecommunications"] = 12.5
    report = uat.reconcile(_journey(uat, oracle=lambda: truth), taken,
                           store=store, tenant_id=TENANT)
    assert report["rows_missing_from_the_answer"] == ["Telecommunications"]


def test_a_derived_claim_is_recomputed_rather_than_trusted(uat, tmp_path):
    """A total across rows has no row of its own and must still reconcile."""
    store, run_id, _ = _settled(
        tmp_path / "live.sqlite3", answer=ANSWER,
        claims=lambda artifact_id: [{
            "claim_id": "book_total", "unit": "SAR million",
            "display_precision": 0, "display_value": "SAR 363 million",
            "evidence": {"artifact_id": "", "row_key": "", "column_id": ""},
            "derivation": {"operation": "sum", "operands": [
                {"artifact_id": artifact_id, "column_id": "ead_sar_mn",
                 "row_ids": ["r0", "r1", "r2"]}]}}])
    taken = uat.capture(store, run_id, TENANT)
    total = sum(TRUTH.values())
    report = uat.reconcile(
        _journey(uat, key="", value="ead_sar_mn", oracle=lambda: total),
        taken, store=store, tenant_id=TENANT)
    assert report["claims_compared"] == 1
    assert report["claims"][0]["kind"] == "derived"
    assert report["claims"][0]["ok"] is True


def test_a_journey_graded_on_behaviour_says_so_rather_than_passing(uat,
                                                                   tmp_path):
    store, run_id, _ = _settled(tmp_path / "live.sqlite3", answer=ANSWER)
    taken = uat.capture(store, run_id, TENANT)
    report = uat.reconcile(_journey(uat, oracle=None), taken, store=store,
                           tenant_id=TENANT)
    assert report["has_oracle"] is False
    assert report["checked"] is False
    assert "ok" not in report, (
        "a journey with no oracle must not report a verdict it did not reach")


def test_the_declared_mapping_is_the_matrix_and_not_the_claim_ids(uat,
                                                                  tmp_path):
    store, run_id, _ = _settled(tmp_path / "live.sqlite3", answer=ANSWER)
    taken = uat.capture(store, run_id, TENANT)
    report = uat.reconcile(_journey(uat), taken, store=store,
                           tenant_id=TENANT)
    assert report["mapping"] == {
        "key_shapes": [["sector"]], "key_separator": " / ",
        "accepted_values": [["ead_sar_mn", "1"]], "tolerance": 0.01,
        "comparison_basis": "relative_tolerance",
        "comparison_precision": -1, "comparison_reason": "",
        "declared": "in the approved matrix, before the run"}
    # The analyst's claim id is nowhere in the mapping.
    assert "manufacturing_ead" not in repr(report["mapping"])


def test_a_claim_bound_to_another_column_is_not_compared(uat, tmp_path):
    """`ecl_sar_mn` is real evidence and is not the figure under test."""
    store, run_id, _ = _settled(
        tmp_path / "live.sqlite3", answer=ANSWER,
        claims=lambda artifact_id: [{
            "claim_id": "manufacturing_ecl", "unit": "SAR million",
            "display_precision": 1, "display_value": "SAR 4.5 million",
            "evidence": {"artifact_id": artifact_id, "row_key": "r0",
                         "column_id": "ecl_sar_mn"}}])
    taken = uat.capture(store, run_id, TENANT)
    report = uat.reconcile(_journey(uat), taken, store=store,
                           tenant_id=TENANT)
    assert report["claims_not_compared"] == ["manufacturing_ecl"]
    assert "ecl_sar_mn" in report["claims"][0]["why"]
    # The rows still reconcile: the artifact holds the figure under test.
    assert report["rows_mismatched"] == []
