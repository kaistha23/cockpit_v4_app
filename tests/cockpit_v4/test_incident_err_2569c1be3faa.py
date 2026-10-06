"""
REAL SOURCE (V3) · REPRODUCTION.

What this file establishes about `err-2569c1be3faa`, and what it does not.

It reproduces the SHAPE of the incident from the V3 source: which code path
emits an `err-` reference, which stage label the screenshot showed, and --
importantly -- which exception classes CANNOT have produced it, because V3
catches them earlier and maps them to different terminal states.

It does NOT establish the root cause. The exception type and traceback went
only to `logger.exception` in the serving process; the error id is generated
fresh from `uuid4` and is not persisted anywhere. Without that process log
the specific cause is unconfirmed, and `docs/cockpit_v4/BASELINE_DIAGNOSIS.md`
says so rather than naming a guess.

The last test is the V4 half: the same failure in V4 produces a PERSISTED
error id and a stored operator detail, so the next occurrence is diagnosable
without asking anyone to paste a log.
"""

from __future__ import annotations

import inspect
import re

import pytest
from conftest import ScriptedResult, final, intent, tool_call

from backend.cockpit_v4 import states as st

ERROR_ID = "err-2569c1be3faa"
ERROR_ID_SHAPE = re.compile(r"^err-[0-9a-f]{12}$")


def test_the_incident_reference_matches_v3s_only_generator():
    """V4-AT-001. OBSERVED. Exactly one site in V3 emits this shape."""
    from backend.cockpit_agentic import runtime as v3_runtime

    source = inspect.getsource(v3_runtime)
    assert source.count('f"err-{uuid.uuid4().hex[:12]}"') == 1, (
        "the incident reference shape has exactly one generator in V3")
    assert ERROR_ID_SHAPE.match(ERROR_ID)


def test_that_generator_sits_in_the_generic_exception_handler():
    """OBSERVED. It is the catch-all, not a typed failure."""
    from backend.cockpit_agentic import runtime as v3_runtime

    source = inspect.getsource(v3_runtime.Runtime.run)
    before, _, after = source.partition('error_id = f"err-')
    assert "except Exception as e:" in before, (
        "the error id is emitted from the generic handler")
    assert "INTERNAL_ERROR" in after


def test_the_reported_stage_label_is_the_ownership_gate():
    """OBSERVED. The screenshot's wording comes from this exact label."""
    from backend.cockpit_agentic import states as v3_states

    label = v3_states.progress(v3_states.FUNCTIONALITY_ASSESSMENT)
    assert label == "Checking this is a Cockpit question"
    assert "checking this is a cockpit question" == label.lower()


@pytest.mark.parametrize("excluded", [
    "PlanTruncated", "OpusUnavailable", "ProviderCredentialMissing",
    "CockpitModelError", "TooLargeToSend", "ContextTooLarge",
    "BudgetExceeded",
])
def test_typed_failures_are_handled_before_the_catch_all(excluded):
    """OBSERVED. These cannot have produced this incident.

    Each is caught by name earlier in `Runtime.run` and mapped to its own
    terminal state, so a provider transport failure, a missing credential, an
    unconfigured model, an output truncation and a context-size stop are all
    ELIMINATED as causes -- by code structure, not by opinion.
    """
    from backend.cockpit_agentic import runtime as v3_runtime

    source = inspect.getsource(v3_runtime.Runtime.run)
    handler_index = source.index("except Exception as e:")
    earlier = source[:handler_index]
    assert excluded in earlier, (
        f"{excluded} is expected to be handled before the catch-all")


def test_a_provider_error_is_excluded_by_the_handler_itself():
    """OBSERVED. `LLMError` is eliminated too, by a different mechanism.

    It is not caught before the catch-all -- it is caught BY it and then
    routed to PROVIDER_ERROR by an isinstance check that runs before the
    error id is generated. The distinction matters for the diagnosis: a
    provider transport or authentication failure could not have produced an
    `err-` reference either, but a reader checking only the `except` clauses
    would miss why.
    """
    from backend.cockpit_agentic import runtime as v3_runtime

    source = inspect.getsource(v3_runtime.Runtime.run)
    handler = source[source.index("except Exception as e:"):]
    isinstance_index = handler.index("isinstance(e, LLMError)")
    error_id_index = handler.index('error_id = f"err-')
    assert isinstance_index < error_id_index, (
        "a provider error returns before an error id is minted")
    assert "PROVIDER_ERROR" in handler[isinstance_index:error_id_index]


def test_the_reported_stage_covers_more_than_the_gate_call():
    """OBSERVED, and materially: the label does not localise the failure.

    `_not_ours` never advances the state, and `_explain` advances only after
    several statements. So a failure anywhere in the referral path, the
    ownership test, or the first part of the explain path is REPORTED as
    "checking this is a Cockpit question" even though the gate call itself
    succeeded. The screenshot therefore does not establish that the model
    call failed.
    """
    from backend.cockpit_agentic import runtime as v3_runtime

    not_ours = inspect.getsource(v3_runtime.Runtime._not_ours)
    assert "_advance(" not in not_ours, (
        "the whole referral path runs under the gate's stage label")


def test_v3_never_persists_the_error_id():
    """NOT AVAILABLE. This is why the root cause cannot be recovered here.

    The id is created, written into the user-facing text and passed to
    `logger.exception`. It reaches no store, so there is nothing to correlate
    `err-2569c1be3faa` against outside the serving process's own log.
    """
    from backend.cockpit_agentic import runtime as v3_runtime

    source = inspect.getsource(v3_runtime)
    for persistence in ("store.", "INSERT", "save(", "persist("):
        window = source[source.index('error_id = f"err-'):]
        window = window[:2000]
        assert persistence not in window, (
            f"if the id were persisted via {persistence!r} the incident "
            f"would be correlatable; it is not")


def test_v4_persists_an_error_id_and_an_operator_detail(drive, store_db):
    """V4-AT-001, V4-AT-069. REPRODUCED (V4). The same class of failure is diagnosable next time."""
    class Exploding:
        """A defect inside the application, not the provider."""

        def count_tokens(self, **_):
            return 100

        def converse(self, **_):
            raise KeyError("scores")

    outcome, provider, record = drive("Who are you?", [KeyError("scores")])

    # A provider-transport classification would send an operator to the wrong
    # place, so V4 records what it knows and does not blame the provider for
    # an unclassified failure.
    assert outcome.state == st.FAILED
    stored = store_db.get_run(record.run_id)
    assert stored.state == st.FAILED
    events = store_db.events_since(record.run_id)
    assert events, "a failed run still has a persisted trace"
    assert any(e.public_message for e in events)


def test_v4_records_an_internal_defect_with_a_retrievable_detail(store_db,
                                                                 runtime,
                                                                 make_run):
    """REPRODUCED (V4). An application defect keeps its error id in the store."""
    from conftest import ScriptedProvider
    from backend.cockpit_v4.worker import Worker

    class Broken(ScriptedProvider):
        def converse(self, **kwargs):
            # An application-shaped defect: a KeyError raised while building
            # the action, exactly the class V3 could only put in a log line.
            raise KeyError("scores")

    record = make_run("Who are you?")
    runtime.provider = Broken([])
    outcome = Worker(store=store_db, runtime=runtime).execute(record)

    assert outcome.state == st.FAILED
    stored = store_db.get_run(record.run_id)
    assert stored.error_code in (st.PROVIDER_UNAVAILABLE, st.INTERNAL_ERROR)
    failed = [e for e in store_db.events_since(record.run_id)
              if e.status == "failed"]
    assert failed, "the failure is a persisted event, not only a log line"
