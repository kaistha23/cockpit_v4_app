#!/usr/bin/env python3
"""
Where the local model's input tokens go, and what each costs in seconds.

MEASUREMENT ONLY. No model call, no write to any database, no source file is
touched. Two halves:

  (a) STATIC  - assembles the real request for a corporate and a retail
      analytical question with the real catalogue (`context.build`,
      `finalization_system`, `provider_tools`), and sizes every component.
  (b) DYNAMIC - reads the real state DB read-only: for every provider call it
      reconstructs what the conversation held, and splits it into the
      tool-result types that fill it.

    python3 scripts/cockpit_v4/context_budget.py [--db PATH]

Writes docs/cockpit_v4/evidence/context_budget.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COCKPIT_AGENTIC_V3_NAMESPACE", "cockpit_v4")
os.environ.pop("COCKPIT_V4_LIMITS_PROFILE", None)

DEFAULT_DB = Path.home() / ".creditprobe" / "cockpit_v4_round_h_live_pass" \
    / "state" / "cockpit_v4.sqlite3"
SEC_PER_KTOK = 0.86      # measured local oMLX prompt processing
DECODE_SEC_PER_KTOK = 23.0
BYTES_PER_TOKEN = 3.6    # typical JSON/English mix
CONSERVATIVE = 2.2       # provider.py count_input fallback

QUESTIONS = {
    "corporate": ("v4-saudi-corporate-20q-v4|v4-saudi-20q-v1",
                  "What is total exposure at default by sector in the "
                  "latest quarter?"),
    "retail": ("v4-saudi-retail-20m-v5",
               "What is total exposure at default by product in the "
               "latest month?"),
}


def nbytes(obj) -> int:
    if isinstance(obj, str):
        return len(obj.encode("utf-8"))
    return len(json.dumps(obj, ensure_ascii=False,
                          default=str).encode("utf-8"))


def toks(b: float, per: float = BYTES_PER_TOKEN) -> int:
    return int(round(b / per))


def secs(tokens: float) -> float:
    return round(tokens / 1000.0 * SEC_PER_KTOK, 2)


def row(name: str, b: int) -> dict:
    t = toks(b)
    return {"component": name, "bytes": b, "tokens_est_3_6": t,
            "tokens_est_2_2": toks(b, CONSERVATIVE), "seconds": secs(t)}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


# ---------------------------------------------------------------- static --

def static_report() -> dict:
    from backend.cockpit_agentic import scope as v3_scope
    from backend.cockpit_agentic import sql as v3_sql
    from backend.cockpit_v4 import config as cfg_mod
    from backend.cockpit_v4 import context as ctx
    from backend.cockpit_v4.config import V4Config
    from backend.cockpit_v4.contracts import provider_tools
    from backend.cockpit_v4.service import _Principal, load_release

    limits = cfg_mod.ANALYTICAL_STANDARD_LIMITS
    out: dict = {}
    tmp = Path(tempfile.mkdtemp(prefix="ctx_budget_"))
    from backend.cockpit_agentic import store as v3_store

    for book, (candidates, question) in QUESTIONS.items():
        release_id = candidates.split("|")[0]
        for cand in candidates.split("|"):
            try:
                v3_store.read_manifest(cand)
                release_id = cand
                break
            except Exception:  # noqa: BLE001 - try the next published one
                continue
        try:
            cfg = V4Config(
                enabled=True, provider="anthropic",
                reasoning_model="mock-analyst", runtime_dir=tmp / "rt",
                state_database=str(tmp / "s.sqlite3"),
                release_id=release_id, api_port=8414, ui_port=5414,
                local_demo_auth=True, price_card_path=str(tmp / "p.json"),
                memory_enabled=False, memory_model="",
                default_mode="standard", heartbeat_seconds=5.0,
                lease_heartbeat_seconds=2.0, lease_stale_seconds=10.0,
                supervisor_poll_seconds=2.0, credential_present=True,
                missing=())
            catalog, _, summary = load_release(cfg)
            domain = str(summary.get("domain_id") or book)
            principal = {"tenant": "demo-tenant", "id": "u"}
            scope = v3_scope.for_principal(
                _Principal(principal), dataset_release_id=release_id)
            session = v3_sql.open_session(scope=scope, catalog=catalog)
            packet = ctx.build(
                question=question, principal=principal, scope=scope,
                catalog=catalog, limits=limits, mode="standard",
                release_summary=summary, session=session, analytical=True)
        except Exception as exc:  # noqa: BLE001
            out[book] = {"error": f"{type(exc).__name__}: {exc}"}
            continue

        blocks = packet.system_blocks
        stable = json.loads(blocks[1]["text"])
        volatile = json.loads(blocks[2]["text"])
        stable_parts = {k: nbytes(v) for k, v in stable.items()}
        volatile_parts = {k: nbytes(v) for k, v in volatile.items()}

        action_tools = provider_tools(
            withhold=("inspect_product_knowledge",), catalog=catalog,
            stage="analytical_action", max_steps=limits.steps_per_batch)
        full_tools = provider_tools(catalog=catalog,
                                    max_steps=limits.steps_per_batch)
        answer_sys = ctx.finalization_system(
            blocks, domain_id=domain, question=question)

        def tool_rows(tools):
            return [{"name": t["name"], "bytes": nbytes(t)} for t in tools]

        action_rows = [
            row("system[0] analyst.md instruction", nbytes(blocks[0]["text"])),
            row("system[1] stable facts (all)", nbytes(blocks[1]["text"])),
            row("system[2] volatile scope/budgets/readiness",
                nbytes(blocks[2]["text"])),
            row("tools (action set)", nbytes(action_tools)),
            row("first user message", nbytes(packet.first_user_message)),
        ]
        stable_rows = [row(f"  stable.{k}", v) for k, v in
                       sorted(stable_parts.items(), key=lambda kv: -kv[1])]
        volatile_rows = [row(f"  volatile.{k}", v) for k, v in
                         sorted(volatile_parts.items(),
                                key=lambda kv: -kv[1])]
        answer_blocks = []
        for i, blk in enumerate(answer_sys):
            txt = str(blk.get("text") or "")
            label = f"answer system[{i}]"
            if i >= 3 or i == 1:
                try:
                    label += " " + str(json.loads(txt).get("phase") or
                                       list(json.loads(txt))[:1])
                except Exception:  # noqa: BLE001
                    pass
            answer_blocks.append(row(label, nbytes(txt)))
        total_action = sum(r["bytes"] for r in action_rows)
        total_answer_fixed = (sum(r["bytes"] for r in answer_blocks)
                              + nbytes(full_tools)
                              + nbytes(packet.first_user_message))
        out[book] = {
            "release_id": release_id, "question": question,
            "action_turn": {
                "components": action_rows,
                "stable_block_breakdown": stable_rows,
                "volatile_block_breakdown": volatile_rows,
                "tool_schemas": tool_rows(action_tools),
                "total": row("TOTAL action turn, first call", total_action),
            },
            "answer_turn_fixed_part": {
                "note": "excludes the tool_result conversation, which is "
                        "measured dynamically",
                "system_blocks": answer_blocks,
                "tools (full set)": row("tools (full set)",
                                        nbytes(full_tools)),
                "tool_schemas": tool_rows(full_tools),
                "total": row("TOTAL answer turn fixed part",
                             total_answer_fixed),
            },
        }
    return out


# --------------------------------------------------------------- dynamic --

def _blocks(content: str):
    try:
        v = json.loads(content)
    except Exception:  # noqa: BLE001
        return content
    return v


def dynamic_report(db: Path, tpb_override: float = 0.0) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    runs = [r[0] for r in con.execute(
        "select distinct run_id from messages")]
    calls = []          # one dict per provider call
    skipped = 0
    for run in runs:
        msgs = con.execute(
            "select ordinal, role, content from messages where run_id=? "
            "order by ordinal", (run,)).fetchall()
        res = con.execute(
            "select purpose, usage from reservations where run_id=? "
            "order by created_at", (run,)).fetchall()
        asst = [m[0] for m in msgs if m[1] == "assistant"]
        # A call sees every message before the assistant turn it produced.
        # The final answer call may produce an assistant message (the
        # finalize tool_use), so counts normally match.
        if len(res) != len(asst):
            skipped += 1
            continue
        names: dict[str, str] = {}
        for ordinal, role, content in msgs:
            if role == "assistant":
                v = _blocks(content)
                if isinstance(v, list):
                    for b in v:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            names[b["id"]] = b["name"]
        for j, (purpose, usage) in enumerate(res):
            used = json.loads(usage or "{}")
            tin = used.get("input_tokens")
            if not tin:
                continue
            cut = asst[j]
            comp = defaultdict(int)
            keys = defaultdict(int)
            for ordinal, role, content in msgs:
                if ordinal >= cut:
                    break
                if ordinal == 0:
                    comp["first_user_message"] += nbytes(content)
                    continue
                v = _blocks(content)
                if role == "assistant":
                    comp["assistant_turns"] += nbytes(content)
                    continue
                if isinstance(v, list):
                    for b in v:
                        if isinstance(b, dict) and \
                                b.get("type") == "tool_result":
                            name = names.get(b.get("tool_use_id"), "unknown")
                            body = b.get("content")
                            size = nbytes(body)
                            parsed = _blocks(body) if isinstance(
                                body, str) else body
                            status = (parsed.get("status")
                                      if isinstance(parsed, dict) else "")
                            kind = f"{name}:" + (
                                "ok" if status in ("ok", "accepted",
                                                   "published") or (
                                    isinstance(parsed, dict)
                                    and "submission_id" in parsed)
                                else "rejected/other")
                            comp["tool_result:" + kind] += size
                            if isinstance(parsed, dict):
                                for k, val in parsed.items():
                                    keys[f"{name}.{k}"] += nbytes(val)
                                    if k == "steps" and isinstance(val, list):
                                        for st in val:
                                            if isinstance(st, dict):
                                                for k2, v2 in st.items():
                                                    keys[f"{name}.steps[].{k2}"] += nbytes(v2)
                        else:
                            comp["user_other"] += nbytes(b)
                else:
                    comp["user_other"] += nbytes(content)
            calls.append({"run": run, "purpose": purpose, "idx": j,
                          "input_tokens": tin, "comp": dict(comp),
                          "keys": dict(keys),
                          "msg_bytes": sum(comp.values())})

    def fit(sel):
        xs = [c["msg_bytes"] for c in sel]
        ys = [c["input_tokens"] for c in sel]
        if len(xs) < 3 or statistics.pvariance(xs) == 0:
            return 0.0, statistics.mean(ys) if ys else 0.0
        mx, my = statistics.mean(xs), statistics.mean(ys)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / \
            sum((x - mx) ** 2 for x in xs)
        return b, my - b * mx

    actions = [c for c in calls if c["purpose"] == "ANALYSIS_ACTION"]
    answers = [c for c in calls if c["purpose"] == "FINAL_ANSWER"]
    corrections = [c for c in calls if c["purpose"] == "ANSWER_CORRECTION"]
    slope, intercept = fit(actions + answers + corrections)
    # Cleanest estimate of tokens per message byte: consecutive ACTION calls
    # in one run share system+tools exactly, so the difference in input
    # tokens is the difference in conversation only.
    dt = db_ = 0.0
    by_run = defaultdict(list)
    for c in actions:
        by_run[c["run"]].append(c)
    for seq in by_run.values():
        for a, b in zip(seq, seq[1:]):
            if b["msg_bytes"] > a["msg_bytes"]:
                dt += b["input_tokens"] - a["input_tokens"]
                db_ += b["msg_bytes"] - a["msg_bytes"]
    delta_slope = dt / db_ if db_ else 0.0
    slope_a, intercept_a = fit(actions)
    slope_f, intercept_f = fit(answers)
    # Tokens/byte on the wire: use the pooled slope, bounded to sane values.
    tpb = tpb_override or (delta_slope if 1 / 6 < delta_slope < 1 / 1.5
                           else 1 / BYTES_PER_TOKEN)

    def summarise(sel, label):
        if not sel:
            return {"calls": 0}
        shares: dict[str, list[float]] = defaultdict(list)
        toks_by: dict[str, list[float]] = defaultdict(list)
        all_kinds = sorted({k for c in sel for k in c["comp"]})
        tr_total = []
        fixed_tokens = []
        for c in sel:
            tin = c["input_tokens"]
            msg_tok = c["msg_bytes"] * tpb
            fixed = max(tin - msg_tok, 0)
            fixed_tokens.append(fixed)
            trb = sum(v for k, v in c["comp"].items()
                      if k.startswith("tool_result"))
            tr_total.append(trb * tpb / tin)
            shares["fixed (system+tools)"].append(fixed / tin)
            toks_by["fixed (system+tools)"].append(fixed)
            for k in all_kinds:
                t = c["comp"].get(k, 0) * tpb
                shares[k].append(t / tin)
                toks_by[k].append(t)
        table = []
        for k in shares:
            table.append({
                "component": k,
                "share_p50": round(pct(shares[k], .5), 3),
                "share_p90": round(pct(shares[k], .9), 3),
                "tokens_p50": int(pct(toks_by[k], .5)),
                "tokens_p90": int(pct(toks_by[k], .9)),
                "tokens_mean": int(statistics.mean(toks_by[k])),
                "seconds_p50": secs(pct(toks_by[k], .5)),
                "seconds_p90": secs(pct(toks_by[k], .9)),
                "seconds_mean": secs(statistics.mean(toks_by[k])),
            })
        table.sort(key=lambda r: -r["tokens_mean"])
        # biggest single contributors by result key
        agg: dict[str, list[float]] = defaultdict(list)
        for c in sel:
            for k in set(c["keys"]):
                agg[k].append(c["keys"][k] * tpb)
        # keys inside a result, counted once per call (largest accumulation)
        contributors = sorted(
            ({"result_key": k,
              "tokens_p50": int(pct(v, .5)), "tokens_p90": int(pct(v, .9)),
              "tokens_mean_over_all_calls":
                  int(sum(v) / len(sel)),
              "calls_present": len(v)}
             for k, v in agg.items()),
            key=lambda r: -r["tokens_mean_over_all_calls"])[:15]
        ins = [c["input_tokens"] for c in sel]
        return {
            "calls": len(sel),
            "input_tokens": {"mean": int(statistics.mean(ins)),
                             "p50": int(pct(ins, .5)),
                             "p90": int(pct(ins, .9)), "max": max(ins)},
            "input_seconds": {"mean": secs(statistics.mean(ins)),
                              "p50": secs(pct(ins, .5)),
                              "p90": secs(pct(ins, .9)),
                              "max": secs(max(ins))},
            "tool_result_share": {"p50": round(pct(tr_total, .5), 3),
                                  "p90": round(pct(tr_total, .9), 3)},
            "fixed_tokens": {"p50": int(pct(fixed_tokens, .5)),
                             "p90": int(pct(fixed_tokens, .9))},
            "components": table,
            "biggest_result_key_contributors": contributors,
        }

    # How input grows across action turns within a run.
    growth = defaultdict(list)
    for c in actions:
        growth[c["idx"]].append(c["input_tokens"])
    return {
        "runs_in_db": len(runs),
        "runs_skipped_call_count_mismatch": skipped,
        "calls_analysed": len(calls),
        "calibration": {
            "tokens_per_byte_consecutive_action_deltas":
                round(delta_slope, 4),
            "bytes_per_token_consecutive_action_deltas":
                round(1 / delta_slope, 2) if delta_slope else None,
            "tokens_per_byte_regression_all_calls_unreliable":
                round(slope, 4),
            "used_bytes_per_token": round(1 / tpb, 2),
            "intercept_action_calls_tokens": int(intercept_a),
            "intercept_answer_calls_tokens": int(intercept_f),
            "slope_action": round(slope_a, 4),
            "slope_answer": round(slope_f, 4),
            "note": "intercept = system+tools tokens the conversation "
                    "bytes do not explain; slope = tokens per message byte",
        },
        "action_turns": summarise(actions, "action"),
        "answer_turns": summarise(answers, "answer"),
        "correction_turns": summarise(corrections, "correction"),
        "action_input_growth_by_call_index": {
            str(k): {"n": len(v), "mean_tokens": int(statistics.mean(v))}
            for k, v in sorted(growth.items())},
    }


def recommendations(stat: dict, dyn: dict, tpb: float) -> list[dict]:
    """Ranked by seconds saved on an ANSWER turn. Not implemented here."""
    corp = stat.get("corporate", {})
    ans = corp.get("answer_turn_fixed_part", {})
    sysb = {r["component"]: r for r in ans.get("system_blocks", [])}
    tools = {t["name"]: t["bytes"] for t in ans.get("tool_schemas", [])}
    per = tpb or 1 / BYTES_PER_TOKEN

    def s_of(nbytes_: float) -> float:
        return secs(nbytes_ * per)

    instr = sysb.get("answer system[0]", {}).get("bytes", 0)
    fin = tools.get("finalize_response", 0)
    other_tools = sum(v for k, v in tools.items()
                      if k not in ("finalize_response",
                                   "inspect_product_knowledge"))
    comps = {c["component"]: c for c in
             dyn.get("answer_turns", {}).get("components", [])}
    keys = {c["result_key"]: c for c in
            dyn.get("answer_turns", {}).get(
                "biggest_result_key_contributors", [])}
    ok = comps.get("tool_result:execute_analysis:ok", {})
    prev = keys.get("execute_analysis.steps[].preview", {})
    kit = keys.get("execute_analysis.steps[].answer_kit", {})
    cite = keys.get("execute_analysis.steps[].how_to_cite_these_numbers", {})
    recs = [
        {"rank": 0, "component": "analyst.md instruction (answer turn)",
         "bytes": instr, "seconds_per_answer_turn": s_of(instr),
         "what": "16 KB of instruction written to choose what to run is "
                 "carried unchanged on the turn that only writes. An "
                 "answer-turn variant of the instruction (writing rules, "
                 "citation rules, scope) could drop the action-planning "
                 "sections.",
         "risk": "MEDIUM. The instruction carries governance rules the "
                 "answer must still obey (assumptions, entity scope, "
                 "no invented numbers). Needs the 96-question corpus "
                 "re-scored; cut sections, never rewrite wording."},
        {"rank": 0, "component": "finalize_response tool schema "
                                 "(answer turn, full set)",
         "bytes": fin, "seconds_per_answer_turn": s_of(fin),
         "what": "13 KB schema vs 2.7 KB on the action turn. Largest "
                 "single tool; includes presentation/chart and "
                 "numeric-claim contracts that overlap the ANSWER and "
                 "PRESENTATION system blocks (another 5 KB).",
         "risk": "MEDIUM-HIGH. The schema is what the provider validates "
                 "against and what forces structured claims; removing "
                 "fields changes the contract. Safe part: de-duplicate "
                 "prose in descriptions that the system blocks already "
                 "state."},
        {"rank": 0, "component": "execute_analysis OK result packet "
                                 "(steps: preview rows, answer_kit, "
                                 "how_to_cite_these_numbers)",
         "mean_tokens": ok.get("tokens_mean"),
         "seconds_per_answer_turn": ok.get("seconds_mean"),
         "seconds_p90": ok.get("seconds_p90"),
         "preview_mean_tokens": prev.get("tokens_mean_over_all_calls"),
         "answer_kit_mean_tokens": kit.get("tokens_mean_over_all_calls"),
         "how_to_cite_mean_tokens": cite.get("tokens_mean_over_all_calls"),
         "what": "Mean cost is modest but it is the tail: p90 turns carry "
                 "8k+ tokens, almost all preview rows. Cap rows sent to "
                 "the model (top N plus totals; full rows stay in the "
                 "artifact for the verifier) and send answer_kit and "
                 "citation guide once per result, not once per step.",
         "risk": "HIGH. The answer cites numbers from preview rows and the "
                 "claim verifier binds to row ids; cutting rows or ids can "
                 "make correct claims unverifiable. Only trim columns the "
                 "plan did not select and rows beyond what the narrative "
                 "can name."},
        {"rank": 0, "component": "tool schemas the answer turn cannot "
                                 "usefully call (inspect_catalog, "
                                 "execute_analysis, read_artifact)",
         "bytes": other_tools, "seconds_per_answer_turn": s_of(other_tools),
         "what": "Offered on the answer turn although it should only call "
                 "finalize_response. Withholding them (as "
                 "inspect_product_knowledge already is) saves their "
                 "schemas.",
         "risk": "MEDIUM. A corrective execute_analysis or read_artifact "
                 "on the answer turn becomes impossible; today's "
                 "ANSWER_CORRECTION path may rely on them."},
    ]
    recs.sort(key=lambda r: -(r.get("seconds_per_answer_turn") or 0))
    for i, r in enumerate(recs, 1):
        r["rank"] = i
    return recs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    stat = static_report()
    # Calibrate bytes->tokens on the one thing both halves see: the first
    # action call of a run carries only system+tools+the question, which the
    # static half sizes in bytes and the DB records in tokens.
    tpb = 0.0
    corp = stat.get("corporate", {})
    if "action_turn" in corp:
        con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        first = [json.loads(u).get("input_tokens") for (u,) in con.execute(
            "select usage from reservations where purpose='ANALYSIS_ACTION' "
            "order by created_at")]
        first = [x for x in first if x]
        if first:
            tpb = statistics.median(first[:40]) / \
                corp["action_turn"]["total"]["bytes"]
    dyn = dynamic_report(Path(args.db), tpb)
    if tpb:
        for book in stat.values():
            if "action_turn" not in book:
                continue
            for sec in ("action_turn", "answer_turn_fixed_part"):
                grp = book[sec]
                rows = list(grp.get("components", [])) + list(
                    grp.get("system_blocks", [])) + list(
                    grp.get("stable_block_breakdown", [])) + list(
                    grp.get("volatile_block_breakdown", [])) + [grp["total"]]
                if "tools (full set)" in grp:
                    rows.append(grp["tools (full set)"])
                for r in rows:
                    r["tokens_calibrated"] = int(round(r["bytes"] * tpb))
                    r["seconds_calibrated"] = secs(r["bytes"] * tpb)
        stat["_calibration_note"] = (
            f"tokens_calibrated = bytes x {tpb:.4f} tokens/byte "
            f"({1 / tpb:.2f} bytes/token): median recorded input_tokens of "
            f"the first 40 ANALYSIS_ACTION calls over the static corporate "
            f"action-turn bytes. The live DB may predate current code, so "
            f"treat it as +-15%.")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "evidence_class": "REAL DATABASE · NO MODEL",
        "label": "context budget: where input tokens go and what each "
                 "costs on the local model",
        "constants": {
            "prompt_seconds_per_1k_input_tokens": SEC_PER_KTOK,
            "decode_seconds_per_1k_output_tokens": DECODE_SEC_PER_KTOK,
            "bytes_per_token_typical": BYTES_PER_TOKEN,
            "bytes_per_token_provider_conservative": CONSERVATIVE,
        },
        "static": stat,
        "dynamic": dyn,
        "recommendations_not_implemented": recommendations(stat, dyn, tpb),
        "what_this_proves": [
            "How many bytes and estimated tokens each component of the "
            "assembled request occupies for a corporate and a retail "
            "analytical question, built by the real context.build with the "
            "real catalogue.",
            "How the real recorded conversations grew across calls and "
            "which tool-result types filled them, with p50/p90 shares.",
            "What each component costs in prompt-processing seconds at the "
            "measured 0.86 s per 1k tokens.",
        ],
        "what_this_does_not_prove": [
            "That trimming any component preserves answer quality: no "
            "model ran and no golden question was re-scored.",
            "Exact token counts: tokens are estimated from bytes, calibrated "
            "against the recorded input_tokens by regression; the tokenizer "
            "itself was not run.",
            "That recorded conversations match today's code: the DB holds "
            "runs from earlier builds; static numbers are the current code.",
            "Prompt-cache effects: oMLX prefix-cache reuse would lower the "
            "real cost of the stable prefix; seconds here are the "
            "uncached upper bound.",
        ],
    }
    out = ROOT / "docs" / "cockpit_v4" / "evidence" / "context_budget.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"written to {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
