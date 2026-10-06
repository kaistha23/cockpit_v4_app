#!/usr/bin/env python3
"""Show what V4 is doing. Reads only; changes nothing."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cockpit_v4._common import (  # noqa: E402
    bad, heading, ok, read_json_url, records, still_ours, table, warn)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", default=os.environ.get(
        "COCKPIT_V4_RUNTIME_DIR",
        str(Path.home() / ".creditprobe" / "cockpit_v4")))
    args = parser.parse_args()
    runtime_dir = Path(args.runtime_dir).expanduser()

    heading("Cockpit V4 · processes we own")
    owned = records(runtime_dir)
    if not owned:
        print("  none recorded — V4 is not running from this runtime "
              "directory.")
        return 0

    api_url = ""
    for item in owned:
        mine, why = still_ours(item)
        if mine:
            print(ok(f"{item.name}: pid {item.pid} · port {item.port} · "
                     f"{item.url}"))
            if item.name == "api":
                api_url = item.url
        else:
            print(warn(f"{item.name}: recorded pid {item.pid} is {why}"))

    if not api_url:
        return 0

    heading("Cockpit V4 · readiness")
    report = read_json_url(f"{api_url}/api/v1/cockpit-v4/diagnostics")
    if report is None:
        print(bad("the API did not answer its diagnostics endpoint."))
        return 1
    table([("route", report.get("route", "")),
           ("startup sha", report.get("startup_sha", "")),
           ("credential", report.get("credential", "")),
           ("product help", str(report.get("ready_for_product_help"))),
           ("sql analysis", str(report.get("ready_for_sql_analysis"))),
           ("python analysis", str(report.get("ready_for_python_analysis"))),
           ("askable books",
            ", ".join(report.get("analysis_domains", [])) or "none")])

    # The BOOKS, not "the release". Reporting one pre-domain quarterly
    # release here was how an operator came to believe this Cockpit was
    # quarterly while both monthly books were open and serving.
    books = (report.get("checks", {}).get("domains", {}) or {}).get(
        "books", []) or []
    for book in books:
        print()
        print(f"  {book.get('domain_label', book.get('domain_id', '')).upper()}")
        if not book.get("browse_ready"):
            print(bad(f"    not published: {book.get('reason') or 'unknown'}"))
            print(f"    remedy: {book.get('provision_command', '')}")
            continue
        table([("release", book.get("release_id", "—")),
               ("fingerprint",
                str(book.get("release_fingerprint", ""))[:16] or "—"),
               ("frequency", book.get("reporting_frequency", "—")),
               ("latest period", book.get("latest_period", "—")),
               ("questions", "ready" if book.get("analysis_ready")
                else "browse only")])

    legacy = (report.get("checks", {}).get("release", {}) or {})
    if legacy.get("ok"):
        print()
        print("  LEGACY COMPATIBILITY RELEASE")
        table([("release", str((legacy.get("release") or {})
                               .get("dataset_release_id", "—"))),
               ("used for",
                "historical threads and /attention-legacy only")])
    # §12. The allowance ladder, by name. An operator looking at a run that
    # stopped on time should be able to see which family it was in and what
    # that family allows, without reading the source or inferring it from the
    # number of seconds in an error message.
    families = (report.get("budget_policy", {}) or {}).get("families", [])
    if families:
        print()
        print("  ALLOWANCES")
        table([(str(f.get("family", "")),
                f"{float(f.get('deadline_seconds') or 0):.0f}s  "
                f"${float(f.get('spend_ceiling_usd') or 0):.2f}")
               for f in families if f.get("named")])

    runner = report.get("checks", {}).get("python_runner", {})
    if not runner.get("available"):
        print(f"  python runner unavailable: {runner.get('reason', '')}")
    print()
    print("  Nothing was changed by this command.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
