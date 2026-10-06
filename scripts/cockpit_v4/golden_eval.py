"""Golden-question evaluation of a model on the running Cockpit V4 (slm_v1 pattern).

Runs each question in tests/cockpit_v4/golden/questions.json as a new thread on
the local Cockpit (loopback demo auth), then scores the run against the
question's hand-written gold SQL:

  state            COMPLETED / PARTIAL / FAILED / ...
  seconds          wall time to a terminal state
  submissions      execute_analysis calls the run made
  rejections       every refused tool call, classified (shape, required,
                   sql_bind, sql_parse, cross_step, evidence, unknown_tool, ...)
  verdict          contract score (golden_scoring.score_contract): gold rows are
                   aligned to the run's rows by entity key and each measure is
                   compared in its own column. pass | fail | unscorable.
  result_match     the OLD bag-of-numbers share, kept as legacy_match only to
                   compare against the contract verdict.
  answer_grounded  share of the numbers written in the published narrative that
                   can be traced to the run's results (a cell, a column total,
                   or a share of one), at the precision they were written.

Usage (the app must be running, `make app`):
  .venv/bin/python scripts/cockpit_v4/golden_eval.py --provider omlx \
      --model Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-text-oQ4e-mtp
  options: --ids c01,r02  --domain corporate  --mode standard  --out results.json
           --split train|holdout|sealed|all (default: everything except sealed)
           --repeat N  --status ready|all  --paired BASELINE.json
           --mine DB   score runs already in a state DB (starts nothing)

Writes nothing into the app's state; it only starts runs and reads the state
database read-only. A markdown + JSON report (golden_report) is written beside
--out, including a paired comparison when --paired is given.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
import urllib.error
import urllib.request
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_report  # noqa: E402
import golden_scoring as gs  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COCKPIT_V4_NAMESPACE", "cockpit_v4")
os.environ.setdefault("COCKPIT_AGENTIC_V3_NAMESPACE", "cockpit_v4")

QUESTIONS = ROOT / "tests" / "cockpit_v4" / "golden" / "questions.json"
DEFAULT_DB = "~/.creditprobe/cockpit_v4_round_h_live_pass/state/cockpit_v4.sqlite3"

#: The qualification bar for a model to answer Cockpit questions.
PASS_BAR = {"completed_share": 0.8, "median_seconds": 90.0,
            "result_match": 0.8, "answer_grounded": 0.95}

REJECTION_CLASSES = (
    ("unknown_tool", r"unknown tool"),
    ("shape", r"must be an (array|object)|is not an array|must be a string"),
    ("required", r"is required and was not supplied"),
    ("cross_step", r"is not readable from this analysis"),
    ("sql_parse", r"could not be parsed|Parser Error"),
    ("sql_bind", r"did not bind|Binder Error"),
    ("evidence", r"not published|claims a ranking|evidence|row_key|numeric claim"),
    ("budget", r"submissions|exhausted|allowance"),
    ("mode_lock", r"answer-correction mode"),
)


def classify(message: str) -> str:
    for name, pattern in REJECTION_CLASSES:
        if re.search(pattern, message, re.I):
            return name
    return "other"


# --------------------------------------------------------------- http

def call(api: str, method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(api + path, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


# --------------------------------------------------------------- numbers

def _flat(tables: list[list[dict]]) -> list[dict]:
    return [r for rows in tables for r in rows]


#: Numbers that are labels, not figures: periods, stage names, years, list markers.
_NOT_A_FIGURE = re.compile(
    r"\b\d{4}Q[1-4]\b|\b\d{4}-\d{2}\b|\bQ[1-4]\b|\b(?:stage|bucket|tier)\s+\d\b|"
    r"\b(?:19|20)\d{2}\b|\b\d+-\d+\b|\b\d+\+", re.I)
_FIGURE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def narrative_figures(text: str) -> list[tuple[float, int]]:
    """(value, decimals written) for each figure in the narrative."""
    cleaned = _NOT_A_FIGURE.sub(" ", text or "")
    out = []
    for match in _FIGURE.finditer(cleaned):
        raw = match.group(0).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        out.append((value, decimals))
    return out


def traceable(run_rows_by_artifact: list[list[dict]]) -> list[float]:
    """Figures an answer may legitimately state: cells, column totals, shares."""
    values: list[float] = []
    for rows in run_rows_by_artifact:
        columns: dict[str, list[float]] = {}
        for row in rows:
            numeric = {k: float(v) for k, v in row.items()
                       if isinstance(v, (int, float)) and not isinstance(v, bool)}
            values.extend(numeric.values())
            for k, v in numeric.items():
                columns.setdefault(k, []).append(v)
            names = list(numeric)
            for i, a in enumerate(names):            # within-row ratios (e.g. coverage)
                for b in names[i + 1:]:
                    if numeric[b]:
                        values.append(numeric[a] / numeric[b] * 100)
                    if numeric[a]:
                        values.append(numeric[b] / numeric[a] * 100)
        totals = {col: sum(series) for col, series in columns.items()}
        for a in totals:                                   # ratio of two column totals
            for b in totals:                               # (e.g. overall coverage)
                if a != b and totals[b]:
                    values.append(totals[a] / totals[b] * 100)
        for col, series in columns.items():
            total = sum(series)
            values.append(total)
            if total:
                values.extend(v / total * 100 for v in series)   # shares of a total
            if len(series) >= 2:
                values.extend(b - a for a, b in zip(series, series[1:]))    # changes
                values.extend((b - a) / a * 100 for a, b in zip(series, series[1:]) if a)
    return values


def answer_grounded(narrative: str, run_rows_by_artifact: list[list[dict]]) -> float | None:
    figures = narrative_figures(narrative)
    if not figures:
        return None
    allowed = traceable(run_rows_by_artifact)
    ok = 0
    for value, decimals in figures:
        tolerance = 0.5 * 10 ** (-decimals) if decimals else 0.5
        if any(abs(abs(value) - abs(a)) <= max(tolerance, 0.005 * abs(a)) for a in allowed):
            ok += 1
    return ok / len(figures)


# --------------------------------------------------------------- state reads


def _json(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _upto(pair, default=0):
    """Budget counters are [used, limit]; the run's own use is the first."""
    return pair[0] if isinstance(pair, (list, tuple)) and pair else default


def call_timings(events: list[dict], reservations: list[dict]) -> dict:
    """Per-call latency (requested -> response_received) and best-effort tokens/s.

    Tokens are paired to calls by order only when the counts agree; otherwise
    only an aggregate rate is given and `tokens_paired` is False, because a
    mispaired rate would be worse than none.
    """
    latencies, pending = [], None
    last_response_ms = None
    for e in events:
        kind = e.get("event_type")
        if kind == "model.requested":
            pending = e.get("elapsed_ms")
        elif kind == "model.response_received":
            if pending is not None and e.get("elapsed_ms") is not None:
                latencies.append(max(0.0, (e["elapsed_ms"] - pending) / 1000))
            pending = None
            last_response_ms = e.get("elapsed_ms")
    outs = [int((r.get("usage") or {}).get("output_tokens") or 0) for r in reservations]
    paired = bool(latencies) and len(outs) == len(latencies)
    if paired:
        rates = [round(o / s, 1) if s > 0 else None for o, s in zip(outs, latencies)]
    else:
        rates = []
    total_s = sum(latencies)
    ready = next((e.get("elapsed_ms") for e in reversed(events)
                  if e.get("event_type") == "answer.ready"), None)
    overhead = None
    if ready is not None and last_response_ms is not None:
        overhead = round(max(0.0, (ready - last_response_ms) / 1000), 2)
    return {"call_seconds": [round(x, 2) for x in latencies],
            "tokens_paired": paired, "tokens_per_s_by_call": rates,
            "output_tokens": sum(outs),
            "tokens_per_s": round(sum(outs) / total_s, 1) if total_s > 0 and outs else None,
            "publication_overhead_s": overhead,
            "run_seconds": round(max((e.get("elapsed_ms") or 0) for e in events) / 1000, 1) if events else None}


def run_facts(db: str, run_id: str) -> dict:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        events = [_json(p, {}) for (p,) in con.execute(
            "SELECT payload FROM events WHERE run_id=? ORDER BY seq", (run_id,))]
        arts = [(aid, _json(cols, []), _json(b, []), n) for aid, cols, b, n in con.execute(
            "SELECT artifact_id, columns, body, row_count FROM artifacts "
            "WHERE run_id=? AND kind='result' ORDER BY rowid", (run_id,))]
        reservations = [{"purpose": p, "usage": _json(u, {})} for p, u in con.execute(
            "SELECT purpose, usage FROM reservations WHERE run_id=? ORDER BY created_at", (run_id,))]
        row = con.execute("SELECT final_response, budget, state, error_code, operation "
                          "FROM runs WHERE run_id=?", (run_id,)).fetchone()
    final = _json(row[0], {}) if row else {}
    final = final if isinstance(final, dict) else {}
    budget = _json(row[1], {}) if row else {}
    rejections = [str(e.get("public_message", "")) for e in events if e.get("event_type") == "tool.failed"]
    submissions = sum(1 for e in events if e.get("event_type") == "tool.requested"
                      and e.get("operation") == "execute_analysis")
    turns = sum(1 for e in events if e.get("event_type") == "model.response_received")
    tables = [b if isinstance(b, list) else b.get("rows", []) for _, _, b, _ in arts]
    return {"rejections": rejections, "submissions": submissions, "turns": turns,
            "artifacts": tables,
            "artifacts_by_id": {aid: t for (aid, _, _, _), t in zip(arts, tables)},
            "final": final, "narrative": str(final.get("narrative", "")),
            "state": row[2] if row else None, "error_code": row[3] if row else "",
            "operation": row[4] if row else "",
            "generations": _upto(budget.get("generation_attempts")),
            "budget_submissions": _upto(budget.get("execution_submissions")),
            "timings": call_timings(events, reservations)}


def gold_result(domain: str, sql: str):
    from backend.cockpit_v4 import analytical_runtime as ar
    cursor = ar.for_domain(domain).session.connection.execute(sql)
    return [d[0] for d in cursor.description], cursor.fetchall()


# --------------------------------------------------------------- items & scoring

def normalise_item(q: dict) -> dict:
    """Fill schema-2 defaults so old {id, group, domain, question, gold_sql} entries work."""
    item = dict(q)
    item.setdefault("split", "train")
    item.setdefault("status", "ready")
    item.setdefault("contract", None)
    item.setdefault("gold_sql", None)
    item.setdefault("expected", None)
    item.setdefault("tags", [])
    item.setdefault("expect_state", None)
    return item


def norm_question(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def select_items(questions: list[dict], args) -> list[dict]:
    items = [normalise_item(q) for q in questions]
    if args.ids:
        items = [q for q in items if q["id"] in set(args.ids.split(","))]
    if args.domain:
        items = [q for q in items if q["domain"] == args.domain]
    if args.split == "all":
        pass
    elif args.split:
        items = [q for q in items if q["split"] == args.split]
    else:
        items = [q for q in items if q["split"] != "sealed"]
    if args.status == "ready":
        items = [q for q in items if q["status"] == "ready"]
    return items


def score_item(item: dict, state: str | None, facts: dict, gold: tuple | None) -> dict:
    """Verdict for one run of one corpus item (see golden_report for the rates).

    A run that never completed is a hard "fail" rather than "unscorable": an
    unscorable run is dropped from eligible accuracy, which would let failures
    flatter the accuracy.
    """
    state = (state or "").upper()
    out: dict = {"verdict": "unscorable", "reasons": [], "headline": None, "legacy_match": None}
    flat = _flat(facts["artifacts"])
    if gold is not None:
        out["legacy_match"] = round(gs.legacy_result_match(gold[1], gold[0], flat), 3)
    if item["status"] == "pending_formula":
        out.update(verdict="pending", reasons=["pending_formula"])
        return out
    expect = (item.get("expect_state") or "").upper()
    if expect and state != expect:
        out.update(verdict="fail", reasons=[f"expected state {expect}, got {state}"])
        return out
    if expect and state == expect and expect != "COMPLETED":
        out.update(verdict="pass", reasons=[f"reached expected state {expect}"])
        return out
    if state != "COMPLETED":
        out.update(verdict="fail", reasons=["no_completed_answer"])
        return out
    if gold is not None:
        contract = item["contract"] or gs.derive_contract(gold[1], gold[0])
        res = gs.score_contract(gold[1], gold[0], facts["artifacts"], contract)
        out.update(verdict=res["verdict"], reasons=res["reasons"],
                   contract_detail={k: res.get(k) for k in ("matched", "missing", "extra", "table_index")})
        hl = gs.score_headline(facts["final"], gold[1], contract, gold[0], facts["artifacts_by_id"])
        out["headline"] = hl["verdict"]
    elif item["expected"]:
        res = gs.score_values(item["expected"], flat, facts["narrative"])
        out.update(verdict=res["verdict"], reasons=res["reasons"], weak=True)
    else:
        out["reasons"] = ["no gold_sql or expected values"]
    return out


def make_record(item: dict | None, run_id: str, state: str | None, seconds, facts: dict,
                gold: tuple | None, repeat: int = 0) -> dict:
    classes: dict[str, int] = {}
    for message in facts["rejections"]:
        key = classify(message)
        classes[key] = classes.get(key, 0) + 1
    grounded = answer_grounded(facts["narrative"], facts["artifacts"])
    t = facts["timings"]
    rec = {"id": item["id"] if item else None,
           "domain": item["domain"] if item else None,
           "group": item.get("group") if item else None,
           "tags": item["tags"] if item else [],
           "split": item["split"] if item else None,
           "status": item["status"] if item else None,
           "expect_state": item.get("expect_state") if item else None,
           "question": item["question"] if item else None,
           "run_id": run_id, "repeat": repeat, "state": state,
           "error_code": facts["error_code"], "operation": facts["operation"],
           "seconds": seconds, "call_seconds": t["call_seconds"],
           "tokens_per_s": t["tokens_per_s"], "tokens_per_s_by_call": t["tokens_per_s_by_call"],
           "tokens_paired": t["tokens_paired"], "output_tokens": t["output_tokens"],
           "generations": facts["generations"], "submissions": facts["submissions"],
           "budget_submissions": facts["budget_submissions"], "turns": facts["turns"],
           "publication_overhead_s": t["publication_overhead_s"],
           "rejections": classes, "rejection_messages": facts["rejections"],
           "answer_grounded": None if grounded is None else round(grounded, 3)}
    if item is None:
        rec["verdict"] = "unmatched"
    else:
        rec.update(score_item(item, state, facts, gold))
    return rec


def _gold_for(item: dict, cache: dict):
    if item["id"] in cache:
        return cache[item["id"]]
    gold = None
    if item.get("gold_sql"):
        try:
            cols, rows = gold_result(item["domain"], item["gold_sql"])
            gold = (cols, rows)
        except Exception as exc:                      # unscorable, not a crash
            print(f"[gold failed] {item['id']}: {exc}", flush=True)
    cache[item["id"]] = gold
    return gold


def mine(db: str, items_all: list[dict], selected: list[dict]) -> list[dict]:
    """Score runs already in a state DB; no runs are started.

    Matching is by exact normalised question text. Runs matching no corpus item
    are kept as 'unmatched' so state/error statistics still cover them.
    """
    by_q = {norm_question(i["question"]): i for i in items_all}
    wanted = {i["id"] for i in selected}
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        runs = con.execute("SELECT run_id, question, state FROM runs ORDER BY created_at").fetchall()
    cache: dict = {}
    records, repeats = [], defaultdict(int)
    for run_id, question, state in runs:
        item = by_q.get(norm_question(question))
        if item is not None and item["id"] not in wanted:
            continue
        facts = run_facts(db, run_id)
        gold = _gold_for(item, cache) if item else None
        seconds = facts["timings"]["run_seconds"]
        n = repeats[item["id"]] if item else 0
        if item:
            repeats[item["id"]] += 1
        records.append(make_record(item, run_id, state, seconds, facts, gold, n))
    return records


# --------------------------------------------------------------- main

def summarise(records: list[dict], report: dict, label: dict) -> dict:
    scored = [r for r in records if isinstance(r.get("seconds"), (int, float))]
    grounded = [r["answer_grounded"] for r in scored if r["answer_grounded"] is not None]
    acc = report["eligible_accuracy"]["rate"]
    summary = {
        **label, "n": len(records),
        "completed_share": round(sum(str(r["state"]).upper() == "COMPLETED" for r in records)
                                 / max(len(records), 1), 3),
        "median_seconds": round(statistics.median([r["seconds"] for r in scored]), 1) if scored else None,
        "result_match": None if acc is None else round(acc, 3),      # contract eligible accuracy
        "total_coverage": report["total_coverage"]["rate"],
        "legacy_result_match": round(sum((r.get("legacy_match") or 0) >= 0.95 for r in scored)
                                     / max(len(scored), 1), 3),
        "answer_grounded": round(statistics.mean(grounded), 3) if grounded else None,
        "rejections": {k: sum(r["rejections"].get(k, 0) for r in scored)
                       for k in sorted({k for r in scored for k in r["rejections"]})},
    }
    summary["passes_bar"] = bool(
        summary["completed_share"] >= PASS_BAR["completed_share"]
        and (summary["median_seconds"] or 1e9) <= PASS_BAR["median_seconds"]
        and (summary["result_match"] or 0) >= PASS_BAR["result_match"]
        and (summary["answer_grounded"] or 0) >= PASS_BAR["answer_grounded"])
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="omlx")
    ap.add_argument("--model", default="")
    ap.add_argument("--mode", default="standard")
    ap.add_argument("--ids", default="")
    ap.add_argument("--domain", default="")
    ap.add_argument("--split", default="", choices=["", "train", "holdout", "sealed", "all"],
                    help="default: every split except sealed")
    ap.add_argument("--status", default="ready", choices=["ready", "all"])
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--paired", default="", help="baseline results JSON to compare against")
    ap.add_argument("--mine", default="", metavar="DB", help="score runs already in this state DB")
    ap.add_argument("--api", default="http://127.0.0.1:8414")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if not args.mine and not args.model:
        ap.error("--model is required unless --mine is given")

    api = args.api.rstrip("/") + "/api/v1/cockpit-v4"
    db = os.path.expanduser(args.mine or args.db)
    corpus = json.loads(QUESTIONS.read_text())["questions"]
    items_all = [normalise_item(q) for q in corpus]
    items = select_items(corpus, args)

    if args.mine:
        results = mine(db, items_all, items)
        matched = sum(r["verdict"] != "unmatched" for r in results)
        print(f"mined {len(results)} runs; {matched} matched corpus items, "
              f"{len(results) - matched} unmatched", flush=True)
    else:
        results, cache = [], {}
        for q in items:
            gold = _gold_for(q, cache)
            for rep in range(max(args.repeat, 1)):
                t0 = time.time()
                status, run = call(api, "POST", "/runs", {"question": q["question"], "mode": args.mode,
                                                          "domain": q["domain"], "provider": args.provider,
                                                          "model": args.model})
                if status not in (200, 202):
                    results.append({"id": q["id"], "domain": q["domain"], "tags": q["tags"],
                                    "status": q["status"], "repeat": rep, "verdict": "fail",
                                    "state": f"HTTP {status}", "detail": run})
                    print(f"[REFUSED {status}] {q['id']} {run}", flush=True)
                    continue
                run_id = run["run_id"]
                body = {}
                while time.time() - t0 < args.timeout:
                    _, body = call(api, "GET", f"/runs/{run_id}")
                    if body.get("terminal"):
                        break
                    time.sleep(3)
                else:
                    body = {**body, "state": "EVAL_TIMEOUT"}
                facts = run_facts(db, run_id)
                row = make_record(q, run_id, body.get("state"), round(time.time() - t0, 1),
                                  facts, gold, rep)
                results.append(row)
                print(f"[{row['state']:>9}] {q['id']}#{rep} {row['seconds']:6.1f}s subm={row['submissions']} "
                      f"verdict={row['verdict']} legacy={row['legacy_match']} "
                      f"grounded={row['answer_grounded']} rej={row['rejections']}", flush=True)

    report = golden_report.build_report(results)
    comparison = None
    if args.paired:
        baseline = json.loads(pathlib.Path(args.paired).read_text())
        comparison = golden_report.compare(baseline.get("results", baseline), results)
    label = {"provider": args.provider, "model": args.model or "(mined)", "mode": args.mode}
    summary = summarise(results, report, label)
    print("\nSUMMARY", json.dumps(summary, indent=1))
    print(golden_report.to_markdown(report, comparison))
    out = args.out or str(ROOT / "artifacts_eval" /
                          f"golden_{'mine' if args.mine else args.provider}_{datetime.now():%Y%m%d_%H%M%S}.json")
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(out).write_text(json.dumps({"summary": summary, "report": report,
                                             "comparison": comparison, "results": results,
                                             "records": results},
                                            indent=1, default=str))
    golden_report.write_report(str(pathlib.Path(out).with_suffix("")), report, comparison)
    print("saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
