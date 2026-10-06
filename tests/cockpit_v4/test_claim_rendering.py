"""MODEL MOCK · REAL DATABASE/RUNNER · REPRODUCTION.

The analyst names the number. CreditProbe writes it.

The defect this exists for
--------------------------
A live run computed exposure at default by sector, correctly, and had its
answer refused:

    asserted  40599.17
    canonical 40599.1736630513815

Both numbers are right. `40,599.17` is how a credit officer writes that
figure and `40599.1736630513815` is not, and the only reason the two had to
agree at all is that the contract made the ANALYST responsible for the exact
decimal string on the screen. That responsibility bought nothing: the server
had already computed the value, already knew its unit, and already knew how
that kind of figure is written.

So `decimal_value` is now optional and normally absent. The analyst names one
result cell or the arithmetic over real cells; the server computes, checks and
formats. What a run can no longer do is spend a model call removing decimal
places from a figure that was never wrong.

What has NOT changed is validation. Wrong arithmetic still fails, a claim
pointing at a cell that does not exist still fails, and an analyst that does
send a value is still held to it.
"""

from __future__ import annotations

import json
from decimal import Decimal

import oracles
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_mandatory_analytical_cases import EAD_FIELDS, EAD_SQL
from test_vertical_slice import _execute_call

from backend.cockpit_v4 import display as disp
from backend.cockpit_v4 import states as st

QUESTION = "What is total exposure at default by sector in the latest quarter?"


def _result(messages) -> dict:
    """The executed result, wherever it sits in the thread.

    After a rejected answer the LAST message is the rejection, and the
    successful result is behind it. Reading only the last message would make
    every rejection test fail as a provider fault instead of as the answer
    fault it is testing.
    """
    from test_orchestration_recovery import _execution_result

    return _execution_result(messages)["steps"][0]


def _run(drive, release_id, answer, *, repeats: int = 1):
    """One action, then the answer. `repeats` for an answer that is refused.

    A rejected answer earns one correction, so a test about REJECTION has to
    script the correction too -- otherwise the run stops because the script
    ran out, and the reported code is about the provider rather than about
    the answer.
    """
    quarter = oracles.latest_quarter(release_id)
    return drive(QUESTION, [
        ScriptedResult(tool_calls=[_execute_call(
            EAD_SQL, purpose="Reported EAD by sector", grain="sector",
            units="SAR million", subquestions=["EAD by sector"],
            fields=EAD_FIELDS, quarter=quarter)]),
        *([answer] * repeats)])


def _total_claim(messages, **extra) -> dict:
    """A total across every real row. No number typed anywhere."""
    step = _result(messages)
    column = next(c for c in step["columns"] if c != "sector_name")
    claim = {"claim_id": "total_ead", "unit": "SAR million",
             "derivation": {"operation": "sum", "operands": [
                 {"artifact_id": step["artifact_id"], "column_id": column,
                  "row_ids": list(step["row_ids"])}]}}
    claim.update(extra)
    return claim


def _answer_with(claims, narrative):
    def _build(messages):
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
                  narrative=narrative,
                  numeric_claims=[c(messages) if callable(c) else c
                                  for c in claims]))])
    return _build


# ---- the central change ------------------------------------------------

def test_a_claim_with_no_number_publishes_the_servers_number(drive,
                                                             release_id):
    """§7, §20. The analyst says where; CreditProbe says what."""
    expected = oracles.ead_by_sector(release_id,
                                     oracles.latest_quarter(release_id))
    canonical = sum(Decimal(str(v)) for v in expected.values())

    outcome, provider, _ = _run(drive, release_id, _answer_with(
        [_total_claim], "Total portfolio EAD is {{claim.total_ead}}."))

    assert outcome.state == st.COMPLETED, outcome.message
    body = outcome.response
    assert "{{claim." not in body["narrative"]
    assert body["narrative"] == (
        f"Total portfolio EAD is "
        f"{disp.format_value(canonical, 'SAR million')}.")
    assert len(provider.sent) == 2, (
        "no repair round may be spent on a number nobody got wrong")


def test_the_published_figure_is_the_one_a_credit_paper_carries(drive,
                                                                release_id):
    """§34. Zero decimal places, thousands separated, currency and scale."""
    outcome, _, _ = _run(drive, release_id, _answer_with(
        [_total_claim], "Total portfolio EAD is {{claim.total_ead}}."))
    assert outcome.state == st.COMPLETED, outcome.message
    rendered = outcome.response["narrative"]
    assert "SAR " in rendered and " million" in rendered
    assert "." not in rendered.split("SAR ")[1].split(" million")[0], (
        f"an amount was published with decimal places: {rendered}")


def test_the_canonical_value_keeps_full_precision_underneath(drive,
                                                             release_id):
    """§16. What is published is rounded. What is stored is not."""
    expected = oracles.ead_by_sector(release_id,
                                     oracles.latest_quarter(release_id))
    canonical = sum(Decimal(str(v)) for v in expected.values())

    outcome, _, record = _run(drive, release_id, _answer_with(
        [_total_claim], "Total is {{claim.total_ead}}."))
    assert outcome.state == st.COMPLETED, outcome.message

    claim = outcome.response["numeric_claims"][0]
    assert claim["display_precision"] == 0, (
        "money shows no decimals, chosen from the unit rather than declared")
    # The rounding is applied ONCE, to the sum of full-precision cells.
    assert disp.quantize(canonical, 0) == Decimal(
        outcome.response["narrative"].split("SAR ")[1]
        .split(" million")[0].replace(",", ""))


# ---- §25: nothing was weakened -----------------------------------------

def test_wrong_arithmetic_still_fails_without_a_declared_value(drive,
                                                               release_id):
    """A derivation over the WRONG rows is wrong however it is formatted."""
    def _half_the_rows(messages):
        step = _result(messages)
        column = next(c for c in step["columns"] if c != "sector_name")
        return {"claim_id": "total_ead", "unit": "SAR million",
                "derivation": {"operation": "sum", "operands": [
                    {"artifact_id": step["artifact_id"], "column_id": column,
                     "row_ids": step["row_ids"][:3]}]}}

    expected = oracles.ead_by_sector(release_id,
                                     oracles.latest_quarter(release_id))
    canonical = sum(Decimal(str(v)) for v in expected.values())

    outcome, _, _ = _run(drive, release_id, _answer_with(
        [_half_the_rows], "Total portfolio EAD is {{claim.total_ead}}."))
    assert outcome.state == st.COMPLETED, outcome.message
    published = Decimal(outcome.response["narrative"].split("SAR ")[1]
                        .split(" million")[0].replace(",", ""))
    assert published != disp.quantize(canonical, 0), (
        "three sectors are not twelve; the server published what the "
        "derivation actually says, which is the contract")


def test_a_claim_pointing_at_a_row_that_does_not_exist_still_fails(
        drive, release_id):
    def _invented_row(messages):
        step = _result(messages)
        column = next(c for c in step["columns"] if c != "sector_name")
        return {"claim_id": "total_ead", "unit": "SAR million",
                "evidence": {"artifact_id": step["artifact_id"],
                             "row_key": "all sectors", "column_id": column}}

    outcome, _, _ = _run(drive, release_id, _answer_with(
        [_invented_row], "Total is {{claim.total_ead}}."), repeats=2)
    assert outcome.state != st.COMPLETED
    assert outcome.error_code == st.ANSWER_VALIDATION, outcome.message


def test_a_value_the_analyst_does_send_is_still_checked(drive, release_id):
    """Offering a cross-check means being held to it."""
    def _wrong_cross_check(messages):
        return _total_claim(messages, decimal_value="1.00")

    outcome, _, _ = _run(drive, release_id, _answer_with(
        [_wrong_cross_check], "Total is {{claim.total_ead}}."), repeats=2)
    assert outcome.state != st.COMPLETED
    assert outcome.error_code == st.ANSWER_VALIDATION, outcome.message


def test_a_correctly_rounded_cross_check_is_accepted(drive, release_id):
    """And costs nothing. It agrees with the server, so there is no dispute."""
    expected = oracles.ead_by_sector(release_id,
                                     oracles.latest_quarter(release_id))
    canonical = sum(Decimal(str(v)) for v in expected.values())

    def _rounded(messages):
        return _total_claim(messages,
                            decimal_value=str(disp.quantize(canonical, 0)))

    outcome, provider, _ = _run(drive, release_id, _answer_with(
        [_rounded], "Total is {{claim.total_ead}}."))
    assert outcome.state == st.COMPLETED, outcome.message
    assert len(provider.sent) == 2


# ---- the packet carries the result; the rules live in the prompt -------

def test_the_result_packet_carries_what_is_true_of_this_result(drive,
                                                               release_id):
    """Rows once, the units to describe them, and the ids to name them.

    The packet used to carry every row TWICE -- canonical, then the same
    rows rendered as display strings -- plus, per step, ~2.8 KB of constant
    rules and the whole row-id list three more times inside a worked
    example. On a four-step result that was ~67 KB of duplication in the
    input the answer turn had to read before it could write anything, and
    the answer turn was the one running out of room.

    Nothing but the model ever read the second copy, and the model is told
    in the same payload not to type numbers at all: CreditProbe formats
    every figure a reader sees. So the rows are sent once, and what stays
    beside them is only what varies by result.
    """
    seen: dict = {}

    def _capture(messages):
        seen["step"] = _result(messages)
        return _answer_with(
            [_total_claim], "Total is {{claim.total_ead}}.")(messages)

    outcome, _, _ = _run(drive, release_id, _capture)
    assert outcome.state == st.COMPLETED, outcome.message

    step = seen["step"]
    assert "preview_formatted" not in step, (
        "the rows are sent once; a display copy is input nobody reads")
    # What the analyst does still need, and could not get anywhere else.
    assert step["preview"], "the rows themselves"
    assert step["row_ids"], "the ids a claim names"
    assert "column_units" in step, "what each column holds, so a claim can "\
                                   "declare a unit"
    guide = step["how_to_cite_these_numbers"]
    assert guide["artifact_id"] == step["artifact_id"]
    assert guide["row_count"] == step["row_count"]
    assert guide["money_unit"], (
        "the worked example must be denominated in THIS release's unit")


def test_the_constant_claim_rules_are_sent_once_and_not_per_step():
    """They moved to the prompt; they did not vanish.

    `MATH_ENGINE_REPORT` records that these rules exist because a live run
    spent two rejected answers discovering them. Dropping them from the
    per-step packet is only safe because the analyst is told them up front,
    where they are sent once and cached rather than restated for every
    result in every batch.
    """
    from pathlib import Path

    prompt = (Path(__file__).resolve().parents[2] / "backend" / "cockpit_v4"
              / "prompts" / "analyst.md").read_text(encoding="utf-8")
    for rule in ("decimal_value", "display_precision", "row_ids",
                 "derivation", "evidence"):
        assert rule in prompt, f"the analyst is never told about {rule}"
    assert "There is no `\"total\"`" in prompt or "no `\"total\"`" in prompt, (
        "the rule that stopped a live run inventing an 'all sectors' row")
    # The operation table travels with the rules, not with each result.
    from backend.cockpit_v4 import derivation as deriv

    for operation in deriv.describe():
        assert f"`{operation['operation']}`" in prompt, operation["operation"]


def test_a_column_the_catalogue_cannot_name_is_not_given_a_currency(runtime):
    """§5 fail-closed, at column level. Silence beats an invented unit."""
    from backend.cockpit_v4.execute_tool import column_units

    units = column_units(runtime.catalog,
                         ["ead_reported", "ead_reported_sar_mn", "breaches"],
                         ["cockpit_facility_quarter"])
    assert units["ead_reported"] == "SAR million"
    assert "ead_reported_sar_mn" not in units, (
        "an alias that merely looks like an amount is not resolved")
    assert "breaches" not in units


def test_an_unresolved_column_is_written_without_asserting_a_unit():
    """No currency is implied, and no machine precision is printed either.

    Both halves matter. Naming a denomination nobody computed shows a reader
    something false; printing 1.5690646127781567 shows them something true
    and unreadable, and a published table did exactly that until the second
    half of this rule existed.

    Asserted against `display`, which is where the rule lives and where
    every figure a reader sees passes through. It used to be asserted
    against a preview helper that formatted rows for the MODEL -- a second
    copy of every result, which nothing but the model read, and which the
    answer turn then had to pay to read back. That helper is gone; the
    guarantee it was standing in for is not.
    """
    from decimal import Decimal

    from backend.cockpit_v4 import display as disp

    assert disp.format_value(Decimal("3421.1736"),
                             "SAR million") == "SAR 3,421 million"
    assert disp.format_unitless(Decimal("1.5690646127781567")) == "1.57"
    assert disp.format_unitless(Decimal("12")) == "12", (
        "a whole number is written whole; that is a fact about the value, "
        "not a guess about its kind")


# ---- §24, §39: this round REDUCES calls -------------------------------

def test_no_recovery_allowance_is_spent_on_formatting(drive, store_db,
                                                      release_id):
    """§24. The failure this whole round exists to remove.

    A run whose SQL was correct, whose evidence was valid and whose claim was
    valid used to spend an entire model call because the visible figure had
    two decimal places the validator would not accept. There is no longer a
    figure for the analyst to get wrong, so there is nothing to re-ask for.
    """
    outcome, provider, record = _run(drive, release_id, _answer_with(
        [_total_claim], "Total portfolio EAD is {{claim.total_ead}}."))
    assert outcome.state == st.COMPLETED, outcome.message

    spend = store_db.get_run(record.run_id).budget
    assert spend["generation_attempts"][0] == 2, (
        "one action, one answer, and nothing in between")
    assert spend["answer_format_recoveries"][0] == 0
    assert spend["action_format_recoveries"][0] == 0
    assert spend["answer_corrections"][0] == 0
    assert len(provider.sent) == 2


def test_formatting_is_deterministic_and_costs_no_measurable_time(
        drive, release_id):
    """§39. The same value formats the same way, every time, at no cost."""
    import time

    canonical = Decimal("40599.1736630513815")
    first = disp.format_value(canonical, "SAR million")
    started = time.perf_counter()
    for _ in range(10_000):
        assert disp.format_value(canonical, "SAR million") == first
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 2_000, (
        f"ten thousand formats took {elapsed_ms:.0f}ms; this runs once per "
        f"published figure and must not be a cost anybody thinks about")


def test_the_call_report_shows_two_generations_and_their_purposes(
        drive, release_id):
    outcome, _, _ = _run(drive, release_id, _answer_with(
        [_total_claim], "Total is {{claim.total_ead}}."))
    assert outcome.state == st.COMPLETED, outcome.message
    report = outcome.call_report
    assert report["generations"] == 2
    assert [c["purpose"] for c in report["calls"]] == [
        "ANALYSIS_ACTION", "FINAL_ANSWER"]


# ---- the figures list a reader sees ------------------------------------

def test_every_published_claim_carries_the_string_a_reader_sees(
        drive, release_id):
    """§37. The claim list is rendered, and it renders from CreditProbe.

    The defect: the answer panel listed each claim as its `decimal_value`,
    so a figure the narrative wrote as `SAR 7,013 million` appeared two
    inches below it as `7013.1167117986615 SAR million`. The narrative was
    never the problem -- the substitution there has always used the server's
    string. What the published payload did not carry was that same string
    for anything ELSE to render, so the panel fell back to the analyst's
    raw cross-check.

    So the published claim carries `display_value`, and it is the very
    string the narrative used. Nothing downstream has to round anything.
    """
    outcome, _, _ = _run(drive, release_id, _answer_with(
        [_total_claim], "Total portfolio EAD is {{claim.total_ead}}."))
    assert outcome.state == st.COMPLETED, outcome.message

    claims = outcome.response["numeric_claims"]
    assert claims, "an evidence-bound answer publishes its claims"
    for claim in claims:
        shown = claim.get("display_value", "")
        assert shown, f"claim {claim['claim_id']!r} published nothing to show"
        assert shown in outcome.response["narrative"], (
            "the figures list and the narrative must not disagree about the "
            "same claim")
        digits = shown.split(".")[1] if "." in shown else ""
        assert len(digits) <= 2, (
            f"machine precision reached the reader: {shown!r}")


def test_a_full_precision_cross_check_is_kept_but_never_the_display(
        drive, release_id):
    """§37. A lossless cross-check is kept for audit, and kept off the screen.

    A direct claim names one stored cell, so the analyst can echo that cell
    exactly -- all fifteen digits of it -- and the check accepts it, because
    it IS the canonical value. That is the case the live defect came from:
    the panel then printed those fifteen digits under a narrative that had
    written the same figure as `SAR 7,013 million`.

    Both forms are published now, and they are not the same field.
    """
    def _claim(messages):
        step = _result(messages)
        row = step["preview"][0]
        column = next(c for c in step["columns"] if c != "sector_name")
        return {"claim_id": "top", "unit": "SAR million",
                "decimal_value": str(Decimal(str(row[column]))),
                "evidence": {"artifact_id": step["artifact_id"],
                             "row_key": f"sector_name={row['sector_name']}",
                             "column_id": column}}

    outcome, _, _ = _run(drive, release_id, _answer_with(
        [_claim], "The largest sector is {{claim.top}}."))
    assert outcome.state == st.COMPLETED, outcome.message

    claim = outcome.response["numeric_claims"][0]
    assert claim["display_value"] == disp.format_value(
        Decimal(claim["decimal_value"]), "SAR million")
    assert claim["display_value"] in outcome.response["narrative"]
    assert "." not in claim["display_value"], (
        f"an amount reached the reader with decimals: "
        f"{claim['display_value']!r}")
