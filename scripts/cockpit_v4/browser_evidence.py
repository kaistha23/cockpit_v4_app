#!/usr/bin/env python3
"""
Bring up the V4 stack and run the real browser suite against it.

Starts the stub V4 API and the real Next.js UI on free ports, waits for both
using the checks that match what each actually serves, runs Playwright, writes
the evidence, and stops only the processes it started.

Usage:
    python3 scripts/cockpit_v4/browser_evidence.py
"""

from __future__ import annotations

import argparse
import json
import os
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


def stop(process: subprocess.Popen | None, name: str, *,
         pid: int = 0) -> None:
    """Stop a process this script started, or one identified by pid."""
    if process is not None:
        if process.poll() is not None:
            return
        pid = process.pid
    if not pid:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        if process is not None:
            process.wait(timeout=10)
        else:
            _wait_gone(pid, seconds=10)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
    print(f"  stopped {name}")


def _wait_gone(pid: int, *, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def stale_next_dev(frontend: Path) -> int:
    """The pid of a `next dev` already running in this working tree, or 0.

    Next holds its own lock rather than only the port, so a dev server
    orphaned by an interrupted run makes the NEXT one exit with "Another
    next dev server is already running" while `pick_port` still reports the
    port free. The suite then reports a product failure that is a leftover
    process.

    Identified by working directory, not by name: a `next dev` whose cwd is
    this repository's frontend has no owner but us.
    """
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid,args"], capture_output=True, text=True,
            check=False).stdout
    except Exception:  # noqa: BLE001
        return 0
    mine = os.getpid()
    for line in listing.splitlines()[1:]:
        pid_text, _, command = line.strip().partition(" ")
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == mine or "next" not in command:
            continue
        if "dev" not in command:
            continue
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if Path(cwd) == frontend.resolve():
            return pid
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=8471)
    parser.add_argument("--ui-port", type=int, default=5471)
    parser.add_argument("--keep-up", action="store_true",
                        help="leave both running after the suite")
    args = parser.parse_args()

    api_port, notes = pick_port(args.api_port)
    ui_port, ui_notes = pick_port(args.ui_port)
    for note in notes + ui_notes:
        print(warn(note))
    if not api_port or not ui_port:
        print(bad("no free ports"))
        return 1

    logs = Path("/tmp/cockpit_v4_browser_logs")
    logs.mkdir(parents=True, exist_ok=True)
    api_proc = ui_proc = None

    try:
        heading("V4 stub API")
        api_log = (logs / "api.log").open("w", encoding="utf-8")
        api_proc = subprocess.Popen(
            [sys.executable, "scripts/cockpit_v4/stub_server.py",
             "--port", str(api_port), "--ui-port", str(ui_port),
             # Its OWN store. Two stacks sharing one state database means two
             # workers claiming each other's runs.
             "--runtime-dir", f"/tmp/cockpit_v4_browser_{api_port}"],
            cwd=str(ROOT),
            env={**os.environ, "COCKPIT_AGENTIC_V3_NAMESPACE": "cockpit_v4",
                 "PYTHONPATH": str(ROOT)},
            stdout=api_log, stderr=subprocess.STDOUT, start_new_session=True)
        healthy, detail = wait_for_json_health(
            f"http://127.0.0.1:{api_port}/health", timeout_seconds=90)
        if not healthy:
            print(bad(f"the stub API did not start: {detail}"))
            print((logs / "api.log").read_text()[-2000:])
            return 1
        print(ok(f"stub API on http://127.0.0.1:{api_port}"))

        heading("V4 UI")
        ui_log = (logs / "ui.log").open("w", encoding="utf-8")
        # Exactly the environment the launcher gives the real UI process.
        ui_env = ui_environment({**os.environ}, api_port=api_port,
                                ui_port=ui_port)
        def start_ui():
            return subprocess.Popen(
                ["npm", "run", "dev", "--", "--port", str(ui_port),
                 "--hostname", "127.0.0.1"],
                cwd=str(ROOT / "frontend"), env=ui_env,
                stdout=ui_log, stderr=subprocess.STDOUT,
                start_new_session=True)

        ui_proc = start_ui()
        ready, detail = wait_for_ui_ready(f"http://127.0.0.1:{ui_port}/",
                                          timeout_seconds=240)
        if not ready:
            # A `next dev` ORPHANED by an earlier run that was interrupted.
            #
            # Next refuses to start on its own lockfile rather than on the
            # port, so `pick_port` sees the port as free, hands it over, and
            # the new server exits with "Another next dev server is already
            # running". The suite then reports a product failure that is a
            # leftover process -- which is exactly how a previous round spent
            # a day analysing a release no thread reads any more.
            #
            # A `next dev` running in THIS working tree has no owner but us:
            # nothing else in this container runs one here. So it is stopped
            # by name and the UI is started once more.
            stale = stale_next_dev(ROOT / "frontend")
            if stale:
                print(warn(f"a leftover next dev (pid {stale}) held this "
                           f"working tree; stopping it and retrying once"))
                stop(None, "leftover next dev", pid=stale)
                ui_log.close()
                ui_log = (logs / "ui.log").open("w", encoding="utf-8")
                ui_proc = start_ui()
                ready, detail = wait_for_ui_ready(
                    f"http://127.0.0.1:{ui_port}/", timeout_seconds=240)
        if not ready:
            print(bad(f"the UI did not become ready: {detail}"))
            print((logs / "ui.log").read_text()[-3000:])
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
        print(ok(f"UI on http://127.0.0.1:{ui_port} ({detail})"))
        print(f"  NEXT_PUBLIC_COCKPIT_V4_API="
              f"{ui_env['NEXT_PUBLIC_COCKPIT_V4_API']}")
        print(f"  NEXT_PUBLIC_API_URL={ui_env['NEXT_PUBLIC_API_URL']}")

        heading("Browser suite")
        # A THEMED RUN KEEPS ITS OWN EVIDENCE.
        #
        # The dark-mode pass writes the same twenty filenames as the
        # default one, so running it second replaced the light screenshots
        # and the committed set silently became all-Midnight -- which
        # proves the dark theme renders and loses the baseline it is
        # supposed to be compared against. A reviewer wants both, side by
        # side, and they are only side by side if they are in two places.
        theme = os.environ.get("V4_THEME", "").strip()
        root = ROOT / "docs" / "cockpit_v4" / "evidence"
        evidence = (root / theme / "browser.json") if theme \
            else (root / "browser.json")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        suite = subprocess.run(
            ["node", "tests/cockpit_v4/browser/cockpit_v4.browser.mjs"],
            cwd=str(ROOT),
            env={**os.environ,
                 "V4_UI_URL": f"http://127.0.0.1:{ui_port}",
                 "V4_API_URL": f"http://127.0.0.1:{api_port}",
                 "V4_CHROME": CHROME,
                 # The browser suite asserts the SELECTED release's currency.
                 "V4_RELEASE": os.environ.get("COCKPIT_V4_TEST_RELEASE",
                                              "v4-saudi-20q-v1"),
                 "V4_BROWSER_EVIDENCE": str(evidence),
                 "V4_THREAD_SHOTS": str(evidence.parent),
                 "V4_BROWSER_SCREENSHOT": str(
                     evidence.parent / "cockpit_v4_answer.png"),
                 # The restored landing page, captured for human review
                 # against the reference the requirement was written from.
                 "V4_LANDING_SCREENSHOT": str(
                     evidence.parent / "cockpit_v4_landing.png")})
        if args.keep_up:
            print(f"\n  left running: UI http://127.0.0.1:{ui_port} · "
                  f"API http://127.0.0.1:{api_port}")
            api_proc = ui_proc = None
        return suite.returncode
    finally:
        stop(ui_proc, "UI")
        stop(api_proc, "stub API")


if __name__ == "__main__":
    raise SystemExit(main())
