"""Golden triage: every real refusal message lands in the hand-labelled fix class.

UNIT. No provider call. The fixture holds the distinct public messages of the
real state database, each labelled by hand with the class its fix belongs to.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "cockpit_v4"))

import golden_triage as gt  # noqa: E402

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "triage_messages.json").read_text())


@pytest.mark.parametrize("case", FIXTURES, ids=lambda c: c["message"][:50])
def test_message_fixture(case):
    assert gt.classify_message(case["message"]) == case["expected"]


def test_fixture_covers_every_refusal_class():
    assert {c["expected"] for c in FIXTURES} >= {
        "timeout", "wrong_relation", "unknown_value", "plan_refused",
        "evidence_check", "shape", "exec_limit", "other"}


def _ev(kind, message, status="failed"):
    return {"event_type": kind, "public_message": message, "status": status}


def _run(state, code="", **extra):
    return {"run_id": "run-x", "question": "q", "state": state, "error_code": code, **extra}


def test_deadline_beats_earlier_refusal():
    run = _run("PARTIAL", "DEADLINE_EXPIRED")
    assert gt.classify_run(run, [_ev("tool.failed", "steps must be an array.")])[0] == "timeout"


def test_provider_unavailable_only_timeout_when_message_says_so():
    run = _run("PARTIAL", "PROVIDER_UNAVAILABLE")
    assert gt.classify_run(run, [_ev("run.failed", "the provider request timed out")])[0] == "timeout"
    assert gt.classify_run(run, [_ev("run.failed", "oMLX request failed")])[0] == "other"


def test_first_rejection_decides():
    events = [_ev("tool.failed", "'Oman' is not a value of region. Its values are: Asir."),
              _ev("tool.failed", "plan.measures must name 1 to 4 measures. Nothing was executed.")]
    assert gt.classify_run(_run("PARTIAL", "EXECUTION_LIMIT"), events)[0] == "unknown_value"


def test_exec_limit_when_nothing_else_found():
    events = [_ev("run.failed", "all 5 execution submissions for this run were used.")]
    assert gt.classify_run(_run("FAILED", "EXECUTION_LIMIT"), events)[0] == "exec_limit"


def test_answer_validation_code_is_evidence():
    assert gt.classify_run(_run("PARTIAL", "ANSWER_VALIDATION"), [])[0] == "evidence_check"


def test_waiting_for_user_needs_label():
    run = _run("WAITING_FOR_USER")
    assert gt.classify_run(run, [])[0] == "clarify (unlabelled)"
    assert gt.classify_run(run, [], {"expect_state": "COMPLETED"})[0] == "over_clarify"
    assert gt.classify_run(run, [], {"expect_state": "WAITING_FOR_USER"})[0] == ""


def test_worker_lost():
    assert gt.classify_run(_run("INTERRUPTED", "WORKER_LOST"), [])[0] == "worker"


def test_completed_needs_results_to_be_triaged():
    assert gt.classify_run(_run("COMPLETED"), [])[0] == ""
    assert gt.classify_run(_run("COMPLETED"), [], {"verdict": "pass"})[0] == ""
    assert gt.classify_run(_run("COMPLETED"), [], {"verdict": "fail"})[0] == "wrong_numbers"


def test_triage_and_cli_on_synthetic_db(tmp_path, capsys):
    db = tmp_path / "s.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE runs (run_id, question, state, error_code, budget, created_at)")
    con.execute("CREATE TABLE events (run_id, seq, payload)")
    for i, (state, code, ts) in enumerate([("COMPLETED", "", "2026-10-01T00"),
                                           ("PARTIAL", "DEADLINE_EXPIRED", "2026-10-02T00")]):
        con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                    (f"r{i}", "question " * 30, state, code, "{}", ts))
    con.execute("INSERT INTO events VALUES ('r1', 1, ?)",
                (json.dumps(_ev("run.expired", "The standard deadline passed while writing.")),))
    con.commit()
    con.close()
    out = tmp_path / "out"
    assert gt.main(["--db", str(db), "--since", "2026-10-02", "--out-dir", str(out),
                    "--results", str(tmp_path / "missing.json")]) == 0
    report = json.loads(next(out.glob("triage_*.json")).read_text())
    assert report["runs_total"] == 1 and report["classes"]["timeout"]["count"] == 1
    assert len(report["classes"]["timeout"]["runs"][0]["question"]) <= 100
    assert next(out.glob("triage_*.md")).exists()
