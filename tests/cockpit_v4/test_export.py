"""MODEL MOCK · REAL DATABASE/RUNNER · REAL API.

§30, §31, §32. Taking an answer out, with what makes it checkable.

Three documents: the analysis, one table, one chart. Every one of them
carries the lineage block -- book, release, fingerprint, tenant, run,
artifacts, code digests, row count -- because a figure pasted into a memo
without its provenance is a number nobody can check.

The row-scope case is the one that bites. A table on screen shows a preview;
an export of it must be the WHOLE result and must say so, and the subset must
be available and labelled as a subset rather than silently served as
complete.
"""

from __future__ import annotations

import csv
import io
import json

import domain_oracles as oracle
import pytest
from conftest import ScriptedResult, final, intent, tool_call
from test_domain_execution import (answer_from, drive_domain,  # noqa: F401
                                   execute_call, make_domain_run)

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import export as export_mod
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


def _charted_answer(messages, *, month: str):
    """An answer with a claim, a table AND a chart over the same artifact."""
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
                       "y_columns": ["ead_sar_mn"],
                       "unit": "SAR million",
                       "why_this_chart": "Ranked comparison across sectors."}],
              limitations=["Synthetic demonstration data."]))])


@pytest.fixture
def finished(store_db, runtime):
    """One finished corporate run with a table and a chart.

    The real runtime from `conftest`, the real worker, a real DuckDB session
    on the corporate book. Only the analyst is scripted.
    """
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
        lambda m: _charted_answer(m, month=month)])
    outcome = Worker(store=store_db, runtime=runtime).execute(record)
    assert outcome.state == st.COMPLETED, outcome.message
    return store_db, store_db.get_run(record.run_id), month


def _artifact_id(record) -> str:
    return str((record.final_response["tables"][0])["artifact_id"])


# ---- §30: the analysis -------------------------------------------------

def test_the_analysis_export_carries_the_answer_and_its_lineage(finished):
    store, record, month = finished
    artifacts = {_artifact_id(record): store.get_artifact(
        _artifact_id(record), tenant_id=record.tenant_id)}
    lineage = export_mod.lineage_for(
        record=record, artifact=artifacts[_artifact_id(record)],
        row_count=len(artifacts[_artifact_id(record)]["rows"]))
    body = export_mod.analysis_markdown(
        record=record, answer=record.final_response, artifacts=artifacts,
        lineage=lineage)

    assert record.question in body
    assert "## Lineage" in body
    assert dom.LABELS[dom.CORPORATE] in body
    assert dom.DEFAULT_RELEASES[dom.CORPORATE] in body
    assert record.release_fingerprint in body
    assert record.run_id in body
    assert _artifact_id(record) in body
    assert "nothing was recomputed" in body.lower()
    # Every sector the query returned is in the document, not just a preview.
    for sector in oracle.corp_ead_by_sector(month):
        assert sector in body


def test_the_analysis_export_publishes_the_claim_as_the_reader_saw_it(
        finished):
    _store, record, _month = finished
    claim = record.final_response["numeric_claims"][0]
    shown = str(claim.get("display_value") or "")
    assert shown.startswith("SAR "), claim
    body = export_mod.analysis_markdown(
        record=record, answer=record.final_response, artifacts={},
        lineage=export_mod.lineage_for(record=record))
    assert shown in body
    assert claim["decimal_value"] not in body, (
        "the raw canonical value is not a reader-facing figure")


# ---- §31: full against displayed ---------------------------------------

def test_the_table_export_is_every_row_and_says_so(finished):
    store, record, month = finished
    artifact = store.get_artifact(_artifact_id(record),
                                  tenant_id=record.tenant_id)
    lineage = export_mod.lineage_for(
        record=record, artifact=artifact, row_scope=export_mod.ALL_ROWS,
        row_count=len(artifact["rows"]))
    body = export_mod.table_csv(
        artifact=artifact, lineage=lineage,
        columns=["sector", "ead_sar_mn"],
        units={"ead_sar_mn": "SAR million"})

    expected = oracle.corp_ead_by_sector(month)
    rows = [r for r in csv.reader(io.StringIO(body))
            if r and not r[0].startswith("#")]
    header, data = rows[0], rows[1:]
    assert header == ["sector", "ead_sar_mn", "ead_sar_mn (as published)"]
    assert len(data) == len(expected)
    for sector, value, published in data:
        assert float(value) == pytest.approx(expected[sector], abs=1e-6)
        assert published.startswith("SAR ") and published.endswith(" million")
    assert "every row of the result" in body


def test_the_displayed_subset_is_labelled_as_a_subset(finished):
    store, record, _month = finished
    artifact = store.get_artifact(_artifact_id(record),
                                  tenant_id=record.tenant_id)
    subset = dict(artifact, rows=artifact["rows"][:3])
    lineage = export_mod.lineage_for(
        record=record, artifact=artifact,
        row_scope=export_mod.DISPLAYED_ROWS, row_count=3,
        total_rows=len(artifact["rows"]), complete=False)
    body = export_mod.table_csv(artifact=subset, lineage=lineage,
                                columns=["sector", "ead_sar_mn"])
    assert "the rows shown on screen" in body
    assert f"Rows: 3 of {len(artifact['rows'])}" in body
    assert "INCOMPLETE" in body
    assert "rows=all for the whole result" in body
    rows = [r for r in csv.reader(io.StringIO(body))
            if r and not r[0].startswith("#")]
    assert len(rows) - 1 == 3


# ---- §32: the chart ----------------------------------------------------

def test_the_chart_export_is_drawn_from_the_servers_own_points(finished):
    _store, record, _month = finished
    chart = record.final_response["charts"][0]
    assert chart["points"], "the server supplies every point"
    lineage = export_mod.lineage_for(record=record,
                                     row_count=len(chart["points"]))
    svg = export_mod.chart_svg(chart=chart, lineage=lineage)

    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert chart["title"] in svg
    assert dom.DEFAULT_RELEASES[dom.CORPORATE] in svg
    assert record.release_fingerprint[:16] in svg
    # The bars carry the PUBLISHED strings, not raw floats.
    top = chart["points"][0]["display"]["ead_sar_mn"]
    assert top in svg
    assert str(chart["points"][0]["values"]["ead_sar_mn"]) not in svg


def test_a_chart_with_no_points_is_refused_rather_than_drawn_empty():
    lineage = export_mod.Lineage(
        run_id="r", question="q", domain_id=dom.CORPORATE,
        domain_label="Corporate Credit", release_id="rel",
        release_fingerprint="f" * 64, tenant_id="t",
        reporting_currency="SAR", amount_scale="million",
        produced_at="2026-09-14T00:00:00+00:00")
    with pytest.raises(export_mod.ExportUnavailable) as raised:
        export_mod.chart_svg(chart={"title": "Nothing", "points": [],
                                    "y_columns": ["x"]}, lineage=lineage)
    assert "table behind it" in str(raised.value)


# ---- both books --------------------------------------------------------

@pytest.mark.parametrize("domain_id", list(dom.DOMAIN_IDS))
def test_an_export_names_the_book_it_came_from(domain_id):
    from backend.cockpit_v4.run_store import RunRecord

    record = RunRecord(
        run_id="run-x", thread_id="th-x", tenant_id=lake.DEFAULT_TENANT,
        principal_id="u1", question="q", mode="standard",
        release_id=dom.DEFAULT_RELEASES[domain_id], state=st.COMPLETED,
        version=1, domain_id=domain_id,
        release_fingerprint=arun.for_domain(domain_id).release_fingerprint)
    lineage = export_mod.lineage_for(record=record)
    text = "\n".join(lineage.lines())
    assert dom.LABELS[domain_id] in text
    assert dom.DEFAULT_RELEASES[domain_id] in text
    other = next(d for d in dom.DOMAIN_IDS if d != domain_id)
    assert dom.LABELS[other] not in text
    assert dom.DEFAULT_RELEASES[other] not in text


# ---- the routes --------------------------------------------------------

@pytest.fixture
def api(finished, runtime):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.cockpit_v4 import routes

    store, _record, _month = finished
    app = FastAPI()
    routes.install(store=store, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {
                       "id": "u1", "tenant": lake.DEFAULT_TENANT},
                   startup_sha="testsha")
    app.include_router(routes.router)
    return TestClient(app)


def test_the_three_exports_are_served_as_downloads(api, finished):
    _store, record, _month = finished
    artifact_id = _artifact_id(record)

    analysis = api.get(f"{P}/runs/{record.run_id}/export")
    assert analysis.status_code == 200, analysis.text
    assert analysis.headers["content-type"].startswith("text/markdown")
    assert "attachment" in analysis.headers["content-disposition"]
    assert ".md" in analysis.headers["content-disposition"]
    assert "## Lineage" in analysis.text

    table = api.get(
        f"{P}/runs/{record.run_id}/artifacts/{artifact_id}/export")
    assert table.status_code == 200, table.text
    assert table.headers["content-type"].startswith("text/csv")
    assert "every row of the result" in table.text

    chart = api.get(f"{P}/runs/{record.run_id}/charts/0/export")
    assert chart.status_code == 200, chart.text
    assert chart.headers["content-type"].startswith("image/svg+xml")
    assert chart.text.startswith("<svg")


def test_the_table_export_honours_the_row_scope(api, finished):
    _store, record, _month = finished
    artifact_id = _artifact_id(record)
    full = api.get(f"{P}/runs/{record.run_id}/artifacts/{artifact_id}/export",
                   params={"rows": "all"}).text
    shown = api.get(f"{P}/runs/{record.run_id}/artifacts/{artifact_id}/export",
                    params={"rows": "displayed"}).text
    assert "every row of the result" in full
    assert "the rows shown on screen" in shown
    assert len(full.splitlines()) >= len(shown.splitlines())
    # This result fits on one screen, so the subset IS everything -- and the
    # document says exactly that rather than attaching a caveat that is not
    # true. A reader who learns the caveats are unreliable stops reading
    # them.
    assert "which is every row of the result" in shown
    assert "INCOMPLETE" not in shown

    refused = api.get(
        f"{P}/runs/{record.run_id}/artifacts/{artifact_id}/export",
        params={"rows": "some"})
    assert refused.status_code == 400
    assert refused.json()["detail"]["error_code"] == "INVALID_ROW_SCOPE"


def test_an_export_of_another_tenants_run_is_not_found(finished, runtime):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.cockpit_v4 import routes

    store, record, _month = finished
    app = FastAPI()
    routes.install(store=store, runtime=runtime, cfg=runtime.cfg,
                   principal_resolver=lambda r: {"id": "u2",
                                                 "tenant": "other-bank"},
                   startup_sha="testsha")
    app.include_router(routes.router)
    other = TestClient(app)
    assert other.get(f"{P}/runs/{record.run_id}/export").status_code == 404


def test_a_chart_index_that_does_not_exist_says_how_many_there_are(api,
                                                                   finished):
    _store, record, _month = finished
    response = api.get(f"{P}/runs/{record.run_id}/charts/7/export")
    assert response.status_code == 404
    assert "published 1 chart" in response.json()["detail"]["message"]
