"""Isolated Python execution for Opus-authored analysis steps.

What this module is
-------------------
Some analysis is not a SELECT. A survival curve, a decomposition across
several result sets, a regression against the macro window -- these are
computations over rows already fetched, and the specification allows Opus to
author Python for them. This module is the boundary that makes running that
code acceptable, and nothing more. It does not write Python, read Python for
intent, or repair Python. `CreditProbe` remains validator, executor,
diagnostician and guardrail enforcer, and the diagnostic it returns on failure
goes to Opus unedited.

The guarantee, and how it is obtained
-------------------------------------
The code never runs in the API process. It runs in a separate process placed
in fresh mount, network, PID, IPC and UTS namespaces, chrooted into a tmpfs
jail containing the interpreter, an explicitly approved dependency surface, a
read-only copy of its inputs and a writable output directory -- and nothing
else. There is no /etc, no repository, no environment file, no socket to any
network, no shell on PATH that reaches anything, and no writable path outside
the workspace. Address space, CPU seconds, file size, process count and core
dumps are bounded. Privileges are dropped before the code runs.

Why the availability flag is measured, not assumed
--------------------------------------------------
`probe()` does not look for `/usr/bin/unshare` and conclude that isolation
works. It runs a real job in a real jail whose code TRIES to escape -- opens a
socket to a routable address, opens /etc/passwd, writes to /usr, looks for the
repository, reads the environment for anything that smells like a credential,
checks its own uid -- and reports what happened. Python execution is offered
only when every one of those attempts failed in the expected way.

If the probe does not pass, `available` is False, the reason is recorded, and
the runtime tells Opus that Python is unavailable. There is no in-process
fallback, no restricted `eval`, no AST allowlist standing in for a kernel
boundary. The alternative to isolation here is not running.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.cockpit_agentic import sql as sql_mod
from backend.cockpit_agentic.contracts import (RESOURCE_LIMIT, RUNTIME_ERROR,
                                               SANDBOX_UNAVAILABLE,
                                               UNSAFE_OPERATION)

BOOT = Path(__file__).with_name("_pyjail_boot.py")

#: The dependency surface, by top-level import name. Everything else in the
#: site directory -- the provider SDK, the database drivers, the HTTP clients,
#: the crypto libraries -- is never mounted into the jail and therefore cannot
#: be imported however the code asks for it.
APPROVED_PACKAGES: tuple[str, ...] = (
    "numpy", "numpy.libs", "pandas", "pandas.libs", "dateutil", "six",
    "pytz", "tzdata")

#: Exit codes the bootstrap uses. Kept in one place so a change in either file
#: is a change in both.
SETUP_FAILED = 91
CODE_RAISED = 92
NO_RESULT = 93
CPU_EXHAUSTED = 94

#: uid/gid the code runs as when the launcher is privileged. `nobody`.
RUN_AS_UID = 65534
RUN_AS_GID = 65534


class PythonRejected(sql_mod.SqlRejected):
    """A Python step that was refused or that failed.

    Subclasses the SQL refusal deliberately: the failure packet builder, the
    ledger and the diagnostics take one refusal type carrying one section 8.2
    category, and a Python failure is not a different kind of event to the
    application. Only the message and the category differ.
    """


@dataclass(frozen=True)
class SandboxLimits:
    """What one step may consume. Derived from the ledger, never widened by
    the model or the browser."""

    wall_seconds: float = 20.0
    cpu_seconds: int = 15
    memory_mib: int = 512
    output_bytes: int = 256_000
    file_bytes: int = 16 * 1024 * 1024
    max_processes: int = 64
    jail_tmpfs_mib: int = 64
    scratch_tmpfs_mib: int = 64
    max_input_rows: int = 20_000

    @classmethod
    def from_ledger(cls, ledger) -> "SandboxLimits":
        limits = ledger.limits
        wall = min(float(limits.step_wall_seconds),
                   max(1.0, ledger.remaining_seconds))
        return cls(wall_seconds=wall,
                   cpu_seconds=max(1, int(wall)),
                   memory_mib=int(limits.python_memory_mib))


@dataclass
class SandboxResult:
    """What came back. `stdout` and `result` are the code's own output and are
    treated as data, never as instructions."""

    status: str
    stdout: str = ""
    columns: list[dict[str, str]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Probe:
    """What isolation this host actually provides, established by running it."""

    available: bool
    strategy: str
    reason: str
    guarantees: dict[str, str]
    checked_at: float
    elapsed_seconds: float
    #: Properties that did NOT hold but do not by themselves breach the
    #: boundary. Reported so a weaker posture is visible rather than rounded
    #: up to the stronger one.
    caveats: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"available": self.available, "strategy": self.strategy,
                "reason": self.reason, "guarantees": dict(self.guarantees),
                "caveats": list(self.caveats),
                "elapsed_seconds": round(self.elapsed_seconds, 3)}


# --------------------------------------------------------- launch strategies

#: Two ways to get the namespaces. The first needs real CAP_SYS_ADMIN; the
#: second needs unprivileged user namespaces to be permitted, and gets its
#: privilege separation from the user namespace itself rather than from a
#: setuid. Each is tried in turn and the one that passes the probe is used.
STRATEGIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("privileged_namespaces",
     ("--mount", "--net", "--pid", "--fork", "--uts", "--ipc")),
    ("user_namespace",
     ("--user", "--map-root-user", "--mount", "--net", "--pid", "--fork",
      "--uts", "--ipc")),
)

#: The code the probe runs. It tries to reach everything it must not reach and
#: reports what each attempt did. A probe that merely printed "hello" would
#: prove that a subprocess starts, which is not the claim being made.
_PROBE_SOURCE = r'''
import json, os, socket, sys

findings = {}

findings["uid"] = os.getuid()
findings["euid"] = os.geteuid()

try:
    s = socket.socket(); s.settimeout(2)
    s.connect(("1.1.1.1", 443))
    findings["network"] = "REACHED"
except Exception as exc:
    findings["network"] = type(exc).__name__

try:
    open("/etc/passwd").read()
    findings["etc"] = "READABLE"
except Exception as exc:
    findings["etc"] = type(exc).__name__

# Two different claims: that the jail's own skeleton is closed, and that the
# read-only bind of the host's libraries actually took. The second is the one
# that would put a write on the host filesystem if it had not.
for label, path in (("system_writable", "/usr/__escape__"),
                    ("host_bind_writable", "/usr/lib/__escape__"),
                    ("site_writable", "/site/__escape__")):
    try:
        open(path, "w").write("x")
        findings[label] = "WRITABLE"
    except Exception as exc:
        findings[label] = type(exc).__name__

findings["repository_visible"] = os.path.exists(REPO)
findings["root_entries"] = sorted(os.listdir("/"))
findings["secretish_env"] = sorted(
    k for k in os.environ
    if any(m in k.upper() for m in ("KEY", "TOKEN", "SECRET", "PASSWORD",
                                    "CREDENTIAL")))
try:
    open("/work/in/code.py", "a")
    findings["input_writable"] = "WRITABLE"
except Exception as exc:
    findings["input_writable"] = type(exc).__name__

try:
    import numpy, pandas
    findings["approved_imports"] = [numpy.__version__, pandas.__version__]
except Exception as exc:
    findings["approved_imports"] = f"{type(exc).__name__}: {exc}"

for forbidden in ("anthropic", "requests", "httpx", "duckdb", "cryptography",
                  "sqlalchemy", "psycopg2"):
    try:
        __import__(forbidden)
        findings.setdefault("forbidden_imports", []).append(forbidden)
    except Exception:
        pass

import subprocess
try:
    subprocess.run(["/bin/sh", "-c", "echo hi"], capture_output=True)
    findings["shell"] = "PRESENT"
except Exception as exc:
    findings["shell"] = type(exc).__name__
findings["binary_dirs"] = [d for d in ("/bin", "/usr/bin", "/sbin",
                                       "/usr/sbin") if os.path.isdir(d)]

# Serialized here, deserialized by the launcher. The result envelope shapes
# structured values into table cells, and a boundary check must not depend on
# surviving that: an empty list read back as the string "[]" is truthy, and a
# truthy answer to "which binary directories exist" would read as a leak.
result = json.dumps(findings)
'''


def _site_packages() -> Path:
    for entry in sys.path:
        if entry.endswith("site-packages") and os.path.isdir(entry):
            return Path(entry)
    raise PythonRejected(
        SANDBOX_UNAVAILABLE,
        "No site-packages directory was found, so the approved dependency "
        "surface cannot be assembled.")


def _approved_sources() -> dict[str, str]:
    """Resolve the approved packages to real directories. A package that is not
    installed is simply absent from the jail; it is never substituted."""
    site = _site_packages()
    found: dict[str, str] = {}
    for name in APPROVED_PACKAGES:
        candidate = site / name
        if candidate.is_dir():
            found[name] = str(candidate)
            continue
        single = site / f"{name}.py"
        if single.is_file():
            # Keyed by the basename the import machinery will look for, not by
            # the distribution name: `six` is a single module file, and a mount
            # called `six` would not be importable.
            found[f"{name}.py"] = str(single)
    return found


# ------------------------------------------------------------------ the run

def _write_workspace(workspace: Path, code: str,
                     inputs: dict[str, Any]) -> None:
    (workspace / "in").mkdir(parents=True)
    (workspace / "out").mkdir(parents=True)
    (workspace / "in" / "code.py").write_text(code)
    for name, value in inputs.items():
        safe = "".join(c for c in str(name) if c.isalnum() or c in "-_")
        (workspace / "in" / f"{safe}.json").write_text(
            json.dumps(value, default=str))
    # Ownership is the second lock on the input snapshot: the bootstrap also
    # binds `in` read-only, and the code runs as a user that owns none of it.
    for path in (workspace / "in").rglob("*"):
        os.chmod(path, 0o444)
    os.chmod(workspace / "in", 0o555)
    os.chmod(workspace / "out", 0o777)
    # The workspace itself must be traversable by the unprivileged user the
    # code runs as, or it cannot reach either directory.
    os.chmod(workspace, 0o755)


def _launch(*, code: str, inputs: dict[str, Any], limits: SandboxLimits,
            strategy: tuple[str, tuple[str, ...]],
            cancel=None) -> tuple[int, dict[str, Any], str, float]:
    """Run one job. Returns (exit code, payload, stderr, elapsed)."""
    name, flags = strategy
    workspace = Path(tempfile.mkdtemp(prefix="cockpit-py-"))
    jail_root = Path(tempfile.mkdtemp(prefix="cockpit-jail-"))
    started = time.monotonic()
    process = None
    try:
        _write_workspace(workspace, code, inputs)
        job = {
            "jail_root": str(jail_root), "workspace": str(workspace),
            "approved_packages": _approved_sources(),
            "memory_mib": limits.memory_mib, "cpu_seconds": limits.cpu_seconds,
            "file_bytes": limits.file_bytes,
            "max_processes": limits.max_processes,
            "output_bytes": limits.output_bytes,
            "jail_tmpfs_mib": limits.jail_tmpfs_mib,
            "scratch_tmpfs_mib": limits.scratch_tmpfs_mib,
            "run_as_uid": RUN_AS_UID, "run_as_gid": RUN_AS_GID,
        }
        # An empty environment, built here rather than inherited. Nothing the
        # API process holds -- least of all a provider credential -- is
        # reachable from inside.
        environment = {"PATH": "", "HOME": "/work/out",
                       "TMPDIR": "/tmp", "LC_ALL": "C.UTF-8",
                       "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"}
        command = [shutil.which("unshare") or "/usr/bin/unshare", *flags, "--",
                   sys.executable, "-I", "-E", "-B", str(BOOT),
                   json.dumps(job)]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, cwd="/", start_new_session=True, text=True)

        deadline = started + limits.wall_seconds
        while True:
            try:
                _out, err = process.communicate(
                    timeout=max(0.05, min(0.25, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel():
                    _kill(process)
                    raise PythonRejected(
                        RUNTIME_ERROR,
                        "The request was cancelled while the Python step was "
                        "running. The step was stopped and its workspace "
                        "removed.")
                if time.monotonic() >= deadline:
                    _kill(process)
                    raise PythonRejected(
                        RESOURCE_LIMIT,
                        f"The Python step ran past its "
                        f"{limits.wall_seconds:.0f}-second wall limit and was "
                        f"stopped. Nothing it had computed is returned, "
                        f"because a partial computation is not a result.",
                        detail="wall clock")

        elapsed = time.monotonic() - started
        payload: dict[str, Any] = {}
        result_file = workspace / "out" / "result.json"
        if result_file.exists():
            try:
                payload = json.loads(
                    result_file.read_text()[:limits.output_bytes * 4])
            except json.JSONDecodeError:
                payload = {}
        return process.returncode, payload, (err or "")[:8_000], elapsed
    finally:
        if process is not None and process.poll() is None:
            _kill(process)
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(jail_root, ignore_errors=True)


def _kill(process) -> None:
    """Kill the whole session. The launcher is PID 1 of its own PID namespace,
    so this takes everything inside it with it."""
    for sig in (signal.SIGKILL,):
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


# -------------------------------------------------------------- the probe

_PROBE: Probe | None = None


#: Every escape attempt the probe makes. A finding that is ABSENT is not a
#: finding that passed: an evaluator that reads a missing key as `None` and
#: moves on would certify a boundary it never tested, which is the one failure
#: mode this whole module exists to avoid.
REQUIRED_FINDINGS: tuple[str, ...] = (
    "uid", "network", "etc", "system_writable", "host_bind_writable",
    "site_writable", "repository_visible",
    "root_entries", "secretish_env", "input_writable", "approved_imports",
    "shell", "binary_dirs")


def _findings_from(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the probe's dictionary back out of the result envelope.

    The sandbox shapes every result into named columns and records, so the
    probe's dict arrives as a one-record table rather than as itself.
    """
    value = payload.get("result")
    if isinstance(value, dict) and "rows" in value:
        rows = value.get("rows") or []
        value = rows[0].get("value") if rows else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _evaluate(findings: dict[str, Any], repo: str, privileges: str = ""
              ) -> tuple[bool, str, dict[str, str], tuple[str, ...]]:
    """Read the escape attempts. Every one must have been made, and every one
    that breaches the boundary must have failed.

    `problems` end the strategy. `caveats` do not: they are properties that did
    not hold and do not by themselves let anything out -- a writable directory
    inside a throwaway tmpfs that the code can already write to in /tmp. They
    are reported rather than rounded away, because the two launch strategies
    genuinely differ in posture and a reader is entitled to know which one
    answered.
    """
    guarantees: dict[str, str] = {}
    problems: list[str] = []
    caveats: list[str] = []

    absent = [name for name in REQUIRED_FINDINGS if name not in findings]
    if absent:
        return False, (f"the probe did not report on {absent}, so those "
                       f"boundaries are unverified"), {}, ()

    network = findings.get("network")
    if network == "REACHED":
        problems.append("the sandbox reached the network")
    guarantees["network_denied"] = f"connect() raised {network}"

    if findings.get("etc") == "READABLE":
        problems.append("/etc was readable")
    guarantees["host_filesystem_hidden"] = (
        f"/etc/passwd raised {findings.get('etc')}; "
        f"root holds {findings.get('root_entries')}")

    # The claim that matters: the host's own files cannot be written. /usr/lib
    # is bound from the host, so a successful write there would land outside.
    if findings.get("host_bind_writable") == "WRITABLE":
        problems.append("the host's system libraries were writable")
    guarantees["host_files_read_only"] = (
        f"writing the bound /usr/lib raised "
        f"{findings.get('host_bind_writable')}")

    # The jail's own skeleton is a tmpfs that dies with the namespace. Writing
    # in it reaches nothing; it is still a layer, and its absence is said.
    writable = [where for label, where in (("system_writable", "/usr"),
                                           ("site_writable", "/site"))
                if findings.get(label) == "WRITABLE"]
    if writable:
        caveats.append(
            f"{' and '.join(writable)} are writable inside the jail's own "
            f"throwaway tmpfs, because this strategy runs the code as uid 0 of "
            f"its namespace. Nothing written there outlives the step or "
            f"reaches the host, and a planted module could only be imported by "
            f"the same step that wrote it.")
    guarantees["jail_skeleton_read_only"] = (
        f"/usr raised {findings.get('system_writable')}; "
        f"/site raised {findings.get('site_writable')}")

    if findings.get("repository_visible"):
        problems.append("the repository was visible")
    guarantees["repository_hidden"] = f"{repo} not present"

    if findings.get("input_writable") == "WRITABLE":
        problems.append("the input snapshot was writable")
    guarantees["inputs_read_only"] = (
        f"appending to the input raised {findings.get('input_writable')}")

    secrets = findings.get("secretish_env") or []
    if secrets:
        problems.append(f"credential-shaped environment variables: {secrets}")
    guarantees["environment_scrubbed"] = "no credential-shaped variables"

    if findings.get("shell") == "PRESENT" or findings.get("binary_dirs"):
        problems.append(
            f"a shell or binary directory was reachable: "
            f"{findings.get('shell')} {findings.get('binary_dirs')}")
    guarantees["no_shell"] = (
        f"/bin/sh raised {findings.get('shell')}; no binary directory exists")

    forbidden = findings.get("forbidden_imports") or []
    if forbidden:
        problems.append(f"unapproved packages were importable: {forbidden}")
    guarantees["dependency_surface"] = (
        f"approved: {findings.get('approved_imports')}; "
        f"unapproved importable: none")

    if isinstance(findings.get("approved_imports"), str):
        problems.append(f"the approved packages did not import: "
                        f"{findings['approved_imports']}")

    uid = findings.get("uid")
    if uid == 0 and os.getuid() == 0 and "user namespace" not in privileges:
        problems.append("the code ran as root")
    guarantees["privileges_dropped"] = privileges or f"ran as uid {uid}"
    guarantees["separate_process"] = "a child process, never the API process"

    return (not problems), "; ".join(problems), guarantees, tuple(caveats)


def probe(*, force: bool = False) -> Probe:
    """Establish, by running it, what isolation this host provides."""
    global _PROBE
    if _PROBE is not None and not force:
        return _PROBE

    repo = str(Path(__file__).resolve().parents[2])
    source = f"REPO = {repo!r}\n" + _PROBE_SOURCE
    started = time.monotonic()
    reasons: list[str] = []

    if not shutil.which("unshare"):
        _PROBE = Probe(
            available=False, strategy="", checked_at=time.time(),
            elapsed_seconds=time.monotonic() - started,
            reason="`unshare` is not on this host, so no namespace can be "
                   "created and no boundary can be established.",
            guarantees={})
        return _PROBE

    limits = SandboxLimits(wall_seconds=45.0, cpu_seconds=40)
    for strategy in STRATEGIES:
        try:
            code, payload, stderr, _elapsed = _launch(
                code=source, inputs={}, limits=limits, strategy=strategy)
        except Exception as exc:                             # noqa: BLE001
            reasons.append(f"{strategy[0]}: {type(exc).__name__}: {exc}")
            continue
        findings = _findings_from(payload) if payload else {}
        if code != 0 or not findings:
            reasons.append(
                f"{strategy[0]}: exit {code} "
                f"{(payload.get('error') or stderr or '').strip()[:400]}")
            continue
        ok, why, guarantees, caveats = _evaluate(
            findings, repo, str(payload.get("privileges") or ""))
        if ok:
            _PROBE = Probe(
                available=True, strategy=strategy[0], reason="",
                guarantees=guarantees, caveats=caveats, checked_at=time.time(),
                elapsed_seconds=time.monotonic() - started)
            return _PROBE
        reasons.append(f"{strategy[0]}: {why}")

    _PROBE = Probe(
        available=False, strategy="", checked_at=time.time(),
        elapsed_seconds=time.monotonic() - started,
        reason="No launch strategy produced a verified boundary. "
               + " | ".join(reasons),
        guarantees={})
    return _PROBE


def reset_probe() -> None:
    """Forget the cached probe. For tests and for an operator re-check."""
    global _PROBE
    _PROBE = None


def unavailable_reason() -> str:
    """The sentence Opus is given when Python cannot be offered."""
    result = probe()
    if result.available:
        return ""
    return ("Isolated Python execution is not available on this host, so only "
            "SQL steps can be run. It is reported unavailable rather than "
            "downgraded to in-process execution, which would not be the same "
            "capability. Cause: " + result.reason)


# --------------------------------------------------------------- execute

def execute(code: str, *, inputs: dict[str, Any] | None = None,
            limits: SandboxLimits | None = None, cancel=None,
            step_id: str = "", request_id: str = "") -> SandboxResult:
    """Run one Opus-authored Python step. Raises `PythonRejected` on refusal or
    failure; the caller hands that to the failure packet builder unedited."""
    detected = probe()
    if not detected.available:
        raise PythonRejected(SANDBOX_UNAVAILABLE, unavailable_reason())

    limits = limits or SandboxLimits()
    inputs = inputs or {}
    digest = hashlib.sha256(code.encode()).hexdigest()[:16]

    exit_code, payload, stderr, elapsed = _launch(
        code=code, inputs=inputs, limits=limits, strategy=(
            detected.strategy,
            dict(STRATEGIES)[detected.strategy]), cancel=cancel)

    audit = {
        "request_id": request_id, "step_id": step_id,
        "code_sha256_16": digest, "code_bytes": len(code),
        "input_keys": sorted(inputs), "strategy": detected.strategy,
        "exit_code": exit_code, "elapsed_seconds": round(elapsed, 3),
        "limits": {"wall_seconds": limits.wall_seconds,
                   "cpu_seconds": limits.cpu_seconds,
                   "memory_mib": limits.memory_mib,
                   "output_bytes": limits.output_bytes},
        "guarantees": dict(detected.guarantees),
    }

    if exit_code == SETUP_FAILED:
        raise PythonRejected(
            SANDBOX_UNAVAILABLE,
            "The isolation could not be established for this step, so the "
            "code was not run. This is an operator-side condition, not "
            "something the analysis can be rewritten around.",
            detail=stderr[:2_000])

    if exit_code == CPU_EXHAUSTED:
        raise PythonRejected(
            RESOURCE_LIMIT,
            f"The Python step used its {limits.cpu_seconds} seconds of CPU "
            f"time and was stopped. Partial work is not returned.",
            detail=str(payload.get("error") or "")[:2_000])

    if exit_code == CODE_RAISED or (payload and
                                    payload.get("status") == "error"):
        trace = str(payload.get("error") or stderr)
        if "MemoryError" in trace:
            # The address-space limit, seen from inside. Naming it a resource
            # limit rather than a runtime error is a diagnostic distinction,
            # not a suggestion: it tells Opus the code was not wrong, it was
            # too large, which is a different thing to decide about.
            raise PythonRejected(
                RESOURCE_LIMIT,
                f"The Python step exhausted its {limits.memory_mib} MiB "
                f"address-space limit. The traceback is the interpreter's.",
                detail=trace[:8_000])
        raise PythonRejected(
            RUNTIME_ERROR,
            "The Python step raised. The traceback below is the interpreter's, "
            "reported exactly as it came back and not interpreted here.",
            detail=(payload.get("error") or stderr)[:8_000])

    if exit_code == NO_RESULT:
        raise PythonRejected(
            RUNTIME_ERROR,
            "The Python step completed without assigning `result` and without "
            "printing anything, so it produced nothing to carry forward.")

    if exit_code and exit_code < 0:
        signalled = -exit_code
        raise PythonRejected(
            RESOURCE_LIMIT,
            f"The Python step was killed by signal {signalled}. "
            + ("This is what exceeding the CPU limit looks like."
               if signalled == signal.SIGXCPU
               else "This is what exhausting the memory limit usually looks "
                    "like." if signalled == signal.SIGKILL
               else "The step did not complete."),
            detail=stderr[:2_000])

    if exit_code:
        raise PythonRejected(
            RUNTIME_ERROR,
            f"The Python step exited with status {exit_code} and produced no "
            f"usable result.", detail=stderr[:2_000])

    value = payload.get("result")
    columns: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    truncated = False
    if isinstance(value, dict) and "rows" in value:
        columns = [dict(c) if isinstance(c, dict)
                   else {"name": str(c), "type": "json"}
                   for c in value.get("columns") or []]
        produced = list(value.get("rows") or [])
        rows = produced
        if len(rows) > sql_mod.MAX_MODEL_ROWS:
            rows = rows[:sql_mod.MAX_MODEL_ROWS]
            truncated = True
            warnings.append(
                f"{len(produced)} rows were produced and the first "
                f"{sql_mod.MAX_MODEL_ROWS} are shown. This is a CLIPPED table, "
                f"not a complete aggregate: do not read a total off it.")

    stdout = (payload.get("stdout") or "")[:limits.output_bytes]
    audit["row_count"] = len(rows)
    audit["stdout_bytes"] = len(stdout)
    return SandboxResult(
        status="empty" if not rows and not stdout else "success",
        stdout=stdout, columns=columns, rows=rows, row_count=len(rows),
        truncated=truncated, elapsed_seconds=round(elapsed, 3),
        warnings=warnings, audit=audit)


__all__ = ["APPROVED_PACKAGES", "Probe", "PythonRejected", "SandboxLimits",
           "SandboxResult", "execute", "probe", "reset_probe",
           "unavailable_reason"]
