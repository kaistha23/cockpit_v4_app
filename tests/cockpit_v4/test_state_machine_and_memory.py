"""
UNIT. Pure unit tests: no model, no database, no runner.

The state-machine test reads the transition registry and proves the property
the design claims: every cycle consumes something finite. That is a static
property and a static test is the right evidence for it -- which is exactly
why the runtime bounds are ALSO tested against the live loop in
`test_protocol_and_bounds.py`. A graph labelled with counters is not proof
that the implementation increments them.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import memory
from backend.cockpit_v4 import states as st


def test_every_declared_edge_names_real_states():
    for edge in st.TRANSITIONS:
        assert edge.source in st.ALL_STATES or edge.source == "*"
        assert edge.target in st.ALL_STATES


def test_every_working_state_has_an_exit():
    for state in st.WORKING_STATES:
        assert st.edges_from(state), f"{state} is a dead end"


def test_terminal_states_have_no_outgoing_analytical_edge():
    for state in st.TERMINAL_STATES:
        specific = [e for e in st.TRANSITIONS if e.source == state]
        assert not specific, f"{state} is terminal and must not transition"


def test_every_cycle_consumes_something_finite():
    """The boundedness argument, checked rather than asserted in a comment."""
    returning = [e for e in st.TRANSITIONS
                 if e.target in st.WORKING_STATES and e.source != "*"
                 and st.WORKING_STATES.index(e.target)
                 <= st.WORKING_STATES.index(e.source)]
    assert returning, "the loop must exist for this test to mean anything"
    for edge in returning:
        assert edge.consumes, (
            f"{edge.source} -> {edge.target} ({edge.event}) closes a cycle "
            f"and declares no finite resource; that is an unbounded loop")


def test_error_codes_are_separate_from_states():
    assert not set(st.ERROR_CODES) & set(st.ALL_STATES)


def test_every_v3_terminal_maps_explicitly():
    """A STOPPED_* envelope must never be read as a success."""
    for v3_state, (state, code) in st.V3_TERMINAL_MAP.items():
        assert state in st.TERMINAL_STATES
        assert code == "" or code in st.ERROR_CODES
        if v3_state.startswith("STOPPED_") or v3_state in (
                "EXECUTION_FAILED", "PROVIDER_ERROR", "INTERNAL_ERROR",
                "CONTEXT_TOO_LARGE", "MODEL_UNAVAILABLE",
                "PROVIDER_CREDENTIAL_MISSING"):
            assert state not in (st.COMPLETED, st.PARTIAL), (
                f"{v3_state} is a stop and must not map to a success state")


# ---- memory ------------------------------------------------------------

def test_a_scalar_is_never_expanded_into_characters():
    """V4-AT-084. The V3 defect, refused at the contract."""
    assert memory.validate_string_list("exposure", field_name="terms") == \
        ["exposure"]


def test_single_letter_grades_are_not_rejoined_by_length(capsys):
    """V4-AT-085. 'B','B','B' is three ratings, not a damaged word."""
    grades = ["A", "B", "C", "D", "E"]
    assert memory.validate_string_list(
        grades, field_name="grades", schema_version="v4.1") == grades


def test_recovery_needs_provenance_not_a_guess():
    """Only a summary that declares the defective schema is recovered."""
    damaged = list("exposure")
    assert memory.validate_string_list(
        damaged, field_name="t", schema_version="v3.0") == ["exposure"]
    assert memory.validate_string_list(
        damaged, field_name="t", schema_version="v4.1") == damaged


def test_a_summary_cannot_overwrite_a_newer_one(store_db):
    """V4-AT-086. Compare-and-swap on coverage."""
    thread_id = store_db.create_thread(tenant_id="t", principal_id="p")
    assert store_db.put_summary(thread_id=thread_id, covered_through=10,
                                body={"a": 1}, schema_version="v4.1")
    assert not store_db.put_summary(thread_id=thread_id, covered_through=6,
                                    body={"a": 2}, schema_version="v4.1")
    assert store_db.get_summary(thread_id)["body"] == {"a": 1}


def test_memory_is_off_by_default_and_schedules_nothing(store_db, v4_config):
    """V4-AT-066. The answer path needs no second model."""
    thread_id = store_db.create_thread(tenant_id="t", principal_id="p")
    for i in range(20):
        store_db.append_turn(thread_id=thread_id, run_id=f"r{i}",
                             question="q", answer={"disposition": "answer"})
    assert memory.maybe_schedule(store_db, thread_id, cfg=v4_config) is False


def test_memory_without_a_configured_model_does_not_run(store_db, v4_config):
    from dataclasses import replace

    cfg = replace(v4_config, memory_enabled=True, memory_model="")
    thread_id = store_db.create_thread(tenant_id="t", principal_id="p")
    for i in range(20):
        store_db.append_turn(thread_id=thread_id, run_id=f"r{i}",
                             question="q", answer={"disposition": "answer"})
    assert memory.maybe_schedule(store_db, thread_id, cfg=cfg) is False
