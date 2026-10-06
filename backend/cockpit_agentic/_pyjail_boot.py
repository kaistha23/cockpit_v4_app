"""The bootstrap that runs INSIDE the isolation namespaces. Not an import.

`pysandbox.py` launches this file through `unshare`, with a JSON job
description as its only argument. Everything here happens before a single line
of model-authored Python runs:

    1. make the mount tree private, so nothing done here reaches the host;
    2. build a jail root on tmpfs and bind into it, read-only, only what the
       approved dependency surface needs;
    3. chroot into it, so the repository, /etc, the environment files and every
       other path on the host simply do not exist;
    4. apply the address-space, CPU, file-size and process limits;
    5. drop group and user privileges;
    6. only then execute the model's code, and serialize whatever it produced.

It is deliberately written against the standard library alone and is started
with `-I -E`, so no site directory, no PYTHONPATH and no environment
inherited from the API process can change what it does.

If any step of the setup fails, this file exits non-zero WITHOUT running the
code. There is no degraded mode: the alternative to isolation is not running.
"""

from __future__ import annotations

import ctypes
import io
import json
import os
import resource
import signal
import sys
import traceback

MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_REMOUNT = 32
MS_BIND = 4096
MS_REC = 16384
MS_PRIVATE = 1 << 18

SETUP_FAILED = 91
CODE_RAISED = 92
NO_RESULT = 93
CPU_EXHAUSTED = 94

_libc = ctypes.CDLL("libc.so.6", use_errno=True)


def _mount(source, target, fstype=None, flags=0, data=None) -> None:
    rc = _libc.mount(
        source.encode() if source else None, target.encode(),
        fstype.encode() if fstype else None, ctypes.c_ulong(flags),
        data.encode() if data else None)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"mount {source or fstype} -> {target}: "
                           f"{os.strerror(err)}")


def _bind_readonly(source: str, target: str, *, executable: bool) -> None:
    """Bind a host path into the jail and take write permission away.

    The remount is what actually makes it read-only: a bind mount inherits the
    source's flags, and asking for MS_RDONLY in the first call is silently
    ignored by the kernel.
    """
    if os.path.isdir(source):
        os.makedirs(target, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb"):
            pass
    _mount(source, target, None, MS_BIND | MS_REC)
    flags = MS_REMOUNT | MS_BIND | MS_REC | MS_RDONLY | MS_NOSUID | MS_NODEV
    if not executable:
        flags |= MS_NOEXEC
    _mount(None, target, None, flags)


def build_jail(job: dict) -> None:
    """Everything between the API process and the model's code."""
    root = job["jail_root"]

    # Nothing done here may propagate back to the host mount tree.
    _mount(None, "/", None, MS_REC | MS_PRIVATE)

    _mount("tmpfs", root, "tmpfs", MS_NOSUID | MS_NODEV,
           f"size={job['jail_tmpfs_mib']}m,mode=755")
    for entry in ("usr", "site", "proc", "dev", "tmp", "work"):
        os.makedirs(os.path.join(root, entry), exist_ok=True)

    # The standard library and the shared objects the interpreter loads, and
    # NOT /usr/bin or /usr/sbin. The interpreter itself was exec'd before the
    # chroot and is already mapped, so nothing needs a binary directory -- and
    # leaving those out is what makes "no shell" true rather than aspirational.
    # `ls`, `sh` and `env` are not absent by policy here; they are absent.
    for name in ("lib", "lib64"):
        source = "/usr/" + name
        if os.path.isdir(source) and not os.path.islink(source):
            _bind_readonly(source, os.path.join(root, "usr", name),
                           executable=True)

    # A merged-/usr layout resolves the stdlib through these names.
    for link, target in (("lib", "usr/lib"), ("lib64", "usr/lib64")):
        path = os.path.join(root, link)
        if not os.path.lexists(path) and os.path.exists(
                os.path.join(root, target)):
            os.symlink(target, path)

    # The approved dependency surface, one package at a time. The rest of the
    # site directory -- the provider SDK, the database drivers, the crypto
    # libraries -- is never mounted, so it cannot be imported.
    for name, source in job["approved_packages"].items():
        _bind_readonly(source, os.path.join(root, "site", name),
                       executable=True)

    _mount("proc", root + "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC)

    # Four character devices, and nothing else under /dev.
    _mount("tmpfs", root + "/dev", "tmpfs", MS_NOSUID | MS_NOEXEC,
           "size=1m,mode=755")
    for device in ("null", "zero", "urandom", "random"):
        target = os.path.join(root, "dev", device)
        with open(target, "wb"):
            pass
        _mount("/dev/" + device, target, None, MS_BIND)

    _mount("tmpfs", root + "/tmp", "tmpfs",
           MS_NOSUID | MS_NODEV | MS_NOEXEC,
           f"size={job['scratch_tmpfs_mib']}m,mode=1777")

    # The workspace: `in` read-only, `out` writable, nothing else.
    _mount(job["workspace"], root + "/work", None, MS_BIND)
    _bind_readonly(os.path.join(job["workspace"], "in"),
                   root + "/work/in", executable=False)

    # The jail's own skeleton is tmpfs, and a tmpfs directory is writable by
    # whoever owns it. Under a user namespace the code runs as uid 0 of that
    # namespace and would own these, so the read-only bind mounts underneath
    # are not by themselves enough: the directories holding them are closed
    # here too. The only writable paths left are /tmp and /work/out.
    for closed in ("", "/usr", "/site", "/dev"):
        try:
            os.chmod(root + closed, 0o555)
        except OSError:
            pass

    os.chroot(root)
    os.chdir("/work/out")


class CpuExhausted(Exception):
    """Raised in the main thread when the soft CPU limit is reached.

    Without this the kernel's hard limit kills the process outright and the
    only evidence left is an exit status, which reads as "something went
    wrong" rather than "this computation was too expensive". The distinction
    matters to whoever decides what to do next, so it is preserved.
    """


def _on_cpu_limit(_signum, _frame):
    raise CpuExhausted()


def apply_limits(job: dict) -> None:
    mib = 1024 * 1024
    signal.signal(signal.SIGXCPU, _on_cpu_limit)
    resource.setrlimit(resource.RLIMIT_AS,
                       (job["memory_mib"] * mib,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU,
                       (job["cpu_seconds"], job["cpu_seconds"] + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE,
                       (job["file_bytes"],) * 2)
    resource.setrlimit(resource.RLIMIT_NPROC,
                       (job["max_processes"],) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _in_user_namespace() -> bool:
    """True when this process is inside a non-initial user namespace.

    The initial namespace maps the whole uid range identically. Anything else
    means our uid 0 is somebody else's unprivileged uid on the host, which is
    the privilege separation itself -- there is nothing left to drop, and the
    setuid would fail anyway because the target uid is not mapped.
    """
    try:
        with open("/proc/self/uid_map") as handle:
            rows = [line.split() for line in handle.read().splitlines() if line]
    except OSError:
        return False
    return rows != [["0", "0", "4294967295"]]


def drop_privileges(job: dict) -> str:
    """Become an unprivileged user, or say why that was not needed."""
    if os.getuid() != 0:
        return "already unprivileged"
    if _in_user_namespace():
        return "user namespace: uid 0 here is an unprivileged uid on the host"
    uid = job["run_as_uid"]
    gid = job["run_as_gid"]
    try:
        os.setgroups([])
    except OSError:
        pass
    os.setgid(gid)
    os.setuid(uid)
    if os.getuid() == 0 or os.geteuid() == 0:
        raise RuntimeError("privileges were not dropped")
    return f"uid {uid}"


def _clean(value):
    """JSON-safe, and NaN becomes null rather than a token that reads as a
    number downstream."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _normalize(value):
    """Turn whatever the code produced into the same shape a SQL step returns:
    named, typed columns and records. Shape only -- nothing here interprets
    what the numbers mean."""
    if value is None:
        return None
    module = type(value).__module__.split(".")[0]
    if module == "pandas":
        frame = value
        if type(value).__name__ == "Series":
            frame = value.to_frame().reset_index()
        columns = [{"name": str(name), "type": str(dtype)}
                   for name, dtype in zip(frame.columns, frame.dtypes)]
        rows = [{str(k): _clean(v) for k, v in record.items()}
                for record in frame.to_dict(orient="records")]
        return {"columns": columns, "rows": rows}
    if module == "numpy":
        value = value.tolist()
    if isinstance(value, dict) and "rows" in value and "columns" in value:
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        names = []
        for record in value:
            for key in record:
                if key not in names:
                    names.append(key)
        return {"columns": [{"name": str(n), "type": "json"} for n in names],
                "rows": [{str(n): _clean(record.get(n)) for n in names}
                         for record in value]}
    if isinstance(value, list):
        return {"columns": [{"name": "value", "type": "json"}],
                "rows": [{"value": _clean(v)} for v in value]}
    if isinstance(value, dict):
        return {"columns": [{"name": str(k), "type": "json"} for k in value],
                "rows": [{str(k): _clean(v) for k, v in value.items()}]}
    return {"columns": [{"name": "value", "type": "json"}],
            "rows": [{"value": _clean(value)}]}


def main() -> int:
    job = json.loads(sys.argv[1])
    try:
        build_jail(job)
        apply_limits(job)
        privileges = drop_privileges(job)
    except Exception:                                        # noqa: BLE001
        sys.stderr.write("sandbox setup failed:\n" + traceback.format_exc())
        return SETUP_FAILED

    sys.path[:] = [p for p in sys.path if p and not p.startswith("/work")]
    sys.path.append("/site")

    inputs = {}
    for name in sorted(os.listdir("/work/in")):
        if name.endswith(".json"):
            with open(os.path.join("/work/in", name)) as handle:
                inputs[name[:-5]] = json.load(handle)

    with open("/work/in/code.py") as handle:
        source = handle.read()

    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    namespace = {"__name__": "__main__", "inputs": inputs, "result": None}
    status, error = "success", None
    try:
        exec(compile(source, "<opus-authored-step>", "exec"), namespace)
    except CpuExhausted:
        status = "cpu_exhausted"
        error = (f"The step used its {job['cpu_seconds']} seconds of CPU time "
                 f"and was stopped there.")
    except BaseException:                                    # noqa: BLE001
        status, error = "error", traceback.format_exc()
    finally:
        sys.stdout = real_stdout

    payload = {
        "status": status,
        "stdout": captured.getvalue()[:job["output_bytes"]],
        "error": error,
        "privileges": privileges,
        "result": None,
    }
    if status == "success":
        try:
            payload["result"] = _normalize(namespace.get("result"))
        except Exception:                                    # noqa: BLE001
            payload["status"] = "error"
            payload["error"] = ("`result` could not be serialized:\n"
                                + traceback.format_exc())

    with open("/work/out/result.json", "w") as handle:
        json.dump(payload, handle, default=str)

    if payload["status"] == "cpu_exhausted":
        return CPU_EXHAUSTED
    if payload["status"] != "success":
        return CODE_RAISED
    if payload["result"] is None and not payload["stdout"]:
        return NO_RESULT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
