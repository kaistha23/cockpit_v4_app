"""Aggregate golden-eval run records into an honest report.

WHY: one "result_match" number hid three different things. Here the rates are
kept apart, each with a Wilson interval, because a corpus of a few dozen items
cannot support a bare percentage:

  eligible accuracy  passes / eligible (status ready and not unscorable)
  total coverage     passes / ALL items; the number that cannot be flattered by
                     dropping hard or unscorable items
  wrong-answer rate  a completed answer that failed its contract
  failure rate       no completed answer at all

`compare` pairs two runs of the same items and gives an exact McNemar test, so
"better" needs evidence, and lists regressions explicitly.

Record fields used: id, verdict ("pass"|"fail"|"unscorable"|"pending"|"unmatched"),
status ("ready"|"pending_formula"), state, domain, tags, category (optional).
"""
from __future__ import annotations

import json
import math
from collections import defaultdict


def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval; sensible at small n and at 0 or n successes."""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def category(rec: dict) -> str:
    tags = rec.get("tags") or []
    return rec.get("category") or (tags[0] if tags else None) or rec.get("domain") or "uncategorised"


def _rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None,
            "ci95": [round(lo, 4), round(hi, 4)]}


def _is_eligible(r: dict) -> bool:
    return r.get("status", "ready") == "ready" and r.get("verdict") in ("pass", "fail")


def _answered(r: dict) -> bool:
    return str(r.get("state", "")).upper() == "COMPLETED"


def _block(recs: list[dict]) -> dict:
    total = len(recs)
    passes = sum(r.get("verdict") == "pass" for r in recs)
    eligible = [r for r in recs if _is_eligible(r)]
    wrong = sum(1 for r in eligible if r["verdict"] == "fail" and _answered(r))
    failed = sum(1 for r in eligible if r["verdict"] == "fail" and not _answered(r))
    return {
        "total": total,
        "passes": passes,
        "eligible": len(eligible),
        "pending_formula": sum(r.get("status") == "pending_formula" for r in recs),
        "unscorable": sum(r.get("status", "ready") == "ready" and r.get("verdict") == "unscorable"
                          for r in recs),
        "eligible_accuracy": _rate(passes, len(eligible)),
        "total_coverage": _rate(passes, total),
        "wrong_answer_rate": _rate(wrong, len(eligible)),
        "failure_rate": _rate(failed, len(eligible)),
    }


def build_report(records: list[dict]) -> dict:
    """Report over matched items; runs with verdict 'unmatched' only feed state stats."""
    items = [r for r in records if r.get("verdict") != "unmatched"]
    unmatched = [r for r in records if r.get("verdict") == "unmatched"]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in items:
        by_cat[category(r)].append(r)
    states: dict[str, int] = defaultdict(int)
    for r in records:
        states[str(r.get("state"))] += 1
    report = _block(items)
    report["categories"] = {c: _block(rs) for c, rs in sorted(by_cat.items())}
    report["states"] = dict(sorted(states.items()))
    report["unmatched_runs"] = len(unmatched)
    return report


def _per_item(records: list[dict]) -> dict[str, bool | None]:
    """Item -> passed (majority of repeats); None when it has no pass/fail verdict."""
    groups: dict[str, list[str]] = defaultdict(list)
    for r in records:
        if r.get("id") is not None and r.get("verdict") in ("pass", "fail"):
            groups[r["id"]].append(r["verdict"])
    return {i: sum(v == "pass" for v in vs) * 2 > len(vs) for i, vs in groups.items()}


def compare(baseline_records: list[dict], candidate_records: list[dict]) -> dict:
    """Paired comparison on items scored (pass or fail) in both runs."""
    base, cand = _per_item(baseline_records), _per_item(candidate_records)
    cats = {r["id"]: category(r) for r in baseline_records + candidate_records if "id" in r}
    shared = sorted(set(base) & set(cand))
    trans = {"pass_pass": 0, "pass_fail": 0, "fail_pass": 0, "fail_fail": 0}
    regressions, improvements = [], []
    per_cat: dict[str, dict] = defaultdict(lambda: {"n": 0, "baseline": 0, "candidate": 0})
    for i in shared:
        b, c = base[i], cand[i]
        trans[f"{'pass' if b else 'fail'}_{'pass' if c else 'fail'}"] += 1
        if b and not c:
            regressions.append(i)
        if c and not b:
            improvements.append(i)
        cell = per_cat[cats.get(i, "uncategorised")]
        cell["n"] += 1
        cell["baseline"] += b
        cell["candidate"] += c
    for cell in per_cat.values():
        cell["delta"] = (cell["candidate"] - cell["baseline"]) / cell["n"]
    return {"paired_items": len(shared), "transitions": trans,
            "mcnemar_p": mcnemar_exact(trans["pass_fail"], trans["fail_pass"]),
            "regressions": regressions, "improvements": improvements,
            "categories": dict(sorted(per_cat.items())),
            "baseline_accuracy": _rate(sum(base[i] for i in shared), len(shared)),
            "candidate_accuracy": _rate(sum(cand[i] for i in shared), len(shared))}


# --------------------------------------------------------------- rendering

def _fmt(rate: dict) -> str:
    if rate["rate"] is None:
        return f"n/a (0/{rate['n']})"
    lo, hi = rate["ci95"]
    return f"{rate['rate']:.1%} ({rate['k']}/{rate['n']}, 95% CI {lo:.1%}-{hi:.1%})"


def to_markdown(report: dict, comparison: dict | None = None, title: str = "Golden evaluation") -> str:
    out = [f"# {title}", "",
           f"- items: {report['total']}  (eligible {report['eligible']}, "
           f"pending_formula {report['pending_formula']}, unscorable {report['unscorable']})",
           f"- eligible accuracy: {_fmt(report['eligible_accuracy'])}",
           f"- total coverage (passes / all items): {_fmt(report['total_coverage'])}",
           f"- wrong-answer rate (completed, contract fail): {_fmt(report['wrong_answer_rate'])}",
           f"- failure rate (no completed answer): {_fmt(report['failure_rate'])}",
           f"- states: {report['states']}  (unmatched runs: {report['unmatched_runs']})", "",
           "## By category", "", "| category | items | eligible | passes | accuracy | coverage |",
           "|---|---|---|---|---|---|"]
    for name, b in report["categories"].items():
        acc = b["eligible_accuracy"]["rate"]
        out.append(f"| {name} | {b['total']} | {b['eligible']} | {b['passes']} | "
                   f"{'n/a' if acc is None else f'{acc:.0%}'} | {b['total_coverage']['rate']:.0%} |")
    if comparison:
        t = comparison["transitions"]
        out += ["", "## Paired comparison", "",
                f"- paired items: {comparison['paired_items']}",
                f"- baseline accuracy: {_fmt(comparison['baseline_accuracy'])}",
                f"- candidate accuracy: {_fmt(comparison['candidate_accuracy'])}",
                f"- transitions: pass->pass {t['pass_pass']}, pass->fail {t['pass_fail']}, "
                f"fail->pass {t['fail_pass']}, fail->fail {t['fail_fail']}",
                f"- McNemar exact p = {comparison['mcnemar_p']:.4f}",
                f"- regressions: {', '.join(comparison['regressions']) or 'none'}",
                f"- improvements: {', '.join(comparison['improvements']) or 'none'}", "",
                "| category | n | baseline | candidate | delta |", "|---|---|---|---|---|"]
        for name, c in comparison["categories"].items():
            out.append(f"| {name} | {c['n']} | {c['baseline']} | {c['candidate']} | {c['delta']:+.0%} |")
    return "\n".join(out) + "\n"


def write_report(path_stem: str, report: dict, comparison: dict | None = None) -> tuple[str, str]:
    """Write <stem>.report.json and <stem>.report.md; returns both paths."""
    jp, mp = f"{path_stem}.report.json", f"{path_stem}.report.md"
    with open(jp, "w") as f:
        json.dump({"report": report, "comparison": comparison}, f, indent=1, default=str)
    with open(mp, "w") as f:
        f.write(to_markdown(report, comparison))
    return jp, mp
