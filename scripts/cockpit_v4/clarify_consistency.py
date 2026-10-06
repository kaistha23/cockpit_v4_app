"""How consistently does the live analyst ask, per question?

The clarification gate (`backend/cockpit_v4/clarify_gate.py`) exists because
the same question was a clarification on some runs and an answer on others.
This measures that, so the gate can be judged on numbers and not on a feeling:
for each case in `tests/evals/cockpit_v4_clarify/cases.json` it runs the
question N times and reports

  * ask rate     -- share of runs that ended as a clarification;
  * flip rate    -- 0 when every run agreed, otherwise the minority share
                    (0.5 is a coin toss, which is what the gate removes);
  * distinct clarification texts -- how many different questions were put;
  * would-have-refused / refused -- gate events per question (shadow records
    the former, enforce does the latter; both live only in run events).

Run it once with COCKPIT_V4_CLARIFY_GATE=shadow and once with =enforce and
compare. It reuses `live_uat.py`'s runtime, store, spend guard and
served-model check, so the same caps and stop conditions apply.

    python3 scripts/cockpit_v4/clarify_consistency.py --dry-run
    python3 scripts/cockpit_v4/clarify_consistency.py --live --runs 5 --cap 10

`--dry-run` makes no provider call: it prints, per case, what the gate's own
grounds say against the real readiness, which is what the offline test pins.
`--live` spends money against the approved model and refuses to start on a
non-empty live ledger (see `live_uat.open_store`).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_uat as uat  # noqa: E402  (also puts the repo and tests on sys.path)

ROOT = uat.ROOT
CASES = ROOT / "tests" / "evals" / "cockpit_v4_clarify" / "cases.json"


def load_cases(only: str = "") -> list[dict[str, Any]]:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    wanted = [x.strip() for x in only.split(",") if x.strip()]
    if wanted:
        known = {c["id"] for c in cases}
        unknown = [x for x in wanted if x not in known]
        if unknown:
            raise SystemExit(f"unknown case id(s): {', '.join(unknown)}")
        cases = [c for c in cases if c["id"] in wanted]
    return cases


def server_view(case: dict[str, Any]) -> dict[str, Any]:
    """What the gate would say about this question, with no model involved."""
    from backend.cockpit_v4 import analytical_runtime as arun
    from backend.cockpit_v4 import clarify_gate

    book = arun.for_domain(case["domain"])
    from backend.cockpit_v4 import semantics as sem

    ready = sem.readiness(book.catalog, case["question"])
    found = clarify_gate.grounds(
        question=case["question"], readiness=ready, value_resolution={},
        envelope=None, final=None)
    return {"grounds": found,
            "gate_says": "must_ask" if found else "must_run"}


def gate_events(store, run_id: str) -> dict[str, int]:
    """What the gate did on this run, read from the run's own events.

    Shadow mode refuses nothing, so its only trace is a `CONTEXT_READY` event
    with `operation="clarify_gate"` ("would have refused"); enforce leaves a
    rejected `INTENT_VALIDATED` with the same operation ("refused").
    """
    would, refused = 0, 0
    for event in store.events_since(run_id):
        if getattr(event, "operation", "") != "clarify_gate":
            continue
        if getattr(event, "status", "") == "rejected":
            refused += 1
        else:
            would += 1
    return {"would_have_refused": would, "refused": refused}


def summarise(case: dict[str, Any], observed: list[dict[str, Any]]
              ) -> dict[str, Any]:
    asked = [o for o in observed if o["asked"]]
    n = len(observed)
    rate = len(asked) / n if n else 0.0
    texts = Counter(o["clarification_question"] for o in asked)
    return {"id": case["id"], "domain": case["domain"],
            "question": case["question"], "expect": case["expect"],
            "runs": n, "ask_rate": round(rate, 3),
            "flip_rate": round(min(rate, 1 - rate), 3),
            "would_have_refused": sum(o.get("would_have_refused", 0)
                                      for o in observed),
            "refused": sum(o.get("refused", 0) for o in observed),
            "distinct_clarification_texts": len(texts),
            "clarification_texts": dict(texts),
            "states": dict(Counter(o["state"] for o in observed))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--runs", type=int, default=3,
                        help="runs per question (live)")
    parser.add_argument("--cap", type=float, default=10.0)
    parser.add_argument("--stop-at", type=float, default=8.0)
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--only", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--state-db", default="")
    args = parser.parse_args()
    if args.live == args.dry_run:
        print("choose exactly one of --live and --dry-run")
        return 2

    cases = load_cases(args.only)
    if args.dry_run:
        for case in cases:
            view = server_view(case)
            mark = "ok " if view["gate_says"] == case["expect"] else "DIFF"
            print(f"{mark} {case['id']:<32} label={case['expect']:<8} "
                  f"gate={view['gate_says']:<8} {','.join(view['grounds'])}")
        return 0

    from backend.cockpit_v4 import capability as cap_mod
    from backend.cockpit_v4 import clarify_gate
    from backend.cockpit_v4 import config as config_mod
    from backend.cockpit_v4 import lake
    from backend.cockpit_v4.service import resolve_provider

    tenant_id = lake.DEFAULT_TENANT
    capability = cap_mod.load_price_card(
        uat.APPROVED_CARD, model_id=uat.APPROVED_MODEL, provider="anthropic")
    cfg = config_mod.load()
    if cfg.missing:
        print(f"NOT READY: {', '.join(cfg.missing)}")
        return 1
    provider = resolve_provider(cfg)
    capability = cap_mod.verify_live(capability, provider)
    print(f"model verified live: {capability.model_id}; "
          f"clarify gate: {clarify_gate.mode()}")

    db = uat.state_db_for(live=True, override=args.state_db)
    runtime = uat.build_runtime(True, capability, provider, state_db=db)
    try:
        store = uat.open_store(db, live=True)
    except uat.Stop as stop:
        print(f"STOP [{stop.condition}] {stop.detail}")
        return 1
    guard = uat.Guard(
        cap_usd=args.cap, stop_at_usd=args.stop_at,
        per_run_ceiling_usd=config_mod.ANALYTICAL_STANDARD_LIMITS
        .spend_ceiling_usd, max_runs=args.max_runs)

    results: list[dict[str, Any]] = []
    stopped: dict[str, Any] | None = None
    try:
        for case in cases:
            observed: list[dict[str, Any]] = []
            for i in range(1, args.runs + 1):
                guard.before_run(f"{case['id']}.{i}")
                # A fresh thread per run: runs must not see one another.
                thread_id = store.create_thread(
                    tenant_id=tenant_id, principal_id="u1",
                    domain_id=case["domain"],
                    release_id=uat.APPROVED_RELEASES[case["domain"]])
                outcome, run_id, latency_ms = uat.run_turn(
                    store, runtime, thread_id=thread_id,
                    domain_id=case["domain"], question=case["question"],
                    tenant_id=tenant_id)
                taken = uat.capture(store, run_id, tenant_id)
                uat.check_served_model(uat._served_model(taken),
                                       uat.APPROVED_MODEL)
                guard.after_run(
                    float(taken["spend"].get("committed_usd") or 0.0))
                response = outcome.response or {}
                asked = response.get("disposition") == "clarification"
                observed.append({
                    "state": str(taken["state"]), "asked": asked,
                    "clarification_question": str(
                        response.get("clarification_question") or ""),
                    **gate_events(store, run_id),
                    "latency_ms": latency_ms})
                print(f"  {case['id']}.{i} {taken['state']:<12} "
                      f"asked={asked} cum ${guard.committed_usd:.4f}")
            results.append(summarise(case, observed))
    except uat.Stop as stop:
        stopped = {"condition": stop.condition, "detail": stop.detail}
        print(f"STOP [{stop.condition}] {stop.detail}")

    summary = {
        "label": "REAL PROVIDER · REAL DATABASE · REAL WORKER",
        "model": capability.model_id, "clarify_gate": clarify_gate.mode(),
        "runs_per_question": args.runs, "runs": guard.runs,
        "cumulative_usd": round(guard.committed_usd, 6),
        "stopped": stopped,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "questions": results,
    }
    out = Path(args.out) if args.out else (
        ROOT / "docs" / "cockpit_v4" / "evidence" /
        f"clarify_consistency_{clarify_gate.mode()}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str) + "\n",
                   encoding="utf-8")
    for row in results:
        print(f"{row['id']:<32} expect={row['expect']:<8} "
              f"ask={row['ask_rate']:.2f} flip={row['flip_rate']:.2f} "
              f"texts={row['distinct_clarification_texts']} "
              f"would_refuse={row['would_have_refused']} "
              f"refused={row['refused']}")
    print(f"\n{guard.runs} runs, ${guard.committed_usd:.4f} committed; "
          f"evidence written to {out}")
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
