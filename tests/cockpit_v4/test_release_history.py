"""
A release is opened through its own manifest, and history stays readable.

REAL DATABASE/RUNNER · REAL API · INDEPENDENT ORACLE. No model call, no paid
provider call.

The defect this exists for
--------------------------
A published release is immutable and self-describing: its manifest records
every relation, every field, the grain, the keys and the calendar. The code
that read one ignored all of that. `Catalog` answered every shape question
-- `relations`, `columns`, `spec`, `resolve`, `outline` -- out of
`schema.py` keyed on the DOMAIN, so the schema this build happens to carry
was imposed on whatever release was opened. `_build_session` then
materialised each parquet with `SELECT "<every column today declares>"`.

Adding four columns to Retail in the enrichment round therefore stopped
`v4-saudi-retail-20m-v3` opening at all:

    BinderException: Referenced column "employment_type" not found

Corporate v3 kept working, which was the dangerous part: its column set
happened to match, so the coupling was invisible until the day a column was
added, and then it took historical reproducibility with it and said nothing.

And a second, worse half. `analytical_runtime.for_domain` treated
`release_id` as an ASSERTION -- any id but the domain's current one was
refused -- so a thread pinned to a superseded release could not be opened by
anything. Every consumer swallowed the refusal: `/threads/{id}` returned
`"release": {}` beside a `release_id` it had just read, and the trace
degraded to an id with no facts. Worse still, `POST /runs` read a thread's
DOMAIN from its pin and discarded its RELEASE, so a follow-up asked in a
superseded thread was silently accepted against the current release and the
transcript ended up holding two turns computed from two different books.

What is pinned here
-------------------
  a release describes itself       -> shape comes from its own manifest
  history opens                    -> every published release, not just today's
  history REPLAYS                  -> the stored SQL runs and returns its rows
  no substitution                  -> v3 never serves v4's columns, or v4 v3's
  no silent rebasing               -> a new turn on a superseded thread is refused
  the bytes did not move           -> fingerprints and content digests unchanged
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake, routes
from backend.cockpit_v4 import schema as schema_mod

P = "/api/v1/cockpit-v4"

#: The superseded releases this build must still be able to read. Written
#: out rather than derived, because the POINT is that these exact ids keep
#: opening: an analysis saved against one names it, and a run that cannot
#: open the release it was accepted against is a run whose stored answer can
#: no longer be checked.
HISTORICAL: dict[str, str] = {
    "v4-saudi-corporate-20q-v3": dom.CORPORATE,
    "v4-saudi-retail-20m-v3": dom.RETAIL,
    # Superseded not because its shape changed but because its id was
    # ambiguous: the generator's customer mean went through the builtin
    # `sum`, so this id names one book before CPython 3.12 and another
    # after. Whichever of the two a machine holds, it still has to open and
    # still has to replay -- an analysis saved against it is no less real
    # for the id having been retired.
    "v4-saudi-retail-20m-v4": dom.RETAIL,
}

#: The columns the enrichment added to Retail. v4 and v5 have them; v3 must
#: not.
ADDED_TO_RETAIL = ("sub_product", "employment_type", "origination_channel",
                   "delinquency_bucket_fine")


#: Parametrised cases below are named `pinned`, not `release_id`, and that
#: is not cosmetic. `release_id` is a session fixture in conftest, and
#: `v4_config` -> `runtime` -> `client` are built from it; a parameter of
#: that name shadows the fixture, so parametrising it rebuilds the whole
#: server around a superseded release and the fixture dies in preflight.
#: The shape under test is the real one: a CURRENT runtime, serving a
#: thread that carries an OLD pin.
def _published(release_id: str) -> None:
    if not lake.exists(release_id):
        pytest.skip(f"{release_id} is not published here")


def _manifest(release_id: str) -> dict:
    return lake.read_manifest(release_id)


@pytest.fixture
def client(store_db, runtime):
    app = FastAPI()
    routes.install(store=store_db, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": "demo-tenant"},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


# ---- every published release opens, whatever shape it was ---------------

@pytest.mark.parametrize("pinned", sorted(lake.releases()))
def test_every_published_release_opens_with_its_own_shape(pinned):
    """The test that would have caught this the day it was introduced.

    Parametrised over what is actually on disk rather than over a list,
    so a release published tomorrow is covered without anybody remembering
    to add it here.
    """
    manifest = _manifest(pinned)
    domain_id = str(manifest.get("domain_id") or "")
    if domain_id not in dom.DEFAULT_RELEASES:
        pytest.skip(f"{pinned} predates the two-book layout")

    catalog = cat.build(domain_id=domain_id, release_id=pinned)
    published = {str(r["relation"]): [str(f["name"]) for f in r["fields"]]
                 for r in manifest["relations"]}

    # The catalogue describes the RELEASE, column for column and in order.
    assert set(catalog.relations()) == set(published)
    for relation, columns in published.items():
        assert list(catalog.columns(relation)) == columns, relation

    # And the session materialises it rather than refusing to bind.
    session = cat.open_session(catalog=catalog, reuse=False)
    for relation in catalog.relations():
        rows = session.connection.execute(
            f'SELECT COUNT(*) FROM "{relation}"').fetchone()[0]
        assert rows == manifest["row_counts"][relation], relation


@pytest.mark.parametrize("pinned", sorted(HISTORICAL))
def test_a_superseded_release_is_not_the_current_one(pinned):
    """Guards the guard: if these ever became current, the tests above
    would pass for the wrong reason."""
    _published(pinned)
    assert pinned not in dom.DEFAULT_RELEASES.values()


# ---- the shape is the release's, in both directions --------------------

def test_the_retail_v4_book_exposes_the_columns_the_enrichment_added():
    release_id = dom.DEFAULT_RELEASES[dom.RETAIL]
    _published(release_id)
    catalog = cat.build(domain_id=dom.RETAIL, release_id=release_id)
    columns = set(catalog.columns("retail_account_month"))
    for column in ADDED_TO_RETAIL:
        assert column in columns, column
        assert catalog.resolve("retail_account_month", column).definition


def test_the_retail_v3_book_does_not_pretend_they_existed():
    """The other half of "no substitution", and the one easier to get wrong.

    Opening an old release must not mean showing it today's columns as
    empty, nor quietly filling them. A column that did not exist is absent,
    and asking for it says so.
    """
    _published("v4-saudi-retail-20m-v3")
    catalog = cat.build(domain_id=dom.RETAIL,
                        release_id="v4-saudi-retail-20m-v3")
    columns = set(catalog.columns("retail_account_month"))
    for column in ADDED_TO_RETAIL:
        assert column not in columns, column
        with pytest.raises(schema_mod.UnknownField):
            catalog.resolve("retail_account_month", column)


def test_the_two_releases_report_different_field_counts():
    """86 against 92. A single number that cannot be right for both."""
    _published("v4-saudi-retail-20m-v3")
    old = cat.build(domain_id=dom.RETAIL,
                    release_id="v4-saudi-retail-20m-v3")
    new = cat.build(domain_id=dom.RETAIL,
                    release_id=dom.DEFAULT_RELEASES[dom.RETAIL])

    def fields(catalog):
        return sum(len(catalog.spec(r).fields) for r in catalog.relations())

    assert fields(old) == 86
    assert fields(new) == 92


def test_a_join_is_not_offered_for_a_release_that_cannot_make_it():
    """The join graph is the one shape no manifest records.

    It is authored per domain, so for a release published before a join
    existed it is today's opinion about an older book. Filtered to the
    relations and columns that release holds, an opinion stays an opinion
    rather than becoming a false statement.
    """
    monthly = "v4-saudi-corporate-20m-v1"
    _published(monthly)
    catalog = cat.build(domain_id=dom.CORPORATE, release_id=monthly)
    assert "corp_borrower_month" in catalog.relations()
    assert catalog.joins() == [], (
        "the quarterly joins name relations this release does not have")
    # While the current book still states all of its own.
    current = cat.build(domain_id=dom.CORPORATE,
                        release_id=dom.DEFAULT_RELEASES[dom.CORPORATE])
    assert len(current.joins()) == 3


@pytest.mark.parametrize("domain_id", sorted(dom.DEFAULT_RELEASES))
def test_reading_the_current_release_back_reproduces_its_own_schema(domain_id):
    """The manifest path and the schema path agree, slot for slot.

    Not `to_dict() == to_dict()`, which would pass while the RAW slots
    differed. `Field.label` and `Field.additive` are each a property over a
    raw slot, and `to_dict` writes only the resolved answer. How the round
    trip puts each back is a decision with consequences, so it is asserted
    rather than assumed:

      * everything a reader sees comes back identical;
      * `aggregation` comes back EMPTY wherever the unit already implies the
        verdict, because a filled slot is a claim that an author overruled
        the unit and `semantics.py` publishes it to the analyst on that
        basis.

    Getting the second one wrong is not theoretical. It put a redundant
    verdict on all thirty canonical measures and 994 bytes on every action
    request, which went over the payload bound -- that is how it was found.
    """
    release_id = dom.DEFAULT_RELEASES[domain_id]
    _published(release_id)
    rebuilt = {spec.name: spec for spec in
               cat.build(domain_id=domain_id, release_id=release_id).specs}
    authored = {spec.name: spec for spec in schema_mod.relations(domain_id)}
    assert set(rebuilt) == set(authored)

    for name, spec in authored.items():
        mine = rebuilt[name]
        assert (mine.grain, mine.period_column, mine.key_columns,
                mine.description) == (spec.grain, spec.period_column,
                                      spec.key_columns, spec.description)
        assert mine.columns == spec.columns
        for published, wrote in zip(mine.fields, spec.fields, strict=True):
            assert (published.dtype, published.unit, published.description,
                    published.group) == (
                wrote.dtype, wrote.unit, wrote.description,
                wrote.group), f"{name}.{wrote.name}"
            # The LABEL, not the slot it came out of. A release records the
            # resolved one, so `tenant_id` -- authored with none and shown
            # as "Tenant id" -- comes back with "Tenant id" written down.
            # Same words on the page, which is the promise being kept.
            assert published.label == wrote.label
            assert published.additive == wrote.additive
            if published.aggregation:
                derived = ("additive"
                           if published.unit in schema_mod.ADDITIVE_UNITS
                           else "not_additive")
                assert published.aggregation != derived, (
                    f"{name}.{wrote.name} carries an aggregation its unit "
                    f"already implies")


def test_a_release_records_the_label_it_showed_and_not_a_guess():
    """Two columns a reader can check by eye.

    `ead_sar_mn` reads as nothing in English and carries a written label;
    `sector` reads as itself. Both come back from the manifest showing what
    the release showed. Neither gains an aggregation, because their units
    have already answered that question.
    """
    release_id = dom.DEFAULT_RELEASES[dom.CORPORATE]
    _published(release_id)
    catalog = cat.build(domain_id=dom.CORPORATE, release_id=release_id)

    written = catalog.resolve("corp_facility_quarter", "ead_sar_mn")
    assert written.label == "Exposure at default"
    assert written.aggregation == ""
    assert written.additive == "additive"

    plain = catalog.resolve("corp_borrower_quarter", "sector")
    assert plain.label == "Sector"
    assert plain.aggregation == ""
    assert plain.additive == "not_additive"


# ---- a pinned thread opens, renders and replays ------------------------

@pytest.mark.parametrize("pinned", sorted(HISTORICAL))
def test_a_thread_pinned_to_a_superseded_release_still_opens(
        client, store_db, pinned):
    """Reopen and RENDER. The stored evidence is in the run store, so this
    half would pass even with the defect present -- it is here because the
    release header beside it would not."""
    _published(pinned)
    domain_id = HISTORICAL[pinned]
    thread_id = store_db.create_thread(
        tenant_id="demo-tenant", principal_id="u1", domain_id=domain_id,
        release_id=pinned,
        release_fingerprint=lake.fingerprint(pinned))
    store_db.append_turn(
        thread_id=thread_id, run_id="run-historical",
        question="What was exposure by segment?",
        answer={"narrative": "As published then.", "disposition": "answer",
                "tables": [], "charts": [], "suggested_questions": []})

    body = client.get(f"{P}/threads/{thread_id}").json()
    assert body["release_id"] == pinned
    assert len(body["turns"]) == 1
    # THE HEADER, which used to come back as `{}` because the runtime
    # refused to open anything but the current release and every caller
    # swallowed the refusal. A payload that names a release in one field
    # and says nothing in the other is a payload a reader cannot check.
    assert body["release"]["release_id"] == pinned, body["release"]
    assert len(body["release"]["release_fingerprint"]) == 64


@pytest.mark.parametrize("pinned", sorted(HISTORICAL))
def test_a_pinned_thread_replays_its_own_sql_against_its_own_release(
        pinned):
    """REPLAY, which is the half that actually exercises the fix.

    Rendering reads stored rows and proves nothing about the release. This
    re-executes against the pinned book and checks the answer against the
    published parquet read independently with pandas.
    """
    import pandas as pd

    _published(pinned)
    domain_id = HISTORICAL[pinned]
    runtime = arun.for_domain(
        domain_id, release_id=pinned,
        release_fingerprint=lake.fingerprint(pinned))
    assert runtime.release_id == pinned

    catalog = runtime.catalog
    relation = catalog.relations()[0]
    period = catalog.spec(relation).period_column
    latest = catalog.calendar.latest
    replayed = runtime.session.connection.execute(
        f'SELECT COUNT(*) FROM "{relation}" WHERE "{period}" = ?',
        [latest]).fetchone()[0]

    frame = pd.read_parquet(lake.relation_path(pinned, relation))
    assert replayed == int((frame[period] == latest).sum())
    assert replayed > 0


def test_a_new_question_in_a_superseded_thread_is_refused_not_rebased(
        client, store_db):
    """The substitution this round exists to stop.

    `POST /runs` read a thread's DOMAIN from its pin and threw the RELEASE
    away, so a follow-up in a superseded thread was accepted against the
    current release with no warning -- two turns in one transcript computed
    from two different books, and nothing afterwards to tell them apart.
    Refused, and the refusal says where to put the question instead.
    """
    release_id = "v4-saudi-corporate-20q-v3"
    _published(release_id)
    thread_id = store_db.create_thread(
        tenant_id="demo-tenant", principal_id="u1",
        domain_id=dom.CORPORATE, release_id=release_id,
        release_fingerprint=lake.fingerprint(release_id))

    response = client.post(f"{P}/runs", json={
        "question": "And how does that look now?", "thread_id": thread_id})
    assert response.status_code == 409, response.text
    body = response.json()["detail"]
    assert body["error_code"] == "RELEASE_SUPERSEDED"
    assert body["release_id"] == release_id
    assert body["current_release_id"] == dom.DEFAULT_RELEASES[dom.CORPORATE]


def test_a_thread_on_the_current_release_still_accepts_a_question(
        client, store_db):
    """The neighbouring failure mode: refusing everything is not a fix."""
    current = dom.DEFAULT_RELEASES[dom.CORPORATE]
    thread_id = store_db.create_thread(
        tenant_id="demo-tenant", principal_id="u1",
        domain_id=dom.CORPORATE, release_id=current)
    response = client.post(f"{P}/runs", json={
        "question": "What is total exposure?", "thread_id": thread_id})
    assert response.status_code in (200, 202), response.text


# ---- nothing was rewritten ---------------------------------------------

#: Content digests for the releases whose generator is EXACT, so these are
#: facts about the data rather than about the machine that built it.
#:
#: Two earlier versions of this table were unsound and passed here for the
#: wrong reason.
#:
#: It recorded a byte FINGERPRINT per release. That hashes the parquet, and
#: parquet encodes compression, row-group layout and writer version, so the
#: same data fingerprints differently on different machines -- this
#: container has corporate v3 at 46962675 where a Mac building the same
#: release has c74cef8f. Both are correct and neither is assertable. The
#: sound check is `lake.verify`, which compares a release against the
#: fingerprint IT recorded, and it is below.
#:
#: It also recorded content digests for the FROZEN releases (v1, v3, and
#: the superseded retail v4). Those came out of the generator while its
#: customer mean still went through the builtin `sum`, whose float
#: behaviour CPython changed in 3.12 -- so what a machine holds under one
#: of those ids depends on the interpreter that first seeded it there.
#: `git log -S` puts that line in cd2f8fa, before v3 was cut. Asserting a
#: digest for them states a machine's history as if it were the release's.
RECORDED: dict[str, str] = {
    "v4-saudi-corporate-20q-v4": "40c05896f0ef2f17",
    "v4-saudi-retail-20m-v5": "54b77d6d0baf4a49",
}

#: Frozen, and readable, and NOT digest-assertable -- see above.
FROZEN = ("v4-saudi-corporate-20m-v1", "v4-saudi-corporate-20m-v2",
          "v4-saudi-corporate-20q-v3", "v4-saudi-retail-20m-v1",
          "v4-saudi-retail-20m-v3", "v4-saudi-retail-20m-v4")


@pytest.mark.parametrize("pinned", sorted(RECORDED))
def test_the_content_of_the_current_releases_is_what_was_published(pinned):
    """The digest is a fact about the values, and these two generators are
    exact arithmetic, so it is the same number on every interpreter. That is
    what makes it assertable at all -- and it is pinned by
    `test_generator_determinism.py`, which builds the book under every
    interpreter on the machine and compares."""
    _published(pinned)

    assert lake.verify(pinned), "the bytes no longer match the manifest"

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]
                           / "scripts" / "cockpit_v4"))
    import release_report  # noqa: PLC0415

    assert release_report.content_digest(pinned).startswith(RECORDED[pinned])


@pytest.mark.parametrize("pinned", sorted(FROZEN))
def test_a_frozen_release_still_matches_the_fingerprint_it_recorded(pinned):
    """The check that IS sound for a superseded release.

    It asks whether these bytes are the bytes this copy was published with,
    which is the question immutability is about, and it asks it against the
    release's own recorded fingerprint rather than against a constant
    written down by whichever machine happened to build it.
    """
    _published(pinned)
    assert lake.verify(pinned), (
        f"{pinned} is published and immutable; its bytes no longer match "
        f"the fingerprint recorded in its own manifest")


def test_the_seeder_still_names_only_the_current_releases():
    """A superseded release cannot be rebuilt, with or without --overwrite,
    because the seeder never names it and both generators refuse the id.

    Retail v4 is on this list for a different reason from the rest. The
    others are shapes this generator outgrew. v4 is an id that means two
    books depending on the interpreter, and the generator could only ever
    rebuild one of them -- so rebuilding it would not restore it, it would
    pick a side and call the result the original.
    """
    from backend.cockpit_v4.generate import corporate as corp_gen
    from backend.cockpit_v4.generate import retail as retail_gen

    assert "v4-saudi-corporate-20q-v3" in corp_gen.FROZEN_RELEASES
    assert "v4-saudi-retail-20m-v3" in retail_gen.FROZEN_RELEASES
    assert "v4-saudi-retail-20m-v4" in retail_gen.FROZEN_RELEASES
    for gen, release_id in ((corp_gen, "v4-saudi-corporate-20q-v3"),
                            (retail_gen, "v4-saudi-retail-20m-v3"),
                            (retail_gen, "v4-saudi-retail-20m-v4")):
        with pytest.raises(gen.FrozenRelease):
            gen.build(release_id)
