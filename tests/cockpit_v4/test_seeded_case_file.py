"""MODEL MOCK · REAL DATABASE/RUNNER · REPRODUCTION.

A seeded investigation arrives with its schema, not with a relation dump.

The live defect
---------------
A user clicked the Manufacturing covenant card and asked a follow-up. The run
executed a query and got seven borrower rows back, and then spent the rest of
its deadline on provider turns. One catalogue call in that run returned
FIFTY-NINE field definitions: `cockpit_covenant_quarter` carries fifty-nine
columns in this release, no covenant column is among the canonical measures
the starting context already resolves, and so the only way the analyst could
ask about covenants at all was to ask for the whole relation.

The fix is not a smaller page size. It is that CreditProbe already knows which
columns that card was computed from -- it ran the SQL -- and can hand them
over before the first model call. What it must NOT hand over is a method: the
case file says what the fields are, never which one answers the question.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import semantics as sem
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.attention import INDICATORS

from .conftest import ScriptedResult, final, intent, tool_call

COVENANT = "cockpit_covenant_quarter"

#: What the release physically carries for the covenant relation, and what a
#: request for the relation therefore returns. The number in the live trace.
COVENANT_COLUMNS_IN_RELEASE = 59


def _flat(sent: str) -> str:
    """The sent payload with JSON-in-JSON escaping undone.

    Context blocks are serialised strings INSIDE a serialised request, so a
    field name reaches the wire as `\\"breach_date\\"`. Matching the
    unescaped form would quietly find nothing and pass every bound.
    """
    return sent.replace('\\"', '"')


def _answer(text: str) -> ScriptedResult:
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("PRODUCT_HELP", "COCKPIT"), narrative=text))])


def _seeded(store_db, release_id, *, metric: str, question: str):
    thread_id = store_db.create_thread(tenant_id="demo-tenant",
                                       principal_id="u1")
    store_db.set_thread_context(
        thread_id, tenant_id="demo-tenant", kind="attention_item", body={
            "segment": "Manufacturing",
            "segment_dimension": "sector_name",
            "reporting_quarter": "2026Q2",
            "comparison_quarter": "2026Q1",
            "metric": metric,
            "metric_label": "Exposure with a covenant breach",
            "headline": "Manufacturing: more exposure sits under a "
                        "covenant breach"})
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="demo-tenant", principal_id="u1",
        question=question, mode="standard", release_id=release_id,
        ui_filters={}, idempotency_key="", body_digest="",
        startup_sha="testsha", deadline_at="")
    return record


# ---- the bound ---------------------------------------------------------

def test_a_seeded_covenant_thread_is_not_handed_the_whole_relation(
        drive, store_db, release_id):
    """The live failure, as an assertion on the bytes that would be sent."""
    record = _seeded(store_db, release_id, metric="covenant_breach_share",
                     question="Which borrowers are in breach and by how much?")
    outcome, provider, _ = drive(
        "Which borrowers are in breach and by how much?",
        [_answer("Seven borrowers are in breach.")], record=record)
    assert outcome.state == st.COMPLETED, outcome.message

    flat = _flat(provider.first_input_text())
    delivered = [c for c in _covenant_columns() if f'"{c}"' in flat]
    assert len(delivered) <= sem.MAX_SEED_FIELDS, (
        f"a seeded covenant thread received {len(delivered)} covenant field "
        f"definitions; the whole relation is "
        f"{COVENANT_COLUMNS_IN_RELEASE} and the bound is "
        f"{sem.MAX_SEED_FIELDS}")
    assert len(delivered) < COVENANT_COLUMNS_IN_RELEASE


def test_the_case_file_carries_the_fields_the_card_was_computed_from(
        drive, store_db, release_id):
    """Narrow is only useful if it is also sufficient."""
    record = _seeded(store_db, release_id, metric="covenant_breach_share",
                     question="Which borrowers are in breach?")
    _, provider, _ = drive("Which borrowers are in breach?",
                           [_answer("Seven borrowers.")], record=record)
    flat = _flat(provider.first_input_text())
    assert "CASE FILE FOR THIS INVESTIGATION" in flat
    # The two columns SQL_COVENANT itself reads to decide what a breach is.
    for column in ("breach_date", "headroom_value"):
        assert f'"{column}"' in flat, (
            f"{column} decides the card's own measure; a follow-up that "
            f"cannot see it has to go and ask for the relation")


def test_the_case_file_is_labelled_as_facts_and_not_as_a_method(
        drive, store_db, release_id):
    record = _seeded(store_db, release_id, metric="covenant_breach_share",
                     question="Which borrowers are in breach?")
    _, provider, _ = drive("Which borrowers are in breach?",
                           [_answer("Seven borrowers.")], record=record)
    flat = _flat(provider.first_input_text())
    head = flat[flat.index("CASE FILE FOR THIS INVESTIGATION"):][:600]
    assert "not a method" in head
    assert "inspect_catalog is still available" in head


def test_a_thread_seeded_on_a_canonical_metric_gets_no_case_file(
        drive, store_db, release_id):
    """No packet is the right packet when the canonical measures cover it.

    `stage2_share` is computed from `ifrs9_stage` and `ead_reported`, both of
    which the starting context already resolves for every run. Adding a case
    file here would be repeating context, not supplying it.
    """
    record = _seeded(store_db, release_id, metric="stage2_share",
                     question="Which borrowers moved to Stage 2?")
    _, provider, _ = drive("Which borrowers moved to Stage 2?",
                           [_answer("Three borrowers.")], record=record)
    sent = provider.first_input_text()
    assert "ACTIVE INVESTIGATION" in sent
    assert "CASE FILE FOR THIS INVESTIGATION" not in sent


# ---- the map cannot rot -------------------------------------------------

def _covenant_columns() -> list[str]:
    from backend.cockpit_agentic import fields as f
    return [spec.name for spec in f.ALL_FIELDS if spec.relation == COVENANT]


@pytest.mark.parametrize("metric,pairs", sorted(sem.SEED_FIELDS.items()))
def test_every_seeded_field_exists_in_the_release(runtime, metric, pairs):
    """A column name typed from memory is a silent hole, not an error.

    An unresolvable entry is skipped by `seed_field_packet`, so a misspelled
    column would quietly shrink the case file and send the analyst back to
    `inspect_catalog` -- exactly the failure this is here to stop.
    """
    for relation, column in pairs:
        assert runtime.catalog.resolve(relation, column) is not None, (
            f"{relation}.{column} is in SEED_FIELDS but not in the release")


def test_every_attention_indicator_has_a_decided_seed(runtime):
    """A new card must not fall back to a relation dump by omission."""
    missing = [spec["id"] for spec in INDICATORS
               if spec["id"] not in sem.SEED_FIELDS]
    assert missing == [], (
        f"these indicators can seed a thread with no decided case file: "
        f"{missing}. An empty tuple is a decision; absence is not.")


@pytest.mark.parametrize("metric", sorted(sem.SEED_FIELDS))
def test_no_case_file_exceeds_the_bound(runtime, metric):
    packet = sem.seed_field_packet(runtime.catalog, metric)
    assert len(packet) <= sem.MAX_SEED_FIELDS


def test_an_unknown_metric_yields_nothing_rather_than_everything(runtime):
    assert sem.seed_field_packet(runtime.catalog, "not_an_indicator") == []
    assert sem.seed_field_packet(runtime.catalog, "") == []


# ---- the cost the case file removes -------------------------------------

def test_asking_for_the_covenant_relation_returns_all_of_it(runtime,
                                                            release_id):
    """The live number, pinned. This is what the old path had to pay.

    Before the case file there was no narrower way for a covenant question to
    ask: no covenant column is a canonical measure, so the analyst named the
    relation and the catalogue answered honestly and completely.
    """
    from backend.cockpit_agentic import scope as v3_scope
    from backend.cockpit_v4.catalog_tool import CatalogService
    from backend.cockpit_v4.contracts import parse_catalog
    from backend.cockpit_v4.service import _Principal

    scope = v3_scope.for_principal(
        _Principal({"tenant": "demo-tenant", "id": "u"}),
        dataset_release_id=release_id)
    service = CatalogService(catalog=runtime.catalog, scope=scope)
    result = service.inspect(parse_catalog({
        "intent": intent("DATA_ANALYSIS", "COCKPIT"), "query": "",
        "relation_ids": [COVENANT], "field_ids": [], "detail": ["fields"],
        "reporting_periods": [], "sample_rows": 0, "cursor": ""}))

    delivered = len(result["fields"]) + int(
        (result.get("omitted") or {}).get("remaining_fields", 0))
    assert delivered == COVENANT_COLUMNS_IN_RELEASE, (
        "if this number moved, the release changed; the case file bound was "
        "chosen against it")
    assert len(sem.SEED_FIELDS["covenant_breach_share"]) < delivered


def test_the_case_file_covers_every_column_the_card_sql_reads(runtime):
    """Sufficiency, proved against the server's own SQL rather than a list.

    `SQL_COVENANT` is the statement CreditProbe ran to build the card. Any
    covenant column it reads is a column the follow-up can be asked about, so
    a case file missing one sends the analyst back to the catalogue for the
    relation -- the whole failure, reintroduced by a typo.
    """
    import re

    from backend.cockpit_v4.attention import SQL_COVENANT

    covenant_clause = SQL_COVENANT[SQL_COVENANT.index(f"FROM {COVENANT}"):]
    read = {c for c in _covenant_columns()
            if re.search(rf"\b{c}\b", SQL_COVENANT)
            or re.search(rf"\b{c}\b", covenant_clause)}
    seeded = {column for _relation, column
              in sem.SEED_FIELDS["covenant_breach_share"]}
    # reporting_quarter is a scope column every relation carries and the
    # pinned scope already states it; it is not a field definition to supply.
    missing = read - seeded - {"reporting_quarter"}
    assert missing == set(), (
        f"SQL_COVENANT reads {sorted(missing)}, which the case file does not "
        f"carry")
