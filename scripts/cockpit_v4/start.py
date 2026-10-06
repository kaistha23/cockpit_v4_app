#!/usr/bin/env python3
"""
Start Cockpit V4. One command, no manual pid work, nothing else touched.

Order of operations, and why it is this order:

  1. Confirm we are in the V4 worktree and record the STARTUP sha. Read once,
     so the trace names the code the process is running rather than whatever
     the checkout says later.
  2. Confirm the runtime paths are V4's own. A branch does not isolate a
     database.
  3. Find free ports. A busy port is reported with who has it and an
     alternative is chosen. Nothing is ever killed to obtain a port.
  4. Obtain the credential from an approved store or a hidden prompt. It is
     never echoed, never written to the repository, never put in an argv, and
     never asked for in a chat window.
  5. Verify the model, its capabilities and its PRICE without a paid call.
  6. Confirm the release opens read-only.
  7. Start the API, then the UI, wait for real health, and print the URL.

Any of those failing stops here with the reason and the exact remedy.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cockpit_v4._common import (  # noqa: E402
    Owned, ROOT, bad, heading, ok, paint, pick_port, read_json_url, record,
    records, still_ours, table, wait_for_json_health, wait_for_ui_ready,
    warn)

DEFAULT_API_PORT = 8414
DEFAULT_UI_PORT = 5414
KEYCHAIN_SERVICE = "creditprobe-cockpit-v4"


def repo_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else "unknown"


def repo_branch() -> str:
    out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                         cwd=str(ROOT), capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else "unknown"


def read_keychain() -> str:
    """macOS Keychain, if the user has stored the key there. Optional."""
    if sys.platform != "darwin":
        return ""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
             "-w"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def obtain_credential(*, allow_prompt: bool) -> tuple[str, str]:
    """The key, and where it came from. Never printed, never stored in Git."""
    existing = os.environ.get("COCKPIT_ANTHROPIC_API_KEY", "").strip()
    if existing:
        return existing, "the environment"
    stored = read_keychain()
    if stored:
        return stored, f"the macOS Keychain ({KEYCHAIN_SERVICE})"
    if not allow_prompt or not sys.stdin.isatty():
        return "", ""
    print()
    print("  The Cockpit's own API key is not set in this shell.")
    print("  Paste it here — it is hidden as you type, is not echoed, is not")
    print("  written into the repository, and is not passed on a command line.")
    print(f"  To avoid this prompt next time:")
    print(f"    security add-generic-password -s {KEYCHAIN_SERVICE} "
          f"-a \"$USER\" -w")
    try:
        value = getpass.getpass("  COCKPIT_ANTHROPIC_API_KEY: ").strip()
    except (EOFError, KeyboardInterrupt):
        return "", ""
    return value, "an interactive prompt (not saved)"


#: The two variables the V4 frontend process needs, and what reads each.
#:
#: `NEXT_PUBLIC_COCKPIT_V4_API` is read by the Cockpit V4 client
#: (`frontend/src/components/cockpit-v4/client.ts`).
#: `NEXT_PUBLIC_API_URL` is read by the application SHELL and the generic
#: client (`frontend/src/lib/api.ts`), which falls back to
#: `http://127.0.0.1:8000` when it is unset.
UI_API_VARIABLES: tuple[str, ...] = ("NEXT_PUBLIC_COCKPIT_V4_API",
                                     "NEXT_PUBLIC_API_URL")

#: The default the generic client uses when told nothing. Never V4's address,
#: which is exactly why the launcher has to say so explicitly.
LEGACY_DEFAULT_API = "http://127.0.0.1:8000"


def ui_environment(base: dict[str, str], *, api_port: int,
                   ui_port: int) -> dict[str, str]:
    """The environment for the V4 frontend process.

    Both API variables are set, and both to the port this launcher ACTUALLY
    selected -- not the proposed one. A busy 8414 means the API is somewhere
    else, and a UI told otherwise is worse than a UI that did not start.

    Setting only the Cockpit variable was the defect: the V4 Cockpit talked to
    the V4 API while the header, the backend-status badge and every
    landing-page widget kept talking to `http://127.0.0.1:8000` — a backend
    this instance never started. Half the page then reported a system that was
    not there.

    This is scoped to the child process. Nothing here edits `lib/api.ts`, so
    every other CreditProbe instance keeps the default it has today.
    """
    api_url = f"http://127.0.0.1:{int(api_port)}"
    if api_url == LEGACY_DEFAULT_API:
        # Not reachable through `pick_port`, but the invariant is worth
        # stating where it would break rather than discovering it live.
        raise ValueError(
            f"the V4 API resolved to {api_url}, which is the generic "
            f"client's legacy default. V4 must run on its own port.")
    return {**base,
            **{name: api_url for name in UI_API_VARIABLES},
            "PORT": str(int(ui_port))}


def already_running(runtime_dir: Path) -> list[Owned]:
    live = []
    for owned in records(runtime_dir):
        mine, _ = still_ours(owned)
        if mine:
            live.append(owned)
    return live


def _period_word(book: dict) -> str:
    """"month" for a monthly book, "quarter" for a quarterly one."""
    return {"monthly": "month", "quarterly": "quarter"}.get(
        str(book.get("reporting_frequency", "")), "period")


def dual_default(books: list[dict]) -> str:
    """Which book the Cockpit opens on. The configured one if it is ready."""
    from backend.cockpit_v4 import domains as dom_mod

    ready = [b for b in books if b.get("browse_ready")]
    for book in ready:
        if book["domain_id"] == dom_mod.DEFAULT_DOMAIN:
            return book["domain_label"]
    return ready[0]["domain_label"] if ready else "none"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--release", default=os.environ.get(
        "COCKPIT_V4_RELEASE_ID", "v4-saudi-20q-v1"))
    parser.add_argument("--runtime-dir", default=os.environ.get(
        "COCKPIT_V4_RUNTIME_DIR",
        str(Path.home() / ".creditprobe" / "cockpit_v4")))
    parser.add_argument("--price-card", default=os.environ.get(
        "COCKPIT_V4_PRICE_CARD",
        str(ROOT / "config" / "cockpit_v4" / "price_card.json")))
    parser.add_argument("--model", default=os.environ.get(
        "AI_COCKPIT_REASONING_MODEL", ""))
    parser.add_argument("--no-ui", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--no-prompt", action="store_true",
                        help="Never prompt for the key; fail instead.")
    args = parser.parse_args()

    runtime_dir = Path(args.runtime_dir).expanduser()

    heading("Cockpit V4 · checkout")
    branch, sha = repo_branch(), repo_sha()
    table([("worktree", str(ROOT)), ("branch", branch),
           ("startup sha", sha)])
    if "cockpit" not in branch and "v4" not in branch:
        print(warn(f"branch {branch!r} does not look like the V4 branch. "
                   f"Starting anyway; nothing else is touched."))
    else:
        print(ok("V4 worktree"))

    heading("Cockpit V4 · already running?")
    live = already_running(runtime_dir)
    if live:
        for owned in live:
            print(warn(f"{owned.name} is already running on port "
                       f"{owned.port} (pid {owned.pid})."))
        print("  Run STOP_COCKPIT_V4 first, or use --api-port/--ui-port for a "
              "second instance.")
        return 1
    print(ok("no V4 instance of ours is running"))

    heading("Cockpit V4 · ports")
    api_port, api_notes = pick_port(args.api_port)
    ui_port, ui_notes = pick_port(args.ui_port)
    for note in api_notes + ui_notes:
        print(warn(note))
    if not api_port or (not ui_port and not args.no_ui):
        print(bad("no free port was available. Nothing was stopped to make "
                  "one; choose ports with --api-port / --ui-port."))
        return 1
    print(ok(f"API {api_port} · UI {ui_port if not args.no_ui else '(off)'}"))

    heading("Cockpit V4 · credential")
    provider = os.environ.get("AI_PROVIDER", "anthropic").strip().lower()
    print(f"  provider                       {provider}"
          + ("" if os.environ.get("AI_PROVIDER") else "  (AI_PROVIDER not set)"))
    key, source = obtain_credential(allow_prompt=not args.no_prompt) if provider == "anthropic" else ("", "")
    if provider == "anthropic" and not key:
        print(bad("COCKPIT_ANTHROPIC_API_KEY is not available."))
        print("  Set it in this shell, or store it in the Keychain:")
        print(f"    security add-generic-password -s {KEYCHAIN_SERVICE} "
              f"-a \"$USER\" -w")
        return 1
    if provider == "anthropic":
        print(ok(f"present · from {source}"))
        print(paint("  (the value is never printed, logged or committed)", "\033[2m"))
    else:
        print(ok("oMLX credentials are read from OMLX_API_KEY when configured"))

    heading("Cockpit V4 · runtime")
    for directory in (runtime_dir, runtime_dir / "state",
                      runtime_dir / "artifacts", runtime_dir / "logs",
                      runtime_dir / "pids"):
        directory.mkdir(parents=True, exist_ok=True)
    table([("runtime dir", str(runtime_dir)),
           ("state db", str(runtime_dir / "state" / "cockpit_v4.sqlite3")),
           ("release", args.release)])
    print(ok("V4 paths are separate from V3 and every other module"))

    environment = {
        **os.environ,
        "COCKPIT_AGENTIC_V4": "true",
        "COCKPIT_ANTHROPIC_API_KEY": key,
        "COCKPIT_V4_RUNTIME_DIR": str(runtime_dir),
        "COCKPIT_V4_STATE_DATABASE": str(
            runtime_dir / "state" / "cockpit_v4.sqlite3"),
        "COCKPIT_V4_RELEASE_ID": args.release,
        "COCKPIT_V4_API_PORT": str(api_port),
        "COCKPIT_V4_UI_PORT": str(ui_port),
        "COCKPIT_V4_PRICE_CARD": args.price_card,
        "COCKPIT_V4_LOCAL_DEMO_AUTH": os.environ.get(
            "COCKPIT_V4_LOCAL_DEMO_AUTH", "true"),
        "COCKPIT_V4_MEMORY_ENABLED": os.environ.get(
            "COCKPIT_V4_MEMORY_ENABLED", "false"),
        "COCKPIT_V4_STARTUP_SHA": sha,
        # The catalog and release readers are shared with V3 and resolve
        # their lake through this namespace. V4 uses its OWN.
        "COCKPIT_AGENTIC_V3_NAMESPACE": os.environ.get(
            "COCKPIT_V4_NAMESPACE", "cockpit_v4"),
        "PYTHONPATH": str(ROOT),
    }
    if args.model:
        environment["AI_COCKPIT_REASONING_MODEL"] = args.model

    heading("Cockpit V4 · configuration and price")
    check = subprocess.run(
        [sys.executable, "-c",
         "import json,sys;"
         "from backend.cockpit_v4 import service, config;"
         "print(json.dumps(service.diagnostics(config.load()), default=str))"],
        cwd=str(ROOT), env=environment, capture_output=True, text=True)
    if check.returncode != 0:
        print(bad("the V4 runtime could not be inspected."))
        print(check.stderr.strip()[-1500:])
        return 1
    try:
        report = json.loads(check.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        print(bad("the diagnostics output could not be read."))
        print(check.stdout[-800:])
        return 1

    checks = report.get("checks", {})
    for name in ("credential", "model_and_price", "release",
                 "state_database"):
        entry = checks.get(name, {})
        if entry.get("ok"):
            print(ok(name.replace("_", " ")))
        else:
            print(bad(f"{name.replace('_', ' ')}: {entry.get('reason', '')}"))
    runner = checks.get("python_runner", {})
    print((ok if runner.get("available") else warn)(
        f"python runner: {'available' if runner.get('available') else 'unavailable'}"
        + (f" — {runner.get('reason')}" if not runner.get("available") else "")))

    # WHICH BOOKS, printed every start.
    #
    # This block used to print one release — the pre-domain quarterly one —
    # under the heading "release", with "latest quarter 2026Q2", while both
    # monthly books were open and serving. An operator reading it had every
    # reason to believe the Cockpit was quarterly, and so, it turned out, did
    # the analyst.
    #
    # So the books come first and the pre-domain release comes last, under a
    # heading that says exactly what it is.
    books = (checks.get("domains", {}) or {}).get("books", []) or []
    if not books:
        heading("Cockpit V4 · books")
        print(bad("This runtime has no analytical book published."))
        print()
        print("Action:")
        print("  python3 scripts/cockpit_v4/seed_domains.py")
        print()
        print(bad("Nothing else started."))
        return 1

    heading("Cockpit V4 · books")
    print(f"  DEFAULT DOMAIN  {dual_default(books)}")
    unopenable = []
    for book in books:
        print()
        print(f"  {book['domain_label'].upper()}")
        if not book.get("browse_ready"):
            print(bad(f"    not published: {book.get('reason') or 'unknown'}"))
            print(f"    remedy: {book.get('provision_command', '')}")
            unopenable.append(book["domain_id"])
            continue
        table([("release", book["release_id"]),
               ("fingerprint", str(book.get("release_fingerprint", ""))[:16]),
               ("currency", f"{book.get('reporting_currency', '')} "
                            f"{book.get('amount_scale', '')}".strip()),
               ("frequency", book.get("reporting_frequency", "")),
               (f"latest {_period_word(book)}",
                book.get("latest_period", "—")),
               ("periods", str(book.get("reporting_periods", "—"))),
               ("relations", str(book.get("relations", "—"))),
               ("questions", "ready" if book.get("analysis_ready")
                else "browse only")])

    askable = [b for b in books if b.get("analysis_ready")]
    if not askable:
        print()
        print(bad("No book in this runtime can answer a question."))
        print()
        print(bad("Nothing else started."))
        return 1

    # The pre-domain release, if it is still on disk. Named for what it is,
    # and NEVER the runtime's analytical default.
    release_check = checks.get("release", {}) or {}
    header = release_check.get("header") or {}
    heading("Cockpit V4 · legacy compatibility release")
    if release_check.get("ok") and header:
        table([("release", header.get("release_id", "—")),
               ("fingerprint",
                str(header.get("release_fingerprint", ""))[:16] or "—"),
               ("latest quarter",
                header.get("latest_populated_quarter", "—")),
               ("used for", "historical threads and /attention-legacy only")])
        print(warn("  New Cockpit threads never read this release."))
    else:
        print(f"  {header.get('release_id', args.release)} is not present. "
              f"That is not a problem: no new thread reads it.")

    if not report.get("ready_for_product_help"):
        print()
        print(bad("V4 cannot answer anything yet. Fix the checks above."))
        return 1
    if not report.get("ready_for_sql_analysis"):
        print()
        print(bad("V4 cannot run an analysis. Fix the checks above."))
        return 1
    if unopenable:
        print()
        print(warn(f"{', '.join(unopenable)} cannot be opened in this "
                   f"runtime. Its questions are refused; nothing is "
                   f"substituted for it."))

    heading("Cockpit V4 · starting")
    logs = runtime_dir / "logs"
    api_log = (logs / "api.log").open("a", encoding="utf-8")
    api_command = [
        sys.executable, "-m", "uvicorn",
        "backend.cockpit_v4.app:create_app", "--factory",
        "--host", "127.0.0.1", "--port", str(api_port), "--log-level", "info"]
    api = subprocess.Popen(api_command, cwd=str(ROOT), env=environment,
                           stdout=api_log, stderr=subprocess.STDOUT,
                           start_new_session=True)
    time.sleep(0.5)
    record(runtime_dir, Owned(
        name="api", pid=api.pid, started_at=time.time(),
        command=" ".join(api_command), cwd=str(ROOT), port=api_port,
        url=f"http://127.0.0.1:{api_port}"))

    healthy, detail = wait_for_json_health(
        f"http://127.0.0.1:{api_port}/health")
    if not healthy:
        print(bad(f"the API did not become healthy: {detail}"))
        print(f"  log: {logs / 'api.log'}")
        api.terminate()
        return 1
    print(ok(f"API healthy on http://127.0.0.1:{api_port}"))

    url = f"http://127.0.0.1:{api_port}/api/v1/cockpit-v4/diagnostics"
    if not args.no_ui:
        ui_dir = ROOT / "frontend"
        ui_log = (logs / "ui.log").open("a", encoding="utf-8")
        ui_command = ["npm", "run", "dev", "--", "--port", str(ui_port),
                      "--hostname", "127.0.0.1"]
        ui_env = ui_environment(environment, api_port=api_port,
                                ui_port=ui_port)
        try:
            ui = subprocess.Popen(ui_command, cwd=str(ui_dir), env=ui_env,
                                  stdout=ui_log, stderr=subprocess.STDOUT,
                                  start_new_session=True)
        except FileNotFoundError:
            print(warn("npm was not found; the API is running without the UI."))
            ui = None
        if ui is not None:
            time.sleep(0.5)
            record(runtime_dir, Owned(
                name="ui", pid=ui.pid, started_at=time.time(),
                command=" ".join(ui_command), cwd=str(ui_dir), port=ui_port,
                url=f"http://127.0.0.1:{ui_port}"))
            # The root page serves HTML. Decoding it as JSON was reporting a
            # healthy UI as failed until the timeout expired.
            healthy, detail = wait_for_ui_ready(
                f"http://127.0.0.1:{ui_port}/", timeout_seconds=120)
            if healthy:
                print(ok(f"UI ready on http://127.0.0.1:{ui_port} ({detail})"))
                url = f"http://127.0.0.1:{ui_port}/"
            else:
                print(warn(f"the UI did not become ready: {detail}. The API "
                           f"is running; log: {logs / 'ui.log'}"))

    heading("Cockpit V4 · ready")
    table([("open this", url),
           ("api", f"http://127.0.0.1:{api_port}"),
           ("UI -> V4 API", (f"http://127.0.0.1:{api_port}"
                             if not args.no_ui else "(UI not started)")),
           ("UI -> shell API", (f"http://127.0.0.1:{api_port}"
                                if not args.no_ui else "(UI not started)")),
           ("shell health", f"http://127.0.0.1:{api_port}/api/v1/health"),
           ("diagnostics",
            f"http://127.0.0.1:{api_port}/api/v1/cockpit-v4/diagnostics"),
           ("release", args.release),
           ("branch / sha", f"{branch} @ {sha[:12]}"),
           ("model", report["checks"].get("model_and_price", {})
            .get("capability", {}).get("model_id", "—")),
           ("product help", "ready" if report["ready_for_product_help"]
            else "not ready"),
           ("sql analysis", "ready" if report["ready_for_sql_analysis"]
            else "not ready"),
           ("python analysis", "ready" if report["ready_for_python_analysis"]
            else "unavailable"),
           ("logs", str(logs)),
           ("stop with", "STOP_COCKPIT_V4.command")])
    print()
    print("  Nothing else on this machine was started, stopped or changed.")
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
