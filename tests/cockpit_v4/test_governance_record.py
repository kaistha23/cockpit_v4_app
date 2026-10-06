"""MODEL MOCK · REAL DATABASE/RUNNER · REAL API.

The record of how a question became a number.

The defect this exists for
--------------------------
A reader pressed "Trace" and got the process panel: a list of stages with
ticks against them. It said the analysis had run. It did not say what was
understood from the question, which governed values the reader's own words
resolved to, what query was written, whether a query was REFUSED, which
relations were read, or how the figure in the second paragraph reached the
page from the row it came out of.

Every one of those was already stored. The exact SQL of every submission,
byte for byte, including the ones the binder refused and which therefore
never ran -- `orchestration.py` says so in its own comment: "a submission
refused at the binder is still a numbered submission, and its exact SQL is
still on file." The intent envelope with its resolved entities. The row id
of every cell. None of it had a route, and the `submissions` table had no
reader at all: it was written on every submission and queried only for a
no-progress key.

What is pinned here

  the questions is not rewritten   normalisation is reported as mechanical
  your words resolve to values     "prject finance" -> "Project Finance"
  a refusal is in the record       with its exact SQL and its reason
  a query names what it READ       not the whole authorized set
  the SQL is role-gated            and says it is withheld, not absent
  the pack cannot bypass the gate  a download is not a permission
  lineage states its own limit     it stops at the result set and says so
"""

from __future__ import annotations

import io
import json
import zipfile

import domain_oracles as oracle
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import (drive_domain, execute_call,  # noqa: F401
                                   make_domain_run)

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import governance as gov
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import states as st

P = "/api/v1/cockpit-v4"


@pytest.fixture(scope="module", autouse=True)
def _published():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    yield
    arun.reset()


SECTOR_SQL = (
    "SELECT sector, SUM(ead_sar_mn) AS ead_sar_mn "
    "FROM corp_facility_quarter WHERE reporting_quarter = '{month}' "
    "GROUP BY sector ORDER BY ead_sar_mn DESC")


def _answer(messages, *, month: str):
    body = json.loads(messages[-1]["content"][0]["content"])
    step = body["steps"][0]
    artifact = step["artifact_id"]
    row = step["preview"][0]
    return ScriptedResult(tool_calls=[tool_call(
        "finalize_response",
        final(intent=intent("DATA_ANALYSIS", "COCKPIT"),
              narrative=f"Exposure by sector in {month}. The largest is "
                        f"{{{{claim.top}}}}.",
              coverage=[{"subquestion": "EAD by sector", "status": "answered",
                         "evidence_refs": [{"artifact_id": artifact,
                                            "row_key": "r0",
                                            "column_id": "ead_sar_mn"}]}],
              numeric_claims=[{
                  "claim_id": "top",
                  "decimal_value": str(row["ead_sar_mn"]),
                  "unit": "SAR million", "display_precision": 0,
                  "evidence": {"artifact_id": artifact, "row_key": "r0",
                               "column_id": "ead_sar_mn"}}],
              tables=[{"title": "EAD by sector", "artifact_id": artifact,
                       "columns": ["sector", "ead_sar_mn"]}],
              charts=[{"title": "EAD by sector", "kind": "bar",
                       "artifact_id": artifact, "x_column": "sector",
                       "y_columns": ["ead_sar_mn"], "unit": "SAR million",
                       "why_this_chart": "Ranked comparison across sectors."}],
              limitations=["Synthetic demonstration data."]))])


@pytest.fixture
def finished(store_db, runtime):
    """One finished corporate run: real worker, real DuckDB, scripted
    analyst."""
    from conftest import ScriptedProvider
    from backend.cockpit_v4.worker import Worker

    month = oracle.latest_month(dom.CORPORATE)
    record = make_domain_run(store_db, dom.CORPORATE,
                             "What is EAD by sector this month?")
    runtime.provider = ScriptedProvider([
        ScriptedResult(tool_calls=[execute_call(
            SECTOR_SQL.format(month=month), purpose="EAD by sector",
            grain="sector", units="SAR million",
            subquestions=["EAD by sector"],
            fields=["corp_facility_quarter.ead_sar_mn",
                    "corp_facility_quarter.sector"], month=month)]),
        lambda m: _answer(m, month=month)])
    outcome = Worker(store=store_db, runtime=runtime).execute(record)
    assert outcome.state == st.COMPLETED, outcome.message
    return store_db, store_db.get_run(record.run_id), month


def _api(store, runtime, *, roles=("administrator",)):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.cockpit_v4 import routes

    app = FastAPI()
    routes.install(store=store, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": lake.DEFAULT_TENANT,
                       "roles": tuple(roles)},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


@pytest.fixture
def admin(finished, runtime):
    store, _record, _month = finished
    return _api(store, runtime, roles=("administrator",))


@pytest.fixture
def reader(finished, runtime):
    store, _record, _month = finished
    return _api(store, runtime, roles=("analyst",))


def _record_of(client, record) -> dict:
    response = client.get(f"{P}/runs/{record.run_id}/governance")
    assert response.status_code == 200, response.text
    return response.json()


# ---- the submissions table finally has a reader -------------------------

def test_every_submission_is_in_the_record(admin, finished):
    _store, record, _month = finished
    body = _record_of(admin, record)
    assert body["submissions"], (
        "the run executed a query and the record shows none")
    assert all("ordinal" in s for s in body["submissions"])


def test_a_step_carries_its_exact_query(admin, finished):
    _store, record, month = finished
    body = _record_of(admin, record)
    step = body["submissions"][0]["steps"][0]
    # BYTE FOR BYTE as submitted. A query shown prettier than it ran is a
    # query that was not shown.
    assert step["code"] == SECTOR_SQL.format(month=month)
    assert step["code_shown"] is True


def test_a_step_names_what_it_READ_not_what_it_could_have(admin, finished):
    """The artifact's `relations` is the whole authorized set. A governance
    record built from it says a question about corporate exposure read the
    retail book."""
    _store, record, _month = finished
    step = _record_of(admin, record)["submissions"][0]["steps"][0]
    assert step["relations_read"] == ["corp_facility_quarter"]
    assert len(step["relations_authorized"]) > len(step["relations_read"])


def test_a_step_carries_its_outcome_and_its_digest(admin, finished):
    _store, record, _month = finished
    step = _record_of(admin, record)["submissions"][0]["steps"][0]
    assert step["artifact_id"]
    assert step["rows_out"]
    assert step["code_digest"]
    assert step["failed"] is None


# ---- the question is not rewritten --------------------------------------

def test_the_question_block_does_not_claim_the_sentence_was_corrected(
        admin, finished):
    """`intake.py` states the policy and this record repeats it. What is
    corrected is a VALUE -- "prject finance" to the catalogue's "Project
    Finance" -- never the reader's sentence."""
    _store, record, _month = finished
    question = _record_of(admin, record)["question"]
    assert question["asked"] == record.question
    assert question["changed"] is False
    assert "spelling" not in question["policy"].lower() or \
        "never" in question["policy"].lower()


def test_the_interpretation_carries_what_was_understood(admin, finished):
    _store, record, _month = finished
    interpretation = _record_of(admin, record)["interpretation"]
    assert interpretation["understood_request"]
    # From the ENVELOPE, which is the only place the book is written. The
    # flat intent in `final_response` has no domain at all.
    assert interpretation["domain_id"] == dom.CORPORATE


# ---- the SQL is gated, and says so --------------------------------------

def test_a_reader_without_the_role_does_not_see_the_query(reader, finished):
    _store, record, month = finished
    body = _record_of(reader, record)
    step = body["submissions"][0]["steps"][0]
    assert SECTOR_SQL.format(month=month) not in json.dumps(body)
    assert step["code_shown"] is False
    assert body["sql_visible"] is False


def test_a_withheld_query_says_it_is_withheld(reader, finished):
    """"Withheld" and "there was no query" must not look the same. A
    governance record that goes quiet reads as a control that did not
    run."""
    _store, record, _month = finished
    body = _record_of(reader, record)
    assert body["sql_policy"]
    assert body["submissions"][0]["steps"][0]["code"] == gov.SQL_WITHHELD


def test_everything_else_is_still_shown_without_the_role(reader, finished):
    """The point of a governance record is that a reader can see the
    controls worked without being handed the internals."""
    _store, record, _month = finished
    step = _record_of(reader, record)["submissions"][0]["steps"][0]
    assert step["purpose"]
    assert step["relations_read"]
    assert step["rows_out"]
    assert step["code_digest"]


def test_a_principal_with_no_roles_at_all_is_not_an_administrator(finished,
                                                                  runtime):
    """An absent `roles` grants nothing. That is the right direction to
    fail in."""
    store, record, _month = finished
    client = _api(store, runtime, roles=())
    assert _record_of(client, record)["sql_visible"] is False


@pytest.mark.parametrize("role", ["Administrator", "ADMINISTRATOR",
                                  " administrator "])
def test_the_role_is_matched_case_and_space_insensitively(role):
    """A governance record that turned on the capitalisation of a role name
    would be a bug nobody could reproduce."""
    assert gov.may_read_sql({"roles": (role,)}) is True


@pytest.mark.parametrize("who", [{}, {"roles": ()}, {"roles": ("analyst",)},
                                 {"roles": "analyst"}])
def test_nobody_else_reads_the_query(who):
    assert gov.may_read_sql(who) is False


# ---- the waterfall, and what it will not claim --------------------------

def test_the_waterfall_runs_from_the_release_to_the_figure(admin, finished):
    _store, record, _month = finished
    waterfall = _record_of(admin, record)["waterfall"]
    assert waterfall["release"]["release_id"] == record.release_id
    assert waterfall["artifacts"]
    row = waterfall["artifacts"][0]
    assert row["step_id"] and row["rows"] and row["relations_read"]
    # And where the reader SAW it.
    kinds = {item["kind"] for item in row["published_as"]}
    assert {"table", "chart"} <= kinds


def test_a_published_chart_records_the_axes_it_was_drawn_against(admin,
                                                                 finished):
    _store, record, _month = finished
    waterfall = _record_of(admin, record)["waterfall"]
    charts = [item
              for row in waterfall["artifacts"]
              for item in row["published_as"] if item["kind"] == "chart"]
    assert charts and charts[0]["y_axis"]


def test_every_figure_is_traced_to_a_stored_cell(admin, finished):
    _store, record, _month = finished
    claims = _record_of(admin, record)["waterfall"]["claims"]
    assert claims
    assert claims[0]["from_cell"]["row_key"]
    assert claims[0]["from_cell"]["column_id"]


def test_the_record_states_where_the_lineage_stops(admin, finished):
    """A waterfall that stops at the result set and does not say so reads
    as a waterfall that goes all the way down."""
    _store, record, _month = finished
    limit = _record_of(admin, record)["waterfall"]["limit"]
    assert "result set" in limit
    assert "aggregated" in limit


# ---- the pack -----------------------------------------------------------

def test_the_pack_carries_the_document_the_json_and_the_rows(admin, finished):
    _store, record, _month = finished
    response = admin.get(f"{P}/runs/{record.run_id}/governance/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")

    pack = zipfile.ZipFile(io.BytesIO(response.content))
    names = pack.namelist()
    assert any(name.endswith(".md") for name in names)
    assert any(name.endswith(".json") for name in names)
    assert any(name.startswith("results/") for name in names)

    document = pack.read(
        next(n for n in names if n.endswith(".md"))).decode("utf-8")
    assert "## The question" in document
    assert "## What was submitted" in document
    assert "## What this record cannot show" in document
    assert "## Lineage" in document


def test_the_pack_does_not_bypass_the_role(reader, finished):
    """A download is not a way around a permission."""
    _store, record, month = finished
    response = reader.get(f"{P}/runs/{record.run_id}/governance/export")
    assert response.status_code == 200
    pack = zipfile.ZipFile(io.BytesIO(response.content))
    whole = b"".join(pack.read(name) for name in pack.namelist())
    assert SECTOR_SQL.format(month=month).encode("utf-8") not in whole


def test_the_pack_carries_the_query_for_an_administrator(admin, finished):
    _store, record, month = finished
    response = admin.get(f"{P}/runs/{record.run_id}/governance/export")
    pack = zipfile.ZipFile(io.BytesIO(response.content))
    whole = b"".join(pack.read(name) for name in pack.namelist())
    assert SECTOR_SQL.format(month=month).encode("utf-8") in whole


# ---- who may open it ----------------------------------------------------

def test_another_tenants_run_is_not_found(finished, runtime):
    store, record, _month = finished
    client = _api(store, runtime)
    client.app.dependency_overrides.clear()
    from backend.cockpit_v4 import routes
    routes.install(store=store, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {"id": "u2",
                                                 "tenant": "somebody-else",
                                                 "roles": ("administrator",)},
                   startup_sha="testsha")
    response = client.get(f"{P}/runs/{record.run_id}/governance")
    assert response.status_code == 404


# ---- a refusal is the part that shows the controls worked ---------------

def test_a_refused_submission_is_in_the_record_with_its_query(
        drive_domain, store_db, runtime):
    """THE claim this route exists for.

    A submission stopped at the binder never ran and produced no artifact,
    so nothing downstream carries any trace of it. It is also the single
    most informative thing in the file: "CreditProbe would not run this
    query, and here is the query and the reason" is the sentence a model
    risk reviewer opens the record to find.

    Driven through the real grain refusal, which refuses a fan-out join,
    takes the model's own correction and completes -- so the record holds
    one refused submission and one that ran.
    """
    from test_join_grain_refusal import (CORPORATE_DEDUPLICATED,
                                         CORPORATE_FAN_OUT, _repair_run)

    seen: dict = {}
    outcome, _provider, record = _repair_run(drive_domain, seen)
    assert outcome.state == st.COMPLETED, outcome.message

    client = _api(store_db, runtime, roles=("administrator",))
    body = _record_of(client, store_db.get_run(record.run_id))

    refused = [s for s in body["submissions"] if not s["ran"]]
    ran = [s for s in body["submissions"] if s["ran"]]
    assert refused, "the refused submission is not in the record"
    assert ran, "the corrected submission is not in the record"

    # Its exact SQL, and why it was stopped.
    assert refused[0]["steps"][0]["code"] == CORPORATE_FAN_OUT
    assert refused[0]["refusal"]["error_code"]
    assert refused[0]["refusal"]["message"]
    # It never ran, so it has no artifact and no rows. Stated, not absent.
    assert refused[0]["steps"][0]["artifact_id"] == ""
    assert CORPORATE_DEDUPLICATED in [step["code"]
                                      for s in ran for step in s["steps"]]
