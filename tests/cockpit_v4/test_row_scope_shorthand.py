"""
An operand that says "every row" instead of reciting them.

MODEL MOCK · REAL DATABASE/RUNNER. No paid provider call.

Two thirds of what a correct four-step answer had to write was row ids. A
`sum` over a hundred-row result named a hundred rows, eight operands did
that across four steps, and the analyst spent most of its output reciting
the result back to the server that had just sent it. The live thread was
cut off mid-answer; this is the last of the four things that made the
object as large as it was.

`rows: "all"` is that shorthand. What it is NOT is a relaxation of the rule
it sits next to. Explicit ids exist because a live run, given none,
invented `"all sectors"` and `"top 4 sectors"` and was refused twice. That
refusal is still here, and the first thing this file asserts is that it
survived: an invented label is refused whichever field it arrives in.

  same number, two spellings   -> "all" recomputes to the enumerated value
  an invented label            -> still refused, in either field
  a clipped result             -> refused, because a part is not the whole
  a null under "all"           -> refused, with advice it can act on
  the bound on cell references -> enforced after expansion, not before
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.cockpit_v4 import derivation as deriv
from backend.cockpit_v4.config import STANDARD_LIMITS

SECTORS = [
    {"sector_name": "Information Technology", "ead_sar_mn": "5231.58",
     "weight": "2.0"},
    {"sector_name": "Real Estate", "ead_sar_mn": "4102.10", "weight": "1.0"},
    {"sector_name": "Manufacturing", "ead_sar_mn": "3980.25", "weight": "3.0"},
    {"sector_name": "Construction", "ead_sar_mn": "2411.77", "weight": "1.0"},
]
TOTAL = sum(Decimal(r["ead_sar_mn"]) for r in SECTORS)
IDS = [deriv.row_id_for(i) for i in range(len(SECTORS))]
COLUMNS = ["sector_name", "ead_sar_mn", "weight"]


@pytest.fixture
def make_artifact(store_db, release_id):
    """One stored result, with the completeness record a real step writes."""
    def _make(rows=None, *, complete: bool = True, produced: int | None = None,
              columns=None):
        rows = SECTORS if rows is None else rows
        scope = {"complete": complete,
                 "produced_rows": len(rows) if produced is None else produced}
        if complete is None:
            scope = {}
        return store_db.put_artifact(
            run_id="run-live", tenant_id="demo-tenant", kind="result",
            release_id=release_id, scope=scope,
            columns=list(columns or COLUMNS), rows=list(rows))
    return _make


@pytest.fixture
def finalizer(store_db, release_id):
    from backend.cockpit_v4.finalization import Finalizer

    def _make(*artifact_ids):
        return Finalizer(store=store_db, tenant_id="demo-tenant",
                         release_id=release_id, limits=STANDARD_LIMITS,
                         run_artifacts=set(artifact_ids))
    return _make


def whole(artifact_id: str, column: str = "ead_sar_mn") -> dict:
    return {"artifact_id": artifact_id, "column_id": column, "rows": "all"}


def named(artifact_id: str, ids, column: str = "ead_sar_mn") -> dict:
    return {"artifact_id": artifact_id, "column_id": column,
            "row_ids": list(ids)}


def compute(body: dict, *artifact_ids: str, store, tenant="demo-tenant"):
    """Recompute against every artifact named, so a refusal that is ABOUT
    two artifacts is not masked by one of them being unresolvable."""
    records = {a: store.get_artifact(a, tenant_id=tenant)
               for a in artifact_ids}
    return deriv.compute(deriv.parse(body), records, label="claim 'x'")


# ---- the same number, two spellings -------------------------------------

def test_every_row_computes_what_naming_every_row_computes(make_artifact,
                                                           store_db):
    """THE point. A shorthand that changed the arithmetic would be a new
    operation wearing the name of an old one."""
    art = make_artifact()
    spelled_out = compute({"operation": "sum", "operands": [named(art, IDS)]},
                          art, store=store_db)
    shorthand = compute({"operation": "sum", "operands": [whole(art)]},
                        art, store=store_db)
    assert shorthand == spelled_out
    assert Decimal(shorthand) == TOTAL


def test_a_share_against_the_whole_result_is_accepted(make_artifact,
                                                      store_db):
    """The shape the saving is actually for: a named numerator over an
    unnamed denominator."""
    art = make_artifact()
    value = compute({"operation": "percentage", "operands": [
        named(art, IDS[:2]), whole(art)]}, art, store=store_db)
    expected = (sum(Decimal(r["ead_sar_mn"]) for r in SECTORS[:2])
                / TOTAL * 100)
    assert Decimal(value) == pytest.approx(expected)


def test_a_part_outside_its_whole_is_still_refused(make_artifact, store_db):
    """`share_of_total`'s subset check is not weakened by the denominator
    being unnamed -- it is only made trivially true when the whole really is
    everything. A part from another artifact still fails."""
    art, other = make_artifact(), make_artifact()
    with pytest.raises(deriv.DerivationError) as caught:
        compute({"operation": "share_of_total", "operands": [
            named(other, IDS[:1]), whole(art)]}, art, other, store=store_db)
    assert "part of its own whole" in str(caught.value)


def test_a_weighted_average_may_use_it_on_both_sides(make_artifact,
                                                     store_db):
    """Both operands expand to the same rows in the same order, which is
    what the operation requires and what stored order guarantees."""
    art = make_artifact()
    value = compute({"operation": "weighted_average", "operands": [
        whole(art), whole(art, "weight")]}, art, store=store_db)
    weighted = sum(Decimal(r["ead_sar_mn"]) * Decimal(r["weight"])
                   for r in SECTORS)
    assert Decimal(value) == pytest.approx(
        weighted / sum(Decimal(r["weight"]) for r in SECTORS))


def test_a_weighted_average_over_reordered_rows_is_still_refused(
        make_artifact, store_db):
    """Expansion is in STORED order, so it cannot be used to sneak past the
    rule that values and weights must line up."""
    art = make_artifact()
    with pytest.raises(deriv.DerivationError) as caught:
        compute({"operation": "weighted_average", "operands": [
            whole(art), named(art, list(reversed(IDS)), "weight")]},
            art, store=store_db)
    assert "same rows in the same order" in str(caught.value)


# ---- the guarantee the live failure bought ------------------------------

def test_an_invented_row_label_is_still_refused(make_artifact, store_db):
    """The rule the shorthand sits next to, and does not touch.

    A run given no ids invented `"all sectors"` and was refused twice. The
    shorthand is a different FIELD; `row_ids` still means real ids, and the
    word "all" inside it is still a row this artifact does not contain.
    """
    art = make_artifact()
    with pytest.raises(deriv.DerivationError) as caught:
        compute({"operation": "sum",
                 "operands": [named(art, ["all sectors"])]}, art, store=store_db)
    assert "all sectors" in str(caught.value)

    with pytest.raises(deriv.DerivationError) as caught:
        compute({"operation": "sum", "operands": [named(art, ["all"])]},
                art, store=store_db)
    assert "'all'" in str(caught.value) or '"all"' in str(caught.value)


# ---- exactly one of the two forms ---------------------------------------

def test_an_operand_says_it_one_way_and_not_both(make_artifact, store_db):
    art = make_artifact()
    operand = {**whole(art), "row_ids": IDS}
    with pytest.raises(deriv.DerivationError) as caught:
        deriv.parse({"operation": "sum", "operands": [operand]})
    assert "both 'rows' and 'row_ids'" in str(caught.value)


def test_an_operand_that_names_no_cells_is_refused(make_artifact):
    """One of the two branches that had no coverage before this round."""
    art = "art-x"
    for empty in ([], None, "r0"):
        with pytest.raises(deriv.DerivationError) as caught:
            deriv.parse({"operation": "sum", "operands": [
                {"artifact_id": art, "column_id": "ead_sar_mn",
                 "row_ids": empty}]})
        assert "names no cells" in str(caught.value), empty


def test_rows_takes_one_word_and_says_which(make_artifact):
    with pytest.raises(deriv.DerivationError) as caught:
        deriv.parse({"operation": "sum", "operands": [
            {"artifact_id": "art-x", "column_id": "ead_sar_mn",
             "rows": "everything"}]})
    assert "'all'" in str(caught.value)
    assert "row_ids" in str(caught.value), "say what to do instead"


# ---- a part is not the whole --------------------------------------------

def test_a_clipped_result_may_not_be_called_every_row(make_artifact,
                                                      store_db):
    """A SQL result past the preview cap is clipped BEFORE it is stored, and
    the engine already warns that a clipped table is not a complete
    aggregate. Enumerating r0..r99 at least made the partial total visible;
    "every row" would not.
    """
    art = make_artifact(complete=False, produced=5_000)
    with pytest.raises(deriv.DerivationError) as caught:
        compute({"operation": "sum", "operands": [whole(art)]}, art, store=store_db)
    said = str(caught.value)
    assert "5,000 rows" in said and "4 of them were published" in said, said
    assert "row_ids" in said, "say what to do instead"


def test_the_rows_that_did_arrive_may_still_be_named(make_artifact,
                                                     store_db):
    """The refusal is about the word, not about the result. A claim over the
    rows that are there is still a claim about real cells."""
    art = make_artifact(complete=False, produced=5_000)
    value = compute({"operation": "sum", "operands": [named(art, IDS)]},
                    art, store=store_db)
    assert Decimal(value) == TOTAL


def test_an_artifact_that_never_recorded_its_scope_is_refused(make_artifact,
                                                             store_db):
    """Fail closed. An artifact written before this was recorded cannot say
    how much of its result it holds, and a total over an unknown fraction is
    the thing this gate exists to prevent."""
    art = make_artifact(complete=None)
    with pytest.raises(deriv.DerivationError) as caught:
        compute({"operation": "sum", "operands": [whole(art)]}, art, store=store_db)
    assert "does not record whether it holds its whole result" in str(
        caught.value)


# ---- nulls, and the bound -----------------------------------------------

def test_a_null_under_every_row_says_what_can_be_done_about_it(
        make_artifact, store_db):
    """A null is still not zero.

    The old advice -- "exclude the row and say so" -- is not available to an
    operand that says "every row", so the refusal offers the move that is:
    name the rows, leaving that one out.
    """
    rows = [dict(SECTORS[0]), {"sector_name": "Unknown", "ead_sar_mn": None,
                               "weight": "1.0"}]
    art = make_artifact(rows)
    with pytest.raises(deriv.DerivationError) as caught:
        compute({"operation": "sum", "operands": [whole(art)]}, art, store=store_db)
    said = str(caught.value)
    assert "NULL" in said and "not zero" in said
    assert "name the rows you mean in 'row_ids'" in said, said


def test_the_cell_bound_is_enforced_after_the_shorthand_expands(
        make_artifact, store_db):
    """`parse` cannot count what it cannot see.

    The bound on how many cells one claim may reference is checked at parse
    time over the ids it was given -- and a shorthand gives it none. The
    authoritative check is after resolution, which is the only place the
    number is known.
    """
    big = [{"sector_name": f"s{i}", "ead_sar_mn": "1.00", "weight": "1.0"}
           for i in range(deriv.MAX_REFS + 1)]
    art = make_artifact(big)
    with pytest.raises(deriv.DerivationError) as caught:
        compute({"operation": "sum", "operands": [whole(art)]}, art, store=store_db)
    said = str(caught.value)
    assert f"{deriv.MAX_REFS + 1:,} cells" in said, said
    assert "as a table" in said


def test_naming_too_many_rows_is_still_refused_before_the_database(
        make_artifact):
    """The other branch with no coverage before this round. The parse-time
    check still earns its place: it refuses a nine-hundred-id list without
    reading an artifact at all."""
    with pytest.raises(deriv.DerivationError) as caught:
        deriv.parse({"operation": "sum", "operands": [
            {"artifact_id": "art-x", "column_id": "ead_sar_mn",
             "row_ids": [f"r{i}" for i in range(deriv.MAX_REFS + 1)]}]})
    assert f"at most {deriv.MAX_REFS}" in str(caught.value)


# ---- through the validator, not only the algebra ------------------------

def test_a_published_claim_may_be_written_the_short_way(make_artifact,
                                                        store_db, finalizer):
    """End to end through `Finalizer.validate`: parsed, artifact-checked,
    unit-checked, recomputed and rendered."""
    from conftest import final, intent
    from backend.cockpit_v4.contracts import parse_final

    art = make_artifact()
    body = final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="Total exposure is {{claim.total}}.",
        numeric_claims=[{"claim_id": "total", "unit": "SAR million",
                         "derivation": {"operation": "sum",
                                        "operands": [whole(art)]}}])
    report = finalizer(art).validate(parse_final(body), executed=True)
    assert report.ok, report.problems
    assert report.claim_values["total"] == "SAR 15,726 million"


def test_the_stored_answer_keeps_the_form_the_analyst_sent(make_artifact,
                                                           store_db,
                                                           finalizer):
    """The raw derivation is what is persisted, so whatever reads a stored
    answer later has to understand this form. Pinned so a reader that
    assumes `row_ids` is always present fails here rather than in a
    transcript."""
    from conftest import final, intent
    from backend.cockpit_v4.contracts import parse_final

    art = make_artifact()
    parsed = parse_final(final(
        intent=intent("DATA_ANALYSIS", "COCKPIT"),
        narrative="Total exposure is {{claim.total}}.",
        numeric_claims=[{"claim_id": "total", "unit": "SAR million",
                         "derivation": {"operation": "sum",
                                        "operands": [whole(art)]}}]))
    operand = parsed.numeric_claims[0].to_dict()["derivation"]["operands"][0]
    assert operand["rows"] == "all"
    assert "row_ids" not in operand
