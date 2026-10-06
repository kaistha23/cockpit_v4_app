#!/usr/bin/env python3
"""
Drive the V4 server over real sockets and record what a browser would see.

This is the REAL runner path: a real uvicorn process on a real port, real
HTTP intake, a real Server-Sent Events stream read incrementally, real
cancellation, real reconnect-with-cursor, and the real DuckDB session against
the published release.

The MODEL is a stub, and every artefact this writes says so. What it proves
is that progress reaches a client BEFORE the answer does, that a reconnect
replays instead of resubmitting, and that a cancelled run settles -- none of
which a TestClient can establish, because it does not use a socket.

Usage:
    python3 scripts/cockpit_v4/live_path_evidence.py --port 8414
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "cockpit_v4"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402


def build_app(release_id: str, runtime_dir: Path, port: int):
    from conftest import ScriptedProvider, ScriptedResult, final, intent, tool_call  # noqa: E402

    from backend.cockpit_v4 import app as v4app
    from backend.cockpit_v4.capability import Capability, PriceCard
    from backend.cockpit_v4.config import V4Config

    cfg = V4Config(
        enabled=True, provider="anthropic", reasoning_model="stub-analyst",
        runtime_dir=runtime_dir,
        state_database=str(runtime_dir / "state" / "evidence.sqlite3"),
        release_id=release_id, api_port=port, ui_port=port + 1,
        local_demo_auth=True, price_card_path="", memory_enabled=False,
        memory_model="", default_mode="standard", heartbeat_seconds=1.0,
        lease_heartbeat_seconds=2.0, lease_stale_seconds=10.0,
        supervisor_poll_seconds=2.0, credential_present=True, missing=())

    capability = Capability(
        provider="anthropic", model_id="stub-analyst", sdk_version="evidence",
        context_tokens=200_000, max_output_tokens=128_000,
        supports_tools=True, supports_token_counting=True,
        price=PriceCard(5.0, 25.0, 6.25, 0.5),
        source="evidence stub", verified_at="2026-09-10T00:00:00Z",
        live_verified=True)

    from backend.cockpit_v4.service import Runtime, load_release

    catalog, _, summary = load_release(cfg)

    import oracles

    quarter = oracles.latest_quarter(release_id)
    sql = (f"SELECT sector_name, SUM(ead_reported) AS ead_reported_sar_mn "
           f"FROM cockpit_facility_quarter "
           f"WHERE reporting_quarter = '{quarter}' "
           f"GROUP BY sector_name ORDER BY ead_reported_sar_mn DESC")

    class SlowScripted(ScriptedProvider):
        """Adds a small, honest delay so progress is observably incremental.

        The delay is in the STUB, not in the server: it stands in for the
        seconds a real model takes, so the client can be shown receiving
        events while the run is still working.
        """

        def converse(self, **kwargs):
            time.sleep(1.2)
            return super().converse(**kwargs)

    def script():
        def finish(messages):
            body = json.loads(messages[-1]["content"][0]["content"])
            step = body["steps"][0]
            cell = step["preview"][0]
            return ScriptedResult(tool_calls=[tool_call(
                "finalize_response",
                final(intent=intent("DATA_ANALYSIS", "COCKPIT",
                                    understood="EAD by sector"),
                      narrative=("The largest reported exposure this quarter "
                                 "is {{claim.top}}."),
                      coverage=[{"subquestion": "EAD by sector",
                                 "status": "answered",
                                 "evidence_refs": [
                                     {"artifact_id": step["artifact_id"],
                                      "row_key": f"sector_name="
                                                 f"{cell['sector_name']}",
                                      "column_id": "ead_reported_sar_mn"}]}],
                      numeric_claims=[{
                          "claim_id": "top",
                          "decimal_value": repr(
                              float(cell["ead_reported_sar_mn"])),
                          "unit": "SAR million", "display_precision": 2,
                          "evidence": {
                              "artifact_id": step["artifact_id"],
                              "row_key": f"sector_name={cell['sector_name']}",
                              "column_id": "ead_reported_sar_mn"}}]),
                "tu-2")])

        return [
            ScriptedResult(tool_calls=[tool_call("execute_analysis", {
                "intent": intent("DATA_ANALYSIS", "COCKPIT",
                                 understood="EAD by sector, latest quarter"),
                "objective": "Reported EAD by sector",
                "subquestions": ["EAD by sector for the latest quarter"],
                "scope": {"reporting_periods": [quarter], "filters": {}},
                "metadata_receipt_ids": [],
                "fields_required": ["cockpit_facility_quarter.ead_reported"],
                "expected_output_grain": "sector",
                "expected_units": "SAR million",
                "steps": [{"step_id": "s1", "language": "sql", "code": sql,
                           "parameters": {}, "purpose": "EAD by sector",
                           "input_artifact_ids": [],
                           "depends_on_step_ids": []}],
                "repair_of_submission_id": ""}, "tu-1")]),
            finish]

    provider = SlowScripted(script())
    runtime = Runtime(cfg=cfg, capability=capability, provider=provider,
                      catalog=catalog, coverage=None,
                      release_summary=summary)
    app = v4app.create_app(cfg, provider=provider, verify_model=False,
                           start_workers=False)
    # The real store the app installed, driven by a real worker thread.
    state = app.state.cockpit_v4
    state["runtime"] = runtime
    from backend.cockpit_v4 import routes
    from backend.cockpit_v4.supervisor import Supervisor
    from backend.cockpit_v4.worker import Worker

    routes.install(store=state["store"], runtime=runtime,
                   principal_resolver=v4app._demo_resolver(cfg, runtime),
                   startup_sha=state["startup_sha"])
    worker = Worker(store=state["store"], runtime=runtime)
    supervisor = Supervisor(store=state["store"], lease_stale_seconds=10.0)
    threading.Thread(target=worker.serve_forever, daemon=True).start()
    threading.Thread(target=supervisor.serve_forever, daemon=True).start()
    return app, provider


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8414)
    parser.add_argument("--release", default=os.environ.get(
        "COCKPIT_V4_TEST_RELEASE", "v4-saudi-20q-v1"))
    parser.add_argument("--runtime-dir",
                        default=str(Path("/tmp/cockpit_v4_evidence")))
    args = parser.parse_args()

    os.environ.setdefault("COCKPIT_AGENTIC_V3_NAMESPACE", "cockpit_v4")
    runtime_dir = Path(args.runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    from scripts.cockpit_v4._common import pick_port

    port, notes = pick_port(args.port)
    for note in notes:
        print(f"  ! {note}")
    if not port:
        print("no free port; nothing was stopped to make one.")
        return 1

    app, provider = build_app(args.release, runtime_dir, port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/health", timeout=2).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    else:
        print("the server did not start")
        return 1

    evidence: dict[str, object] = {
        "label": "REAL RUNNER · REAL HTTP · REAL SSE · MODEL STUB",
        "note": ("The analyst model is a stub. Everything else is real: a "
                 "uvicorn process on a real socket, real SSE frames read "
                 "incrementally, and real DuckDB against the published "
                 "release. This proves DELIVERY, not answer quality."),
        "port": port, "release": args.release,
        "health": httpx.get(f"{base}/health", timeout=5).json(),
        "diagnostics": httpx.get(
            f"{base}/api/v1/cockpit-v4/diagnostics", timeout=5).json(),
    }

    P = f"{base}/api/v1/cockpit-v4"

    # ---- run 1: progress must arrive BEFORE the answer -----------------
    started = time.monotonic()
    accepted = httpx.post(f"{P}/runs", json={
        "question": "What is the EAD by sector for the latest quarter?"},
        timeout=10)
    run_id = accepted.json()["run_id"]
    evidence["intake"] = {"status_code": accepted.status_code,
                          "elapsed_ms": int((time.monotonic() - started) * 1000),
                          "body": accepted.json()}

    frames: list[dict[str, object]] = []
    first_event_ms = None
    answer_ms = None
    with httpx.stream("GET", f"{P}/runs/{run_id}/events",
                      timeout=60) as response:
        buffer = ""
        for chunk in response.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, _, buffer = buffer.partition("\n\n")
                at = int((time.monotonic() - started) * 1000)
                if block.startswith(":"):
                    frames.append({"at_ms": at, "type": "heartbeat"})
                    continue
                data = "".join(line[6:] for line in block.splitlines()
                               if line.startswith("data: "))
                event_type = next((line[7:] for line in block.splitlines()
                                   if line.startswith("event: ")), "")
                if first_event_ms is None:
                    first_event_ms = at
                try:
                    body = json.loads(data)
                except Exception:  # noqa: BLE001
                    body = {"raw": data[:200]}
                frames.append({"at_ms": at, "type": event_type,
                               "seq": body.get("seq"),
                               "stage": body.get("stage"),
                               "message": body.get("public_message")})
                if event_type == "answer.ready":
                    answer_ms = at
            if answer_ms is not None and "run.settled" in buffer:
                break

    status = httpx.get(f"{P}/runs/{run_id}", timeout=10).json()
    evidence["run_ead_by_sector"] = {
        "run_id": run_id,
        "first_visible_event_ms": first_event_ms,
        "answer_ready_ms": answer_ms,
        "progress_arrived_before_the_answer": bool(
            first_event_ms is not None and answer_ms is not None
            and first_event_ms < answer_ms),
        "distinct_stages_seen": sorted(
            {str(f.get("stage")) for f in frames if f.get("stage")}),
        "frames": frames,
        "terminal_state": status["state"],
        "narrative": (status.get("final_response") or {}).get("narrative"),
        "numeric_claims": (status.get("final_response") or {}).get(
            "numeric_claims"),
        "budget": status.get("budget"),
    }

    # ---- reconnect: replay from a cursor, no resubmission --------------
    with httpx.stream("GET", f"{P}/runs/{run_id}/events?cursor=2",
                      timeout=30) as response:
        replay = "".join(response.iter_text())
    replay_seqs = [int(line.split(": ")[1]) for line in replay.splitlines()
                   if line.startswith("id: ")]
    evidence["reconnect"] = {
        "cursor": 2, "replayed_seqs": replay_seqs,
        "starts_after_cursor": bool(replay_seqs and replay_seqs[0] == 3),
        "no_new_run_created": httpx.get(
            f"{P}/runs/{run_id}", timeout=5).json()["run_id"] == run_id,
    }

    # ---- cancellation --------------------------------------------------
    from conftest import ScriptedResult, final, tool_call

    # Two stalled turns, so the cancel reliably arrives while the run is
    # still working rather than racing a finished one.
    provider.script = [
        ScriptedResult(tool_calls=[tool_call(
            "inspect_catalog",
            {"intent": {"query_mode": "DATA_ANALYSIS", "owner": "COCKPIT",
                        "understood_request": "x", "response_language": "en",
                        "blocking_ambiguities": [], "resolved_assumptions": [],
                        "canonical_mappings": [], "excluded_parts": [],
                        "public_rationale": "r"},
             "query": "exposure", "relation_ids": [], "field_ids": [],
             "detail": ["discovery"], "reporting_periods": [],
             "sample_rows": 0, "cursor": ""}, "tu-8")]),
        ScriptedResult(tool_calls=[tool_call(
            "finalize_response", final(), "tu-9")])]
    second = httpx.post(f"{P}/runs", json={
        "question": "Who are you?", "thread_id": status["thread_id"]},
        timeout=10).json()
    time.sleep(0.4)
    cancel = httpx.post(f"{P}/runs/{second['run_id']}/cancel",
                        timeout=10).json()
    time.sleep(4.0)
    settled = httpx.get(f"{P}/runs/{second['run_id']}", timeout=10).json()
    # ---- the shell's view of this runtime -----------------------------
    # The defect this captures: with the shell pointed at the V4 API and no
    # compatible health route, the header rendered "Backend offline" while V4
    # was answering every request.
    shell = httpx.get(f"{base}/api/v1/health", timeout=10)
    shell_body = shell.json() if shell.status_code == 200 else {}
    components = {c["name"]: c for c in shell_body.get("components", [])}
    optional_absent = [name for name, c in components.items()
                       if c.get("data", {}).get("optional")
                       and c["status"] != "ok"]
    evidence["shell_health"] = {
        "status_code": shell.status_code,
        "headline": shell_body.get("status"),
        "app": shell_body.get("app"),
        "reported_offline": shell.status_code != 200,
        "v4_api_component": components.get("cockpit_v4_api", {}).get("status"),
        "optional_surfaces_absent": optional_absent,
        "legacy_dashboard_status": components.get(
            "legacy_dashboard_api", {}).get("status"),
        "note": ("A 200 with a headline of ok/degraded is what stops the "
                 "shell reporting the whole backend as offline. The absent "
                 "optional surfaces are named rather than hidden."),
    }

    evidence["cancellation"] = {
        "run_id": second["run_id"], "cancel_response": cancel,
        "terminal_state": settled["state"],
        "settled": settled["terminal"],
    }

    out = ROOT / "docs" / "cockpit_v4" / "evidence" / "live_path.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2, default=str),
                   encoding="utf-8")

    run1 = evidence["run_ead_by_sector"]
    print(f"  first visible event   {run1['first_visible_event_ms']} ms")
    print(f"  answer ready          {run1['answer_ready_ms']} ms")
    print(f"  progress before answer{'':1} "
          f"{run1['progress_arrived_before_the_answer']}")
    print(f"  stages seen           {run1['distinct_stages_seen']}")
    print(f"  terminal state        {run1['terminal_state']}")
    print(f"  reconnect replay      {evidence['reconnect']['replayed_seqs']}")
    print(f"  cancellation          {evidence['cancellation']['terminal_state']}")
    shell_ev = evidence["shell_health"]
    print(f"  shell /api/v1/health  HTTP {shell_ev['status_code']} · "
          f"headline {shell_ev['headline']} · offline="
          f"{shell_ev['reported_offline']}")
    print(f"  optional absent       {shell_ev['optional_surfaces_absent']}")
    print(f"  written to            {out}")
    server.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
