"""Unit tests for the clarification gate's inputs.

NO MODEL. No run, no provider.

`semantics.ambiguous_methods_in` reads the QUESTION only; `readiness()` now
says what the gate will weigh; `clarify_gate.grounds()` combines them.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import clarify_gate
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import semantics as sem
from backend.cockpit_v4.model_capabilities import traits_for


@pytest.mark.parametrize("question,expected", [
    ("compare between PD, LGD & CCF and tell me what is most dominant",
     ["attribution_order"]),
    ("Which of PD and LGD contributes most to the ECL change?",
     ["attribution_order"]),
    ("What is the average LGD by sector?", ["average_weighting"]),
    ("mean coverage ratio for Stage 3", ["average_weighting"]),
    ("What is driving Stage 2 and ECL growth?", []),
    ("What is the EAD-weighted average PD?", []),
    ("What is the simple average LGD by sector?", []),
    ("Split EAD and PD by sector", []),
    ("What is driving PD and LGD up?", []),
    ("Sequential attribution of PD, LGD and CCF to the ECL change", []),
    ("What is the PD of the top ten borrowers?", []),   # one factor, no method
    ("Total ECL by sector", []),
    ("Show me the average balance", []),                # no ratio-like word
    ("", []),
])
def test_ambiguous_methods_in(question, expected):
    assert [m["method"] for m in sem.ambiguous_methods_in(question)] == expected


def test_a_method_carries_options_and_a_reason():
    (found,) = sem.ambiguous_methods_in("average LGD")
    assert len(found["options"]) == 2 and found["why"]


@pytest.mark.parametrize("domain", [dom.CORPORATE, dom.RETAIL])
def test_readiness_clarification_basis(domain):
    book = arun.for_domain(domain)
    plain = sem.readiness(book.catalog, "What is driving Stage 2 and ECL growth?")
    assert "clarification_basis" not in plain
    assert "clarification_not_needed" in plain

    method = sem.readiness(
        book.catalog, "compare PD, LGD & CCF and tell me what is dominant")
    assert method["clarification_basis"] == {"methods": ["attribution_order"]}
    assert "clarification_not_needed" not in method

    term = sem.readiness(book.catalog, "what is exposure")
    assert term["clarification_basis"] == {"terms": ["exposure"]}
    assert "clarification_not_needed" not in term


def test_mode_defaults_and_unknown_values_are_shadow(monkeypatch):
    monkeypatch.delenv(clarify_gate.ENV, raising=False)
    assert clarify_gate.mode() == "shadow"
    monkeypatch.setenv(clarify_gate.ENV, "bogus")
    assert clarify_gate.mode() == "shadow"
    for value in clarify_gate.MODES:
        monkeypatch.setenv(clarify_gate.ENV, value.upper())
        assert clarify_gate.mode() == value


def test_sampling_control_is_limited_to_the_listed_families():
    for model in ("claude-opus-4-6", "claude-sonnet-4-6", "claude-sonnet-4-5",
                  "claude-haiku-4-5-20251001"):
        assert traits_for(model).sampling_control, model
    for model in ("claude-opus-5", "claude-opus-4-8", "mock-analyst",
                  "unknown-model"):
        assert not traits_for(model).sampling_control, model
    # Everything else about the families that gained it is unchanged.
    assert traits_for("claude-opus-4-6").effort_control is True
    assert traits_for("claude-haiku-4-5").effort_control is False


def test_temperature_is_sent_only_when_the_registry_allows_it(monkeypatch):
    from types import SimpleNamespace
    from backend.cockpit_v4.provider import Analyst

    def temperature(allowed):
        fake = SimpleNamespace(capability=SimpleNamespace(
            supports_sampling_control=allowed))
        return Analyst._temperature(fake)

    monkeypatch.delenv("COCKPIT_V4_TEMPERATURE", raising=False)
    assert temperature(False) is None
    assert temperature(True) == 0.0
    monkeypatch.setenv("COCKPIT_V4_TEMPERATURE", "0.3")
    assert temperature(True) == 0.3
    monkeypatch.setenv("COCKPIT_V4_TEMPERATURE", "junk")
    assert temperature(True) == 0.0


def test_two_ambiguous_terms_do_not_crash_readiness(monkeypatch):
    """`sorted` over the term dicts used to raise TypeError for two terms."""
    book = arun.for_domain(dom.CORPORATE)
    registry = {**sem._AMBIGUOUS_BY_DOMAIN,
                "corporate": {**sem._AMBIGUOUS_BY_DOMAIN["corporate"],
                              "limit": ("limit_sar_mn", "approved_limit")}}
    monkeypatch.setattr(sem, "_AMBIGUOUS_BY_DOMAIN", registry)
    ready = sem.readiness(book.catalog, "exposure and limit by sector")
    terms = [t["term"] for t in ready["terms_still_needing_a_decision"]]
    assert terms == ["exposure", "limit"]


def test_a_non_latin_question_is_never_called_unneeded():
    book = arun.for_domain(dom.CORPORATE)
    question = "ما هو التعرض في Real Estate؟"
    assert sem.non_latin(question) and not sem.non_latin("exposure, café")
    ready = sem.readiness(book.catalog, question)
    assert "clarification_not_needed" not in ready
    assert clarify_gate.grounds(
        question=question, readiness=ready, value_resolution={},
        envelope=None, final=None) == ["non_latin_question"]


def test_a_measure_carried_from_the_thread_does_not_make_a_question_unneeded():
    book = arun.for_domain(dom.CORPORATE)
    ready = sem.readiness(book.catalog, "just the top five by that measure",
                          already_asked=["total ECL by sector"])
    assert ready["sufficient"] is True
    found = clarify_gate.grounds(
        question="just the top five by that measure", readiness=ready,
        value_resolution={}, envelope=None, final=None)
    assert found == ["no_measure_in_this_question"]
