"""
The Cockpit home feed: the ranking, its gates, and the click-through.

Evidence: REAL DATABASE for every number -- the ranking runs over the pinned
release through the same DuckDB session an analysis uses -- and MODEL MOCK for
the two tests that drive a run, which use the scripted provider. No paid
provider call is made anywhere in this module.

The ranking is checked against `attention_oracle`, a pandas re-implementation
that never touches the engine. The gates are checked on hand-built rows,
because the cases that matter -- a missing value, a tie, a one-facility
"spike" -- are exactly the ones a fixed dataset does not happen to contain.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import attention_oracle as oracle
from backend.cockpit_agentic import sql as v3_sql
from backend.cockpit_v4 import attention, attention_v2, routes

P = "/api/v1/cockpit-v4"


@pytest.fixture
def session(runtime):
    scope = runtime.scope_for({"id": "u1", "tenant": "demo-tenant"})
    return v3_sql.open_session(scope=scope, catalog=runtime.catalog)


@pytest.fixture
def feed(session, runtime, release_id):
    attention.clear_cache()
    catalog = runtime.catalog
    currency = " ".join(p for p in (catalog.reporting_currency,
                                    catalog.amount_scale) if p)
    return attention.compute(
        session=session, release_id=release_id,
        quarters=[str(q) for q in catalog.calendar.populated],
        currency=currency)


@pytest.fixture
def client(store_db, runtime):
    app = FastAPI()

    def resolver(request: Request):
        tenant = request.headers.get("X-Test-Tenant", "demo-tenant")
        if tenant == "anonymous":
            return None
        return {"id": request.headers.get("X-Test-User", "u1"),
                "tenant": tenant}

    attention.clear_cache()
    routes.install(store=store_db, runtime=runtime,
                   principal_resolver=resolver, startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


# ---- the ranking, against an independent implementation -----------------

def test_the_top_five_match_an_independent_pandas_ranking(feed, release_id):
    expected = oracle.top_segments(release_id, limit=attention.TOP_N)
    actual = feed["segments_requiring_attention"]
    assert [(e["segment"], e["metric"], e["basis"]) for e in expected] == \
        [(a["segment"], a["metric"], a["comparison_basis"]) for a in actual]
    for want, got in zip(expected, actual):
        assert round(float(want["score"]), 3) == round(float(got["score"]), 3)
        assert want["value_old"] == pytest.approx(
            got["evidence"]["value_old"], rel=1e-9)
        assert want["value_new"] == pytest.approx(
            got["evidence"]["value_new"], rel=1e-9)
        assert want["exposure_at_risk"] == pytest.approx(
            got["evidence"]["exposure_at_risk"], rel=1e-9)


def test_the_latest_quarter_and_both_comparison_bases_are_the_calendars(
        feed, release_id):
    quarters = oracle.aggregates(release_id)["reporting_quarter"]
    ordered = sorted(str(q) for q in quarters.unique())
    assert feed["reporting_quarter"] == ordered[-1]
    assert feed["prior_quarter"] == ordered[-2]
    assert feed["prior_year_quarter"] == ordered[-5]


def test_every_item_carries_the_evidence_the_drawer_needs(feed):
    for item in (feed["segments_requiring_attention"]
                 + feed["ecl_highlights"]):
        evidence = item["evidence"]
        for field in ("release_id", "reporting_quarter", "comparison_quarter",
                      "segment", "metric", "unit", "value_new",
                      "ranking_reason", "reporting_currency"):
            assert field in evidence, f"{item['item_id']} lacks {field}"
        assert item["why_it_appeared"]
        assert item["what_to_review_next"]
        assert item["evidence_url"].endswith(item["item_id"])


def test_no_two_cards_are_the_same_issue(feed):
    pairs = [(i["segment"], i["family"])
             for i in feed["segments_requiring_attention"]]
    assert len(pairs) == len(set(pairs))
    families = [i["family"] for i in feed["segments_requiring_attention"]]
    # Five distinct issue types are available in this release, so breadth
    # should actually be achieved and not merely attempted.
    assert len(set(families)) == len(families)


def test_the_feed_costs_nothing_at_the_provider(feed):
    assert feed["model_calls"] == 0


def test_the_feed_is_cockpit_ownership_not_early_warning(feed):
    assert feed["ownership"]["functionality"] == "cockpit"
    assert feed["ownership"]["basis"] == "recorded_book"
    blob = str(feed["method"]["sql"]).lower()
    for forbidden in ("ews", "early_warning", "alert", "trigger_"):
        assert forbidden not in blob


def test_the_engine_is_reproducible(session, runtime, release_id):
    currency = runtime.catalog.reporting_currency
    quarters = [str(q) for q in runtime.catalog.calendar.populated]
    first = attention.compute(session=session, release_id=release_id,
                              quarters=quarters, currency=currency)
    second = attention.compute(session=session, release_id=release_id,
                               quarters=quarters, currency=currency)
    def shape(feed):
        return [(i["item_id"], i["score"], i["headline"])
                for i in feed["segments_requiring_attention"]]
    assert shape(first) == shape(second)


# ---- ECL highlights -----------------------------------------------------

def test_ecl_highlights_match_the_oracle_exactly(feed, release_id):
    quarter, comparison = feed["reporting_quarter"], feed["prior_quarter"]
    new = oracle.ecl_by_sector(release_id, quarter)
    old = oracle.ecl_by_sector(release_id, comparison)
    deltas = {s: new[s] - old[s] for s in new if s in old}

    by_metric = {i["metric"]: i for i in feed["ecl_highlights"]}
    rising = [s for s in deltas if deltas[s] > 0]
    if rising:
        rise = by_metric["ecl_increase_sector"]
        top = max(rising, key=lambda s: (deltas[s], s))
        assert rise["segment"] == top
        assert rise["evidence"]["delta"] == pytest.approx(deltas[top],
                                                          rel=1e-9)
    else:
        # Every sector's ECL fell this quarter. The engine must not dress the
        # least bad fall up as a rise: an increase highlight with no increase
        # behind it would be the dashboard inventing a finding.
        assert "ecl_increase_sector" not in by_metric, (
            "no sector's ECL rose, so there is no largest riser to show")

    contributor = by_metric["ecl_largest_contributor"]
    totals = oracle.book_totals(release_id, quarter)
    biggest = max(new, key=lambda s: (new[s], s))
    assert contributor["segment"] == biggest
    assert contributor["evidence"]["value_new"] == pytest.approx(
        new[biggest] / totals["ecl"], rel=1e-9)


def test_ecl_highlights_do_not_repeat_one_measure_five_times(feed):
    families = [i["family"] for i in feed["ecl_highlights"]]
    assert len(families) == len(set(families))


def test_an_improvement_never_appears_as_something_requiring_attention(feed):
    """Every card is a deterioration in its own indicator's direction."""
    directions = {s["id"]: s["direction"] for s in attention.INDICATORS}
    for item in feed["segments_requiring_attention"]:
        delta = float(item["evidence"]["delta"])
        assert delta * directions[item["metric"]] > 0, item["headline"]


def test_a_meaningful_reduction_is_still_reported_in_ecl_highlights(feed):
    stage_mix = next((i for i in feed["ecl_highlights"]
                      if i["metric"] == "stage2_share_of_book"), None)
    assert stage_mix is not None
    # This release's stage mix FELL. The highlight reports it anyway, because
    # a credit officer wants to see a book-level move whichever way it went.
    assert float(stage_mix["evidence"]["delta"]) < 0
    assert "fell" in stage_mix["headline"]


# ---- the gates, on rows built for the case ------------------------------

BOOK = 10_000.0


def _row(quarter: str, **fields) -> dict:
    base = {"reporting_quarter": quarter, "sector_name": "Testing",
            "facilities": 10, "ead": 1_000.0, "ecl": 10.0, "stage2_ead": 0.0,
            "stage3_ead": 0.0, "pd_base": 1_000.0, "pd_weight": 20.0,
            "uncovered_ead": 0.0, "past_due_ead": 0.0,
            "rating_base": 1_000.0, "rating_weight": 5_000.0,
            "covenant_base": 1_000.0, "breach_ead": 0.0}
    base.update(fields)
    return base


def _try(metric: str, old: dict, new: dict):
    spec = next(s for s in attention.INDICATORS if s["id"] == metric)
    return attention._candidate(spec, "Testing", "prior_quarter", old, new,
                                book_ead_new=BOOK, book_ead_old=BOOK)


def test_a_missing_value_is_not_read_as_zero():
    """The covenant gap in this release: rows present, measure not observed."""
    old = _row("2026Q1", covenant_base=None, breach_ead=None)
    new = _row("2026Q2", covenant_base=1_000.0, breach_ead=500.0)
    candidate, reason = _try("covenant_breach_share", old, new)
    assert candidate is None
    assert reason == "missing_value"


def test_a_measure_observed_over_a_sliver_of_the_sector_is_refused():
    old = _row("2026Q1", covenant_base=100.0, breach_ead=0.0)
    new = _row("2026Q2", covenant_base=100.0, breach_ead=90.0)
    candidate, reason = _try("covenant_breach_share", old, new)
    assert candidate is None
    assert reason == "insufficient_observation"


def test_no_change_is_not_an_issue():
    candidate, reason = _try("stage2_share", _row("2026Q1", stage2_ead=100.0),
                             _row("2026Q2", stage2_ead=100.0))
    assert candidate is None
    assert reason == "no_deterioration"


def test_an_improvement_is_not_an_issue():
    candidate, reason = _try("stage2_share", _row("2026Q1", stage2_ead=300.0),
                             _row("2026Q2", stage2_ead=100.0))
    assert candidate is None
    assert reason == "no_deterioration"


def test_a_spike_off_a_single_facility_is_refused():
    old = _row("2026Q1", facilities=1, ead=900.0)
    new = _row("2026Q2", facilities=1, ead=900.0, stage2_ead=900.0)
    candidate, reason = _try("stage2_share", old, new)
    assert candidate is None
    assert reason == "below_minimum_facility_count"


def test_a_hundred_percent_move_on_a_tiny_sector_is_refused():
    """Relative movement is not materiality. 4 million of a 10,000 book."""
    old = _row("2026Q1", ead=4.0)
    new = _row("2026Q2", ead=4.0, stage2_ead=4.0)
    candidate, reason = _try("stage2_share", old, new)
    assert candidate is None
    assert reason == "sector_below_minimum_exposure"


def test_a_real_but_immaterial_move_on_a_large_sector_is_refused():
    old = _row("2026Q1", ead=1_000.0, stage2_ead=0.0)
    new = _row("2026Q2", ead=1_000.0, stage2_ead=1.0)   # 0.1pp = 1.0 million
    candidate, reason = _try("stage2_share", old, new)
    assert candidate is None
    assert reason == "below_materiality"


def test_a_thin_sector_that_passes_the_gates_still_scores_lower():
    """Confidence scales with the number of facilities behind the movement."""
    thin_old, thin_new = _row("2026Q1", facilities=2), _row(
        "2026Q2", facilities=2, stage2_ead=200.0)
    thick_old, thick_new = _row("2026Q1", facilities=20), _row(
        "2026Q2", facilities=20, stage2_ead=200.0)
    thin, _ = _try("stage2_share", thin_old, thin_new)
    thick, _ = _try("stage2_share", thick_old, thick_new)
    assert thin.score < thick.score
    assert thick.confidence == 1.0


def test_ties_break_the_same_way_every_time():
    def make(sector: str, metric: str) -> attention.Candidate:
        spec = next(s for s in attention.INDICATORS if s["id"] == metric)
        return attention.Candidate(
            indicator=spec, sector=sector, basis="prior_quarter",
            quarter="2026Q2", comparison_quarter="2026Q1", value_old=0.0,
            value_new=0.2, delta=0.2, adverse_delta=0.2,
            exposure_at_risk=200.0, score=50.0, relative=0.5, money=0.5,
            confidence=1.0, facilities_old=10, facilities_new=10,
            sector_ead_new=1_000.0, sector_ead_old=1_000.0)

    pool = [make("Zinc", "stage2_share"), make("Alpha", "past_due_share"),
            make("Alpha", "stage2_share")]
    first = [(c.sector, c.indicator["id"]) for c in attention.select(pool)]
    second = [(c.sector, c.indicator["id"])
              for c in attention.select(list(reversed(pool)))]
    assert first == second
    # Alphabetical on segment, then on metric, is the documented tail of the
    # tie-break, and it is what decides when scores are genuinely equal.
    assert first[0] == ("Alpha", "past_due_share")


def test_one_sector_cannot_take_two_slots_while_another_waits():
    def make(sector: str, metric: str, score: float) -> attention.Candidate:
        spec = next(s for s in attention.INDICATORS if s["id"] == metric)
        return attention.Candidate(
            indicator=spec, sector=sector, basis="prior_quarter",
            quarter="2026Q2", comparison_quarter="2026Q1", value_old=0.0,
            value_new=0.2, delta=0.2, adverse_delta=0.2,
            exposure_at_risk=score, score=score, relative=0.5, money=0.5,
            confidence=1.0, facilities_old=10, facilities_new=10,
            sector_ead_new=1_000.0, sector_ead_old=1_000.0)

    pool = [make("Alpha", "stage2_share", 90.0),
            make("Alpha", "past_due_share", 80.0),
            make("Beta", "uncovered_share", 10.0)]
    chosen = attention.select(pool, limit=2)
    assert [c.sector for c in chosen] == ["Alpha", "Beta"]


# ---- the API, the drawer's evidence, and Investigate Further ------------

def test_the_feed_endpoint_serves_both_sections(client):
    response = client.get(f"{P}/attention")
    assert response.status_code == 200
    body = response.json()
    assert len(body["segments_requiring_attention"]) == attention.TOP_N
    assert body["ecl_highlights"]
    assert body["model_calls"] == 0
    # The published surface carries a method summary, not the whole method
    # and not the dropped-candidate ledger: those are operator detail.
    assert "method" not in body and "dropped" not in body
    assert body["method_summary"]["candidates_considered"] > 0


def test_the_second_request_is_served_from_the_release_cache(client):
    # The domain-aware engine keeps its own cache, keyed by tenant, domain,
    # release AND fingerprint. Clearing the superseded module's cache leaves
    # this one warm from an earlier test in the same process.
    attention_v2.clear_cache()
    assert client.get(f"{P}/attention").json()["cached"] is False
    assert client.get(f"{P}/attention").json()["cached"] is True
    assert client.get(f"{P}/attention?refresh=true").json()["cached"] is False


def test_one_tenant_never_reads_another_tenants_feed(client):
    attention.clear_cache()
    assert client.get(f"{P}/attention").status_code == 200
    other = client.get(f"{P}/attention", headers={"X-Test-Tenant": "other"})
    # A different tenant gets its own computation, never the cached one --
    # and a tenant the release holds nothing for is refused with a typed
    # code rather than handed an empty feed, which would read as "your
    # portfolio is fine".
    assert other.status_code in (200, 403, 503)
    if other.status_code == 200:
        assert other.json()["cached"] is False
    if other.status_code == 403:
        assert other.json()["detail"]["error_code"] == "SECURITY_DENIED"


def test_the_item_endpoint_carries_the_full_technical_evidence(client):
    item = client.get(f"{P}/attention").json(
        )["segments_requiring_attention"][0]
    detail = client.get(f"{P}/attention/{item['item_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["item"]["headline"] == item["headline"]
    # The method the domain-aware engine publishes: how many candidates it
    # weighed, which families it watched, and the rule that ordered them.
    # The superseded engine published an SQL bundle here; this one names the
    # ranking instead, because the SQL is now per family rather than one
    # query a reader could usefully be shown.
    assert body["method"]["ranking"]
    assert body["method"]["candidates_considered"] > 0
    assert body["method"]["families"]
    assert body["domain_id"] in ("corporate", "retail")
    assert body["release_fingerprint"]


def test_an_unknown_item_is_a_plain_404(client):
    assert client.get(f"{P}/attention/att-nope").status_code == 404


def test_investigate_further_opens_a_seeded_thread(client, store_db):
    item = client.get(f"{P}/attention").json(
        )["segments_requiring_attention"][0]
    response = client.post(f"{P}/attention/{item['item_id']}/investigate")
    assert response.status_code == 201
    body = response.json()
    seed = body["seed"]
    assert seed["segment"] == item["segment"]
    assert seed["reporting_quarter"] == item["reporting_quarter"]
    assert seed["comparison_quarter"] == item["comparison_quarter"]
    assert seed["metric"] == item["metric"]
    assert seed["evidence"] == item["evidence"]
    assert body["suggested_questions"]

    stored = store_db.thread_context(body["thread_id"],
                                     tenant_id="demo-tenant")
    assert stored["kind"] == "attention_item"
    assert stored["body"]["item_id"] == item["item_id"]
    assert store_db.thread_context(body["thread_id"],
                                   tenant_id="someone-else") is None


def test_a_run_in_a_seeded_thread_can_ask_without_restating_the_segment(
        client, store_db):
    item = client.get(f"{P}/attention").json(
        )["segments_requiring_attention"][0]
    thread_id = client.post(
        f"{P}/attention/{item['item_id']}/investigate").json()["thread_id"]
    response = client.post(f"{P}/runs", json={
        "question": "show me the customers behind this",
        "thread_id": thread_id})
    assert response.status_code == 202
    record = store_db.get_run(response.json()["run_id"])
    assert record.thread_id == thread_id
    assert record.question == "show me the customers behind this"


def test_the_dataset_offers_borrowers_and_refuses_to_invent_a_subsegment(
        feed):
    for item in feed["segments_requiring_attention"]:
        drill = item["drilldown"]
        assert "borrower" in drill["available"]
        assert "subsegment" in drill["unavailable"]
        assert "no subsegment" in drill["note"]
        assert drill["borrower_count"] >= 0


def test_drivers_are_association_never_causation(feed):
    for item in feed["segments_requiring_attention"]:
        for driver in item["possible_drivers"]:
            assert driver["relationship"] in ("coincides with",
                                              "associated with",
                                              "none recorded")
            statement = driver["statement"].lower()
            for causal in ("because", "caused by", "due to", "driven by",
                           "as a result of"):
                assert causal not in statement, driver["statement"]


def test_a_feed_failure_is_a_component_failure_with_a_reference(
        store_db, monkeypatch, runtime):
    app = FastAPI()

    def resolver(request: Request):
        return {"id": "u1", "tenant": "demo-tenant"}

    attention_v2.clear_cache()
    routes.install(store=store_db, runtime=runtime,
                   principal_resolver=resolver, startup_sha="testsha")
    app.include_router(routes.router)
    local = TestClient(app)

    def boom(**_kwargs):
        raise attention_v2.AttentionUnavailable(
            "the aggregate query did not return.")

    monkeypatch.setattr(attention_v2, "cached", boom)
    failure = local.get(f"{P}/attention")
    assert failure.status_code == 503
    detail = failure.json()["detail"]
    assert detail["error_code"] == "DATA_UNAVAILABLE"
    assert detail["component"] == "corporate_attention_feed"
    assert detail["error_reference"].startswith("att-")

    # And Ask is untouched: a dashboard that cannot compute is not a backend
    # that is down.
    assert local.post(f"{P}/runs",
                      json={"question": "Who are you?"}).status_code == 202
    assert local.get("/api/v1/cockpit-v4/diagnostics").status_code == 200


def test_the_seeded_context_reaches_the_model_and_shows_in_the_trace(
        store_db, runtime, drive, release_id):
    """Seeding is a context load, and the panel says so because it happened."""
    from conftest import final, intent, tool_call

    seed = {"item_id": "att-test", "segment": "Construction",
            "reporting_quarter": "2026Q2", "comparison_quarter": "2026Q1",
            "metric": "stage2_share", "headline": "Construction: Stage 2 up",
            "movement": "+12.00 pp", "evidence": {"value_new": 0.12}}
    thread_id = store_db.create_thread(tenant_id="demo-tenant",
                                       principal_id="u1")
    store_db.set_thread_context(thread_id, tenant_id="demo-tenant",
                                kind="attention_item", body=seed)
    record, _ = store_db.accept_run(
        thread_id=thread_id, tenant_id="demo-tenant", principal_id="u1",
        question="show me the customers behind this", mode="standard",
        release_id=release_id, ui_filters={}, idempotency_key="",
        body_digest="", startup_sha="testsha", deadline_at="")

    script = [tool_call("finalize_response", {
        **intent(mode="PRODUCT_HELP", owner="COCKPIT"),
        **final(disposition="clarification",
                clarification_question="Borrowers by exposure or by ECL?")})]
    outcome, provider, _ = drive("show me the customers behind this",
                                 script, record=record)

    sent = str(provider.sent[0])
    assert "ACTIVE INVESTIGATION" in sent
    assert "Construction" in sent and "2026Q2" in sent

    messages = [e.public_message for e in store_db.events_since(record.run_id)]
    assert any("Investigation context loaded" in m for m in messages)
    assert any("Construction" in m for m in messages)


def test_an_ordinary_thread_says_nothing_about_an_investigation(
        store_db, drive, make_run):
    from conftest import final, intent, tool_call

    script = [tool_call("finalize_response", {
        **intent(), **final(disposition="answer", narrative="Hello.")})]
    record = make_run("Who are you?")
    _outcome, provider, _ = drive("Who are you?", script, record=record)
    assert "ACTIVE INVESTIGATION" not in str(provider.sent[0])
    messages = [e.public_message for e in store_db.events_since(record.run_id)]
    assert not any("Investigation context loaded" in m for m in messages)


def test_the_feed_is_fast_enough_to_render_a_page(feed):
    """A local UAT target, measured rather than asserted in prose."""
    assert feed["computed_ms"] < 2_000, feed["computed_ms"]


# ---- two dashboards, two questions, no overlap -------------------------

def test_every_segment_card_is_about_a_segment(feed):
    """The composition defect: ECL highlights in the segment list."""
    for item in feed["segments_requiring_attention"]:
        assert item["scope"] == attention.SCOPE_SEGMENT, item["headline"]
        assert item["segment_dimension"] == "sector_name"
        assert item["segment"] and item["segment"] != "Whole book"


def test_a_borrower_highlight_can_never_enter_the_segment_list(feed):
    borrower = [i for i in feed["ecl_highlights"]
                if i["scope"] == attention.SCOPE_BORROWER]
    assert borrower, "this release has a largest-borrower highlight"
    ids = {i["item_id"] for i in feed["segments_requiring_attention"]}
    for item in borrower:
        assert item["item_id"] not in ids, item["headline"]
        assert item["evidence"].get("borrower_id")


def test_a_book_wide_observation_can_never_enter_the_segment_list(feed):
    book = [i for i in feed["ecl_highlights"]
            if i["scope"] == attention.SCOPE_PORTFOLIO]
    assert book, "this release has a book-level stage-mix highlight"
    ids = {i["item_id"] for i in feed["segments_requiring_attention"]}
    for item in book:
        assert item["item_id"] not in ids, item["headline"]
        assert item["segment"] == "Whole book"


def test_no_item_appears_in_both_dashboards(feed):
    segments = [i["item_id"] for i in feed["segments_requiring_attention"]]
    highlights = [i["item_id"] for i in feed["ecl_highlights"]]
    assert set(segments).isdisjoint(highlights)
    # And no headline is repeated either: two cards saying the same sentence
    # are duplication whether or not they share an id.
    assert set(i["headline"] for i in feed["segments_requiring_attention"]) \
        .isdisjoint(i["headline"] for i in feed["ecl_highlights"])


def test_the_release_supplies_five_qualifying_segment_items(feed):
    segments = feed["segments_requiring_attention"]
    assert len(segments) == attention.TOP_N
    assert len({i["segment"] for i in segments}) == len(segments)


def test_the_engine_refuses_a_contaminated_segment_list(feed):
    """The invariant lives in the engine, not in whatever renders it."""
    segments = feed["segments_requiring_attention"]
    highlights = feed["ecl_highlights"]
    attention.check_composition(segments, highlights)

    borrower = next(i for i in highlights
                    if i["scope"] == attention.SCOPE_BORROWER)
    book = next(i for i in highlights
                if i["scope"] == attention.SCOPE_PORTFOLIO)

    for intruder, why in ((borrower, "a single borrower"),
                          (book, "a book-wide observation")):
        with pytest.raises(attention.AttentionUnavailable) as caught:
            attention.check_composition(segments + [intruder], highlights)
        assert "segment-scoped items only" in caught.value.message, why

    # A sector-scoped ECL highlight passes the scope check and is still a
    # duplicate: the same card cannot be in both lists.
    with pytest.raises(attention.AttentionUnavailable) as caught:
        attention.check_composition(segments + [highlights[0]], highlights)
    assert "one dashboard, not both" in caught.value.message

    duplicate = {**segments[0], "item_id": "att-other"}
    with pytest.raises(attention.AttentionUnavailable) as caught:
        attention.check_composition(segments, highlights + [duplicate])
    assert "the same sentence" in caught.value.message


def test_neither_dashboard_costs_a_model_call(feed):
    """Both are built from SQL over the pinned release and nothing else."""
    assert feed["model_calls"] == 0
    blob = str(feed["method"]["sql"]).lower()
    assert "select" in blob
    for forbidden in ("anthropic", "opus", "prompt", "completion"):
        assert forbidden not in blob


def test_the_ranking_method_is_unchanged_by_this_round(feed, release_id):
    """The composition fix touched presentation, not the engine."""
    expected = oracle.top_segments(release_id, limit=attention.TOP_N)
    actual = feed["segments_requiring_attention"]
    assert [(e["segment"], e["metric"], e["basis"]) for e in expected] == \
        [(a["segment"], a["metric"], a["comparison_basis"]) for a in actual]
    assert feed["method"]["formula"].startswith(
        "100 * (0.5*relative + 0.5*money) * confidence")
    assert feed["method"]["gates"]["materiality_fraction_of_book_ead"] == \
        attention.MATERIAL_FRACTION
