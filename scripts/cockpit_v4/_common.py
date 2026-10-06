"""
Shared launcher plumbing: ports, owned-process records, health, printing.

The rule that matters in every function here: V4 touches only what V4 owns.

A busy port is REPORTED, never freed. A process is stopped only when its
recorded pid, start time, command line AND working directory all still match
the record V4 wrote when it started it -- because a pid is reused within
minutes on a busy Mac, and `kill $(lsof -ti:8414)` on a reused pid stops
whatever happens to be there now. That is how a demo dies mid-presentation.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
AMBER = "\033[33m"
DIM = "\033[2m"


def supports_colour() -> bool:
    return sys.stdout.isatty() and os.environ.get("TERM") not in ("", "dumb")


def paint(text: str, colour: str) -> str:
    return f"{colour}{text}{RESET}" if supports_colour() else text


def ok(text: str) -> str:
    return paint(f"✓ {text}", GREEN)


def bad(text: str) -> str:
    return paint(f"✗ {text}", RED)


def warn(text: str) -> str:
    return paint(f"! {text}", AMBER)


def heading(text: str) -> None:
    print()
    print(paint(text, BOLD))
    print(paint("─" * max(8, len(text)), DIM))


# ---- ports -------------------------------------------------------------

def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def describe_port(port: int) -> str:
    """Who has the port, for the REPORT. Nothing is killed on this basis."""
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=5)
        lines = [ln for ln in out.stdout.splitlines()[1:] if ln.strip()]
        if lines:
            parts = lines[0].split()
            return f"{parts[0]} (pid {parts[1]})"
    except Exception:  # noqa: BLE001
        pass
    return "another process"


def pick_port(preferred: int, *, span: int = 20) -> tuple[int, list[str]]:
    """Return a usable port and what happened. Never frees the preferred one."""
    notes: list[str] = []
    if port_free(preferred):
        return preferred, notes
    notes.append(
        f"port {preferred} is in use by {describe_port(preferred)}. It was "
        f"NOT stopped: it may be another CreditProbe instance.")
    for candidate in range(preferred + 1, preferred + span):
        if port_free(candidate):
            notes.append(f"using {candidate} instead.")
            return candidate, notes
    notes.append(f"no free port between {preferred} and {preferred + span}.")
    return 0, notes


# ---- owned process records ---------------------------------------------

@dataclass
class Owned:
    """A process V4 started, identified by more than its pid."""

    name: str
    pid: int
    started_at: float
    command: str
    cwd: str
    port: int = 0
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def pid_dir(runtime_dir: Path) -> Path:
    directory = Path(runtime_dir) / "pids"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def record(runtime_dir: Path, owned: Owned) -> None:
    (pid_dir(runtime_dir) / f"{owned.name}.json").write_text(
        json.dumps(owned.to_dict(), indent=2), encoding="utf-8")


def records(runtime_dir: Path) -> list[Owned]:
    out: list[Owned] = []
    for path in sorted(pid_dir(runtime_dir).glob("*.json")):
        try:
            out.append(Owned(**json.loads(path.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001
            continue
    return out


def forget(runtime_dir: Path, name: str) -> None:
    path = pid_dir(runtime_dir) / f"{name}.json"
    if path.exists():
        path.unlink()


def _ps(pid: int, fields: str) -> str:
    """One `ps` field for one pid, or "" if the process is gone.

    The trailing `=` in every caller's format suppresses the header, so the
    FIRST non-empty line is the value. Skipping a line that is not printed
    made every process look dead, which in turn made the stop script decline
    to stop anything -- a guard that refuses everything is as useless as one
    that refuses nothing.
    """
    try:
        out = subprocess.run(["ps", "-o", fields, "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return ""
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        return lines[0] if lines else ""
    except Exception:  # noqa: BLE001
        return ""


def process_start_time(pid: int) -> float:
    """Elapsed seconds since the process started, as a start-time proxy.

    A reused pid has a much shorter elapsed time than the process V4 started,
    which is what makes the identity check work without parsing platform-
    specific absolute start timestamps.
    """
    elapsed = _ps(pid, "etimes=")
    try:
        return time.time() - float(elapsed)
    except ValueError:
        return 0.0


def process_command(pid: int) -> str:
    return _ps(pid, "command=")


def process_cwd(pid: int) -> str:
    try:
        out = subprocess.run(["lsof", "-a", "-d", "cwd", "-p", str(pid),
                              "-Fn"], capture_output=True, text=True,
                             timeout=5)
        for line in out.stdout.splitlines():
            if line.startswith("n"):
                return line[1:]
    except Exception:  # noqa: BLE001
        pass
    return ""


def still_ours(owned: Owned, *, tolerance_seconds: float = 5.0
               ) -> tuple[bool, str]:
    """Four checks. A pid alone proves nothing."""
    if not _ps(owned.pid, "pid="):
        return False, "not running"
    started = process_start_time(owned.pid)
    if started and abs(started - owned.started_at) > tolerance_seconds:
        return False, (
            f"pid {owned.pid} is now a DIFFERENT process (started "
            f"{abs(started - owned.started_at):.0f}s away from our record). "
            f"Refusing to stop it.")
    command = process_command(owned.pid)
    if command and owned.command:
        marker = owned.command.split()[0]
        if "cockpit_v4" not in command and marker not in command:
            return False, (
                f"pid {owned.pid} is running {command[:60]!r}, which is not "
                f"what V4 started. Refusing to stop it.")
    cwd = process_cwd(owned.pid)
    if cwd and owned.cwd and Path(cwd).resolve() != Path(owned.cwd).resolve():
        return False, (
            f"pid {owned.pid} is working in {cwd}, not the V4 worktree "
            f"{owned.cwd}. Refusing to stop it.")
    return True, "ours"


#: Markers that identify a served page as THIS application's shell rather
#: than a proxy error page, a parked domain or a different app on a reused
#: port. Any one of them is enough; all three would be brittle across Next
#: versions, and none would accept anything that returned 200.
UI_IDENTITY_MARKERS: tuple[str, ...] = ("CreditProbe", "/_next/", "__next")


def _fetch(url: str, timeout: float = 3.0) -> tuple[int, str, str]:
    """Return (status, body, error). Never raises, never parses."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
            charset = (response.headers.get_content_charset()
                       or "utf-8") if hasattr(response, "headers") else "utf-8"
            return int(response.status), raw.decode(charset, "replace"), ""
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return int(exc.code), body, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return 0, "", str(exc)[:160]


def wait_for_json_health(url: str, *, timeout_seconds: float = 45.0
                         ) -> tuple[bool, Any]:
    """Bounded readiness for a JSON health endpoint.

    Used ONLY where the endpoint actually returns JSON. Pointing this at a
    page that serves HTML is the bug this function was split to prevent: the
    decode raised, the exception was swallowed as "not ready yet", and a
    perfectly healthy UI was reported as failed until the timeout expired.
    """
    deadline = time.time() + timeout_seconds
    last: Any = "no response yet"
    while time.time() < deadline:
        status, body, error = _fetch(url)
        if error and status == 0:
            last = error
        elif 200 <= status < 300:
            try:
                return True, json.loads(body)
            except json.JSONDecodeError:
                # A 2xx that is not JSON is a real mismatch, not a retry: the
                # caller asked for a JSON endpoint and got something else.
                return False, (
                    f"{url} returned HTTP {status} but the body is not JSON "
                    f"({body[:80]!r}...). This is the wrong kind of endpoint "
                    f"for a JSON health check.")
        else:
            last = f"HTTP {status}"
        time.sleep(0.5)
    return False, last


def check_ui_page(body: str, status: int) -> tuple[bool, str]:
    """Is this a served CreditProbe page? Identity, not merely a 200."""
    if not 200 <= status < 300:
        return False, f"HTTP {status}"
    if not body.strip():
        return False, f"HTTP {status} with an empty body"
    lowered = body.lower()
    if "<html" not in lowered and "<!doctype html" not in lowered:
        return False, (f"HTTP {status} but the body is not an HTML document "
                       f"({body[:60]!r}...)")
    found = [m for m in UI_IDENTITY_MARKERS if m.lower() in lowered]
    if not found:
        return False, (
            f"HTTP {status} served HTML, but nothing in it identifies the "
            f"CreditProbe UI. Something else may be answering on this port.")
    return True, f"HTTP {status}, identified by {found[0]}"


def wait_for_ui_ready(url: str, *, timeout_seconds: float = 90.0
                      ) -> tuple[bool, str]:
    """Bounded readiness for the Next.js UI.

    Validates an HTTP 2xx AND that the page is recognisably this application.
    It does NOT parse the body as JSON -- the root page is HTML, and treating
    a decode failure as "not ready" is how a working UI gets reported as
    broken.

    A dev server compiles the first request, so a slow first response is
    normal and the timeout is generous. A 500 or a page with no CreditProbe
    identity is reported immediately rather than waited out: neither becomes
    correct by trying again.
    """
    deadline = time.time() + timeout_seconds
    last = "no response yet"
    while time.time() < deadline:
        status, body, error = _fetch(url, timeout=10.0)
        if status == 0:
            # Not listening yet. The expected state while it boots.
            last = error or "connection refused"
        else:
            ok, detail = check_ui_page(body, status)
            if ok:
                return True, detail
            if 500 <= status < 600:
                return False, (
                    f"the UI answered with {detail}. A server error does not "
                    f"become correct by waiting; see the UI log.")
            last = detail
        time.sleep(0.5)
    return False, last


def wait_for_health(url: str, *, timeout_seconds: float = 45.0
                    ) -> tuple[bool, Any]:
    """Deprecated alias for `wait_for_json_health`.

    Kept so nothing that already imports it silently changes meaning. New
    callers pick the one that matches what the endpoint actually serves.
    """
    return wait_for_json_health(url, timeout_seconds=timeout_seconds)


def read_json_url(url: str, timeout: float = 5.0) -> dict[str, Any] | None:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def table(rows: list[tuple[str, str]], *, width: int = 30) -> None:
    for label, value in rows:
        print(f"  {label.ljust(width)} {value}")


def warm_routes(base_url: str, paths: tuple[str, ...],
                timeout_seconds: float = 240.0) -> list[str]:
    """Make the dev server compile these routes before anything is timed.

    `next dev` compiles a route on its FIRST request. The readiness check
    warms `/` and nothing else, so the first navigation to the transcript
    page paid for compiling it -- and a browser assertion waiting sixty
    seconds for an answer that had not started rendering reported a product
    defect that was a cold bundler.

    Best effort by design: a route that will not warm is reported and the
    caller carries on, because failing to pre-compile a page is not a reason
    to refuse to test it.
    """
    notes: list[str] = []
    for path in paths:
        started = time.time()
        status, _body, error = _fetch(f"{base_url.rstrip('/')}{path}",
                                      timeout=timeout_seconds)
        took = time.time() - started
        if status == 0:
            notes.append(f"{path} did not warm: {error}")
        else:
            notes.append(f"{path} compiled in {took:.1f}s (HTTP {status})")
    return notes


__all__ = ["Owned", "ROOT", "UI_IDENTITY_MARKERS", "bad", "check_ui_page",
           "describe_port", "forget", "heading", "ok", "paint",
           "pick_port", "port_free", "process_command", "process_cwd",
           "process_start_time", "read_json_url", "record", "records",
           "still_ours", "table", "wait_for_health",
           "wait_for_json_health", "wait_for_ui_ready", "warm_routes",
           "warn"]
