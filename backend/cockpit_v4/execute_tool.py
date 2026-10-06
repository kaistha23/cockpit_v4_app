"""
`execute_analysis`: validate exactly, execute exactly, repair never.

The single rule
---------------
The code that runs is byte-for-byte the code the analyst submitted. This
module parses an AST to decide whether to ALLOW it; the parse result is never
what executes. There is no path here that rewrites a filter, removes a join,
substitutes a column that looked close, drops a step to fit a bound, or
computes a business number itself. Every one of those turns a wrong analysis
into a confident one.

When something is wrong, this returns a failure packet naming the exact check
and the exact step, and the analyst writes the replacement. That is the only
repair mechanism V4 has.

Isolation is the actual security boundary, not the allowlists. The DuckDB
session holds ONE domain's relations, already filtered to tenant, release and
domain; it is built with file access enabled and then locked before any
submitted SQL is admitted (`catalog.open_session` does this). A step naming a
relation of the other book is refused by name, not served an empty table.
Python runs in a separate process jail or is reported UNAVAILABLE -- it is
never quietly evaluated in this process.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4 import derivation as deriv
from backend.cockpit_v4 import sql as v4_sql
from backend.cockpit_v4.contracts import ExecutionSubmission, Rejection, Step
from backend.cockpit_v4.provider import code_digest
from backend.cockpit_v4.sqlbind import (BindFailure, parameter_argument,
                                        placeholders, prove_bindable)
from backend.cockpit_v4.states import (INTERNAL_ERROR, PYTHON_UNAVAILABLE,
                                       SECURITY_DENIED, SQL_RUNTIME,
                                       SQL_VALIDATION)

#: Checks are named so a failure says WHICH one refused, not "invalid query".
CHECK_STRUCTURE = "sql_structure"
CHECK_AUTHORIZATION = "relation_authorization"
CHECK_BIND = "bind"
CHECK_GRAIN = "join_grain"
CHECK_DOMAIN = "domain_authorization"
CHECK_RUNTIME = "runtime"
CHECK_SANDBOX = "sandbox"
#: `failed_check` on a step the batch never reached. Not checks, and not
#: failures OF the step -- named here so the phase table below can be
#: exhaustive over every value the field actually takes.
CHECK_DEPENDENCY = "dependency"
CHECK_BATCH = "batch"

#: HOW FAR A STEP GOT BEFORE IT STOPPED. A LADDER, NOT A SET OF LABELS.
#:
#: "runtime" is the only rung on which the engine executed anything, so
#: whether a step ran is not a second judgement about its failure -- it is
#: this one, read again. That matters because the two used to be separate
#: expressions over the same input, and separate expressions may disagree:
#: a join-grain refusal was published as `phase="runtime"` AND
#: `executed=true`, two wrong answers from two unrelated mistakes, about a
#: batch in which the engine was never asked for anything.
PHASE_NOT_STARTED = "not_started"
PHASE_CHECK = "check"
PHASE_BIND = "bind"
PHASE_RUNTIME = "runtime"

#: Where each refusal happens. Exhaustive by test: every CHECK_* constant in
#: this module must appear here, because the cost of an omission is exactly
#: the defect this table replaces -- `join_grain` and `sandbox` fell through
#: a two-valued switch and were published as queries that ran.
PHASE_OF_CHECK: dict[str, str] = {
    CHECK_STRUCTURE: PHASE_CHECK,
    CHECK_AUTHORIZATION: PHASE_CHECK,
    CHECK_DOMAIN: PHASE_CHECK,
    CHECK_GRAIN: PHASE_CHECK,
    CHECK_SANDBOX: PHASE_CHECK,
    CHECK_BIND: PHASE_BIND,
    CHECK_RUNTIME: PHASE_RUNTIME,
    CHECK_DEPENDENCY: PHASE_NOT_STARTED,
    CHECK_BATCH: PHASE_NOT_STARTED,
}

#: The checks that refuse before the engine is asked for anything.
REFUSED_BEFORE_EXECUTION: frozenset[str] = frozenset(
    name for name, phase in PHASE_OF_CHECK.items() if phase != PHASE_RUNTIME)


def phase_of(check: str) -> str:
    """The rung a step reached, from the check that stopped it.

    An unknown check reads as "check", never "runtime": a name this module
    does not recognise cannot be evidence that a query ran.
    """
    return PHASE_OF_CHECK.get(str(check or ""), PHASE_CHECK)


def column_units(catalog: Any, columns: list[str],
                 relations: list[str]) -> dict[str, str]:
    """The unit of each result column, where the catalogue can say.

    Resolved by EXACT column name against the relations this step was
    authorized to read. An alias that matches a catalogue column is that
    column; an alias that does not -- `ead_reported_sar_mn`, `breaches`,
    `total` -- is not resolved, and is left out rather than guessed at.

    Guessing here would be worse than silence. A column whose name merely
    LOOKS like an amount could be a count of amounts, a ratio of them, or a
    year; publishing "SAR" against it because the letters matched is how a
    reader is shown a currency nobody computed. What is unresolved stays a
    plain number, which is honest, and a claim that names the column can
    still declare its unit -- that declaration is checked.
    """
    from backend.cockpit_v4 import display as disp

    out: dict[str, str] = {}
    for column in columns:
        for relation in relations:
            unit = disp.unit_for_field(catalog, relation, column)
            if unit:
                out[column] = unit
                break
    return out


def claim_guide(artifact_id: str, columns: list[str], row_ids: list[str],
                row_count: int, money_unit: str = "",
                complete: bool = True) -> dict[str, Any]:
    """What is true of THIS result, for the claims written about it.

    The rules a claim must satisfy are constant -- they are the validator's
    rules, and they do not vary by result -- so they live in the analyst
    system prompt, where they are sent once and cached. What cannot live
    there is this: which artifact, which columns, how many rows, and what a
    correct claim over THEM looks like in THIS release's money unit.

    It used to carry both, restated in full for every step of every batch:
    ~2.8 KB of identical prose and the whole row-id list three more times
    over, per step. On a four-step result that was 20 KB of repetition in
    the payload the answer turn then had to read before writing anything --
    and the answer turn was the one running out of room.

    The worked examples stay, and stay HERE rather than in the prompt,
    because `money_unit` is what makes them correct: an example denominated
    in the wrong currency steers the analyst into declaring the wrong unit
    on every amount, which is exactly the release-isolation failure this
    argument was added to prevent.
    """
    sample = list(row_ids[:2]) or ["r0"]
    value_column = columns[-1] if columns else "value"
    addressed = len(row_ids)
    whole = {"artifact_id": artifact_id, "column_id": value_column,
             "rows": deriv.ALL_ROWS}
    return {
        "artifact_id": artifact_id,
        "columns": list(columns),
        "row_count": row_count,
        "money_unit": money_unit,
        "complete": complete,
        # WHAT IS ADDRESSABLE, WHICH IS NOT ALWAYS WHAT WAS PRODUCED.
        #
        # This sentence used to quote the QUERY's row count while `row_ids`
        # held only the preview's, so a clipped result told the analyst
        # there were five thousand rows addressed "r0" upward when there
        # were a hundred. It now says how many can be named, and whether
        # that is the whole result -- which is also what decides whether
        # `rows: "all"` may be said about it.
        "row_ids_are": (
            f"published with this result as `row_ids` -- {addressed} of "
            f"them, \"r0\" upward. Name the real ones; a row id this "
            f"artifact does not contain is refused."
            + ("" if complete else
               f" This result was CLIPPED: {row_count} rows were produced "
               f"and {addressed} are published, so no total over it is that "
               f"result's total.")),
        # TWO FORMS, BECAUSE THERE ARE TWO CASES. A total is usually over
        # everything, which is what `rows: "all"` says in nine bytes rather
        # than in a hundred row ids; a share usually is not, so its
        # numerator still names the rows it means.
        "example_total": {
            "claim_id": "total_x", "unit": money_unit,
            "derivation": {"operation": "sum", "operands": [dict(whole)]}},
        "example_share": {
            "claim_id": "top_share", "unit": "percent",
            "derivation": {"operation": "percentage", "operands": [
                {"artifact_id": artifact_id, "column_id": value_column,
                 "row_ids": sample[:1]},
                dict(whole)]}},
    }


@dataclass
class StepResult:
    step_id: str
    status: str
    language: str
    code_digest: str
    purpose: str
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    preview: list[dict[str, Any]] = field(default_factory=list)
    #: The published id of each preview row, aligned with `preview`. These
    #: are what a numeric claim references. They are published rather than
    #: left to be guessed because a live run, given no ids, invented row
    #: labels ("all sectors", "top 4 sectors") and had its answer refused.
    row_ids: list[str] = field(default_factory=list)
    #: The money unit of THIS release, used for the worked example in the
    #: claim guide. Carried rather than imported: a worked example showing a
    #: currency the selected release does not use would steer the analyst
    #: into declaring the wrong unit on every amount.
    money_unit: str = ""
    #: column -> the unit that column holds, resolved from the catalogue.
    #: Published so the analyst can DECLARE a unit on a claim over that
    #: column: without it a column of floats is just floats, and "SAR"
    #: would be a guess. Only columns the catalogue could actually name
    #: appear here.
    units: dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    artifact_id: str = ""
    warnings: list[str] = field(default_factory=list)
    error_code: str = ""
    failed_check: str = ""
    message: str = ""
    elapsed_ms: int = 0
    #: The rung this step reached: "not_started", "check", "bind" or
    #: "runtime". Empty means "derive it from `failed_check`", which is what
    #: every caller should let it do. A query that never bound did not
    #: execute, and the trace must not say it did.
    phase: str = ""
    engine_detail: dict[str, Any] = field(default_factory=dict)

    @property
    def reached_phase(self) -> str:
        return self.phase or phase_of(self.failed_check)

    @property
    def ran(self) -> bool:
        """Whether the engine executed this step's code.

        One fact, read here and by `reached_phase`, so the two can never
        contradict each other the way they used to.
        """
        return self.status == "ok" or self.reached_phase == PHASE_RUNTIME

    def to_dict(self) -> dict[str, Any]:
        out = {"step_id": self.step_id, "status": self.status,
               "language": self.language, "purpose": self.purpose,
               "executed_code_digest": self.code_digest,
               "elapsed_ms": self.elapsed_ms}
        if self.status == "ok":
            out.update({"columns": self.columns, "row_count": self.row_count,
                        "preview": self.preview, "row_ids": self.row_ids,
                        "preview_truncated": self.truncated,
                        "artifact_id": self.artifact_id,
                        # `column_units` names what each column holds, which
                        # is what the analyst needs to declare a unit. The
                        # rows themselves are sent ONCE, canonical: a second
                        # copy rendered as display strings used to ride
                        # along (~11 KB a step, 44% of the block) and
                        # nothing but the model ever read it -- while the
                        # model is told, in the same payload, not to type
                        # numbers at all. CreditProbe formats every figure
                        # a reader sees; the analyst never needed the
                        # formatted form to write about it.
                        "column_units": dict(self.units),
                        "how_to_cite_these_numbers": claim_guide(
                            self.artifact_id, self.columns, self.row_ids,
                            self.row_count, self.money_unit,
                            not self.truncated)})
        else:
            out.update({"error_code": self.error_code,
                        "failed_check": self.failed_check,
                        "message": self.message,
                        "phase": self.reached_phase,
                        "executed": self.ran})
        if self.warnings:
            out["warnings"] = self.warnings
        return out


@dataclass
class BatchResult:
    submission_id: str
    status: str
    steps: list[StepResult]
    validated_all: bool
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"submission_id": self.submission_id, "status": self.status,
                "validated_whole_batch_before_running": self.validated_all,
                "steps": [s.to_dict() for s in self.steps],
                **({"message": self.message} if self.message else {})}


class StepFailed(Exception):
    def __init__(self, code: str, check: str, message: str, *,
                 detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.check = check
        self.message = message
        #: Operator-only. The engine's own diagnostic, for the trace.
        self.detail = dict(detail or {})


_RELATION_IN_SQL = re.compile(
    r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

#: `WITH name AS (` and `, name AS (`. A common table expression is a name the
#: query defines for itself, not a relation it reads from the release --
#: treating one as an unauthorized table would refuse perfectly legitimate
#: analyst SQL, which is a worse failure than the check is worth.
_CTE_NAME = re.compile(
    r"(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", re.IGNORECASE)


def _local_names(sql: str) -> set[str]:
    """Names the query defines itself: CTEs and derived-table aliases."""
    names = {m.lower() for m in _CTE_NAME.findall(sql)}
    # `) alias` and `) AS alias` after a derived table.
    names |= {m.lower() for m in re.findall(
        r"\)\s*(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*)", sql, re.IGNORECASE)}
    return names


@dataclass
class ExecutionService:
    """Validates and runs one submission against the pinned release."""

    session: Any
    scope: Any
    catalog: Any
    store: Any
    run_id: str
    tenant_id: str
    release_id: str
    limits: Any
    python_runner: Any = None
    #: The run's release header. Stamped on every artifact, so an artifact
    #: carries a statement about the BYTES it was computed from and not only
    #: the name they were published under.
    header: Any = None
    #: Result artifacts produced in this run, by step id, for dependencies.
    artifacts: dict[str, str] = field(default_factory=dict)
    #: Raw-SQL intent checks (SHADOW ONLY). The orchestrator sets the question
    #: and envelope; `validate_batch` appends `intent_check.Finding`s to
    #: `shadow_findings` and the orchestrator may read and emit them. Nothing
    #: here ever refuses, and with no question set the check is a no-op.
    intent_question: str = ""
    intent_envelope: Any = None
    shadow_findings: list = field(default_factory=list)

    # -- static validation of the WHOLE batch ---------------------------

    def _money_unit(self) -> str:
        """The money unit of the release this run is reading."""
        from backend.cockpit_v4 import precision as prec

        return prec.money_unit(self.catalog)

    def validate_batch(self, submission: ExecutionSubmission) -> None:
        """Every step is checked before ANY step runs.

        A batch whose fourth step names a table that does not exist should
        not have run its first three: a partial execution leaves artifacts an
        analyst may reasonably think are complete.
        """
        if not submission.intent.may_execute:
            raise Rejection(
                SECURITY_DENIED,
                f"Execution is available only for a declared DATA_ANALYSIS "
                f"owned by COCKPIT with no BLOCKING ambiguity. This "
                f"submission declared {submission.intent.query_mode} / "
                f"{submission.intent.owner}"
                + (f" and left "
                   f"{list(submission.intent.blocking_ambiguities)} "
                   f"unresolved, so the question goes back to the reader "
                   f"rather than being answered under a reading nobody "
                   f"chose. If the reading would NOT change the figures it "
                   f"was never blocking: that belongs in "
                   f"resolved_assumptions or canonical_mappings, which do "
                   f"not stop execution."
                   if submission.intent.blocking_ambiguities else "."),
                field_path="intent")
        bound: list[str] = []
        deferred: list[str] = []
        for step in submission.steps:
            if step.language == "sql":
                self._validate_sql(step)
                self._check_join_grain(step)
                if self._bindable_now(step):
                    self._prove_bindable(step)
                    bound.append(step.step_id)
                else:
                    deferred.append(step.step_id)
                self._shadow_intent(step)
            else:
                self._validate_python(step)
        return {"bound_now": bound, "bound_at_run_time": deferred}

    def _shadow_intent(self, step: Step) -> None:
        """Record raw-SQL intent findings on `self.shadow_findings`; never refuse.

        Least invasive channel: a plain list attribute, so validate_batch's
        return value and every rejection path stay exactly as they were. Any
        failure here is swallowed -- a check bug must not break execution.
        """
        try:
            from backend.cockpit_v4 import intent_check

            if intent_check.mode() == "off" or not self.intent_question:
                return
            self.shadow_findings.extend(intent_check.check_sql(
                step.code, question=self.intent_question,
                envelope=self.intent_envelope, session=self.session))
        except Exception:  # noqa: BLE001
            pass

    def _check_join_grain(self, step: Step) -> None:
        """An additive measure aggregated across a join that repeats it.

        Checked HERE, with the other static checks, and no longer inside
        `_run_sql`. It is a text-and-catalogue judgement -- no bound
        parameters, no engine, no earlier artifact -- so there was never a
        reason for it to wait until the step budget had been spent and the
        run had publicly announced the very query it refuses.

        Ordered AFTER `_validate_sql`, because it must not lecture about
        join cardinality when the real fault is that the query names the
        other book's table. That order also means the statement has already
        been proven to parse by the time the grain check parses it again:
        if `multiplication_risk` cannot parse what `_validate_sql` could,
        the two disagree, and that is exactly the operator's problem the
        arm below reports rather than an analyst's syntax error.
        Ordered BEFORE `_prove_bindable`, because that one is skipped for
        steps with dependencies -- putting grain after it would make the
        refusal depend on whether a step happened to declare one.
        """
        if self.session is None:
            return
        try:
            risk = v4_sql.multiplication_risk(step.code, self.session)
        except Exception as exc:  # noqa: BLE001
            # A DIAGNOSTIC THAT CANNOT RUN IS NOT AN ALL-CLEAR. This used to
            # be `except Exception: risk = None`, which turned a broken
            # check into a clean bill of health -- the one outcome a check
            # must never produce. It is an operator's problem and is
            # reported as one, so the reader is pointed at somebody who can
            # fix it rather than asked to rewrite SQL that is not wrong.
            raise Rejection(
                INTERNAL_ERROR,
                f"step {step.step_id} was not run: the join-grain check "
                f"could not be completed ({exc}). Nothing was executed and "
                f"the query was not modified.",
                field_path=f"steps.{step.step_id}.code",
                detail={"failed_check": CHECK_GRAIN, "phase": PHASE_CHECK,
                        "check_completed": False}) from exc
        if risk is None:
            return
        raise Rejection(
            SQL_VALIDATION,
            f"step {step.step_id}: {risk}",
            field_path=f"steps.{step.step_id}.code",
            detail={"failed_check": CHECK_GRAIN, "phase": PHASE_CHECK,
                    "category": risk.category, "relation": risk.relation,
                    "owning_domain": risk.domain_id,
                    "explanation": risk.detail, **dict(risk.facts)})

    @staticmethod
    def _bindable_now(step: Step) -> bool:
        """Whether this step can be bound before anything has run.

        A SQL step in V4 is self-contained: earlier step outputs are not
        registered as relations, so a query that needs one carries it as a
        CTE. A step that nonetheless declares a dependency is bound
        immediately before it runs instead, and the validation message says
        so rather than implying a proof that was not obtained.
        """
        return not (step.depends_on_step_ids or step.input_artifact_ids)

    def _prove_bindable(self, step: Step) -> None:
        """DuckDB must resolve the query, or this is not a validated query.

        Announcing "validated" and then failing the binder was the defect:
        two true statements in the wrong order. The proof costs an EXPLAIN,
        which executes nothing.
        """
        try:
            prove_bindable(step.code, self.session, parameters=step.parameters)
        except BindFailure as exc:
            # Where to look, never what to use instead (V4-AT-048): the
            # catalogue is the only authority on which names exist.
            lookup = (f" '{exc.unresolved}' is not a name in this book; check "
                      f"the canonical measures or call inspect_catalog before "
                      f"using a column you have not seen there."
                      if exc.unresolved else "")
            raise Rejection(
                SQL_VALIDATION,
                f"step {step.step_id} did not bind: {exc.message}{lookup} "
                f"Nothing was executed and nothing was repaired.",
                field_path=f"steps.{step.step_id}.code",
                detail={"failed_check": CHECK_BIND, **exc.detail()}) from exc

    def _validate_sql(self, step: Step) -> None:
        try:
            v4_sql.check_structure(step.code)
        except v4_sql.SqlRejected as exc:
            raise Rejection(
                SQL_VALIDATION,
                f"step {step.step_id}: {exc}",
                field_path=f"steps.{step.step_id}.code",
                detail={"failed_check": CHECK_STRUCTURE}) from exc
        except Exception as exc:  # noqa: BLE001 - a parse failure is a refusal
            raise Rejection(
                SQL_VALIDATION,
                f"step {step.step_id}: the SQL could not be parsed: {exc}",
                field_path=f"steps.{step.step_id}.code",
                detail={"failed_check": CHECK_STRUCTURE}) from exc

        if self.session is not None:
            try:
                v4_sql.authorize(step.code, self.session,
                                 also_allowed=tuple(self.artifacts))
            except v4_sql.SqlRejected as exc:
                # A relation of the other book is refused as exactly that,
                # naming its owner. Reaching across is a SECURITY refusal and
                # not a validation nit: the alternative is an answer computed
                # from a book the reader did not ask about.
                raise Rejection(
                    SECURITY_DENIED if exc.category ==
                    v4_sql.CROSS_DOMAIN_ACCESS else SQL_VALIDATION,
                    f"step {step.step_id}: {exc}",
                    field_path=f"steps.{step.step_id}.code",
                    detail={"failed_check": CHECK_DOMAIN,
                            "category": exc.category,
                            "relation": exc.relation,
                            "owning_domain": exc.domain_id}) from exc

        authorized = set(self.session.relations) if self.session else set()
        named = {m.lower() for m in _RELATION_IN_SQL.findall(step.code)}
        # Names the query defines for itself, and step outputs registered as
        # views, are legitimate inputs rather than unauthorized tables.
        named -= _local_names(step.code)
        named -= {a.lower() for a in self.artifacts}
        unknown = {n for n in named
                   if n not in {r.lower() for r in authorized}}
        if unknown and authorized:
            raise Rejection(
                SQL_VALIDATION,
                f"step {step.step_id} names {sorted(unknown)}, which are not "
                f"authorized relations in this release. The authorized "
                f"relations are {sorted(authorized)}. Nothing was "
                f"substituted.",
                field_path=f"steps.{step.step_id}.code",
                detail={"failed_check": CHECK_AUTHORIZATION,
                        "authorized_relations": sorted(authorized)})

    def _validate_python(self, step: Step) -> None:
        if self.python_runner is None or not getattr(
                self.python_runner, "available", False):
            raise Rejection(
                PYTHON_UNAVAILABLE,
                f"step {step.step_id} is Python and no isolated Python "
                f"runner is available in this runtime. The step was NOT "
                f"rewritten as SQL and nothing was executed. Either submit "
                f"SQL deliberately, or report the capability as unavailable.",
                field_path=f"steps.{step.step_id}.language",
                detail={"failed_check": CHECK_SANDBOX})
        check = getattr(self.python_runner, "validate", None)
        if callable(check):
            problem = check(step.code)
            if problem:
                raise Rejection(
                    SECURITY_DENIED,
                    f"step {step.step_id}: {problem}",
                    field_path=f"steps.{step.step_id}.code",
                    detail={"failed_check": CHECK_SANDBOX})

    # -- execution -------------------------------------------------------

    def run_batch(self, submission: ExecutionSubmission, *,
                  submission_id: str, deadline_seconds: float,
                  on_step: Any = None) -> BatchResult:
        """Run the validated batch serially, stopping dependents on failure."""
        results: list[StepResult] = []
        failed: set[str] = set()

        for step in submission.steps:
            if callable(on_step):
                on_step("started", step, None)
            blocked = [d for d in step.depends_on_step_ids if d in failed]
            if blocked:
                result = StepResult(
                    step_id=step.step_id, status="not_run",
                    language=step.language, code_digest=code_digest(step.code),
                    purpose=step.purpose, error_code="DEPENDENCY_FAILED",
                    failed_check=CHECK_DEPENDENCY,
                    message=(f"not run: it depends on {blocked}, which "
                             f"failed. Earlier successful steps are "
                             f"preserved."))
                results.append(result)
                failed.add(step.step_id)
                if callable(on_step):
                    on_step("skipped", step, result)
                continue

            remaining = deadline_seconds - sum(
                r.elapsed_ms for r in results) / 1000.0
            budget = max(0.5, min(self.limits.step_seconds, remaining))
            started = time.monotonic()
            try:
                result = self._run_step(step, deadline_seconds=budget)
            except StepFailed as exc:
                result = StepResult(
                    step_id=step.step_id, status="failed",
                    language=step.language, code_digest=code_digest(step.code),
                    purpose=step.purpose, error_code=exc.code,
                    failed_check=exc.check, message=exc.message,
                    # `phase` is NOT passed. It is derived from the check,
                    # in one place, so a check added later cannot land in
                    # the wrong rung by default -- which is how `join_grain`
                    # and `sandbox` came to be published as queries that ran.
                    engine_detail=dict(exc.detail))
                failed.add(step.step_id)
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            results.append(result)
            if callable(on_step):
                on_step("completed" if result.status == "ok" else "failed",
                        step, result)
            if result.status != "ok":
                # Stop the batch. Later steps that did not depend on this one
                # are reported not_run rather than silently omitted.
                for later in submission.steps[len(results):]:
                    results.append(StepResult(
                        step_id=later.step_id, status="not_run",
                        language=later.language,
                        code_digest=code_digest(later.code),
                        purpose=later.purpose, error_code="BATCH_STOPPED",
                        failed_check=CHECK_BATCH,
                        message=("not run: an earlier step in this batch "
                                 "failed.")))
                break

        ok = all(r.status == "ok" for r in results)
        return BatchResult(
            submission_id=submission_id,
            status="ok" if ok else "failed", steps=results,
            validated_all=True,
            message="" if ok else (
                "One step failed. Earlier successful results are preserved "
                "as artifacts and are listed above. CreditProbe did not "
                "repair anything: the corrected code is yours to write."))

    def _run_step(self, step: Step, *, deadline_seconds: float) -> StepResult:
        if step.language == "python":
            return self._run_python(step, deadline_seconds=deadline_seconds)
        return self._run_sql(step, deadline_seconds=deadline_seconds)

    def _run_sql(self, step: Step, *, deadline_seconds: float) -> StepResult:
        digest = code_digest(step.code)
        # Bound again here, with its parameters, because a step deferred at
        # validation has not been proven yet and because the binder is cheap.
        try:
            prove_bindable(step.code, self.session, parameters=step.parameters)
        except BindFailure as exc:
            raise StepFailed(SQL_VALIDATION, CHECK_BIND, exc.message,
                             detail=exc.detail()) from exc

        # The join-grain check used to sit here, between the bind and the
        # execution, with three branches around it that could not run: an
        # `except` for an exception the check did not then raise, an
        # `except Exception: risk = None` that turned a broken diagnostic
        # into a clean bill of health, and a `warnings.append` the branch
        # above it had already made unreachable. It is a judgement about the
        # query TEXT, so it now lives with the other static checks in
        # `validate_batch` -- where it refuses before the step budget is
        # spent and before the run announces the query as checked.
        try:
            result = self._execute_sql(step, deadline_seconds=deadline_seconds)
        except v4_sql.SqlRejected as exc:
            raise StepFailed(SQL_RUNTIME, CHECK_RUNTIME, str(exc)) from exc
        except StepFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StepFailed(SQL_RUNTIME, CHECK_RUNTIME, str(exc)) from exc

        columns = [c["name"] for c in result.columns]
        artifact_id = self.store.put_artifact(
            run_id=self.run_id, tenant_id=self.tenant_id, kind="result",
            release_id=self.release_id,
            scope={"relations": list(getattr(self.session, "relations", ())),
                   # WHAT THIS QUERY ACTUALLY READ.
                   #
                   # `relations` above is the whole authorized set -- every
                   # table the session grants, whether the query touched it
                   # or not. A governance record built from that says a
                   # question about corporate exposure read the retail
                   # book, which is both wrong and alarming. The parser
                   # that can tell the difference already existed and its
                   # answer was thrown away at the authorization check.
                   "referenced_relations": sorted(
                       v4_sql.referenced_relations(step.code)),
                   "step_id": step.step_id,
                   "domain_id": str(getattr(self.catalog, "domain_id", "")),
                   # WHETHER THIS ARTIFACT IS THE WHOLE RESULT.
                   #
                   # A query past the preview cap is clipped before it is
                   # stored, so the artifact holds the first N rows of a
                   # larger answer and nothing in the record said so. A
                   # claim that says "every row" of a clipped result would
                   # publish a partial total that reads as a complete one,
                   # which is what the engine's own clipped-table warning
                   # is about. Recorded here so the claim validator can
                   # refuse it rather than infer it.
                   "complete": not result.truncated,
                   "produced_rows": int(result.row_count),
                   **({"release_fingerprint":
                       self.header.release_fingerprint} if self.header
                      else {})},
            columns=columns, rows=result.rows, code_digest=digest)
        self.artifacts[step.step_id] = artifact_id

        warnings: list[str] = []
        preview_columns = columns[:self.limits.preview_columns]
        if len(columns) > self.limits.preview_columns:
            warnings.append(
                f"This preview shows {len(preview_columns)} of "
                f"{len(columns)} columns. The full result is artifact "
                f"{artifact_id}; read it rather than concluding from the "
                f"preview.")
        preview = [{k: row.get(k) for k in preview_columns}
                   for row in result.rows[:self.limits.preview_rows]]
        row_ids = [deriv.row_id_for(i) for i in range(len(preview))]
        units = column_units(
            self.catalog, preview_columns,
            list(getattr(self.session, "relations", ())))
        return StepResult(
            step_id=step.step_id, status="ok", language="sql",
            code_digest=digest, purpose=step.purpose, columns=columns,
            row_count=result.row_count, preview=preview, row_ids=row_ids,
            money_unit=self._money_unit(), units=units,
            truncated=bool(result.truncated) or len(
                columns) > self.limits.preview_columns,
            artifact_id=artifact_id,
            warnings=warnings + list(result.warnings))

    def _execute_sql(self, step: Step, *, deadline_seconds: float):
        """Run the step exactly as submitted, with its declared parameters.

        With no parameters this is the V4 executor unchanged — the deadline
        watchdog, the row limits and the error classification are all its
        own. With parameters it is the same connection and the same deadline,
        with the argument DuckDB needs, because a `parameters` object the
        engine never sees is a contract the application advertised and did
        not honour.
        """
        argument = parameter_argument(step.code, step.parameters)
        if argument is None:
            return v4_sql.execute(
                step.code, self.session, deadline_seconds=deadline_seconds,
                max_rows=self.limits.preview_rows)

        import threading

        timed_out = threading.Event()
        finished = threading.Event()

        def watchdog() -> None:
            if not finished.wait(max(0.05, deadline_seconds)):
                timed_out.set()
                try:
                    self.session.connection.interrupt()
                except Exception:  # noqa: BLE001
                    pass

        started = time.monotonic()
        threading.Thread(target=watchdog, daemon=True).start()
        try:
            frame = self.session.connection.execute(
                step.code, argument).fetch_df()
        except Exception as exc:  # noqa: BLE001
            finished.set()
            if timed_out.is_set():
                raise StepFailed(
                    SQL_RUNTIME, CHECK_RUNTIME,
                    f"the query was cancelled after {deadline_seconds:.0f} "
                    f"seconds.") from exc
            from backend.cockpit_v4 import sqlbind as _sqlbind
            raise StepFailed(SQL_RUNTIME, CHECK_RUNTIME,
                             _sqlbind.sanitize(str(exc)) or type(exc).__name__) from exc
        finally:
            finished.set()

        total = int(len(frame))
        warnings: list[str] = []
        shown = frame.head(self.limits.preview_rows)
        truncated = total > self.limits.preview_rows
        if truncated:
            warnings.append(
                f"{total} rows were produced and the first "
                f"{self.limits.preview_rows} are shown. This is a CLIPPED "
                f"table, not a complete aggregate: do not read a total off "
                f"it.")
        return v4_sql.SqlResult(
            columns=[{"name": str(name), "type": str(dtype)}
                     for name, dtype in zip(frame.columns, frame.dtypes)],
            rows=shown.replace({float("nan"): None}).to_dict(
                orient="records"),
            row_count=total, truncated=truncated,
            elapsed_seconds=round(time.monotonic() - started, 4),
            warnings=warnings,
            domain_id=str(getattr(self.catalog, "domain_id", "")),
            dataset_release_id=str(getattr(
                self.catalog, "dataset_release_id", "")))

    def _run_python(self, step: Step, *, deadline_seconds: float) -> StepResult:
        if self.python_runner is None or not getattr(
                self.python_runner, "available", False):
            raise StepFailed(
                PYTHON_UNAVAILABLE, CHECK_SANDBOX,
                "no isolated Python runner is available in this runtime; the "
                "step was not executed and was not converted to SQL.")
        digest = code_digest(step.code)
        inputs = {sid: self.artifacts.get(sid, "")
                  for sid in step.depends_on_step_ids}
        outcome = self.python_runner.run(
            code=step.code, parameters=step.parameters, inputs=inputs,
            deadline_seconds=deadline_seconds,
            memory_mib=self.limits.python_memory_mib,
            output_bytes=self.limits.output_bytes_per_step)
        if not outcome.get("ok"):
            raise StepFailed(
                str(outcome.get("error_code") or SQL_RUNTIME),
                str(outcome.get("failed_check") or CHECK_RUNTIME),
                str(outcome.get("message") or "the Python step failed."))
        columns = list(outcome.get("columns") or [])
        rows = list(outcome.get("rows") or [])
        artifact_id = self.store.put_artifact(
            run_id=self.run_id, tenant_id=self.tenant_id, kind="result",
            release_id=self.release_id,
            scope={"step_id": step.step_id, "language": "python",
                   "domain_id": str(getattr(self.catalog, "domain_id", "")),
                   # A Python step reads ARTIFACTS, not relations, and the
                   # step it read them from is the lineage. Stated rather
                   # than left absent, so a governance record does not
                   # have to decide what an empty field means.
                   "referenced_relations": [],
                   "input_artifact_ids": list(step.input_artifact_ids),
                   # The other direction: a Python step stores every row it
                   # produced, and publishes an id for the first N. "Every
                   # row" would then mean cells the analyst was never shown.
                   "complete": len(rows) <= self.limits.preview_rows,
                   "produced_rows": len(rows)},
            columns=columns, rows=rows, code_digest=digest)
        self.artifacts[step.step_id] = artifact_id
        return StepResult(
            step_id=step.step_id, status="ok", language="python",
            code_digest=digest, purpose=step.purpose, columns=columns,
            row_count=len(rows), preview=rows[:self.limits.preview_rows],
            row_ids=[deriv.row_id_for(i) for i in
                     range(min(len(rows), self.limits.preview_rows))],
            money_unit=self._money_unit(),
            artifact_id=artifact_id,
            warnings=list(outcome.get("warnings") or []))


def no_progress_key(submission: ExecutionSubmission, *, release_id: str,
                    error_class: str = "") -> str:
    """Identity of an attempt, for the no-progress rule.

    Includes the error CLASS so a transient environment failure is not cached
    forever as an invalid query, while an identical deterministic failure
    cannot be paid for twice.
    """
    import hashlib
    parts = [release_id, error_class]
    for step in submission.steps:
        parts.append(step.language)
        parts.append(step.code)
        parts.append(repr(sorted(step.parameters.items())))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


__all__ = ["BatchResult", "CHECK_AUTHORIZATION", "CHECK_BIND", "CHECK_GRAIN",
           "CHECK_RUNTIME", "CHECK_SANDBOX", "CHECK_STRUCTURE",
           "ExecutionService", "StepFailed", "StepResult", "no_progress_key"]
