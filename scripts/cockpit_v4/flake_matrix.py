#!/usr/bin/env python3
"""
Run the critical browser flows five times each and record what happened.

Why this exists
---------------
A suite that passes once tells you it can pass. The failures that reach a
reader are the ones that happen one time in five: a race between a fetch and
a render, an ordering that usually holds, a cache that is usually warm. Those
do not show up in a single green run and they are exactly what a reader
meets on a Monday morning.

So eleven flows, five runs each, and the RESULT is the artifact -- including
the runs that failed. A matrix that only records successes is a matrix that
cannot say anything about flake.

The stack is brought up ONCE and every run goes to the same server, because
restarting between runs would hide any state that leaks between them, which
is half of what this is looking for.

Usage:
    python3 scripts/cockpit_v4/flake_matrix.py [--runs 5]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.cockpit_v4._common import (  # noqa: E402
    bad, heading, ok, pick_port, wait_for_json_health, wait_for_ui_ready,
    warm_routes, warn)
from scripts.cockpit_v4.start import ui_environment  # noqa: E402

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

#: The eleven flows a reader actually performs. Each is a regular expression
#: matched against the browser suite's own test names, so this list cannot
#: drift into testing something the suite does not have.
FLOWS: tuple[tuple[str, str], ...] = (
    ("ask-and-answer", r"asking from home opens a conversation"),
    ("process-panel", r"the process panel appears and consumes"),
    ("follow-up", r"a follow-up appends and does not replace"),
    ("refresh-restore", r"a refresh restores the transcript"),
    ("continue-where-left-off", r"Continue where you left off"),
    ("dashboard", r"segments requiring attention loads"),
    ("drawer", r"clicking a segment card opens the right-side drawer"),
    ("investigate", r"Investigate Further opens a seeded V4 thread"),
    ("domain-switch", r"switching the book asks the server again"),
    ("domain-numbers", r"each book shows its own release, currency"),
    ("back-to-cockpit", r"the way back to Cockpit is at the top left"),
    # §31/§34. Both are rendering races by nature -- the chips depend on a
    # transcript fetch, and the strip's geometry on a layout pass -- so they
    # belong in the flow that runs things five times rather than once.
    ("opening-questions", r"a seeded thread opens on three to five"),
    ("ask-next-overlap", r"the Ask next strip never overlaps"),
)


def stop(process, name: str) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
    print(f"  stopped {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--api-port", type=int, default=8473)
    parser.add_argument("--ui-port", type=int, default=5473)
    args = parser.parse_args()

    api_port, notes = pick_port(args.api_port)
    ui_port, ui_notes = pick_port(args.ui_port)
    for note in notes + ui_notes:
        print(warn(note))
    if not api_port or not ui_port:
        print(bad("no free ports"))
        return 1

    logs = Path("/tmp/cockpit_v4_flake_logs")
    logs.mkdir(parents=True, exist_ok=True)
    api_proc = ui_proc = None
    started = time.time()

    try:
        heading("Stack")
        api_proc = subprocess.Popen(
            [sys.executable, "scripts/cockpit_v4/stub_server.py",
             "--port", str(api_port), "--ui-port", str(ui_port),
             # Its OWN store. Two stacks sharing one state database means two
             # workers claiming each other's runs.
             "--runtime-dir", f"/tmp/cockpit_v4_browser_{api_port}"],
            cwd=str(ROOT),
            env={**os.environ, "COCKPIT_AGENTIC_V3_NAMESPACE": "cockpit_v4",
                 "PYTHONPATH": str(ROOT)},
            stdout=(logs / "api.log").open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT, start_new_session=True)
        healthy, detail = wait_for_json_health(
            f"http://127.0.0.1:{api_port}/health", timeout_seconds=90)
        if not healthy:
            print(bad(f"the stub API did not start: {detail}"))
            return 1
        print(ok(f"stub API on http://127.0.0.1:{api_port}"))

        ui_env = ui_environment({**os.environ}, api_port=api_port,
                                ui_port=ui_port)
        ui_proc = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(ui_port),
             "--hostname", "127.0.0.1"],
            cwd=str(ROOT / "frontend"), env=ui_env,
            stdout=(logs / "ui.log").open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT, start_new_session=True)
        ready, detail = wait_for_ui_ready(f"http://127.0.0.1:{ui_port}/",
                                          timeout_seconds=240)
        if not ready:
            print(bad(f"the UI did not become ready: {detail}"))
            return 1
        # Compile every route the suite navigates to BEFORE anything is
        # timed. A sixty-second assertion waiting for an answer on a page the
        # bundler has not built yet reports a product defect that is a cold
        # dev server.
        for note in warm_routes(f"http://127.0.0.1:{ui_port}",
                                # EVERY route a test navigates to.
                                #
                                # `next dev` compiles on first request, and
                                # a page left off this list pays for its own
                                # bundler on the clock -- which is a browser
                                # assertion timing out and reporting a
                                # product defect that is a cold compile. The
                                # governance and saved-analysis routes are
                                # new, and the thread page's bundle grew
                                # with them.
                                ("/cockpit/thread/warmup", "/cockpit/data",
                                 "/cockpit/trace/warmup",
                                 "/cockpit/saved/warmup")):
            print(f"  warmed {note}")
        print(ok(f"UI on http://127.0.0.1:{ui_port}"))

        heading(f"{len(FLOWS)} flows x {args.runs} runs")
        matrix: list[dict] = []
        for name, pattern in FLOWS:
            outcomes: list[dict] = []
            for attempt in range(1, args.runs + 1):
                began = time.monotonic()
                run = subprocess.run(
                    ["node", "tests/cockpit_v4/browser/cockpit_v4.browser.mjs"],
                    cwd=str(ROOT), capture_output=True, text=True,
                    env={**os.environ,
                         "V4_UI_URL": f"http://127.0.0.1:{ui_port}",
                         "V4_API_URL": f"http://127.0.0.1:{api_port}",
                         "V4_CHROME": CHROME,
                         "V4_BROWSER_ONLY": pattern,
                         "V4_RELEASE": os.environ.get(
                             "COCKPIT_V4_TEST_RELEASE", "v4-saudi-20q-v1")})
                seconds = round(time.monotonic() - began, 2)
                passed = run.returncode == 0 and " FAIL " not in run.stdout
                detail = ""
                if not passed:
                    found = re.search(r"FAIL .*(?:\n\s+.*)*",
                                      run.stdout or "")
                    detail = ((found.group(0) if found else
                               (run.stderr or run.stdout))[:600]).strip()
                outcomes.append({"attempt": attempt, "passed": passed,
                                 "seconds": seconds, "detail": detail})
            passes = sum(1 for o in outcomes if o["passed"])
            times = [o["seconds"] for o in outcomes]
            matrix.append({
                "flow": name, "pattern": pattern, "runs": args.runs,
                "passed": passes, "failed": args.runs - passes,
                "stable": passes == args.runs,
                "seconds_min": min(times), "seconds_max": max(times),
                "seconds_median": sorted(times)[len(times) // 2],
                "attempts": outcomes,
            })
            mark = ok if passes == args.runs else bad
            print(mark(f"{name:<24} {passes}/{args.runs}  "
                       f"{min(times):.1f}-{max(times):.1f}s"))

        flaky = [m["flow"] for m in matrix if not m["stable"]]
        summary = {
            "label": "REAL BROWSER · REAL UI · REAL BACKEND · MOCK ANALYST",
            "note": ("Each flow run independently against one long-lived "
                     "stack, so state leaking between runs is visible rather "
                     "than reset away. Failures are recorded, not retried."),
            "ui": f"http://127.0.0.1:{ui_port}",
            "runs_per_flow": args.runs,
            "flows": len(FLOWS),
            "total_runs": len(FLOWS) * args.runs,
            "stable_flows": sum(1 for m in matrix if m["stable"]),
            "flaky_flows": flaky,
            "elapsed_seconds": round(time.time() - started, 1),
            "matrix": matrix,
        }
        out = ROOT / "docs" / "cockpit_v4" / "evidence" / "flake_matrix.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2) + "\n",
                       encoding="utf-8")
        print(f"\n{summary['stable_flows']}/{len(FLOWS)} flows stable over "
              f"{summary['total_runs']} runs")
        if flaky:
            print(bad(f"flaky: {', '.join(flaky)}"))
        print(f"evidence written to {out}")
        return 0 if not flaky else 1
    finally:
        stop(ui_proc, "UI")
        stop(api_proc, "stub API")


if __name__ == "__main__":
    raise SystemExit(main())
