"""REAL DATABASE · MODEL MOCK · UNIT. What is actually sent on an action.

§6, §17. The two questions that failed on the Mac, measured -- system bytes,
tool-schema bytes, seed bytes, conversation bytes, counted tokens -- and
asserted against bounds rather than described.

Why bounds and not a golden file
--------------------------------
A byte-for-byte snapshot of a 45-kilobyte request fails on every wording
change and teaches a reader nothing about why it moved. What matters is the
shape: the action turn carries the governed measures it needs to author SQL,
does NOT carry the product pack or the answer contract, names this book's
release and calendar once, asks the analyst to restate nothing, and is
bounded in what it may cost.

The numbers in the bounds are the measured ones with headroom, so a change
that doubles the action payload fails here and a change that trims it does
not.
"""

from __future__ import annotations

import json

import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import context as ctx_mod
from backend.cockpit_v4 import contracts as contracts_mod
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake

import test_mac_action_replay as mac

#: The measured ceiling for one analytical ACTION request, in bytes on the
#: wire.
#:
#: The plain question measures 52.1KB and the seeded one 60.1KB, of which
#: the analysis packet is a few hundred bytes -- the thing that stops a
#: seeded thread going to look for its own relation, segment value and
#: periods, and worth every byte it costs. Both were about 8KB larger
#: before the compaction round: the product pack and the answer half of
#: `finalize_response` came off.
#:
#: RE-BASELINED IN THE FIRST-LIVE-UAT REPAIR, by 142 measured bytes.
#:
#: The bound stood at 52,000 against a measured 51,926 -- seventy-four
#: bytes of headroom -- and H-LIVE-03 needs one rule ON THE ACTION TURN,
#: because the action turn is where the decision it governs is made:
#:
#:     "Once you can name the measure, dimension, book and period a data
#:      question means, RUN IT -- never ask permission to do what was just
#:      asked."
#:
#: The live analyst understood a typo-heavy data question completely and
#: then published PRODUCT_HELP / unsupported with an offer to proceed if
#: the reader said "go ahead". Opening the tool surface (`action_state`)
#: makes that possible; this sentence is what makes it expected. It was
#: paid for where it could be: the policy rule the same round needed moved
#: into `context.POLICY_RULE`, carried with the pack on the answer turn, so
#: it costs the action turn nothing. What is left is 142 bytes, and the
#: numbers below are the measurement rather than a round figure with room
#: in it: the headroom is 32 bytes, as tight as the 74 it replaces.
#:
#: Raised by 520 bytes, deliberately, for the clarification gate: one generic
#: rule in `analyst.md` (a driver question runs the usual breakdown; "A and
#: B" means both, ~290 bytes) and `clarification_not_needed` in the readiness
#: block (~200 bytes). Both exist so the analyst stops asking about questions
#: whose every term maps -- a round trip costs the reader far more than 520
#: bytes. Measured 52,555; the headroom is 65 bytes.
MAX_ACTION_BYTES = 52_620
#: A seeded thread pays for its seed. Bounded separately so the allowance
#: for one cannot quietly become the allowance for the other.
MAX_SEEDED_ACTION_BYTES = 60_590  # same +490 as above; measured 60,527
#: The tool schemas alone. The full contract is ~16.8KB; the action set is
#: ~8.6KB because the answer half of `finalize_response` is not on it.
MAX_ACTION_TOOL_BYTES = 10_000


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    att.clear_cache()
    arun.reset()
    yield
    arun.reset()


def _size(obj) -> int:
    if isinstance(obj, str):
        return len(obj.encode("utf-8"))
    return len(json.dumps(obj, ensure_ascii=False, default=str).encode())


def _packet(question: str, *, investigation=None):
    book = arun.for_domain(dom.CORPORATE)
    principal = {"tenant_id": lake.DEFAULT_TENANT, "user_id": "u1",
                 "roles": ["analyst"]}
    packet = ctx_mod.build(
        question=question, principal=principal,
        scope=book.read_scope(principal), catalog=book.catalog,
        limits=config_mod.ANALYTICAL_STANDARD_LIMITS, mode="standard",
        release_summary=book.release_summary(), investigation=investigation,
        session=book.session, analytical=True)
    tools = contracts_mod.provider_tools(
        catalog=book.catalog, stage="analytical_action",
        withhold=(contracts_mod.TOOL_PRODUCT,))
    return packet, tools


def _measure(question: str, *, investigation=None) -> dict[str, int]:
    packet, tools = _packet(question, investigation=investigation)
    system = _size(packet.system_blocks)
    return {
        "system": system,
        "instruction": _size(packet.system_blocks[0]),
        "facts": _size(packet.system_blocks[1]),
        "volatile": _size(packet.system_blocks[-1]),
        "tools": _size(tools),
        "conversation": _size(packet.first_user_message),
        "total": system + _size(tools) + _size(packet.first_user_message),
    }


# ---- §6. the measurement, per failing question -------------------------

def test_the_stage2_action_payload_is_within_its_bound():
    sizes = _measure(mac.STAGE2)
    assert sizes["total"] <= MAX_ACTION_BYTES, sizes
    assert sizes["tools"] <= MAX_ACTION_TOOL_BYTES, sizes


def test_the_seeded_action_payload_is_within_its_bound(store_db):
    _, card = mac.seeded_construction_run(store_db)
    sizes = _measure(mac.CONSTRUCTION, investigation=card)
    assert sizes["total"] <= MAX_SEEDED_ACTION_BYTES, sizes
    # The seed is the point of a seeded thread and it is small: the whole
    # analysis packet costs a few hundred bytes and removes every reason
    # this thread would have to go looking for the relation, the segment
    # value or the two periods.
    plain = _measure(mac.CONSTRUCTION)
    seed_bytes = sizes["conversation"] - plain["conversation"]
    assert 0 < seed_bytes <= 9_000, (
        f"the seed added {seed_bytes:,} bytes to the conversation")


def test_the_seeded_packet_carries_what_a_query_needs():
    """§6. Domain, segment, period, comparison, metric, fields, drilldowns."""
    book = arun.for_domain(dom.CORPORATE)
    scope = book.read_scope({"tenant_id": lake.DEFAULT_TENANT,
                             "user_id": "u1", "roles": ["analyst"]})
    assert scope.domain_id == dom.CORPORATE


# ---- §17. what the request says, and what it does not ------------------

def test_the_action_request_names_this_book_once_and_correctly(store_db):
    packet, _ = _packet(mac.STAGE2)
    pinned = json.loads(packet.system_blocks[-1]["text"])["pinned_scope"]
    assert pinned["domain"] == dom.CORPORATE
    assert pinned["release_id"] == dom.DEFAULT_RELEASES[dom.CORPORATE]
    assert pinned["reporting_frequency"] == "quarterly"
    assert pinned["latest_populated_quarter"].endswith("Q2")
    assert "reporting_months" not in pinned, (
        "a quarterly book must not be handed a monthly period key")


def test_the_action_request_carries_no_product_pack():
    facts = json.loads(_packet(mac.STAGE2)[0].system_blocks[1]["text"])
    assert "creditprobe" not in facts
    assert "product_functionalities" not in facts
    # What it DOES carry is what §8 asks for: the governed measures.
    measures = facts["cockpit_semantics"]["canonical_measures"]
    terms = {m["term"] for m in measures}
    for needed in ("exposure at default", "ecl", "stage"):
        assert needed in terms, f"{needed} is not resolved in the packet"


def test_the_analytical_action_is_not_offered_the_product_tool():
    _, tools = _packet(mac.STAGE2)
    assert "inspect_product_knowledge" not in {t["name"] for t in tools}
    assert {t["name"] for t in tools} == {
        "inspect_catalog", "execute_analysis", "read_artifact",
        "finalize_response"}


def test_no_tool_on_the_action_turn_asks_for_a_nested_intent():
    _, tools = _packet(mac.STAGE2)
    for tool in tools:
        assert '"intent"' not in json.dumps(tool["input_schema"]), tool["name"]


def test_the_action_instruction_is_about_choosing_an_action():
    """§3. The instruction the turn opens on."""
    packet, _ = _packet(mac.STAGE2)
    opening = packet.first_user_message
    assert "take your next action now" in opening.lower()


def test_the_action_output_allowance_is_bounded_and_smaller(store_db):
    limits = config_mod.ANALYTICAL_STANDARD_LIMITS
    assert limits.action_output_tokens < limits.reserved_output_tokens
    assert limits.action_call_seconds < limits.deadline_seconds / 2


# ---- the report's own numbers, printed where a reader can find them ----

def test_the_measured_payload_is_recorded_for_the_report(store_db, tmp_path):
    _, card = mac.seeded_construction_run(store_db)
    rows = {
        "stage2_ecl_growth": _measure(mac.STAGE2),
        "seeded_construction": _measure(mac.CONSTRUCTION,
                                        investigation=card),
    }
    out = tmp_path / "action_payload.json"
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    for name, sizes in rows.items():
        assert sizes["total"] > 0, name
        print(f"{name}: " + ", ".join(f"{k}={v:,}"
                                      for k, v in sizes.items()))
