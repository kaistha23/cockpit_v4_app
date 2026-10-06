"""Replay recorded oMLX exchanges through the CURRENT adapter and V4 contract.

Record live traffic once (`OMLX_RECORD_DIR=... make app`), then run this after
any adapter or contract change to see, in seconds and without a model, what
share of the model's real outputs V4 would now accept:

  adapter   the adapter's own outcome (ok / LLMError)
  parse     the V4 parser's verdict on the resulting tool call
  bind      for execute_analysis, whether every SQL step binds (EXPLAIN only,
            nothing executes) against the book the SQL names

Usage:
  .venv/bin/python scripts/cockpit_v4/replay_check.py ~/omlx-record/*.jsonl
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "cockpit_v4"))
os.environ.setdefault("COCKPIT_V4_NAMESPACE", "cockpit_v4")
os.environ.setdefault("COCKPIT_AGENTIC_V3_NAMESPACE", "cockpit_v4")


class _Reply:
    def __init__(self, status: int, text: str) -> None:
        self.status_code, self.text, self.ok = status, text, 200 <= status < 300

    def json(self):
        return json.loads(self.text)


class _ReplaySession:
    """Serves a recorded response, then any recorded re-ask that followed it."""

    def __init__(self, first: dict, followups: list[dict]) -> None:
        self.queue = [first, *followups]

    def post(self, url, **kwargs):
        if not self.queue:
            return _Reply(599, '{"error": "no recorded response for this re-ask"}')
        item = self.queue.pop(0)
        return _Reply(int(item.get("status") or 200), item.get("response") or "{}")


def _tools(request: dict) -> list[dict]:
    return [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
             "input_schema": t["function"].get("parameters", {})} for t in request.get("tools") or []]


def _tool_choice(request: dict) -> dict | None:
    choice = request.get("tool_choice")
    if isinstance(choice, dict):
        return {"type": "tool", "name": choice["function"]["name"]}
    if choice == "required":
        return {"type": "any"}
    return None


def _domain(sql: str) -> str:
    return "retail" if "retail_" in sql else "corporate"


def main(paths: list[str]) -> int:
    from backend.cockpit_v4 import analytical_runtime as ar
    from backend.cockpit_v4 import contracts as c
    from backend.cockpit_v4.sqlbind import BindFailure, prove_bindable
    from backend.llm.base import LLMError
    from backend.llm.omlx_provider import OMLXProvider
    from golden_eval import classify

    carried = c.parse_intent({"query_mode": "DATA_ANALYSIS", "owner": "COCKPIT",
                              "understood_request": "replay", "response_language": "en",
                              "public_rationale": ""})
    parsers = {"execute_analysis": lambda a: c.parse_execution(a, max_steps=8, carried=carried),
               "inspect_catalog": lambda a: c.parse_catalog(a, carried=carried),
               "read_artifact": lambda a: c.parse_artifact(a, carried=carried),
               "finalize_response": lambda a: c.parse_final(a, carried=carried),
               "inspect_product_knowledge": lambda a: c.parse_product_knowledge(a, carried=carried)}

    records = [json.loads(line) for p in paths for line in open(p, encoding="utf-8") if line.strip()]
    tally: collections.Counter = collections.Counter()
    examples: dict[str, str] = {}
    i = 0
    while i < len(records):
        record = records[i]
        request = record["request"]
        i += 1
        if "response_format" in request or not request.get("tools"):
            continue                                  # re-asks are consumed with their turn
        followups = []
        while i < len(records) and "response_format" in records[i]["request"]:
            followups.append(records[i]); i += 1
        provider = OMLXProvider(model=request.get("model", ""), base_url="replay://",
                                session=_ReplaySession(record, followups))
        try:
            result = provider.converse(system="", messages=[], tools=_tools(request),
                                       tool_choice=_tool_choice(request), max_tokens=int(request.get("max_tokens") or 4096),
                                       allow_retry=False)
        except LLMError as exc:
            tally["adapter_error"] += 1; examples.setdefault("adapter_error", str(exc)); continue
        if not result.tool_calls:
            tally["prose_turn"] += 1; continue
        for call in result.tool_calls:
            parse = parsers.get(call["name"])
            if parse is None:
                tally["unknown_tool"] += 1; examples.setdefault("unknown_tool", call["name"]); continue
            try:
                parsed = parse(call["input"])
            except c.Rejection as exc:
                key = f"parse:{classify(str(exc))}"
                tally[key] += 1; examples.setdefault(key, str(exc)[:200]); continue
            tally[f"ok:{call['name']}"] += 1
            if call["name"] == "execute_analysis":
                for step in parsed.steps:
                    if step.language != "sql":
                        continue
                    try:
                        prove_bindable(step.code, ar.for_domain(_domain(step.code)).session,
                                       parameters=step.parameters)
                        tally["bind_ok"] += 1
                    except BindFailure as exc:
                        tally["bind_fail"] += 1; examples.setdefault("bind_fail", exc.message[:200])

    total = sum(v for k, v in tally.items() if not k.startswith("bind"))
    print(f"turns replayed: {total}")
    for key, value in sorted(tally.items()):
        print(f"  {key:32s} {value}")
    for key, text in examples.items():
        print(f"\ne.g. {key}: {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
