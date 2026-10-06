"""Measure the precision of the raw-SQL SHADOW intent rules on past runs.

WHY. `intent_check.check_sql` is shadow-only until a rule's precision is
measured at >= 0.95 (artifacts_eval/gates.json, phase_2_wrong_answers). This
script replays every historical raw-SQL submission (step_id not starting with
plan_/sensitivity_) from the state DB, opened READ-ONLY, and lists each flag
for a human to label. `--labels PATH` then computes precision per rule.

Plan submissions are replayed too (from the stored assistant messages, through
`intent_check.check_plan` against the live book) for the SHADOW plan rule
`operation_dropped`; its precision gates promotion the same way.

Entity rules need `resolved_entities`, which come from the live release
session; a stored run has none, so this replay passes an empty envelope and
`sql_entity` is NOT exercised (the report says so). sql_period and
sql_ratio_sum read only the question and the SQL.

Usage:
  shadow_precision.py [--db PATH] [--out labels.json]
  shadow_precision.py --labels labelled.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import types
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.cockpit_v4 import intent_check  # noqa: E402

DEFAULT_DB = Path("~/.creditprobe/cockpit_v4_round_h_live_pass/state/cockpit_v4.sqlite3")
RULES = ("sql_period", "sql_entity", "sql_ratio_sum", "sql_operation_dropped",
         "operation_dropped")
BAR = 0.95


def collect(db: Path) -> tuple[list[dict], int, int]:
    con = sqlite3.connect(f"file:{db.expanduser()}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT s.run_id, r.question, s.payload, s.status FROM submissions s "
        "JOIN runs r ON r.run_id = s.run_id ORDER BY s.created_at").fetchall()
    con.close()
    envelope = types.SimpleNamespace(resolved_entities=())
    items: list[dict] = []
    checked = 0
    for run_id, question, payload, status in rows:
        try:
            steps = json.loads(payload).get("steps") or []
        except (ValueError, AttributeError):
            continue
        for step in steps:
            sid = str(step.get("step_id", ""))
            if step.get("language") != "sql" or sid.startswith(("plan_", "sensitivity_")):
                continue
            checked += 1
            code = str(step.get("code", ""))
            for f in intent_check.check_sql(code, question=question, envelope=envelope):
                items.append({"run_id": run_id, "step_id": sid, "status": status,
                              "question": question, "sql": code, "rule": f.rule,
                              "message": f.message, "label": None})
    return items, len(rows), checked


def _plans_in(content: str):
    """Every governed plan the analyst sent in one stored assistant message."""
    try:
        blocks = json.loads(content)
    except ValueError:
        return
    for block in blocks if isinstance(blocks, list) else []:
        payload = block.get("input") if isinstance(block, dict) else None
        plan = payload.get("plan") if isinstance(payload, dict) else None
        if isinstance(plan, str):
            try:
                plan = json.loads(plan)
            except ValueError:
                plan = None
        if isinstance(plan, dict):
            yield block.get("id", ""), plan


def collect_plans(db: Path) -> tuple[list[dict], int]:
    """SHADOW plan findings over every stored plan; a plan the book refuses is skipped."""
    from backend.cockpit_v4 import catalog as cat
    from backend.cockpit_v4 import plan_compiler as pc
    from backend.cockpit_v4.contracts import Rejection

    con = sqlite3.connect(f"file:{db.expanduser()}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT m.run_id, r.question, r.domain_id, m.content FROM messages m "
        "JOIN runs r ON r.run_id = m.run_id WHERE m.role = 'assistant' "
        "AND m.content LIKE '%\"plan\"%' ORDER BY r.created_at, m.ordinal").fetchall()
    con.close()
    envelope = types.SimpleNamespace(resolved_entities=())
    books: dict[str, object] = {}
    items: list[dict] = []
    checked = 0
    for run_id, question, domain, content in rows:
        for call_id, plan in _plans_in(content):
            if domain not in books:
                catalog = cat.build(domain_id=domain)
                books[domain] = pc.build_book(domain_id=domain, catalog=catalog,
                                              session=cat.open_session(catalog=catalog))
            try:
                found = intent_check.check_plan(plan, question=question, envelope=envelope,
                                                book=books[domain])
            except Rejection:
                continue
            checked += 1
            for f in found:
                if f.severity == intent_check.SHADOW:
                    items.append({"run_id": run_id, "step_id": call_id, "status": "plan",
                                  "question": question, "sql": json.dumps(plan),
                                  "rule": f.rule, "message": f.message, "label": None})
    return items, checked


def report(items: list[dict], submissions: int, checked: int) -> None:
    print(f"submissions={submissions} raw_sql_steps_checked={checked}")
    print("NOTE: no resolved_entities in stored runs -> sql_entity not exercised")
    for rule in RULES:
        hits = [i for i in items if i["rule"] == rule]
        print(f"\n== {rule}: flags={len(hits)}")
        for i in hits:
            snippet = " ".join(i["sql"].split())[:160]
            print(f"  {i['run_id']} | {i['question'][:90]!r} | {snippet} | {i['message'][:100]}")


def precision(path: Path) -> int:
    items = json.loads(path.read_text())
    items = items.get("items", items) if isinstance(items, dict) else items
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # tp, fp, unlabelled
    for i in items:
        s = stats[i["rule"]]
        label = i.get("label")
        if label in (True, "correct", "tp"):
            s[0] += 1
        elif label in (False, "incorrect", "fp"):
            s[1] += 1
        else:
            s[2] += 1
    ok = True
    for rule in RULES:
        tp, fp, un = stats[rule]
        p = tp / (tp + fp) if tp + fp else None
        verdict = "n/a" if p is None else ("PROMOTE" if p >= BAR and not un else "hold")
        print(f"{rule}: tp={tp} fp={fp} unlabelled={un} precision="
              f"{'n/a' if p is None else f'{p:.3f}'} -> {verdict}")
        ok = ok and verdict != "hold"
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default="shadow_precision_labels.json")
    ap.add_argument("--labels", help="labelled JSON: label true=flag was right, false=wrong")
    args = ap.parse_args()
    if args.labels:
        return precision(Path(args.labels))
    items, submissions, checked = collect(Path(args.db))
    plan_items, plans_checked = collect_plans(Path(args.db))
    items += plan_items
    print(f"plans_checked={plans_checked}")
    report(items, submissions, checked)
    Path(args.out).write_text(json.dumps(
        {"label_help": "true = the flag correctly caught a wrong answer; false = false alarm",
         "items": items}, indent=2))
    print(f"\nwrote {args.out} ({len(items)} items to label)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
