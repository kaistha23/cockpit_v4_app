#!/usr/bin/env python3
"""
Stop Cockpit V4 — and only Cockpit V4.

A recorded pid is not enough to stop a process. Every one of these must still
match what V4 wrote when it started it: the pid exists, its start time is
within seconds of the record, its command line is the one V4 launched, and
its working directory is the V4 worktree. A pid that fails any check is
REPORTED and left alone, because on a busy Mac that pid now belongs to
something else -- possibly the demo someone is presenting.

Termination is graceful first (SIGTERM to the process group V4 created), then
SIGKILL only to a group that ignored it. There is no `pkill`, no
`kill $(lsof -ti:PORT)`, and no pid chosen with `tail -1`.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cockpit_v4._common import (  # noqa: E402
    bad, forget, heading, ok, records, still_ours, warn)


def terminate(pid: int, *, grace_seconds: float = 8.0) -> str:
    """SIGTERM the group V4 created, then SIGKILL only if it ignored it."""
    try:
        group = os.getpgid(pid)
    except ProcessLookupError:
        return "already gone"
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        return "already gone"
    except PermissionError:
        return "not permitted (this process is not ours to stop)"

    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "stopped"
        time.sleep(0.2)
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        return "stopped"
    return "stopped after SIGKILL"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", default=os.environ.get(
        "COCKPIT_V4_RUNTIME_DIR",
        str(Path.home() / ".creditprobe" / "cockpit_v4")))
    args = parser.parse_args()
    runtime_dir = Path(args.runtime_dir).expanduser()

    heading("Cockpit V4 · stopping only what V4 started")
    owned = records(runtime_dir)
    if not owned:
        print("  nothing recorded. Nothing to stop.")
        return 0

    refused = 0
    for item in owned:
        mine, why = still_ours(item)
        if not mine:
            if why == "not running":
                print(f"  {item.name}: already stopped.")
                forget(runtime_dir, item.name)
            else:
                print(bad(f"{item.name}: {why}"))
                refused += 1
            continue
        result = terminate(item.pid)
        print(ok(f"{item.name} (pid {item.pid}, port {item.port}): {result}"))
        forget(runtime_dir, item.name)

    print()
    if refused:
        print(warn(f"{refused} recorded process(es) were left alone because "
                   f"they are no longer the processes V4 started. Their pid "
                   f"records were kept for you to inspect."))
    print("  No other CreditProbe instance, Docker container or demo was "
          "touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
