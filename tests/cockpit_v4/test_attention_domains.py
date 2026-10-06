"""REAL DATABASE · NO MODEL · INDEPENDENT ORACLE.

Two dashboards, two books, one ranking rule.

Every number a card shows is recomputed here from the parquet with pandas --
a different engine, a different code path, and no shared helper with the
thing under test. A card that agrees with the SQL that produced it proves
only that the SQL ran twice.
"""

from __future__ import annotations

import pytest

from backend.cockpit_v4 import attention_v2 as att
from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import schema as schema_mod

from . import domain_oracles as oracle


def _period_column(domain_id: str) -> str:
    """Each book keeps its periods in its own column. §2, §3."""
    return schema_mod.period_column(domain_id)


def _latest(domain_id: str) -> str:
    return oracle.latest_period(domain_id)


def _previous(domain_id: str) -> str:
    return oracle.previous_period(domain_id)


def _frame(domain_id: str, relation: str):
    import pandas as pd

    return pd.read_parquet(
        lake.relation_path(dom.DEFAULT_RELEASES[domain_id], relation))


@pytest.fixture(scope="module")
def feeds():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    att.clear_cache()
    out = {}
    for domain_id in dom.DOMAIN_IDS:
        scope = resolver.scope_for(domain_id)
        session = cat.open_session(catalog=cat.build(domain_id=domain_id))
        out[domain_id] = (att.compute(session=session, scope=scope), scope,
                          session)
    return out


# ---- the two dashboards are different --------------------------------

def test_each_book_gets_its_own_section_titles(feeds):
    """§26. The labels change because the DATA changes, not the other way."""
    corporate, _, _ = feeds[dom.CORPORATE]
    retail, _, _ = feeds[dom.RETAIL]
    assert corporate["attention_label"] == "Segments requiring attention"
    assert retail["attention_label"] == "Retail portfolio requiring attention"
    assert corporate["highlights_label"] == (
        "Latest-quarter ECL highlights")
    assert retail["highlights_label"] == (
        "Latest-month retail ECL highlights")


def test_the_two_feeds_share_no_card(feeds):
    """§26. Switching is not a relabel: nothing on one page is on the other."""
    corporate, _, _ = feeds[dom.CORPORATE]
    retail, _, _ = feeds[dom.RETAIL]
    corporate_ids = {i["item_id"]
                     for i in corporate["segments_requiring_attention"]}
    retail_ids = {i["item_id"]
                  for i in retail["segments_requiring_attention"]}
    assert corporate_ids and retail_ids
    assert not corporate_ids & retail_ids
    assert not ({h["item_id"] for h in corporate["ecl_highlights"]}
                & {h["item_id"] for h in retail["ecl_highlights"]})


def test_each_feed_watches_its_own_dimensions(feeds):
    """§17, §18, §27, §28. A retail page does not talk about sectors it does
    not have, and a corporate page does not talk about score bands.

    The permitted set is READ FROM THE GOVERNED SCHEMA rather than listed
    here. A hand-written list turns every new lens into a test edit, which
    is how a list stops being a check: the lens that matters is the one
    nobody thought to add to it. What is actually being asserted is that a
    book's dashboard groups by columns THAT BOOK HAS -- so the columns come
    from the book.
    """
    for domain_id in dom.DOMAIN_IDS:
        feed, _, _ = feeds[domain_id]
        mine = {column for relation in schema_mod.relations(domain_id)
                for column in (f.name for f in relation.fields)}
        mine |= set(att.SYNTHETIC_DIMENSIONS)
        theirs = {column
                  for other in dom.DOMAIN_IDS if other != domain_id
                  for relation in schema_mod.relations(other)
                  for column in (f.name for f in relation.fields)}
        exclusive = theirs - mine
        used = {i["segment_dimension"]
                for i in feed["segments_requiring_attention"]}
        used |= {i["segment_dimension"] for i in feed["ecl_highlights"]
                 if i.get("segment_dimension")}
        assert used <= mine, (domain_id, used - mine)
        assert not (used & exclusive), (domain_id, used & exclusive)

    corporate, _, _ = feeds[dom.CORPORATE]
    retail, _, _ = feeds[dom.RETAIL]
    corporate_dimensions = {i["segment_dimension"]
                            for i in corporate["segments_requiring_attention"]}
    retail_dimensions = {i["segment_dimension"]
                         for i in retail["segments_requiring_attention"]}
    assert "sector" not in retail_dimensions
    assert "score_band" not in corporate_dimensions
    assert "vintage_year" not in corporate_dimensions


@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_every_card_names_the_book_it_came_from(feeds, domain_id):
    """§10, §20. domain, release and fingerprint travel with the finding."""
    feed, scope, _ = feeds[domain_id]
    for item in (feed["segments_requiring_attention"]
                 + feed["ecl_highlights"]):
        assert item["domain_id"] == domain_id
        assert item["release_id"] == scope.release_id
        assert item["release_fingerprint"] == scope.release_fingerprint


@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_the_feed_costs_no_model_call(feeds, domain_id):
    """§63. A page that costs a generation is a page nobody leaves open."""
    assert feeds[domain_id][0]["model_calls"] == 0


@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_the_feed_compares_the_latest_period_to_the_one_before(feeds,
                                                               domain_id):
    """§2, §3. Each book steps by its OWN period, and the periods are the
    ones its release publishes: quarters for Corporate, months for Retail."""
    feed, _, _ = feeds[domain_id]
    noun = att.PERIOD_NOUNS[domain_id]
    assert feed["period_noun"] == noun
    assert feed["reporting_period"] == _latest(domain_id)
    assert feed["comparison_period"] == _previous(domain_id)
    assert feed[f"reporting_{noun}"] == _latest(domain_id)
    assert feed[f"comparison_{noun}"] == _previous(domain_id)
    assert feed["comparison_basis"] == f"previous {noun}"
    other = "month" if noun == "quarter" else "quarter"
    assert feed[f"reporting_{other}"] == "", (
        "a book must not fill the other calendar's key")


# ---- the oracle -------------------------------------------------------

@pytest.mark.parametrize("domain_id,relation,dimension", [
    (dom.CORPORATE, "corp_facility_quarter", "sector"),
    (dom.RETAIL, "retail_account_month", "product"),
])
def test_every_ecl_card_matches_an_independent_pandas_oracle(
        feeds, domain_id, relation, dimension):
    """The figure on the card is the figure in the parquet. Recomputed."""
    feed, _, _ = feeds[domain_id]
    frame = _frame(domain_id, relation)
    latest = frame[frame[_period_column(domain_id)] == _latest(domain_id)]
    expected = latest.groupby(dimension)["ecl_sar_mn"].sum().round(4)

    checked = 0
    for item in feed["segments_requiring_attention"]:
        if item["segment_dimension"] != dimension or item["metric"] != "ecl":
            continue
        segment = item["segment"]
        assert segment in expected.index, segment
        assert abs(item["movement"]["to"] - float(expected[segment])) < 0.01
        checked += 1
    assert checked or all(
        i["segment_dimension"] != dimension
        for i in feed["segments_requiring_attention"])


@pytest.mark.parametrize("domain_id,relation,dimension", [
    (dom.CORPORATE, "corp_facility_quarter", "sector"),
    (dom.RETAIL, "retail_account_month", "product"),
])
def test_every_stage_two_card_matches_the_oracle(feeds, domain_id, relation,
                                                 dimension):
    feed, _, _ = feeds[domain_id]
    frame = _frame(domain_id, relation)
    latest = frame[frame[_period_column(domain_id)] == _latest(domain_id)]

    for item in feed["segments_requiring_attention"]:
        if item["metric"] != "stage2_share":
            continue
        group = latest[latest[item["segment_dimension"]].astype(str)
                       == item["segment"]]
        if group.empty:
            continue
        share = (group.loc[group["stage"] >= 2, "ead_sar_mn"].sum()
                 / group["ead_sar_mn"].sum())
        assert abs(item["movement"]["to"] - share) < 1e-6


@pytest.mark.parametrize("domain_id,relation", [
    (dom.CORPORATE, "corp_facility_quarter"),
    (dom.RETAIL, "retail_account_month"),
])
def test_the_total_ecl_highlight_matches_the_oracle(feeds, domain_id,
                                                    relation):
    feed, _, _ = feeds[domain_id]
    frame = _frame(domain_id, relation)
    total = frame.loc[
        frame[_period_column(domain_id)] == _latest(domain_id),
        "ecl_sar_mn"].sum()
    from decimal import Decimal

    from backend.cockpit_v4 import display as disp

    expected = disp.format_value(Decimal(str(total)), "SAR million")
    assert feed["ecl_highlights"][0]["display"] == expected


# ---- the ranking rule -------------------------------------------------

@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_cards_are_ordered_by_score_and_capped(feeds, domain_id):
    feed, _, _ = feeds[domain_id]
    items = feed["segments_requiring_attention"]
    assert len(items) <= att.TOP_N
    assert [i["score"] for i in items] == sorted(
        (i["score"] for i in items), reverse=True)


@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_no_single_lens_takes_the_whole_page(feeds, domain_id):
    """One dimension moving every slice of itself is one finding."""
    feed, _, _ = feeds[domain_id]
    counts: dict[str, int] = {}
    for item in feed["segments_requiring_attention"]:
        counts[item["segment_dimension"]] = counts.get(
            item["segment_dimension"], 0) + 1
    assert counts, "the feed is empty"
    assert max(counts.values()) <= att.MAX_PER_DIMENSION


@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_one_segment_appears_once(feeds, domain_id):
    feed, _, _ = feeds[domain_id]
    seen = [(i["segment_dimension"], i["segment"])
            for i in feed["segments_requiring_attention"]]
    assert len(seen) == len(set(seen))


@pytest.mark.parametrize("domain_id", dom.DOMAIN_IDS)
def test_only_rising_segments_are_shown(feeds, domain_id):
    """"Requiring attention" is not "here is every segment"."""
    feed, _, _ = feeds[domain_id]
    for item in feed["segments_requiring_attention"]:
        assert item["movement"]["to"] > item["movement"]["from"]


def test_the_feed_is_deterministic(feeds):
    """Two loads of one book rank identically or nobody can act on either."""
    for domain_id in dom.DOMAIN_IDS:
        _, scope, session = feeds[domain_id]
        first = att.compute(session=session, scope=scope)
        second = att.compute(session=session, scope=scope)
        assert ([i["item_id"] for i in first["segments_requiring_attention"]]
                == [i["item_id"]
                    for i in second["segments_requiring_attention"]])


# ---- the wall, again --------------------------------------------------

def test_a_feed_cannot_be_computed_from_the_other_books_session(feeds):
    """§5. Fail closed rather than quietly answering with the wrong book."""
    _, corporate_scope, _ = feeds[dom.CORPORATE]
    _, _, retail_session = feeds[dom.RETAIL]
    with pytest.raises(att.AttentionUnavailable):
        att.compute(session=retail_session, scope=corporate_scope)


def test_the_cache_cannot_serve_one_book_from_the_others_entry(feeds):
    """§55. The key carries tenant, domain, release AND fingerprint."""
    att.clear_cache()
    keys = set()
    for domain_id in dom.DOMAIN_IDS:
        _, scope, session = feeds[domain_id]
        first = att.cached(session=session, scope=scope, tenant_id="t1")
        assert first["cached"] is False
        again = att.cached(session=session, scope=scope, tenant_id="t1")
        assert again["cached"] is True
        assert again["domain_id"] == domain_id
        keys.add(scope.cache_key("attention", "t1"))
    assert len(keys) == len(dom.DOMAIN_IDS)


# ---- the authored stories are findable --------------------------------

def test_the_corporate_book_surfaces_a_stressed_sector(feeds):
    """The generator authored construction and real-estate stress."""
    feed, _, _ = feeds[dom.CORPORATE]
    # Either lens counts. The movement cards answer "what changed this
    # month" and the ECL highlights answer "where is the loss"; a book whose
    # authored crisis is deep and no longer accelerating belongs in the
    # second, and demanding it appear in the first would be demanding the
    # dashboard rank by level while calling itself a movement feed.
    named = {i["segment"] for i in feed["segments_requiring_attention"]
             if i["segment_dimension"] == "sector"}
    named |= {word for h in feed["ecl_highlights"]
              for word in ("Construction", "Real Estate")
              if word in str(h.get("headline", ""))}
    assert named & {"Construction", "Real Estate"}, named


def test_the_retail_book_surfaces_its_worst_vintage_or_band(feeds):
    """The generator authored a 2024 vintage and a thinning bottom band."""
    feed, _, _ = feeds[dom.RETAIL]
    found = {(i["segment_dimension"], str(i["segment"]))
             for i in feed["segments_requiring_attention"]}
    assert any(d in ("vintage_year", "score_band") for d, _ in found), found


# ---- the page reads this feed; the feed must carry what it reads --------

#: Every top-level key `attention-panel.tsx` dereferences. Checked against
#: the real feed because losing one of them is not a missing label: when the
#: per-domain engine replaced the quarterly one it dropped `ownership`, the
#: footnote read `feed.ownership.note`, and the whole Cockpit home page came
#: down with a TypeError inside an error boundary. A unit test of the engine
#: could not see it and the panel's own tests used a fixture that still had
#: the key.
PANEL_KEYS = (
    "domain_id", "domain_label", "release_id", "release_fingerprint",
    "reporting_period", "comparison_period", "period_noun",
    "reporting_month", "comparison_month",
    "reporting_quarter", "comparison_quarter", "reporting_currency",
    "amount_scale", "attention_label", "highlights_label",
    "segments_requiring_attention", "ecl_highlights", "model_calls",
    "computed_ms", "ownership",
)

#: Every field a card is dereferenced for, by the panel AND by the drawer.
#:
#: The drawer is the half that was missed. It reads
#: `item.possible_drivers.length` and maps `item.what_to_review_next`, and
#: the per-domain engine emitted neither -- so clicking ANY card threw inside
#: the component, the error boundary unmounted the page beneath it, and the
#: Cockpit home screen went blank on a click. The panel's own tests passed
#: throughout, because they never opened a card.
CARD_KEYS = (
    "item_id", "section", "headline", "segment", "segment_dimension",
    "metric", "metric_label", "what_changed", "why_it_appeared", "movement",
    "key_numbers", "evidence", "evidence_url", "reporting_period",
    "comparison_period", "period_noun", "reporting_month",
    "comparison_month", "reporting_quarter", "comparison_quarter",
    "possible_drivers", "what_to_review_next", "drilldown",
)


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_feed_carries_every_key_the_page_reads(feeds, domain_id):
    feed, _scope, _session = feeds[domain_id]
    missing = [key for key in PANEL_KEYS if key not in feed]
    assert missing == [], (
        f"the {domain_id} feed is missing {missing}, which the Cockpit home "
        f"page dereferences")
    assert feed["ownership"]["note"]
    assert feed["ownership"]["basis"] == "recorded_book"
    assert isinstance(feed["computed_ms"], int)
    for card in feed["segments_requiring_attention"] + feed["ecl_highlights"]:
        absent = [key for key in CARD_KEYS if key not in card]
        assert absent == [], f"a card is missing {absent}: {card}"
        assert isinstance(card["possible_drivers"], list)
        assert card["what_to_review_next"], (
            "a card with nothing to review next is a dead end")
        assert card["drilldown"]["suggested_questions"]


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_a_driver_is_an_association_and_never_a_cause(feeds, domain_id):
    feed, _scope, _session = feeds[domain_id]
    for card in feed["segments_requiring_attention"]:
        for driver in card["possible_drivers"]:
            assert driver["relationship"] == "recorded alongside"
            assert driver["metric"] != card["metric"], (
                "a measure is not a driver of itself")
            assert "caused" not in driver["statement"].lower()
            assert "because" not in driver["statement"].lower()


@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_the_footnote_names_the_book_and_disclaims_prediction(feeds,
                                                              domain_id):
    feed, _scope, _session = feeds[domain_id]
    note = feed["ownership"]["note"]
    assert dom.LABELS[domain_id] in note
    assert "Not Early Warning" in note
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    assert dom.LABELS[other] not in note
