"""Triage non-passing Cockpit V4 runs into one primary fix class each.

Fixes must be general (semantics, aliases, plan schema, prompt, limits), never
per question. Every non-passing run is therefore put in exactly ONE class, and
each class names the file where the general fix belongs.

Input is the state database, opened read-only, and optionally a golden_eval
results JSON (records with run_id, verdict pass/fail/unscorable, expect_state).

Usage:
  .venv/bin/python scripts/cockpit_v4/golden_triage.py
  options: --db PATH  --results PATH  --since 2026-10-01T07  --out-dir DIR

Writes artifacts_eval/triage_<UTC ts>.md and .json. Writes nothing into the app.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_DB = "~/.creditprobe/cockpit_v4_round_h_live_pass/state/cockpit_v4.sqlite3"
DEFAULT_OUT = ROOT / "artifacts_eval"

#: Class -> where the general fix belongs. Order is the detection priority.
TARGETS = {
    "timeout": "limits profile (config.effective_limits)",
    "wrong_relation": "semantics.py term tables / catalog JOINS",
    "unknown_value": "values.py ALIASES",
    "plan_refused": "plan_compiler.plan_schema / metric_glossary",
    "evidence_check": "prompts/analyst.md finishing rules; finalization",
    "shape": "omlx_provider._coerce / contracts",
    "exec_limit": "inspect underlying rejections",
    "over_clarify": "prompts/analyst.md clarification rules; semantics.py term tables",
    "clarify (unlabelled)": "label expect_state in the golden questions, then re-triage",
    "worker": "worker/supervisor",
    "wrong_numbers": "review gold, then semantics/few-shot",
    "other": "read the run trace",
}

_TIMEOUT = re.compile(
    r"timed[ -]?out|did not complete|standard deadline passed|-second \w+ deadline passed", re.I)
_RELATION = re.compile(
    r"is not recorded on .*Relations holding|is not a governed field"
    r"|Binder Error: (Referenced column|Referenced table|Table \"\w+\" does not have a column"
    r"|column \"[^\"]+\" not found)|does not have a column|Referenced column"
    r"|did not bind and was not run", re.I)
_VALUE = re.compile(r"is not a value of", re.I)
_PLAN = re.compile(r"^plan\.|Nothing was executed\.$|^plan\.\w+ ", re.I)
_EVIDENCE = re.compile(
    r"did not pass its evidence check|no numeric_claim with that id|carries no numeric claim"
    r"|0 evidence-bound|^The response was not published", re.I)
_SHAPE = re.compile(
    r"must be an (array|object)|is required and was not supplied|unknown tool"
    r"|A clarification must carry", re.I)
_EXEC_LIMIT = re.compile(r"execution submissions for this run were used", re.I)
_WORKER = re.compile(r"process handling this request stopped responding", re.I)


def classify_message(text: str) -> str:
    """Class of one public rejection message ('other' when nothing matches).

    Same priority as the run-level rules. A trailing 'The first analytical
    failure in this run was: ...' is classified on its own, because that is the
    rejection the run actually died of.
    """
    text = (text or "").strip()
    if not text:
        return "other"
    if _TIMEOUT.search(text.split("The first analytical failure")[0]):
        return "timeout"
    if _WORKER.search(text):
        return "worker"
    # A GROUP BY or placeholder Binder error is a SQL-writing slip, not a relation problem.
    if re.search(r"must appear in the GROUP BY|positional placeholder", text):
        return "exec_limit" if _EXEC_LIMIT.search(text) else "other"
    for name, pattern in (("wrong_relation", _RELATION), ("unknown_value", _VALUE),
                          ("plan_refused", _PLAN), ("evidence_check", _EVIDENCE),
                          ("shape", _SHAPE)):
        if pattern.search(text):
            return name
    if _EXEC_LIMIT.search(text):
        return "exec_limit"
    return "other"


def _rejections(events: list[dict]) -> list[dict]:
    """Refusals in order: failed tool calls, failed runs, rejected answers."""
    out = []
    for e in events:
        kind = e.get("event_type", "")
        if kind in ("tool.failed", "run.failed", "run.expired") or (
                kind == "answer.validated" and e.get("status") not in ("ok", "", None)):
            out.append(e)
    return out


def classify_run(run: dict, events: list[dict], verdict: dict | None = None) -> tuple[str, str]:
    """(class, sample message) for one run; class '' means passed or unknown."""
    state, code = run.get("state", ""), run.get("error_code", "")
    rejected = _rejections(events)
    first = rejected[0].get("public_message", "") if rejected else ""
    first_class = classify_message(first)
    verdict = verdict or {}

    def hit(name: str) -> bool:
        if first_class == name:
            return True
        if name == "timeout":
            return code == "DEADLINE_EXPIRED" or (
                code == "PROVIDER_UNAVAILABLE" and bool(_TIMEOUT.search(first)))
        if name == "evidence_check":
            return code == "ANSWER_VALIDATION"
        if name == "exec_limit":
            return code == "EXECUTION_LIMIT"
        if name == "worker":
            return code == "WORKER_LOST" or state == "INTERRUPTED"
        return False

    # A deadline decides the outcome whatever refusals came first.
    if hit("timeout"):
        return "timeout", first
    for name in ("wrong_relation", "unknown_value", "plan_refused", "evidence_check", "shape"):
        if hit(name):
            return name, first
    if code == "EXECUTION_LIMIT":
        return "exec_limit", first
    if state == "WAITING_FOR_USER":
        if verdict.get("expect_state") == "COMPLETED":
            return "over_clarify", first
        if not verdict:
            return "clarify (unlabelled)", first
        return "", first
    if hit("worker"):
        return "worker", first
    if state == "COMPLETED":
        return ("wrong_numbers", first) if verdict.get("verdict") == "fail" else ("", first)
    if state in ("COMPLETED", "CANCELLED") and not code:
        return "", first
    return "other", first


def load_runs(db: str, since: str = "") -> list[dict]:
    path = os.path.expanduser(db)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        runs = [dict(r) for r in con.execute(
            "SELECT run_id, question, state, error_code, budget, created_at FROM runs "
            "ORDER BY created_at")]
        if since:
            runs = [r for r in runs if r["created_at"] >= since]
        for run in runs:
            run["events"] = [json.loads(r["payload"]) for r in con.execute(
                "SELECT payload FROM events WHERE run_id = ? ORDER BY seq", (run["run_id"],))]
    finally:
        con.close()
    return runs


def load_results(path: str | None) -> dict[str, dict]:
    if not path or not os.path.exists(path):
        return {}
    data = json.loads(pathlib.Path(path).read_text())
    records = data.get("results", data.get("records", [])) if isinstance(data, dict) else data
    return {r["run_id"]: r for r in records if isinstance(r, dict) and r.get("run_id")}


def triage(runs: list[dict], results: dict[str, dict]) -> dict:
    classes: dict[str, dict] = {}
    untriaged = 0
    messages: dict[str, int] = {}
    for run in runs:
        for e in _rejections(run["events"]):
            name = classify_message(e.get("public_message", ""))
            messages[name] = messages.get(name, 0) + 1
        name, sample = classify_run(run, run["events"], results.get(run["run_id"]))
        if not name:
            untriaged += 1
            continue
        entry = classes.setdefault(name, {"count": 0, "target": TARGETS[name], "sample": sample,
                                          "runs": []})
        entry["count"] += 1
        entry["runs"].append({"run_id": run["run_id"], "state": run["state"],
                              "question": run["question"][:100]})
        if not entry["sample"]:
            entry["sample"] = sample
    ordered = {k: classes[k] for k in TARGETS if k in classes}
    for entry in ordered.values():
        entry["sample"] = entry["sample"][:240]
    states: dict[str, int] = {}
    for run in runs:
        states[run["state"]] = states.get(run["state"], 0) + 1
    return {"runs_total": len(runs), "runs_not_triaged": untriaged,
            "runs_by_state": dict(sorted(states.items())), "classes": ordered,
            "rejection_messages_by_class": dict(sorted(messages.items()))}


def render_md(report: dict) -> str:
    lines = ["# Golden triage", "",
             f"Runs: {report['runs_total']}; passed or unknown (not triaged): "
             f"{report['runs_not_triaged']}", "", "| class | runs | target |", "|---|---|---|"]
    for name, e in report["classes"].items():
        lines.append(f"| {name} | {e['count']} | {e['target']} |")
    lines += ["", "Runs by state: " + ", ".join(f"{k} {v}" for k, v in report["runs_by_state"].items()),
              "", "Tool-level rejection messages by class: "
              + ", ".join(f"{k} {v}" for k, v in report["rejection_messages_by_class"].items()), ""]
    for name, e in report["classes"].items():
        lines += [f"## {name} ({e['count']})", f"Fix in: {e['target']}", "",
                  f"Sample: {e['sample'] or '(no rejection message)'}", ""]
        lines += [f"- {r['run_id']} [{r['state']}] {r['question']}" for r in e["runs"]]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--results", default="")
    ap.add_argument("--since", default="", help="ISO prefix; keep runs created at or after it")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    report = triage(load_runs(args.db, args.since), load_results(args.results))
    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"triage_{stamp}.json").write_text(json.dumps(report, indent=2))
    (out / f"triage_{stamp}.md").write_text(render_md(report))
    print(render_md(report).split("\n## ")[0])
    print(f"wrote {out / f'triage_{stamp}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
