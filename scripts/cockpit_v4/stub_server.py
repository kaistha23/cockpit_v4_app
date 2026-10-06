#!/usr/bin/env python3
"""
Run the real V4 API with a scripted analyst, for browser testing.

Everything here is the real thing except the model: the real routes, the real
durable store, the real worker and supervisor, the real event stream, and the
real DuckDB session against the published release. Only `converse` is
scripted.

The script is chosen by the QUESTION, so one server covers every browser
scenario without the test reaching in to reconfigure it between cases:

    "Who are you?"          one call, a product-help answer
    "...fail..."            an application failure with a support reference
    "...slow..."            a stalled run, so cancel and refresh have
                            something to act on
    anything else           a real SQL analysis over the release

Usage:
    python3 scripts/cockpit_v4/stub_server.py --port 8414
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "cockpit_v4"))

RELEASE = os.environ.get("COCKPIT_V4_TEST_RELEASE", "v4-saudi-20q-v1")


def money_unit() -> str:
    """The money unit of the SELECTED release, not a baked-in one.

    The stub's canned answer used to say "SAR million" whatever it was
    serving, which is the same hard-coded-currency defect the runtime had:
    pointed at an INR release it published Saudi money over Indian data.
    """
    os.environ.setdefault("COCKPIT_AGENTIC_V3_NAMESPACE", "cockpit_v4")
    from backend.cockpit_agentic import store
    from backend.cockpit_v4 import service

    try:
        currency, scale = service.denomination(
            RELEASE, store.read_manifest(RELEASE))
        return f"{currency} {scale}".strip() or "amount"
    except Exception:                                          # noqa: BLE001
        return "amount"


def build_app(port: int, runtime_dir: Path, ui_port: int = 0):
    os.environ.setdefault("COCKPIT_AGENTIC_V3_NAMESPACE", "cockpit_v4")

    import oracles
    from conftest import ScriptedResult, final, intent, tool_call

    from backend.cockpit_v4 import app as v4app
    from backend.cockpit_v4 import routes
    from backend.cockpit_v4.capability import Capability, PriceCard
    from backend.cockpit_v4.config import V4Config
    from backend.cockpit_v4.service import Runtime, load_release
    from backend.cockpit_v4.supervisor import Supervisor
    from backend.cockpit_v4.worker import Worker

    cfg = V4Config(
        enabled=True, provider="anthropic", reasoning_model="stub-analyst",
        runtime_dir=runtime_dir,
        state_database=str(runtime_dir / "state" / "browser.sqlite3"),
        # The REAL UI port. The app's CORS allow-list is built from it,
        # and a guessed value silently blocks every browser request.
        release_id=RELEASE, api_port=port,
        ui_port=ui_port or port + 1,
        local_demo_auth=True, price_card_path="", memory_enabled=False,
        memory_model="", default_mode="standard", heartbeat_seconds=1.0,
        lease_heartbeat_seconds=2.0, lease_stale_seconds=10.0,
        supervisor_poll_seconds=2.0, credential_present=True, missing=())
    capability = Capability(
        provider="anthropic", model_id="stub-analyst", sdk_version="browser",
        context_tokens=200_000, max_output_tokens=128_000,
        supports_tools=True, supports_token_counting=True,
        price=PriceCard(5.0, 25.0, 6.25, 0.5), source="browser stub",
        verified_at="2026-09-11T00:00:00Z", live_verified=True)

    catalog, _, summary = load_release(cfg)
    quarter = oracles.latest_quarter(RELEASE)

    # WHICH BOOK this turn is being asked in.
    #
    # The stub used to hold one hard-coded query over the pre-domain
    # quarterly relations, which is the same defect the runtime had: pointed
    # at a Retail thread it would have submitted corporate SQL and the run
    # would have failed with an authorization refusal that had nothing to do
    # with what was being tested. The analyst is TOLD the book in its pinned
    # scope, so the stub reads it from there, exactly as a real one would.
    BOOKS = {
        "corporate": {"relation": "corp_facility_quarter",
                      "dimension": "sector", "amount": "ead_sar_mn"},
        "retail": {"relation": "retail_account_month",
                   "dimension": "product", "amount": "ead_sar_mn"},
    }

    def book_of(system) -> dict:
        """The pinned book, read out of the packet the analyst was handed.

        PARSED, not grepped. This searched the serialised blocks for the
        literal `"domain": "corporate"` -- and each block's `text` is itself
        a JSON string, so what the search actually had to match was
        `\"domain\": \"corporate\"`. It never matched, the stub always took
        its pre-domain fallback, and the browser suite spent a round
        analysing `cockpit_facility_quarter` in a release no thread reads any
        more. A stub that says it reads the pinned scope has to read it.
        """
        from backend.cockpit_v4 import domains as dom_mod

        chosen = ""
        for block in list(system or []):
            try:
                body = json.loads(block.get("text") or "")
            except (AttributeError, TypeError, ValueError):
                continue
            if not isinstance(body, dict):
                continue
            scope = body.get("pinned_scope") or {}
            named = str(scope.get("domain") or "")
            if named in dom_mod.DOMAIN_IDS:
                chosen = named
                break
        if not chosen:
            return {"domain_id": "", "relation": "cockpit_facility_quarter",
                    "dimension": "sector_name", "amount": "ead_sar_mn",
                    "period_column": "reporting_quarter", "period": quarter}
        import domain_oracles
        from backend.cockpit_v4 import schema as schema_mod

        # The CALENDAR comes from the book too. This hard-coded
        # `reporting_month` for both books, which was right while both
        # reported months and became a binder error the day the Corporate
        # book started reporting quarters -- so the only browser test that
        # runs an analysis failed, on the one path the suite exists to
        # cover, for a reason that had nothing to do with the UI.
        book = dict(BOOKS[chosen])
        book.update({"domain_id": chosen,
                     "period_column": schema_mod.period_column(chosen),
                     "period": domain_oracles.latest_period(chosen)})
        return book

    def ead_sql_for(book: dict) -> str:
        source = ("SUM(ead_reported)" if not book["domain_id"]
                  else f"SUM({book['amount']})")
        return (f"SELECT {book['dimension']}, {source} AS ead_sar_mn "
                f"FROM {book['relation']} "
                f"WHERE {book['period_column']} = '{book['period']}' "
                f"GROUP BY {book['dimension']} ORDER BY ead_sar_mn DESC")

    #: A product-help answer in the shape a real one takes: Markdown, and
    #: every optional field left null, because there is no clarification and
    #: no referral. Sending null here is the payload that used to be refused.
    HELP_NARRATIVE = """# CreditProbe AI

CreditProbe is an intelligent credit-investigation layer for risk teams. It helps a senior credit officer move from seeing a risk signal to understanding why it matters, deciding what should happen next, and aligning the organisation around the evidence.

## The problem CreditProbe addresses

Credit teams usually have plenty of information — exposure, ECL, ratings, financials, covenants, collateral, behavioural signals and committee material. The difficulty is connecting those pieces quickly enough to answer:

- Where is risk building?
- Why is it happening?
- Which borrowers matter?
- What should the committee discuss?

## How CreditProbe helps

**Cockpit** — interrogate the recorded credit book, identify movement and explain drivers.

**Early Warning** — identify emerging deterioration using behavioural, financial, external and relationship evidence.

**What-If** — test prospective shocks and understand portfolio impact.

Together: **Detect → Diagnose → Decide → Drive Alignment.**

The senior credit officer remains accountable for judgement and approval; CreditProbe accelerates the investigation and connects the evidence.

This environment uses synthetic demonstration data rather than a real bank portfolio."""

    def help_answer():
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response", {
                "intent": {
                    "query_mode": "PRODUCT_HELP", "owner": "COCKPIT",
                    "understood_request": "who this assistant is",
                    "response_language": "en",
                    "blocking_ambiguities": None, "resolved_assumptions": None,
                    "canonical_mappings": None, "excluded_parts": None,
                    "public_rationale": "Answering from product knowledge."},
                "disposition": "answer",
                "narrative": HELP_NARRATIVE,
                "coverage": None, "numeric_claims": None,
                "evidence_refs": None, "tables": None, "charts": None,
                "limitations": None,
                "suggested_questions": [
                    {"question": "What does Early Warning do?",
                     "required_fields": [], "required_quarters": [],
                     "kind": "product_help"},
                    {"question": "How do Cockpit and What-If differ?",
                     "required_fields": [], "required_quarters": [],
                     "kind": "product_help"}],
                "clarification_question": None,
                "clarification_options": None,
                "referral_owner": None, "referral_reason": None},
            "tu-help")])

    def analysis_call(book: dict):
        grain = book["dimension"].replace("_name", "")
        return ScriptedResult(tool_calls=[tool_call("execute_analysis", {
            "intent": intent("DATA_ANALYSIS", "COCKPIT",
                             understood=f"exposure at default by {grain}"),
            "objective": f"Exposure at default by {grain}",
            "subquestions": [f"EAD by {grain}"],
            "scope": {"reporting_periods": [book["period"]], "filters": {}},
            "metadata_receipt_ids": [],
            "fields_required": [f"{book['relation']}.{book['amount']}"],
            "expected_output_grain": grain,
            "expected_units": money_unit(),
            "steps": [{"step_id": "s1", "language": "sql",
                       "code": ead_sql_for(book),
                       "parameters": {}, "purpose": f"EAD by {grain}",
                       "input_artifact_ids": [], "depends_on_step_ids": []}],
            "repair_of_submission_id": ""}, "tu-exec")])

    def analysis_finish(messages, book: dict):
        body = json.loads(messages[-1]["content"][0]["content"])
        step = body["steps"][0]
        cell = step["preview"][0]
        dimension = book["dimension"]
        grain = dimension.replace("_name", "")
        return ScriptedResult(tool_calls=[tool_call(
            "finalize_response",
            final(intent=intent("DATA_ANALYSIS", "COCKPIT",
                                understood=f"exposure at default by {grain}"),
                  narrative=("The largest reported exposure this quarter is "
                             "{{claim.top}}."),
                  coverage=[{"subquestion": f"EAD by {grain}",
                             "status": "answered",
                             "evidence_refs": [
                                 {"artifact_id": step["artifact_id"],
                                  "row_key": f"{dimension}="
                                             f"{cell[dimension]}",
                                  "column_id": "ead_sar_mn"}]}],
                  numeric_claims=[{
                      "claim_id": "top",
                      "decimal_value": repr(float(cell["ead_sar_mn"])),
                      "unit": money_unit(), "display_precision": 2,
                      "evidence": {"artifact_id": step["artifact_id"],
                                   "row_key": f"{dimension}="
                                              f"{cell[dimension]}",
                                   "column_id": "ead_sar_mn"}}],
                  tables=[{"title": f"Exposure at default by {grain}",
                           "artifact_id": step["artifact_id"],
                           "columns": [dimension, "ead_sar_mn"]}],
                  # A ranked comparison across sectors: the analyst decides a
                  # chart helps HERE, and the stub stands in for that
                  # decision. It supplies no values -- the schema has nowhere
                  # to put one -- so every figure in the rendered chart still
                  # comes out of the stored artifact.
                  # THREE FORMS, ONE ANSWER. A reader asked for "multiple
                  # product x DPD line charts" and got one bar chart,
                  # because the browser rendered the FIRST useful chart and
                  # hid it behind a toggle. Three charts of different kinds
                  # is the smallest arrangement that proves the stack
                  # renders them all and draws each as itself.
                  charts=[{"kind": "bar",
                           "title": f"Exposure at default by {grain}",
                           "artifact_id": step["artifact_id"],
                           "x_column": dimension,
                           "y_columns": ["ead_sar_mn"],
                           "unit": money_unit()},
                          {"kind": "line",
                           "title": f"Exposure profile across {grain}s",
                           "artifact_id": step["artifact_id"],
                           "x_column": dimension,
                           "y_columns": ["ead_sar_mn"],
                           "unit": money_unit()},
                          {"kind": "donut",
                           "title": f"Share of exposure by {grain}",
                           "artifact_id": step["artifact_id"],
                           "x_column": dimension,
                           "y_columns": ["ead_sar_mn"],
                           "unit": money_unit()}],
                  suggested_questions=[
                      {"question": f"Show the accounts behind the largest "
                                   f"{grain}." if book["domain_id"]
                                   == "retail"
                                   else f"Show the borrowers behind the "
                                        f"largest {grain}.",
                       "kind": "drilldown"},
                      {"question": f"How has {grain} concentration changed "
                                   f"over the latest year?",
                       "kind": "comparison"}]),
            "tu-final")])

    def stall():
        """A run that keeps working, one modest action at a time.

        Deliberately NOT one long sleep. V4 observes a cancellation BETWEEN
        actions -- it does not abort an in-flight provider call, which is what
        the run deadline and the supervisor are for. A stub that blocks for
        thirty seconds inside `converse` would be testing whether we can
        interrupt a socket, which is not the mechanism, and would make a
        correct implementation look broken.
        """
        return ScriptedResult(tool_calls=[tool_call(
            "inspect_catalog",
            {"intent": intent("DATA_ANALYSIS", "COCKPIT",
                              understood="looking for the right fields"),
             "query": "exposure", "relation_ids": [], "field_ids": [],
             "detail": ["discovery"], "reporting_periods": [],
             "sample_rows": 0, "cursor": ""},
            "tu-slow")])

    class QuestionDrivenProvider:
        """One `converse` per turn, scripted from the question under way."""

        def __init__(self) -> None:
            self.lock = threading.RLock()

        def count_tokens(self, *, system=None, messages=None, tools=None,
                         model="") -> int:
            return int(len(json.dumps({"s": system, "m": messages,
                                       "t": tools}, default=str)) / 3.5) + 1

        @staticmethod
        def _question(messages) -> str:
            for message in messages:
                content = message.get("content")
                if isinstance(content, str) and "USER REQUEST" in content:
                    return content.lower()
            return ""

        def converse(self, *, system, messages, tools=None, max_tokens=4096,
                     model="", purpose="", role="", timeout=0.0,
                     allow_retry=True, effort="", tool_choice=None,
                     output_config=None):
            assert allow_retry is False
            question = self._question(messages)
            # Which turn of THIS conversation this is, counted from the
            # history the request carries. A counter keyed by the question
            # text was shared across runs, so a second run of the same
            # question started at turn 1 and tried to read a tool result that
            # did not exist yet -- a stub defect that made a working seeded
            # investigation look like a provider failure.
            turn = sum(1 for message in messages
                       if message.get("role") == "assistant")

            # A visible pause so the browser observes progress arriving
            # BEFORE the answer, which is the property under test.
            time.sleep(1.0)

            if "retry once" in question:
                # A genuinely invalid first answer, then a correct one. This
                # is the shape of the recorded live run: the panel must keep
                # the failed attempt visible after the retry succeeds.
                if turn == 0:
                    return ScriptedResult(tool_calls=[tool_call(
                        "finalize_response", {
                            "intent": {
                                "query_mode": "PRODUCT_HELP",
                                "owner": "COCKPIT",
                                "understood_request": "x",
                                "response_language": "en",
                                "blocking_ambiguities": None, "resolved_assumptions": None,
                    "canonical_mappings": None, "excluded_parts": None,
                                "public_rationale": "r"},
                            "disposition": "answer",
                            # Refers to a claim it never supplies: a real
                            # contract failure, not a null optional field.
                            "narrative": "Exposure is {{claim.missing}}.",
                            "coverage": None, "numeric_claims": None,
                            "evidence_refs": None, "tables": None,
                            "charts": None, "limitations": None,
                            "suggested_questions": None,
                            "clarification_question": None,
                            "clarification_options": None,
                            "referral_owner": None,
                            "referral_reason": None},
                        "tu-bad")])
                return help_answer()
            if "cut off" in question:
                # The MAC failure shape, for the browser: the first ACTION
                # turn reaches its output allowance with nothing complete in
                # it. The panel must say the attempt was cut off and then go
                # on through the real stages -- not report a call limit for
                # what was one bad call.
                # Counted in USER turns, not assistant ones: a truncated
                # response never enters history, so an assistant counter
                # sees the same turn twice and truncates for ever.
                asks = sum(1 for m in messages if m.get("role") == "user")
                if turn == 0 and asks == 1:
                    from backend.cockpit_v4.provider import OutputTruncated

                    raise OutputTruncated(
                        "the response reached its output allowance",
                        limit=3072)
                book = book_of(system)
                return (analysis_call(book) if turn == 0
                        else analysis_finish(messages, book))
            if "no write-up" in question:
                # Fault 4, for the browser: the analysis RUNS and stores its
                # rows, and then every attempt to WRITE the answer about them
                # reaches its output allowance. The run stops as
                # ANSWER_FORMAT_EXHAUSTED with a real result behind it.
                #
                # The thread must show that result, under a caveat saying the
                # explanation is what failed -- never the empty red stop box
                # the Mac showed over rows that had already been computed.
                if turn == 0:
                    return analysis_call(book_of(system))
                from backend.cockpit_v4.provider import OutputTruncated

                raise OutputTruncated(
                    "the response reached its output allowance", limit=4096)
            if "fail" in question:
                raise KeyError("a scripted application defect")
            if "slow" in question:
                return stall()
            if "who are you" in question:
                return help_answer()
            book = book_of(system)
            return (analysis_call(book) if turn == 0
                    else analysis_finish(messages, book))

    provider = QuestionDrivenProvider()
    runtime = Runtime(cfg=cfg, capability=capability, provider=provider,
                      catalog=catalog, coverage=None,
                      release_summary=summary)
    app = v4app.create_app(cfg, provider=provider, verify_model=False,
                           start_workers=False)
    state = app.state.cockpit_v4
    state["runtime"] = runtime
    routes.install(store=state["store"], runtime=runtime,
                   principal_resolver=v4app._demo_resolver(cfg, runtime),
                   startup_sha=state["startup_sha"])
    worker = Worker(store=state["store"], runtime=runtime)
    supervisor = Supervisor(store=state["store"], lease_stale_seconds=10.0)
    threading.Thread(target=worker.serve_forever, daemon=True).start()
    threading.Thread(target=supervisor.serve_forever, daemon=True).start()
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8414)
    parser.add_argument("--ui-port", type=int, default=0)
    # PER PORT, not one directory for every stack that ever runs.
    #
    # It defaulted to `/tmp/cockpit_v4_browser` for everybody, so two stub
    # servers -- a leftover from an earlier run and the one a suite had just
    # started -- shared one SQLite state database. Both workers polled it,
    # both claimed runs, and the stale process answered questions the live UI
    # was watching, with its own older code and its own older release. Every
    # browser assertion waiting for an answer then timed out against a run
    # that had been settled by a server nobody knew was still up.
    parser.add_argument("--runtime-dir", default="")
    args = parser.parse_args()

    import uvicorn

    runtime_dir = Path(args.runtime_dir
                       or f"/tmp/cockpit_v4_browser_{args.port}")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    app = build_app(args.port, runtime_dir, ui_port=args.ui_port)
    print(f"stub V4 API listening on http://127.0.0.1:{args.port}",
          flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
