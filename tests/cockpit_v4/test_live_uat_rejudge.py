"""Regrading a settled first pass, offline, with the provider sealed shut.

UNIT · REAL DATABASE. No model call, no paid provider call.

Why this exists
---------------
The first live UAT spent USD 8.886715 across twenty-eight approved runs and
recorded almost nothing, because `capture()` read a field the store does not
have (H-LIVE-01) and `reconcile()` compared two keys that no published claim
carries (H-LIVE-02). The runs themselves are intact: `runs.final_response`
holds every answer and the `artifacts` table holds every number those answers
were built from.

So the first pass is regradeable without paying again -- and a regrade that
could reach the provider would be a regrade that might. Every door a provider
can be built through is replaced with a raise for the duration, and this file
asserts each one is sealed rather than trusting a comment that says so.

The reconstruction is written to its OWN file and labelled. The original
`live_uat.json` is first-pass evidence and is never rewritten and presented
as the original result.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from backend.cockpit_v4 import states as st
from backend.cockpit_v4.run_store import RunStore

HARNESS = (Path(__file__).resolve().parents[2]
           / "scripts" / "cockpit_v4" / "live_uat.py")

TENANT = "t-uat"
QUESTION = "What is exposure at default by sector this quarter?"

#: The approved 28-run queue, in the frozen order it was executed in.
APPROVED_QUEUE = ("L01", "L02", "L12", "L17", "L16", "L18", "L13", "L15",
                  "L14", "M05", "M02", "M04", "L11", "L05", "L04", "L08",
                  "L10")

#: The ten journeys of that queue that carry an independent pandas oracle,
#: and the seven graded on behaviour. Declared HERE so a later round cannot
#: quietly move a journey from "reconciled" to "graded on behaviour" and
#: have the evidence file still read as fully reconciled.
HAS_ORACLE = ("L01", "L02", "L04", "L05", "L08", "L10", "L11", "L12",
              "L15", "L17")
BEHAVIOUR_ONLY = ("L13", "L14", "L16", "L18", "M02", "M04", "M05")


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


# ---- which journeys are actually reconcilable -------------------------

def test_the_matrix_declares_which_journeys_have_an_oracle(uat):
    index = {j.jid: j for j in uat.matrix()}
    with_oracle = tuple(sorted(j for j in APPROVED_QUEUE
                               if index[j].oracle is not None))
    behaviour = tuple(sorted(j for j in APPROVED_QUEUE
                             if index[j].oracle is None))
    assert with_oracle == HAS_ORACLE
    assert behaviour == BEHAVIOUR_ONLY
    assert sum(len(index[j].turns) for j in APPROVED_QUEUE) == 28


def test_every_oracle_journey_declares_its_value_column(uat):
    """The join is declared before the run or it is not declared at all."""
    index = {j.jid: j for j in uat.matrix()}
    for jid in HAS_ORACLE:
        assert index[jid].value, (
            f"{jid} has an oracle and no value column, so nothing could be "
            f"compared without inventing the mapping after the fact")


# ---- the provider is sealed -------------------------------------------

def test_every_provider_door_is_named(uat):
    """A door this does not know about is a door it cannot seal."""
    assert uat.PROVIDER_DOORS
    for module_name, attribute in uat.PROVIDER_DOORS:
        module = importlib.import_module(module_name)
        assert hasattr(module, attribute), (
            f"{module_name}.{attribute} is in PROVIDER_DOORS and does not "
            f"exist; a renamed door is an unsealed one")


def test_sealing_makes_every_door_raise(uat):
    restore = uat.seal_the_provider()
    try:
        for module_name, attribute in uat.PROVIDER_DOORS:
            module = importlib.import_module(module_name)
            with pytest.raises(uat.ProviderForbidden):
                getattr(module, attribute)()
    finally:
        for module, attribute, original in restore:
            setattr(module, attribute, original)
    # And the originals are back, so sealing is not a one-way change to the
    # process.
    from backend.cockpit_v4 import service

    assert service.resolve_provider.__name__ != "refuse"


# ---- the regrade itself -----------------------------------------------

def _first_pass_ledger(db: Path, *, turns: int = 1) -> RunStore:
    """A settled ledger that looks like the first pass: real answers in
    `final_response`, real rows in `artifacts`, real committed spend."""
    from test_live_uat_capture import ANSWER, COLUMNS, ROWS

    store = RunStore(db)
    thread_id = store.create_thread(
        tenant_id=TENANT, principal_id="u1", domain_id="corporate",
        release_id="rel-1")
    for turn in range(turns):
        record, _ = store.accept_run(
            thread_id=thread_id, tenant_id=TENANT, principal_id="u1",
            question=QUESTION, mode="standard", release_id="rel-1",
            domain_id="corporate", release_fingerprint="fp-1",
            ui_filters={}, idempotency_key="", body_digest="",
            startup_sha="live-uat", deadline_at="")
        artifact_id = store.put_artifact(
            run_id=record.run_id, tenant_id=TENANT, kind="table",
            release_id="rel-1",
            scope={"release_fingerprint": "fp-1",
                   "relations": ["corp_facility_quarter"]},
            columns=COLUMNS, rows=ROWS)
        body = dict(ANSWER)
        body["numeric_claims"] = [{
            "claim_id": "manufacturing_ead", "unit": "SAR million",
            "evidence": {"artifact_id": artifact_id, "row_key": "r0",
                         "column_id": "ead_sar_mn"},
            "display_precision": 0, "display_value": "SAR 219 million"}]
        body["tables"] = [{"title": "EAD by sector",
                           "artifact_id": artifact_id, "columns": COLUMNS,
                           "column_units": {}, "rows": [{}, {}, {}]}]
        body["charts"] = [{"kind": "bar", "title": "EAD by sector",
                           "artifact_id": artifact_id,
                           "why_this_chart": "comparable magnitudes",
                           "points": [{}, {}, {}]}]
        reservation = store.reserve(run_id=record.run_id, purpose="ANSWER",
                                    reserved_usd=0.40)
        store.settle(reservation, settled_usd=0.3174, usage={})
        store.update_state(record.run_id, expect_version=record.version,
                           state=st.COMPLETED, final_response=body,
                           terminal=True)
    # COLD, like a preserved first pass. An OPEN connection checkpoints its
    # WAL into the main database when it closes, which rewrites the file --
    # and that is the hazard the regrade's copy exists for, so a fixture
    # that left it open would be measuring the fixture.
    store.close()
    return store


def test_a_regrade_reads_the_answers_the_first_pass_dropped(uat, tmp_path):
    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db)
    out = tmp_path / "rejudge.json"

    assert uat.rejudge(db, out=out, first_pass=None,
                       tenant_id=TENANT) == 0
    body = json.loads(out.read_text())

    assert body["label"] == ("RECONSTRUCTED FROM SETTLED FIRST-PASS RUNS · "
                             "NO PROVIDER CALL")
    assert body["reconstructed"] is True
    assert body["live"] is False
    assert body["provider_calls"] == 0
    assert body["runs"] == 1
    turn = body["journeys"][0]["turns"][0]
    assert turn["narrative"], "the answer the first pass recorded blank"
    assert turn["disposition"] == "answer"
    assert turn["numeric_claims"]
    assert turn["chart_count"] == 1


def test_a_regrade_preserves_the_original_run_ids_and_spend(uat, tmp_path):
    db = tmp_path / "live-first-pass.sqlite3"
    store = _first_pass_ledger(db, turns=2)
    before = [r for r in store._connect().execute(
        "SELECT run_id FROM runs ORDER BY created_at, rowid")]
    ids = [str(r["run_id"]) for r in before]
    out = tmp_path / "rejudge.json"
    uat.rejudge(db, out=out, first_pass=None, tenant_id=TENANT)
    body = json.loads(out.read_text())

    assert [t["run_id"] for t in body["journeys"][0]["turns"]] == ids
    assert body["cumulative_usd"] == pytest.approx(0.6348)
    # And the ledger is unchanged: same runs, same spend, nothing added.
    after = [str(r["run_id"]) for r in store._connect().execute(
        "SELECT run_id FROM runs ORDER BY created_at, rowid")]
    assert after == ids


def test_a_regrade_runs_the_oracles_and_reports_a_verdict(uat, tmp_path):
    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db)
    out = tmp_path / "rejudge.json"
    uat.rejudge(db, out=out, first_pass=None, tenant_id=TENANT,
                queue=["L01"])
    body = json.loads(out.read_text())

    journey = body["journeys"][0]
    # POSITION, not text: L01 and L17 ask this same question on purpose.
    assert journey["journey"] == "L01"
    assert journey["ambiguous"] == []
    assert journey["oracle"]["has_oracle"] is True
    assert journey["oracle"]["checked"] is True
    assert "ok" in journey["oracle"]
    grade = journey["grade"]
    assert grade["dispositions"] == ["answer"]
    assert grade["query_modes"] == ["DATA_ANALYSIS"]
    assert grade["chart_counts"] == [1]
    assert grade["expectations"]["chart"]["expected"] == "yes"
    assert grade["expectations"]["chart"]["met"] is True
    assert grade["policy_citations"] == [["CP-1.1"]]


def test_a_regrade_grades_a_multi_turn_thread_as_one_journey(uat, tmp_path):
    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db, turns=3)
    out = tmp_path / "rejudge.json"
    uat.rejudge(db, out=out, first_pass=None, tenant_id=TENANT)
    body = json.loads(out.read_text())

    assert body["threads"] == 1
    assert body["runs"] == 3
    journey = body["journeys"][0]
    assert [t["turn"] for t in journey["turns"]] == [1, 2, 3]
    assert journey["grade"]["turns"] == 3
    assert journey["grade"]["every_turn_executed"] is True


def test_no_provider_is_constructed_during_a_regrade(uat, tmp_path,
                                                     monkeypatch):
    """The guarantee, tested by making the sealed doors observable."""
    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db)
    seen: list[str] = []
    original = uat.seal_the_provider

    def watched():
        restore = original()
        seen.extend(f"{m}.{a}" for m, a in uat.PROVIDER_DOORS)
        return restore

    monkeypatch.setattr(uat, "seal_the_provider", watched)
    assert uat.rejudge(db, out=tmp_path / "r.json", first_pass=None,
                       tenant_id=TENANT) == 0
    assert "backend.cockpit_v4.service.resolve_provider" in seen
    assert "backend.llm.anthropic_provider.AnthropicProvider" in seen


def test_a_regrade_records_the_first_pass_hash_when_given_one(uat, tmp_path):
    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db)
    first = tmp_path / "live_uat-first-pass.json"
    first.write_text('{"first": "pass"}\n')
    out = tmp_path / "rejudge.json"
    uat.rejudge(db, out=out, first_pass=first, tenant_id=TENANT)
    body = json.loads(out.read_text())

    import hashlib

    assert body["source_first_pass_sha256"] == hashlib.sha256(
        first.read_bytes()).hexdigest()
    assert body["source_ledger"] == str(db)


def test_a_regrade_refuses_a_ledger_that_is_not_there(uat, tmp_path):
    assert uat.rejudge(tmp_path / "absent.sqlite3", out=tmp_path / "o.json",
                       first_pass=None, tenant_id=TENANT) == 2


def test_a_regrade_refuses_an_empty_ledger(uat, tmp_path):
    db = tmp_path / "empty.sqlite3"
    RunStore(db)
    assert uat.rejudge(db, out=tmp_path / "o.json", first_pass=None,
                       tenant_id=TENANT) == 2


def test_the_reconstruction_never_writes_the_first_pass_file_name(uat):
    """`live_uat.json` is first-pass evidence. A regrade gets its own name."""
    source = HARNESS.read_text()
    assert "live_uat_first_pass_rejudge.json" in source
    default = source.split('"live_uat_first_pass_rejudge.json"')[0]
    assert default.rstrip().endswith("evidence" + '" /'), (
        "the rejudge default output must be the reconstruction's own file")


# ---- two journeys, one question ---------------------------------------

def test_a_question_two_journeys_share_is_ambiguous_without_a_queue(uat,
                                                                    tmp_path):
    """L01 and L17 ask the same question. Text alone cannot separate them.

    A regrade that picked whichever came first would grade the negative
    control against the positive one's expectations.
    """
    assert {j.jid for j in uat.journeys_for(QUESTION)} == {"L01", "L17"}
    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db)
    out = tmp_path / "rejudge.json"
    uat.rejudge(db, out=out, first_pass=None, tenant_id=TENANT)
    journey = json.loads(out.read_text())["journeys"][0]
    assert journey["journey"] == ""
    assert sorted(journey["ambiguous"]) == ["L01", "L17"]
    assert journey["grade"]["expectations"]["checked"] is False


def test_the_queue_separates_them_by_position(uat, tmp_path):
    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db)
    for jid in ("L01", "L17"):
        out = tmp_path / f"rejudge-{jid}.json"
        uat.rejudge(db, out=out, first_pass=None, tenant_id=TENANT,
                    queue=[jid])
        journey = json.loads(out.read_text())["journeys"][0]
        assert journey["journey"] == jid
    # And the two carry DIFFERENT expectations, which is why it matters.
    index = {j.jid: j for j in uat.matrix()}
    assert index["L17"].expect_clarification == "no"
    assert index["L01"].expect_clarification == ""


def test_a_queue_that_does_not_match_the_ledger_is_refused_not_guessed(
        uat, tmp_path):
    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db)
    out = tmp_path / "rejudge.json"
    uat.rejudge(db, out=out, first_pass=None, tenant_id=TENANT,
                queue=["L02"])
    journey = json.loads(out.read_text())["journeys"][0]
    assert journey["journey"] == ""
    assert "position 1" in journey["ambiguous"][0]
    assert "L02" in journey["ambiguous"][0]


def test_the_queue_is_recorded_in_the_reconstruction(uat, tmp_path):
    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db)
    out = tmp_path / "rejudge.json"
    uat.rejudge(db, out=out, first_pass=None, tenant_id=TENANT,
                queue=["L01"])
    assert json.loads(out.read_text())["queue"] == ["L01"]


# ---- the preserved ledger is never opened -----------------------------

def test_a_regrade_reads_a_copy_and_proves_the_source_is_unchanged(uat,
                                                                   tmp_path):
    """First-pass evidence is identified by its SHA256. A regrade must not
    be the thing that changes it.

    `RunStore` runs SQLite in WAL mode. A ledger left with a non-empty
    `-wal` beside it -- what an interrupted harness process leaves -- is
    checkpointed into the main database when the next connection closes.
    """
    import hashlib

    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    out = tmp_path / "rejudge.json"
    assert uat.rejudge(db, out=out, first_pass=None, tenant_id=TENANT,
                       queue=["L01"]) == 0

    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    body = json.loads(out.read_text())
    assert body["source_ledger_sha256"] == before
    assert body["source_ledger_opened"] is False
    # And nothing was left beside it.
    assert not (tmp_path / "live-first-pass.sqlite3-wal").exists()
    assert not (tmp_path / "live-first-pass.sqlite3-shm").exists()


def test_a_regrade_whose_source_moved_fails_and_publishes_nothing(
        uat, tmp_path, monkeypatch):
    """The mutation check for that guarantee.

    Something that changes the source mid-read must not be able to publish
    a reconstruction claiming it read an untouched first pass.
    """
    db = tmp_path / "live-first-pass.sqlite3"
    _first_pass_ledger(db)
    out = tmp_path / "rejudge.json"

    real = uat._ledger_runs

    def touch_then_read(store):
        db.write_bytes(db.read_bytes() + b"\x00")
        return real(store)

    monkeypatch.setattr(uat, "_ledger_runs", touch_then_read)
    assert uat.rejudge(db, out=out, first_pass=None, tenant_id=TENANT) == 1
    assert not out.exists(), (
        "a reconstruction was published for a ledger that changed underneath "
        "it")
