"""UNIT, NO MODEL: two silent wrong-answer modes are caught before publication.

(A) ENTITY MISMATCH. The question names one entity ("Riyadh"), the server
resolves it to region=Riyadh, and the published headline number is read
from the Makkah row. The cell exists and the unit is right, so nothing else
objects. `Finalizer._check_entity_scope` compares the cited row's value of
the field to the resolved one, and stands down for comparisons.

(B) UNDISCLOSED AVERAGING. Governed plain averages are SQL AVG(col), so
every row counts equally; the bank's weighting convention is still pending.
`_check_average_disclosure` demands that the answer call such a figure a
simple, unweighted average, or ask. Both checks do nothing when their
optional inputs are absent, so legacy constructions are unchanged.

The checks are called directly over a tiny in-memory store: no model, no
database.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from backend.cockpit_v4.config import STANDARD_LIMITS
from backend.cockpit_v4.finalization import Finalizer

ROWS = [{"region": "Riyadh", "ead": 10, "avg_pd_pit_12m": 0.02},
        {"region": "Makkah", "ead": 20, "avg_pd_pit_12m": 0.05}]
COLUMNS = ["region", "ead", "avg_pd_pit_12m"]
RIYADH = ({"term": "Riyadh", "field": "region", "value": "Riyadh",
           "say": "Riyadh"},)


class _Store:
    def __init__(self, rows=ROWS, columns=COLUMNS):
        self.rec = {"rows": rows, "columns": columns}

    def get_artifact(self, artifact_id, tenant_id=None):
        return self.rec


def _finalizer(store=None, **kw):
    return Finalizer(store=store or _Store(), tenant_id="t",
                     release_id="rel", limits=STANDARD_LIMITS,
                     run_artifacts={"a1"}, **kw)


def _claim(row, column="ead"):
    return NS(claim_id="c1", is_derived=False, derivation=None,
              evidence=NS(artifact_id="a1", row_key=row, column_id=column))


def _final(*claims, narrative="x", limitations=(), assumptions=()):
    return NS(numeric_claims=claims, narrative=narrative,
              limitations=limitations, clarification_question="",
              intent=NS(resolved_assumptions=assumptions))


def test_claim_on_the_named_entity_row_passes():
    f = _finalizer(resolved_entities=RIYADH, question="EAD for Riyadh?")
    problems, warnings = f._check_entity_scope(_final(_claim("r0")))
    assert problems == [] and warnings == []


def test_riyadh_claim_plus_makkah_comparison_passes():
    f = _finalizer(resolved_entities=RIYADH, question="Why is EAD up in Riyadh?")
    final = _final(_claim("r0"), _claim("r1"))
    assert f._check_entity_scope(final) == ([], [])


def test_claim_on_another_entity_row_is_a_problem():
    f = _finalizer(resolved_entities=RIYADH, question="EAD for Riyadh?")
    problems, _ = f._check_entity_scope(_final(_claim("r1")))
    assert len(problems) == 1
    assert "region=Makkah" in problems[0] and "region=Riyadh" in problems[0]


def test_comparison_question_is_not_checked():
    f = _finalizer(resolved_entities=RIYADH,
                   question="Compare Riyadh versus the rest of regions")
    assert f._check_entity_scope(_final(_claim("r1"))) == ([], [])


def test_two_resolved_values_is_a_comparison():
    both = RIYADH + ({"term": "Makkah", "field": "region",
                      "value": "Makkah", "say": "Makkah"},)
    f = _finalizer(resolved_entities=both, question="EAD for Riyadh, Makkah")
    assert f._check_entity_scope(_final(_claim("r1"))) == ([], [])


def test_no_entity_column_means_no_problem():
    store = _Store(rows=[{"ead": 30}], columns=["ead"])
    f = _finalizer(store, resolved_entities=RIYADH, question="EAD Riyadh")
    assert f._check_entity_scope(_final(_claim("r0"))) == ([], [])


def test_unweighted_average_without_disclosure_is_a_problem():
    f = _finalizer(unweighted_measures=frozenset({"avg_pd_pit_12m"}))
    out = f._check_average_disclosure(
        _final(_claim("r0", "avg_pd_pit_12m"), narrative="PD is high"))
    assert len(out) == 1
    assert "simple (unweighted) average" in out[0] and "avg_pd_pit_12m" in out[0]


def test_disclosure_in_assumptions_or_narrative_passes():
    f = _finalizer(unweighted_measures=frozenset({"avg_pd_pit_12m"}))
    c = _claim("r0", "avg_pd_pit_12m")
    assert f._check_average_disclosure(
        _final(c, assumptions=("Reported as a Simple Average",))) == []
    assert f._check_average_disclosure(
        _final(c, narrative="an unweighted mean")) == []


def test_legacy_construction_is_unchanged():
    f = _finalizer()
    final = _final(_claim("r1", "avg_pd_pit_12m"))
    assert f._check_entity_scope(final) == ([], [])
    assert f._check_average_disclosure(final) == []
