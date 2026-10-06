"""
The Python capability: an isolated runner, or an honest UNAVAILABLE.

This module deliberately has no in-process fallback. `exec` in the worker
process would have the provider credential, the state database and the whole
filesystem in reach; an AST allowlist does not change that, because the
allowlist is a supplement to a boundary, not the boundary.

So: if a subprocess jail can be established with the limits this deployment
requires, Python steps run in it. If it cannot -- and on a stock macOS
checkout without a container it usually cannot -- `probe()` reports
`available: False` and every Python step fails with PYTHON_UNAVAILABLE. A UAT
that reports "Python passed" while the analyst only ever wrote SQL is not
evidence of a Python capability, and this is where that distinction is kept.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

#: Set true only by a deployment that has established a real jail.
ENABLE_VAR = "COCKPIT_V4_PYTHON_RUNNER"

_FORBIDDEN_IMPORTS = (
    "os", "sys", "socket", "subprocess", "shutil", "pathlib", "ctypes",
    "importlib", "requests", "httpx", "urllib", "http", "multiprocessing",
    "threading", "pickle", "marshal", "builtins", "__builtin__")
_FORBIDDEN_NAMES = ("eval", "exec", "compile", "open", "__import__",
                    "globals", "locals", "vars", "getattr", "setattr")


def probe() -> dict[str, Any]:
    """Report the Python capability truthfully, including why it is off."""
    if os.environ.get(ENABLE_VAR, "").strip().lower() not in {
            "1", "true", "yes", "on"}:
        return {"available": False, "reason": (
            f"{ENABLE_VAR} is not enabled in this runtime. Python analysis "
            f"is reported UNAVAILABLE rather than run in this process."),
            "escape_self_test": "not run"}
    if not hasattr(resource, "setrlimit"):
        return {"available": False,
                "reason": "this platform provides no resource limits.",
                "escape_self_test": "not run"}
    result = self_test()
    return {"available": bool(result.get("ok")),
            "reason": result.get("reason", ""),
            "escape_self_test": result.get("detail", "")}


def self_test() -> dict[str, Any]:
    """Prove the jail refuses what it claims to refuse, before trusting it."""
    attempts = {
        "network": "import socket; socket.socket().connect(('1.1.1.1', 80))",
        "filesystem": "open('/etc/passwd').read()",
        "subprocess": "import subprocess; subprocess.run(['id'])",
        "environment": "import os; print(os.environ)",
    }
    blocked = []
    for name, code in attempts.items():
        outcome = _spawn(code, deadline_seconds=5.0, memory_mib=128,
                         output_bytes=64_000, payload={})
        if outcome.get("ok"):
            return {"ok": False,
                    "reason": f"the jail did not block {name} access.",
                    "detail": f"{name}: NOT BLOCKED"}
        blocked.append(name)
    return {"ok": True, "reason": "",
            "detail": "blocked: " + ", ".join(blocked)}


class PythonRunner:
    """Runs one submitted step in a bounded subprocess. Never in-process."""

    def __init__(self) -> None:
        self.available = bool(probe().get("available"))

    def validate(self, code: str) -> str:
        """A cheap pre-check. NOT the security boundary -- the jail is."""
        import ast

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return f"the Python did not parse: {exc}"
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([a.name.split(".")[0] for a in node.names]
                         if isinstance(node, ast.Import)
                         else [(node.module or "").split(".")[0]])
                bad = [n for n in names if n in _FORBIDDEN_IMPORTS]
                if bad:
                    return (f"importing {bad} is not available to analysis "
                            f"code. Use the supplied inputs.")
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
                return (f"{node.id} is not available to analysis code.")
        return ""

    def run(self, *, code: str, parameters: dict[str, Any],
            inputs: dict[str, str], deadline_seconds: float,
            memory_mib: int, output_bytes: int) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "error_code": "PYTHON_UNAVAILABLE",
                    "failed_check": "sandbox",
                    "message": ("no isolated Python runner is available in "
                                "this runtime.")}
        return _spawn(code, deadline_seconds=deadline_seconds,
                      memory_mib=memory_mib, output_bytes=output_bytes,
                      payload={"parameters": parameters, "inputs": inputs})


_BOOT = r'''
import json, sys, resource, os
limits = json.loads(sys.argv[1])
resource.setrlimit(resource.RLIMIT_AS,
                   (limits["memory"], limits["memory"]))
resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
resource.setrlimit(resource.RLIMIT_FSIZE, (limits["output"], limits["output"]))
resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu"], limits["cpu"]))
payload = json.loads(sys.stdin.read())
namespace = {"parameters": payload.get("parameters", {}),
             "inputs": payload.get("inputs", {}), "result": None}
source = payload["code"]
exec(compile(source, "<analysis>", "exec"), namespace, namespace)
result = namespace.get("result")
rows, columns = [], []
if isinstance(result, list):
    rows = result
    columns = sorted({k for r in rows if isinstance(r, dict) for k in r})
elif isinstance(result, dict):
    rows = [result]
    columns = sorted(result)
print("\x1e" + json.dumps({"rows": rows, "columns": columns}, default=str))
'''


def _spawn(code: str, *, deadline_seconds: float, memory_mib: int,
           output_bytes: int, payload: dict[str, Any]) -> dict[str, Any]:
    limits = {"memory": memory_mib * 1024 * 1024, "output": output_bytes,
              "cpu": max(1, int(deadline_seconds))}
    body = json.dumps({**payload, "code": code})
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
           "HOME": tempfile.gettempdir()}
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _BOOT, json.dumps(limits)],
            input=body, capture_output=True, text=True, env=env,
            timeout=max(1.0, deadline_seconds), start_new_session=True,
            cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error_code": "SQL_RUNTIME",
                "failed_check": "runtime",
                "message": (f"the Python step was terminated after "
                            f"{deadline_seconds:.0f} seconds.")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_code": "PYTHON_UNAVAILABLE",
                "failed_check": "sandbox", "message": str(exc)[:200]}
    if completed.returncode != 0:
        return {"ok": False, "error_code": "SQL_RUNTIME",
                "failed_check": "runtime",
                "message": (completed.stderr or "").strip()[-500:]
                or "the Python step exited non-zero."}
    marker = completed.stdout.rfind("\x1e")
    if marker < 0:
        return {"ok": False, "error_code": "SQL_RUNTIME",
                "failed_check": "runtime",
                "message": ("the step produced no `result`. Assign a list of "
                            "dicts to `result`.")}
    try:
        parsed = json.loads(completed.stdout[marker + 1:])
    except json.JSONDecodeError as exc:
        return {"ok": False, "error_code": "SQL_RUNTIME",
                "failed_check": "runtime", "message": str(exc)[:200]}
    return {"ok": True, "rows": parsed.get("rows") or [],
            "columns": parsed.get("columns") or [], "warnings": []}


__all__ = ["ENABLE_VAR", "PythonRunner", "probe", "self_test"]
