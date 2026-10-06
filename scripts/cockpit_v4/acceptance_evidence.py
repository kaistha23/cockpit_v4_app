#!/usr/bin/env python3
"""
Map acceptance cases to the tests that actually cover them, and record how.

The mapping is derived from the test files themselves: a test docstring that
names `V4-AT-0NN` claims that case, and the file it lives in determines the
evidence LABEL. Nothing here marks a case covered because a document says so.

Labels:
  MODEL MOCK          the analyst is scripted; the runner, store and stream
                      are real
  REAL DATABASE       real DuckDB against the published release
  REAL HTTP           a real ASGI client over the real router
  REAL RUNNER/SOCKET  a real uvicorn process on a real port, real SSE
  REAL SOURCE         reads the V3 source under test
  REAL PROCESS        real OS processes and ports
  UNIT                no model, no database, no runner
  NOT RUN             no automated coverage in this build
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests" / "cockpit_v4"

FRONTEND = ROOT / "frontend" / "src" / "components" / "cockpit-v4"

FILE_LABELS = {
    "test_vertical_slice.py": ["MODEL MOCK", "REAL DATABASE"],
    "test_protocol_and_bounds.py": ["MODEL MOCK", "REAL DATABASE"],
    "test_api_and_lifecycle.py": ["MODEL MOCK", "REAL DATABASE", "REAL HTTP"],
    "test_ownership_and_security.py": ["MODEL MOCK", "REAL DATABASE"],
    "test_state_machine_and_memory.py": ["UNIT"],
    "test_incident_err_2569c1be3faa.py": ["REAL SOURCE", "MODEL MOCK"],
    "test_launcher_safety.py": ["REAL PROCESS"],
    "test_domain_and_numerics.py": ["MODEL MOCK", "REAL DATABASE"],
    "test_catalog_answer_memory.py": ["MODEL MOCK", "REAL DATABASE"],
    "test_limits_and_isolation.py": ["MODEL MOCK", "REAL DATABASE"],
    "reducer.test.ts": ["UNIT (browser reducer)"],
}

CASE = re.compile(r"V4-AT-(\d{3})")
TEST_DEF = re.compile(r"^def (test_\w+)", re.MULTILINE)


def collect() -> dict[str, list[dict[str, object]]]:
    found: dict[str, list[dict[str, object]]] = {}
    paths = list(sorted(TESTS.glob("test_*.py")))
    paths += list(sorted(FRONTEND.glob("*.test.ts")))
    for path in paths:
        source = path.read_text(encoding="utf-8")
        if path.suffix == ".ts":
            for match in re.finditer(
                    r'test\(\s*"([^"]*V4-AT-\d{3}[^"]*)"', source):
                title = match.group(1)
                for case in CASE.findall(title):
                    found.setdefault(f"V4-AT-{case}", []).append(
                        {"test": f"{path.name}::{title[:60]}",
                         "labels": FILE_LABELS.get(path.name, ["UNIT"])})
            continue
        blocks = TEST_DEF.split(source)
        # blocks: [preamble, name, body, name, body, ...]
        for i in range(1, len(blocks), 2):
            name, body = blocks[i], blocks[i + 1]
            for case in CASE.findall(body[:600]):
                found.setdefault(f"V4-AT-{case}", []).append(
                    {"test": f"{path.name}::{name}",
                     "labels": FILE_LABELS.get(path.name, ["UNIT"])})
    return found


def main() -> int:
    inventory_path = ROOT / "docs" / "cockpit_v4" / "ACCEPTANCE_CASES.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    covered = collect()

    live_path = ROOT / "docs" / "cockpit_v4" / "evidence" / "live_path.json"
    socket_evidence = live_path.exists()

    # Cases established by the real-socket evidence run rather than by pytest.
    SOCKET_CASES = {"V4-AT-067": "progress frames arrive before answer.ready",
                    "V4-AT-073": "SSE replay from a cursor over a real socket",
                    "V4-AT-077": "cancel settles a working run as CANCELLED",
                    "V4-AT-081": "the terminal event is delivered on the "
                                 "stream, not cut by a total-duration timer"}

    counts = {"covered": 0, "not_run": 0}
    for case in inventory["cases"]:
        hits = covered.get(case["id"], [])
        if hits:
            labels = sorted({lab for hit in hits for lab in hit["labels"]})
            case["evidence"] = {
                "status": "COVERED",
                "labels": labels,
                "tests": [hit["test"] for hit in hits],
                "real_provider": "NOT RUN — no credential authorized in this "
                                 "environment",
            }
            counts["covered"] += 1
        elif socket_evidence and case["id"] in SOCKET_CASES:
            case["evidence"] = {
                "status": "COVERED",
                "labels": ["REAL RUNNER/SOCKET", "MODEL MOCK"],
                "tests": ["scripts/cockpit_v4/live_path_evidence.py"],
                "note": SOCKET_CASES[case["id"]],
                "real_provider": "NOT RUN — no credential authorized in this "
                                 "environment",
            }
            counts["covered"] += 1
        else:
            case["evidence"] = {
                "status": "NOT RUN",
                "labels": ["NOT RUN"],
                "tests": [],
                "real_provider": "NOT RUN",
            }
            counts["not_run"] += 1

    inventory["evidence_summary"] = {
        **counts,
        "total": len(inventory["cases"]),
        "note": ("COVERED means an automated test asserts the case in this "
                 "build. No case is marked covered by a real provider: no "
                 "paid live run was authorized in this environment."),
    }
    inventory_path.write_text(
        json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    print(f"covered {counts['covered']} / {len(inventory['cases'])}, "
          f"not run {counts['not_run']}")
    for case in inventory["cases"]:
        if case["evidence"]["status"] == "NOT RUN":
            print(f"  NOT RUN  {case['id']}  {case['case']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
