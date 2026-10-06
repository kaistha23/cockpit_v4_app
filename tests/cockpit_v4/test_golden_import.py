"""golden_import: merge, group-safe splits, validation, promote. UNIT, NO MODEL.

The invariants worth pinning are the ones whose failure is silent: a paraphrase
landing in a different split than its twin (holdout then measures memorisation),
a CSV merge that blanks fields the sheet did not mention, and a promote that
glues several steps into a gold no single step computed.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import pathlib
import sqlite3

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "golden_import",
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "cockpit_v4" / "golden_import.py")
gi = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gi)

HEADER = ["id", "domain", "question", "gold_sql", "expected_values", "expected_text",
          "split", "status", "expect_state", "keys", "group", "mode", "tags"]


def _write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({**{h: "" for h in HEADER}, **r})


def _seed(path, entries):
    gi.save(path, entries)


def _entry(i, group=None, domain="corporate", tag="t", **kw):
    return gi.normalize({"id": i, "group": group or i, "domain": domain, "question": f"q {i}",
                         "gold_sql": "SELECT 1 AS x", "tags": [tag], **kw})


def test_csv_merge_adds_updates_and_keeps_unmentioned_fields(tmp_path):
    q = tmp_path / "q.json"
    _seed(q, [_entry("a", split="train", tags=["keep"])])
    src = tmp_path / "in.csv"
    _write_csv(src, [
        {"id": "a", "question": "new text"},
        {"id": "b", "domain": "retail", "question": "how many", "gold_sql": "SELECT 2 AS n",
         "expected_values": "1.5; 2", "expected_text": "SAR;stage", "keys": "stage; sector",
         "tags": "x;y", "expect_state": "completed"},
    ])
    assert gi.main(["--questions", str(q), "--from-csv", str(src)]) == 0
    got = {e["id"]: e for e in gi.load(q)}
    assert got["a"]["question"] == "new text"
    assert got["a"]["split"] == "train" and got["a"]["tags"] == ["keep"]
    b = got["b"]
    assert b["expected"] == {"values": [1.5, 2.0], "text_contains": ["SAR", "stage"]}
    assert b["contract"]["keys"] == ["stage", "sector"]
    assert b["expect_state"] == "COMPLETED" and b["tags"] == ["x", "y"]
    assert b["source"] == "user"


def test_csv_rejects_duplicate_ids_and_bad_enums_without_writing(tmp_path, capsys):
    q = tmp_path / "q.json"
    _seed(q, [_entry("a", split="train")])
    before = q.read_text()
    dup = tmp_path / "dup.csv"
    _write_csv(dup, [{"id": "z", "domain": "retail", "question": "q", "gold_sql": "s"},
                     {"id": "z", "domain": "retail", "question": "q", "gold_sql": "s"}])
    assert gi.main(["--questions", str(q), "--from-csv", str(dup)]) == 2
    bad = tmp_path / "bad.csv"
    _write_csv(bad, [{"id": "y", "domain": "mortgage", "question": "q", "gold_sql": "s"}])
    assert gi.main(["--questions", str(q), "--from-csv", str(bad)]) == 2
    bad_split = tmp_path / "bad_split.csv"
    _write_csv(bad_split, [{"id": "y", "domain": "retail", "question": "q",
                            "gold_sql": "s", "split": "dev"}])
    assert gi.main(["--questions", str(q), "--from-csv", str(bad_split)]) == 2
    assert q.read_text() == before


def test_splits_keep_groups_together_and_are_deterministic():
    entries = []
    for g in range(30):
        domain = "corporate" if g % 2 else "retail"
        for k in range(1 + g % 3):  # groups of 1-3 paraphrases
            entries.append(_entry(f"g{g}_{k}", group=f"g{g}", domain=domain, tag=f"t{g % 3}"))
    entries.append(_entry("loner", group=None))
    out = gi.assign_splits(entries, seed=3)
    by_group = {}
    for e in out:
        by_group.setdefault(e["group"], set()).add(e["split"])
    assert all(len(s) == 1 for s in by_group.values())
    assert {e["split"] for e in out} == set(gi.SPLITS)
    assert [e["split"] for e in out] == [e["split"] for e in gi.assign_splits(entries, seed=3)]
    assert gi.validate_all(out) == []


def test_splits_never_reassign_unless_asked_and_siblings_follow():
    entries = [_entry("a1", group="A", split="sealed"), _entry("a2", group="A")]
    entries += [_entry(f"x{i}") for i in range(20)]
    out = gi.assign_splits(entries, seed=1)
    assert [e["split"] for e in out[:2]] == ["sealed", "sealed"]
    pinned = [e for e in entries if e["id"].startswith("x")]
    for e in pinned:
        e["split"] = "train"
    out2 = gi.assign_splits(entries, seed=9)
    assert all(e["split"] == "train" for e in out2 if e["id"].startswith("x"))
    out3 = gi.assign_splits(entries, seed=9, reassign=True)
    assert {e["split"] for e in out3 if e["id"].startswith("x")} != {"train"}


def test_validation_reports_each_problem_and_exits_nonzero(tmp_path, capsys):
    good = _entry("ok", split="train")
    bad = [
        _entry("badtol", split="train",
               contract={"keys": ["k"], "measures": {"m": "m"}, "tolerance": {"rel": -1}}),
        _entry("badscale", split="train", contract={"scale": {"m": "pct"}}),
        _entry("nosplit"),
        _entry("nogold", split="train", gold_sql=None),
        _entry("baddomain", split="train", domain="mortgage"),
        _entry("p1", group="P", split="train"),
        _entry("p2", group="P", split="holdout"),
        _entry("ok", split="train"),
    ]
    problems = gi.validate_all([good] + bad)
    text = "\n".join(problems)
    for needle in ("badtol", "tolerance", "badscale", "nosplit", "nogold", "baddomain",
                   "span more than one split", "duplicate id"):
        assert needle in text
    q = tmp_path / "q.json"
    _seed(q, [good] + bad)
    assert gi.main(["--questions", str(q), "--validate"]) == 1
    clean = tmp_path / "clean.json"
    _seed(clean, [good])
    assert gi.main(["--questions", str(clean), "--validate"]) == 0


def test_waiting_for_user_needs_no_gold():
    e = _entry("w", split="train", gold_sql=None, expect_state="WAITING_FOR_USER")
    assert gi.validate_entry(e) == []


def test_pending_formula_detection():
    assert gi.needs_formula("avg lgd", "SELECT AVG(lgd_pct) FROM t")
    assert gi.needs_formula("avg pd", "SELECT AVG(t.pd_lifetime) FROM t")
    assert gi.needs_formula("ECL under a sensitivity shock", "SELECT SUM(ecl_sar_mn) FROM t")
    assert not gi.needs_formula("ecl by sector", "SELECT sector, SUM(ecl_sar_mn) FROM t GROUP BY 1")
    assert not gi.needs_formula("avg tenor", "SELECT AVG(tenor_months) FROM t")


def test_derive_contract_reads_group_by():
    c = gi.derive_contract("SELECT sector, SUM(ead_sar_mn) AS ead_sar_mn FROM t GROUP BY 1")
    assert c["keys"] == ["sector"] and c["measures"] == {"ead_sar_mn": "ead_sar_mn"}
    assert gi.derive_contract("SELECT COUNT(*) FROM t") is None


def _db(path, steps_by_submission):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE submissions (submission_id TEXT PRIMARY KEY, run_id TEXT, "
                "ordinal INTEGER, payload TEXT, status TEXT)")
    for n, (run, ordinal, status, steps) in enumerate(steps_by_submission):
        con.execute("INSERT INTO submissions VALUES (?,?,?,?,?)",
                    (f"s{n}", run, ordinal, json.dumps({"steps": [{"code": c} for c in steps]}), status))
    con.commit()
    con.close()


def test_promote_uses_latest_ok_submission_and_requires_yes(tmp_path, capsys):
    db = tmp_path / "s.sqlite3"
    _db(db, [("r1", 1, "ok", ["SELECT 1"]), ("r1", 2, "rejected", ["SELECT 2"]),
             ("r1", 3, "ok", ["SELECT 3 AS x"]), ("r2", 1, "ok", ["SELECT 4", "SELECT 5"])])
    q = tmp_path / "q.json"
    _seed(q, [_entry("a", split="train", gold_sql=None, status="pending_formula")])
    args = ["--questions", str(q), "--db", str(db), "--promote", "r1", "--id", "a"]
    assert gi.main(args) == 0
    assert gi.load(q)[0]["gold_sql"] is None  # no --yes, nothing written
    assert gi.main(args + ["--yes"]) == 0
    assert gi.load(q)[0]["gold_sql"] == "SELECT 3 AS x"
    multi = ["--questions", str(q), "--db", str(db), "--promote", "r2", "--id", "a", "--yes"]
    assert gi.main(multi) == 2  # more than one step
    assert gi.main(multi + ["--step", "2"]) == 0
    assert gi.load(q)[0]["gold_sql"] == "SELECT 5"
    assert gi.main(["--questions", str(q), "--db", str(db), "--promote", "nope", "--id", "a"]) == 2


def test_promote_opens_db_read_only(tmp_path):
    db = tmp_path / "s.sqlite3"
    _db(db, [("r1", 1, "ok", ["SELECT 1"])])
    before = db.read_bytes()
    gi.latest_ok_sql(str(db), "r1")
    assert db.read_bytes() == before


def test_twin_grouping_merges_period_and_sector_variants_only():
    entries = [
        _entry("c1", domain="corporate"), _entry("r1", domain="retail"),
        _entry("o1", group="orig"), _entry("o2", group="orig"),
        _entry("far"),
    ]
    entries[0]["question"] = "What is the loss given default by product type in the latest quarter?"
    entries[1]["question"] = "What is the loss given default by product in the latest month?"
    entries[2]["question"] = "How many covenants are in breach?"
    entries[3]["question"] = "Count covenant breaches by type"
    entries[4]["question"] = "Rank sectors by watchlist borrowers"
    out = {e["id"]: e["group"] for e in gi.group_twins(entries)}
    assert out["c1"] == out["r1"] == "twin_c1"
    assert out["o1"] == out["o2"] == "orig"  # untouched: matched nothing else
    assert out["far"] == "far"
    assert gi.group_twins(gi.group_twins(entries)) == gi.group_twins(entries)


def test_twins_then_splits_leave_no_cross_split_near_duplicates(tmp_path):
    entries = []
    for i in range(12):
        for dom, word in (("corporate", "sector"), ("retail", "product")):
            e = _entry(f"{dom[0]}{i}", domain=dom)
            e["question"] = f"Total metric{i} by {word} this quarter"
            entries.append(e)
    out = gi.assign_splits(gi.group_twins(entries), seed=7)
    assert gi.cross_split_twins(out) == []
    assert gi.validate_all(out) == []


def test_validate_fails_on_cross_split_near_duplicates():
    a = _entry("a", split="train")
    b = _entry("b", split="sealed")
    a["question"], b["question"] = "ECL by sector this quarter", "ECL by product this month"
    problems = gi.validate_all([a, b])
    assert any("near-duplicate" in p for p in problems)
