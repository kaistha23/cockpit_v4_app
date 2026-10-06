"""
UNIT. No model, no database, no runner.

The browser renders the process trace from NAMED SSE frames. A frame whose
name has no listener is silently discarded — which is how a run with fifteen
correctly persisted events rendered as "Answered in 0s" with every stage still
"not started".

So the two lists have to agree, and a new backend event type must fail this
test rather than fail quietly in a browser.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.cockpit_v4 import events as ev

ROOT = Path(__file__).resolve().parents[2]
CLIENT_TS = ROOT / "frontend" / "src" / "components" / "cockpit-v4" / "client.ts"

_EVENT_TYPES_BLOCK = re.compile(
    r"export const EVENT_TYPES = \[(.*?)\] as const;", re.S)


def _client_event_types() -> list[str]:
    match = _EVENT_TYPES_BLOCK.search(CLIENT_TS.read_text(encoding="utf-8"))
    assert match, "the client must declare EVENT_TYPES"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_the_client_listens_for_every_event_the_backend_emits():
    client = set(_client_event_types())
    backend = set(ev.EVENT_TYPES)
    missing = sorted(backend - client)
    assert not missing, (
        f"the backend emits {missing} and the browser has no listener for "
        f"them. Named SSE frames without a listener are DISCARDED, so these "
        f"events would never reach the process panel.")


def test_the_client_does_not_listen_for_events_that_do_not_exist():
    client = set(_client_event_types())
    backend = set(ev.EVENT_TYPES)
    stray = sorted(client - backend)
    assert not stray, (
        f"the client listens for {stray}, which the backend never emits")


def test_the_order_is_kept_in_step_so_a_diff_is_readable():
    assert _client_event_types() == list(ev.EVENT_TYPES), (
        "keep the two lists in the same order; a reordered copy makes a real "
        "divergence hard to see in review")


def test_every_emitted_frame_carries_its_event_name():
    """The property that makes the listener list necessary."""
    event = ev.Event(run_id="run-1", seq=3, event_type=ev.MODEL_REQUESTED,
                     stage="understanding", operation="generate",
                     status=ev.STATUS_STARTED,
                     public_message="Understanding the request")
    frame = event.to_sse()
    assert "event: model.requested\n" in frame, (
        "frames are named; that is why the client needs a listener per name")
    assert frame.startswith("id: 3\n"), "the id is what a reconnect resumes from"


def test_the_settled_frame_is_named_too():
    """`run.settled` is synthesised by the route, not by `Event`."""
    from backend.cockpit_v4 import routes

    source = (ROOT / "backend" / "cockpit_v4" / "routes.py").read_text(
        encoding="utf-8")
    assert 'event: run.settled' in source
    client = CLIENT_TS.read_text(encoding="utf-8")
    assert 'addEventListener("run.settled"' in client
