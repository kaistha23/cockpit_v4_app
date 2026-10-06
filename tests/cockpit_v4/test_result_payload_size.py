"""
The result packet is input the answer turn has to pay for.

NO MODEL · UNIT. Nothing here calls a provider, scripted or otherwise: the
packet is constructed in memory and measured, because a byte count is a
property of the code, not of a run.

Every byte in a `tool_result` is read back on the answer turn, counted
against the context window, and billed. The live thread was cut off writing
its answer, and part of why is what it had to read first: a four-step,
100-row result measured ~116 KB, of which ~67 KB was the same rows a second
time and the same constant prose restated once per step.

This pins the shape after the shrink, in the same spirit as
`test_action_payload_snapshot.py` pins the request envelope: a bound, and
the reason it holds.
"""

from __future__ import annotations

import json

import answer_object as ao
import pytest

from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4.execute_tool import claim_guide

#: A four-step, 100-row batch at the preview cap. Measured before the
#: shrink at ~116 KB; the bound is set with room to move but not enough to
#: let a second copy of the rows back in.
MAX_FOUR_STEP_BYTES = 60_000

#: What one step's constant overhead may be, EXCLUDING its rows. Everything
#: that does not scale with the result: ids, digests, the guide. Before the
#: shrink this was ~18 KB a step; the rows themselves were ~9 KB.
MAX_PER_STEP_OVERHEAD_BYTES = 2_500


def _size(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str))


def test_a_four_step_result_fits_in_the_budget_it_is_read_under():
    batch = ao.four_step_tool_result()
    size = _size(batch)
    assert size <= MAX_FOUR_STEP_BYTES, (
        f"a four-step result packet measures {size:,} B; the answer turn "
        f"reads all of it before writing a word")


def test_the_rows_are_sent_once():
    """The single biggest win, and the one most likely to come back."""
    step = ao.four_step_tool_result()["steps"][0]
    assert "preview_formatted" not in step
    assert step["preview"], "the canonical rows are what a claim resolves to"
    # And nothing else in the block is row-shaped.
    rows = _size(step["preview"])
    overhead = _size(step) - rows - _size(step["row_ids"])
    assert overhead <= MAX_PER_STEP_OVERHEAD_BYTES, (
        f"{overhead:,} B of per-step overhead beside {rows:,} B of rows")


def test_the_guide_carries_what_varies_and_nothing_that_does_not():
    """Per-result facts here; the constant rules are in the prompt.

    `money_unit` is why the examples stay: an example denominated in the
    wrong currency steers the analyst into declaring the wrong unit on every
    amount, which is the release-isolation failure the argument exists to
    prevent.
    """
    ids = ao.row_ids()
    guide = claim_guide("art-x", ["sector", "ead_sar_mn"], ids, ao.ROWS,
                        "SAR million")

    assert guide["artifact_id"] == "art-x"
    assert guide["row_count"] == ao.ROWS
    assert guide["money_unit"] == "SAR million"
    assert guide["example_total"]["unit"] == "SAR million"

    # The constant rules moved out.
    for gone in ("you_do_not_type_numbers", "direct_value",
                 "calculated_value", "how_numbers_are_written", "never",
                 "operations"):
        assert gone not in guide, f"{gone} is constant; it belongs in the prompt"

    # And the whole row-id list is not restated three more times.
    assert "row_ids" not in guide, (
        "the packet publishes row_ids at top level; the guide copied them, "
        "and then both worked examples copied them again")
    body = json.dumps(guide, ensure_ascii=False)
    assert body.count('"r99"') <= 1, (
        f"the guide repeats the row list: {len(body):,} B")
    assert len(body) <= 1_200, f"the guide measures {len(body):,} B"


def test_the_guide_shows_both_ways_of_naming_cells():
    """Slimmer is not vaguer. The examples must be runnable as written.

    And there are two of them for a reason: the model copies the example it
    is handed, so an example that lists a hundred row ids teaches the model
    to list a hundred row ids. A total is usually over everything and says
    so; a share's numerator usually is not, and names its rows.
    """
    ids = ao.row_ids()
    guide = claim_guide("art-x", ["sector", "ead_sar_mn"], ids, ao.ROWS,
                        "SAR million")

    total = guide["example_total"]["derivation"]["operands"][0]
    assert total["rows"] == "all", "the worked total spans the whole result"
    assert "row_ids" not in total, "an operand says it one way, not both"

    part, whole = guide["example_share"]["derivation"]["operands"]
    assert part["row_ids"], "an example with no rows teaches nothing"
    assert set(part["row_ids"]) <= set(ids), "an example names rows that exist"
    assert whole["rows"] == "all", "a share is measured against everything"
    assert "r0" in str(guide), "the first row id is the one to pattern from"


def test_a_clipped_result_says_so_where_the_analyst_will_read_it():
    """The sentence used to be false.

    It quoted the QUERY's row count while `row_ids` held only the preview's,
    so a clipped result told the analyst there were five thousand rows
    addressed "r0" upward when there were a hundred. That is also the
    difference between a total and a total over the part that fitted.
    """
    ids = ao.row_ids()
    clipped = claim_guide("art-x", ["sector", "ead_sar_mn"], ids, 5_000,
                          "SAR million", False)
    assert clipped["complete"] is False
    said = clipped["row_ids_are"]
    assert f"{len(ids)} of them" in said, said
    assert "CLIPPED" in said and "5000 rows were produced" in said, said

    whole = claim_guide("art-x", ["sector", "ead_sar_mn"], ids, ao.ROWS,
                        "SAR million", True)
    assert whole["complete"] is True
    assert "CLIPPED" not in whole["row_ids_are"]


@pytest.mark.parametrize(
    "limits", (config_mod.STANDARD_LIMITS, config_mod.DEEP_LIMITS),
    ids=lambda lim: lim.mode)
def test_the_preview_cap_still_bounds_the_packet(limits):
    """The bound above is meaningless if the row count is unbounded."""
    assert limits.preview_rows == ao.ROWS, (
        "answer_object measures at the cap; if the cap moved, so must it")
    assert limits.preview_columns > 0
