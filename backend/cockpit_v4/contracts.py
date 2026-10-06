"""
The typed contracts: intent, the four tools, the answer, events and errors.

The rule this module exists to enforce
--------------------------------------
Validation may REJECT. It may never REPAIR. Every function here that finds a
problem returns a `Rejection` naming the exact field and why, and that
rejection goes back to the analyst as a tool_result so IT can revise. Nothing
here rewrites a query, drops a step, trims a subquestion, coerces a scalar
into a list "helpfully", or substitutes a field name that looked close. Those
are the failures V3 shipped, and each of them changes the substance of an
analysis while every test still passes.

The one thing that is NOT authorship: deterministic formatting of a value
that is already bound to executed evidence. Rounding 1234.5 to "1,234.50" at
the declared precision is presentation. Changing which column it came from is
not, and this module cannot do it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.cockpit_v4 import derivation as derivation_mod
from backend.cockpit_v4 import TOOL_CONTRACT_VERSION

CONTRACTS_DIR = Path(__file__).resolve().parent / "contracts"

# ---- modes, owners, dispositions --------------------------------------

PRODUCT_HELP = "PRODUCT_HELP"
THEORY_CONCEPT = "THEORY_CONCEPT"
DATA_ANALYSIS = "DATA_ANALYSIS"
OTHER_FUNCTIONALITY = "OTHER_FUNCTIONALITY"
CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
UNSUPPORTED = "UNSUPPORTED"

QUERY_MODES: tuple[str, ...] = (
    PRODUCT_HELP, THEORY_CONCEPT, DATA_ANALYSIS, OTHER_FUNCTIONALITY,
    CLARIFICATION_REQUIRED, UNSUPPORTED)

#: The modes that answer from knowledge and never touch the portfolio.
NO_EXECUTION_MODES: frozenset[str] = frozenset(
    {PRODUCT_HELP, THEORY_CONCEPT, OTHER_FUNCTIONALITY,
     CLARIFICATION_REQUIRED, UNSUPPORTED})

COCKPIT = "COCKPIT"
OWNERS: tuple[str, ...] = (
    COCKPIT, "EWS", "CREDIT_SCORING", "SCORECARD_VALIDATION", "WHAT_IF",
    "LENSES", "GENERAL_CREDITPROBE_HELP", "NONE")

DISPOSITIONS: tuple[str, ...] = (
    "answer", "partial_answer", "referral", "clarification",
    "unsupported", "safe_failure")

TOOL_INSPECT = "inspect_catalog"
TOOL_PRODUCT = "inspect_product_knowledge"
TOOL_EXECUTE = "execute_analysis"
TOOL_READ = "read_artifact"
TOOL_FINALIZE = "finalize_response"
TOOL_NAMES: tuple[str, ...] = (
    TOOL_INSPECT, TOOL_PRODUCT, TOOL_EXECUTE, TOOL_READ, TOOL_FINALIZE)

#: The reads the analyst may batch in one response. Anything that executes or
#: finalizes is one action per response, because a batch that mixes them has
#: no defined order and no safe partial outcome.
BATCHABLE: frozenset[str] = frozenset(
    {TOOL_INSPECT, TOOL_PRODUCT, TOOL_READ})
MAX_BATCHED_READS = 4


class Rejection(Exception):
    """A contract violation, stated as a fact the analyst can act on.

    Carries the field path so the tool_result says WHICH part was wrong. A
    rejection that says only "invalid arguments" costs a whole generation
    attempt to discover what this one already knows.
    """

    def __init__(self, code: str, message: str, *, field_path: str = "",
                 detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_path = field_path
        self.detail = dict(detail or {})

    def to_tool_result(self) -> dict[str, Any]:
        return {"status": "rejected", "error_code": self.code,
                "field": self.field_path, "message": self.message,
                **({"detail": self.detail} if self.detail else {})}


# ---- intent ------------------------------------------------------------

@dataclass(frozen=True)
class Intent:
    """What the analyst says this turn is, declared with its first action."""

    query_mode: str
    owner: str
    understood_request: str
    response_language: str
    #: Terms with two defensible readings that would produce materially
    #: different numbers. THE ONLY THING THAT STOPS EXECUTION.
    blocking_ambiguities: tuple[str, ...]
    #: Choices the analyst made and is declaring. Transparency, not a
    #: question: "period not specified, using the latest populated quarter".
    resolved_assumptions: tuple[str, ...]
    #: Terms this domain already defines: "exposure at default = EAD =
    #: ead_reported". Not even a choice.
    canonical_mappings: tuple[str, ...]
    excluded_parts: tuple[str, ...]
    public_rationale: str

    @property
    def may_execute(self) -> bool:
        """Execution is allowed for a declared Cockpit data analysis with no
        BLOCKING ambiguity.

        The distinction is the point. A live run recorded "Exposure read as
        reported EAD (ead_reported)" and "Period not specified: using the
        latest populated quarter" as ambiguities -- both correct resolutions,
        written down for the reader -- and a single `ambiguities` list turned
        each of them into a refusal. Declaring a resolution now costs
        nothing; only an unresolved question stops the analysis.

        A previous turn's owner authorizes nothing: this is recomputed from
        THIS turn's declaration.
        """
        return (self.query_mode == DATA_ANALYSIS
                and self.owner == COCKPIT
                and not self.blocking_ambiguities)

    def to_dict(self) -> dict[str, Any]:
        return {"query_mode": self.query_mode, "owner": self.owner,
                "understood_request": self.understood_request,
                "response_language": self.response_language,
                "blocking_ambiguities": list(self.blocking_ambiguities),
                "resolved_assumptions": list(self.resolved_assumptions),
                "canonical_mappings": list(self.canonical_mappings),
                "excluded_parts": list(self.excluded_parts),
                "public_rationale": self.public_rationale}


def _require_text(payload: Any, key: str, path: str, *,
                  allow_empty: bool = False) -> str:
    if not isinstance(payload, dict) or key not in payload:
        raise Rejection("INVALID_MODEL_OUTPUT",
                        f"{path}.{key} is required and was not supplied.",
                        field_path=f"{path}.{key}")
    value = payload[key]
    if not isinstance(value, str):
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"{path}.{key} must be a string, not {type(value).__name__}.",
            field_path=f"{path}.{key}")
    if not value.strip() and not allow_empty:
        raise Rejection("INVALID_MODEL_OUTPUT",
                        f"{path}.{key} must not be empty.",
                        field_path=f"{path}.{key}")
    return value


def _optional_text(payload: Any, key: str, path: str) -> str:
    """An optional string. `null` and a missing key both mean "none".

    The model-facing schema declares these as `["string", "null"]`, so `null`
    is a representation the analyst was TOLD it may send. A validator that
    then rejects it disagrees with the contract the model was given, and the
    cost of that disagreement is a whole extra generation: the first live
    product-help run answered correctly in 16.6 seconds, was refused because
    `clarification_question` was null, and had to be asked again — two
    generations and 29.4 seconds for a question that needed one.

    Normalizing here is REPRESENTATION only. It maps three spellings of
    absence onto one stored form. It never invents content, never rewrites
    meaning, and never rescues a field that carries substance.
    """
    if not isinstance(payload, dict):
        raise Rejection("INVALID_MODEL_OUTPUT",
                        f"{path} must be an object.", field_path=path)
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"{path}.{key} must be a string or null, not "
            f"{type(value).__name__}.", field_path=f"{path}.{key}")
    return value


def _optional_str_list(payload: Any, key: str, path: str) -> tuple[str, ...]:
    """An optional array of strings. `null` and a missing key mean empty.

    A bare string is still REJECTED rather than wrapped, for the same reason
    it always was: V3 expanded a scalar into a list of its own characters and
    turned a rating of "B" into ["B"]. Accepting absence is not the same as
    accepting a wrong shape.
    """
    if not isinstance(payload, dict):
        raise Rejection("INVALID_MODEL_OUTPUT",
                        f"{path} must be an object.", field_path=path)
    if payload.get(key) is None:
        return ()
    return _require_str_list(payload, key, path)


def _optional_object(payload: Any, key: str, path: str) -> dict[str, Any]:
    """An optional object. `null` and a missing key mean no entries."""
    value = payload.get(key) if isinstance(payload, dict) else None
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"{path}.{key} must be an object or null, not "
            f"{type(value).__name__}.", field_path=f"{path}.{key}")
    return dict(value)


def _optional_list(payload: Any, key: str, path: str) -> list[Any]:
    """An optional array of anything. `null` and a missing key mean empty."""
    value = payload.get(key) if isinstance(payload, dict) else None
    if value is None:
        return []
    if not isinstance(value, list):
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"{path}.{key} must be an array or null, not "
            f"{type(value).__name__}.", field_path=f"{path}.{key}")
    return list(value)


def _require_str_list(payload: Any, key: str, path: str) -> tuple[str, ...]:
    """A list of strings. A bare string is REJECTED, never expanded.

    V3 turned a scalar string into a list of its characters here. The fix is
    not a smarter coercion -- it is refusing to coerce at all and telling the
    analyst the field is a list, which costs one tool_result and cannot
    silently turn "BBB" into ["B", "B", "B"].
    """
    if not isinstance(payload, dict) or key not in payload:
        raise Rejection("INVALID_MODEL_OUTPUT",
                        f"{path}.{key} is required and was not supplied.",
                        field_path=f"{path}.{key}")
    value = payload[key]
    if isinstance(value, str):
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"{path}.{key} must be an array of strings. A single string is "
            f"not an array; send [\"...\"] if you meant one item.",
            field_path=f"{path}.{key}")
    if not isinstance(value, list):
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"{path}.{key} must be an array of strings, not "
            f"{type(value).__name__}.", field_path=f"{path}.{key}")
    out: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise Rejection(
                "INVALID_MODEL_OUTPUT",
                f"{path}.{key}[{i}] must be a string, not "
                f"{type(item).__name__}.", field_path=f"{path}.{key}[{i}]")
        out.append(item)
    return tuple(out)


#: What to send, when nothing has been declared yet and nothing can be
#: carried. Naming the fields beats naming the type: "must be an object" told
#: the analyst what was wrong and never what to do about it.
_INTENT_HELP = (
    "Declare it once with query_mode (one of "
    + ", ".join(QUERY_MODES) + "), owner (one of " + ", ".join(OWNERS)
    + "), understood_request, response_language and public_rationale. Later "
      "calls in this run inherit it and may omit it.")


#: The intent fields an ANSWER may restate, flat, at the top level of
#: `finalize_response`. Nowhere else, and never nested.
FLAT_INTENT_FIELDS = (
    "understood_request", "response_language", "blocking_ambiguities",
    "resolved_assumptions", "canonical_mappings", "excluded_parts",
    "public_rationale")


def parse_intent(payload: Any, *, path: str = "intent",
                 carried: "Intent | None" = None) -> Intent:
    """The run's intent, authored ONCE by the server and refined by the
    answer.

    The defect this exists for
    --------------------------
    `intent` was a required NESTED OBJECT on all five tools, so a nine-field
    structure was retyped on every catalogue read, every artifact read, every
    execution and the answer. It does not change between them. A field
    restated for no reason is a field that will eventually come back wrong,
    and this one came back as a STRING on a live Mac run: `intent must be an
    object` reached a reader's screen twice in two rounds, and each round
    made the parser more forgiving instead of removing the restatement.

    So the nested object is gone from every tool schema. It cannot arrive
    malformed because it cannot arrive at all.

    What is left
    ------------
    The run owns an IntentEnvelope from its first second: the server already
    knows the domain, the release, the mode (`envelope.classify`) and the
    values the question named (`values.phrases`). `finalize_response` may
    restate the READER-FACING half of it -- what it understood, what it
    assumed, what it mapped, what it excluded -- as FLAT optional fields,
    because that half is answer content and belongs with the answer.

    This function is the merge. `payload` may be a flat dict of those fields
    (the new shape), a nested object (an older caller, still honoured), or
    nothing at all.
    """
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
        else:
            # A model that sends a sentence where an object was once
            # required is describing its intent, not corrupting one. The
            # field no longer exists in any schema, so this can only be an
            # older caller; take the sentence as the understood request
            # rather than refusing a run over it.
            payload = {"understood_request": text} if text else None
    if payload is None or payload == {} or payload == "":
        if carried is not None:
            return carried
        raise Rejection("INVALID_MODEL_OUTPUT",
                        f"This run has no {path} yet. " + _INTENT_HELP,
                        field_path=path)
    if not isinstance(payload, dict):
        if carried is not None:
            return carried
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"{path} must be an object, not a {type(payload).__name__}. "
            + _INTENT_HELP,
            field_path=path)

    base = carried
    mode = str(payload.get("query_mode") or "").strip()
    owner = str(payload.get("owner") or "").strip()
    if base is None:
        if mode not in QUERY_MODES:
            raise Rejection(
                "INVALID_MODEL_OUTPUT",
                f"{path}.query_mode must be one of {', '.join(QUERY_MODES)}.",
                field_path=f"{path}.query_mode")
        if owner not in OWNERS:
            raise Rejection(
                "INVALID_MODEL_OUTPUT",
                f"{path}.owner must be one of {', '.join(OWNERS)}.",
                field_path=f"{path}.owner")
        base = Intent(
            query_mode=mode, owner=owner, understood_request="",
            response_language="en", blocking_ambiguities=(),
            resolved_assumptions=(), canonical_mappings=(),
            excluded_parts=(), public_rationale="")

    def text_or(key: str, fallback: str) -> str:
        value = payload.get(key)
        return str(value).strip() if isinstance(value, str) and value.strip() \
            else fallback

    def list_or(key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
        if key not in payload or payload.get(key) is None:
            return fallback
        return _optional_str_list(payload, key, path)

    # The MODE and the OWNER are the run's, not the answer's. A model cannot
    # talk a Retail run into being a Corporate one, or a data analysis into
    # being product help, by restating a field.
    #
    # One movement is allowed, and only one: a run may WIDEN into a data
    # analysis. The budget classifier decides the mode from the question's
    # words before anything is spent and can read an analytical turn as
    # product help -- "tell me more", in an analytical thread, names no
    # measure at all. An analyst that then declares an analysis is telling
    # us something the classifier could not know, and refusing the
    # declaration would leave a real analysis running on the sixty-second
    # product-help clock. It can never narrow: a run that has already read
    # the book does not get moved onto the cheaper clock by saying so.
    widened = (carried is not None and mode == DATA_ANALYSIS
               and base.query_mode != DATA_ANALYSIS)
    return Intent(
        query_mode=(mode if (mode in QUERY_MODES and carried is None)
                    or widened else base.query_mode),
        owner=(owner if owner in OWNERS and carried is None
               else base.owner),
        understood_request=text_or("understood_request",
                                   base.understood_request),
        response_language=text_or("response_language", base.response_language),
        blocking_ambiguities=list_or("blocking_ambiguities",
                                     base.blocking_ambiguities),
        resolved_assumptions=list_or("resolved_assumptions",
                                     base.resolved_assumptions),
        canonical_mappings=list_or("canonical_mappings",
                                   base.canonical_mappings),
        excluded_parts=list_or("excluded_parts", base.excluded_parts),
        public_rationale=text_or("public_rationale", base.public_rationale))


def flat_intent(payload: Any) -> dict[str, Any]:
    """The intent fields a tool payload carries at its TOP level.

    `finalize_response` states them flat. Nothing else states them at all.
    """
    if not isinstance(payload, dict):
        return {}
    return {k: payload[k] for k in FLAT_INTENT_FIELDS if k in payload}


# ---- execute_analysis --------------------------------------------------

_STEP_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
MAX_CODE_BYTES = 64_000


@dataclass(frozen=True)
class Step:
    """One executable unit, exactly as the analyst wrote it."""

    step_id: str
    language: str
    #: Byte-for-byte the analyst's source. Never reformatted, never trimmed.
    code: str
    parameters: dict[str, Any]
    purpose: str
    input_artifact_ids: tuple[str, ...]
    depends_on_step_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "language": self.language,
                "code": self.code, "parameters": dict(self.parameters),
                "purpose": self.purpose,
                "input_artifact_ids": list(self.input_artifact_ids),
                "depends_on_step_ids": list(self.depends_on_step_ids)}


@dataclass(frozen=True)
class ExecutionSubmission:
    intent: Intent
    objective: str
    subquestions: tuple[str, ...]
    scope: dict[str, Any]
    metadata_receipt_ids: tuple[str, ...]
    fields_required: tuple[str, ...]
    expected_output_grain: str
    #: Output column -> unit. The schema declares a mapping, because a result
    #: with an amount and a count does not have one unit. A bare string is
    #: accepted as the shorthand for "every output column is in this unit"
    #: and is stored under the empty key.
    expected_units: dict[str, str]
    steps: tuple[Step, ...]
    repair_of_submission_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"intent": self.intent.to_dict(), "objective": self.objective,
                "subquestions": list(self.subquestions),
                "scope": dict(self.scope),
                "metadata_receipt_ids": list(self.metadata_receipt_ids),
                "fields_required": list(self.fields_required),
                "expected_output_grain": self.expected_output_grain,
                "expected_units": dict(self.expected_units),
                "steps": [s.to_dict() for s in self.steps],
                "repair_of_submission_id": self.repair_of_submission_id}


def parse_steps(payload: Any, *, max_steps: int) -> tuple[Step, ...]:
    """Parse a batch. An overlong batch is REJECTED WHOLE, never clipped.

    Clipping is the failure mode that matters: a six-step analysis silently
    executed as four steps produces a number, and the number is wrong, and
    nothing in the trace says a step is missing.
    """
    if not isinstance(payload, list):
        raise Rejection("INVALID_MODEL_OUTPUT",
                        "steps must be an array.", field_path="steps")
    if not payload:
        raise Rejection("INVALID_MODEL_OUTPUT",
                        "steps must contain at least one step.",
                        field_path="steps")
    if len(payload) > max_steps:
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"This batch has {len(payload)} steps and the limit is "
            f"{max_steps}. The batch was not executed and no step was "
            f"dropped: resubmit within the limit, or split the work across "
            f"submissions, but do not shorten the analysis to fit.",
            field_path="steps",
            detail={"submitted": len(payload), "limit": max_steps})

    seen: set[str] = set()
    steps: list[Step] = []
    for i, raw in enumerate(payload):
        path = f"steps[{i}]"
        if not isinstance(raw, dict):
            raise Rejection("INVALID_MODEL_OUTPUT",
                            f"{path} must be an object.", field_path=path)
        step_id = _require_text(raw, "step_id", path)
        if not _STEP_ID.match(step_id):
            raise Rejection(
                "INVALID_MODEL_OUTPUT",
                f"{path}.step_id must match {_STEP_ID.pattern}.",
                field_path=f"{path}.step_id")
        if step_id in seen:
            raise Rejection("INVALID_MODEL_OUTPUT",
                            f"{path}.step_id {step_id!r} is repeated.",
                            field_path=f"{path}.step_id")
        seen.add(step_id)
        language = _require_text(raw, "language", path)
        if language not in ("sql", "python"):
            raise Rejection(
                "INVALID_MODEL_OUTPUT",
                f"{path}.language must be 'sql' or 'python'. A python step "
                f"is never executed as SQL, or the reverse.",
                field_path=f"{path}.language")
        code = _require_text(raw, "code", path)
        if len(code.encode("utf-8")) > MAX_CODE_BYTES:
            raise Rejection(
                "INVALID_MODEL_OUTPUT",
                f"{path}.code exceeds {MAX_CODE_BYTES} bytes and was not "
                f"truncated. Send shorter code; nothing was executed.",
                field_path=f"{path}.code")
        parameters = _optional_object(raw, "parameters", path)
        steps.append(Step(
            step_id=step_id, language=language, code=code,
            parameters=parameters,
            purpose=_require_text(raw, "purpose", path),
            input_artifact_ids=_optional_str_list(
                raw, "input_artifact_ids", path),
            depends_on_step_ids=_optional_str_list(
                raw, "depends_on_step_ids", path)))

    ids = {s.step_id for s in steps}
    order = {s.step_id: i for i, s in enumerate(steps)}
    for s in steps:
        for dep in s.depends_on_step_ids:
            if dep not in ids:
                raise Rejection(
                    "INVALID_MODEL_OUTPUT",
                    f"step {s.step_id!r} depends on {dep!r}, which is not in "
                    f"this batch.", field_path="steps")
            if order[dep] >= order[s.step_id]:
                raise Rejection(
                    "INVALID_MODEL_OUTPUT",
                    f"step {s.step_id!r} depends on {dep!r}, which is not "
                    f"earlier in the batch. Steps run in the order sent.",
                    field_path="steps")
    return tuple(steps)


def _parse_units(value: Any, *, required: bool = True) -> dict[str, str]:
    """The declared units of the result.

    The published schema is `{"column": "unit"}`, because a result carrying an
    amount and a row count does not have a single unit. The validator used to
    demand a plain string, so a model that followed the schema it was given
    would have had EVERY analysis submission rejected — a submission slot and
    a generation burned each time, for agreeing with us.

    A bare string is still accepted as the shorthand for "all of it is in this
    unit"; that is representation, not meaning.
    """
    if value is None:
        if not required:
            # The display policy resolves a column's unit from the CATALOGUE
            # when the submission does not declare one. A declaration is a
            # useful cross-check, not a thing worth refusing an otherwise
            # valid query over.
            return {}
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            "expected_units is required: declare the unit of each output "
            "column, or one unit for all of them.",
            field_path="expected_units")
    if isinstance(value, str):
        if not value.strip():
            raise Rejection("INVALID_MODEL_OUTPUT",
                            "expected_units must not be empty.",
                            field_path="expected_units")
        return {"": value}
    if not isinstance(value, dict):
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"expected_units must be an object mapping a column to its unit, "
            f"or a single string; got {type(value).__name__}.",
            field_path="expected_units")
    if not value:
        if not required:
            return {}
        raise Rejection("INVALID_MODEL_OUTPUT",
                        "expected_units must name at least one unit.",
                        field_path="expected_units")
    out: dict[str, str] = {}
    for column, unit in value.items():
        if not isinstance(unit, str) or not unit.strip():
            raise Rejection(
                "INVALID_MODEL_OUTPUT",
                f"expected_units[{column!r}] must be a non-empty string.",
                field_path=f"expected_units.{column}")
        out[str(column)] = unit
    return out


def units_display(units: dict[str, str]) -> str:
    """The units as one line, for the process trace."""
    if not units:
        return "unspecified"
    if len(units) == 1 and "" in units:
        return units[""]
    return ", ".join(f"{column}: {unit}" if column else unit
                     for column, unit in units.items())


def parse_execution(payload: Any, *, max_steps: int,
                    carried: "Intent | None" = None) -> ExecutionSubmission:
    if not isinstance(payload, dict):
        raise Rejection("INVALID_MODEL_OUTPUT",
                        "execute_analysis arguments must be an object.")
    # §26: `execute_analysis` is `{objective, steps}` and a few optional
    # descriptors. It was a ten-field object of which nine were required, so
    # a run that had already said what it was doing said it again on every
    # submission -- and a submission refused for a missing descriptor costs a
    # generation and a submission slot for saying nothing about the SQL.
    intent = parse_intent(payload.get("intent"), carried=carried)
    scope = _optional_object(payload, "scope", "execute_analysis")
    objective = _require_text(payload, "objective", "execute_analysis")
    subquestions = _optional_str_list(payload, "subquestions",
                                      "execute_analysis") or (objective,)
    return ExecutionSubmission(
        intent=intent,
        objective=objective,
        subquestions=subquestions,
        scope=scope,
        metadata_receipt_ids=_optional_str_list(
            payload, "metadata_receipt_ids", "execute_analysis"),
        fields_required=_optional_str_list(payload, "fields_required",
                                           "execute_analysis"),
        expected_output_grain=_optional_text(
            payload, "expected_output_grain", "execute_analysis") or "row",
        expected_units=_parse_units(payload.get("expected_units"),
                                    required=False),
        steps=parse_steps(payload.get("steps"), max_steps=max_steps),
        repair_of_submission_id=_optional_text(
            payload, "repair_of_submission_id", "execute_analysis"))


# ---- inspect_catalog / read_artifact -----------------------------------

CATALOG_DETAILS: tuple[str, ...] = (
    "discovery", "fields", "relationships", "coverage", "samples")
MAX_SAMPLE_ROWS = 10
#: What "samples" without a row count means. Small on purpose: a sample shows
#: SHAPE, and ten rows of a 198-column relation is already a lot of text.
DEFAULT_SAMPLE_ROWS = 3


@dataclass(frozen=True)
class CatalogRequest:
    intent: Intent
    query: str
    relation_ids: tuple[str, ...]
    field_ids: tuple[str, ...]
    detail: tuple[str, ...]
    #: Reporting periods, under whichever name the analyst used. A book that
    #: reports monthly is asked about months; one that reports quarterly is
    #: asked about quarters; the request means the same thing either way and
    #: refusing one spelling would be refusing a valid tool call.
    reporting_periods: tuple[str, ...]
    sample_rows: int
    cursor: str

    @property
    def reporting_quarters(self) -> tuple[str, ...]:
        """The name this field had before the Cockpit had a monthly book."""
        return self.reporting_periods


#: Every spelling of "which periods" an analyst may send. The tool schema
#: offers `reporting_periods`; a book that reports monthly may prompt the
#: word "months" in its own description, and a run carried over from the
#: quarterly release may still say quarters. All three are the same request.
PERIOD_KEYS = ("reporting_periods", "reporting_months", "reporting_quarters")


def _periods(payload: dict[str, Any], tool: str) -> tuple[str, ...]:
    for key in PERIOD_KEYS:
        if payload.get(key) is not None:
            return _optional_str_list(payload, key, tool)
    return ()


def parse_catalog(payload: Any, *,
                  carried: "Intent | None" = None) -> CatalogRequest:
    if not isinstance(payload, dict):
        raise Rejection("INVALID_MODEL_OUTPUT",
                        "inspect_catalog arguments must be an object.")
    intent = parse_intent(payload.get("intent"), carried=carried)
    detail = _optional_str_list(payload, "detail", "inspect_catalog")
    for d in detail:
        if d not in CATALOG_DETAILS:
            raise Rejection(
                "INVALID_MODEL_OUTPUT",
                f"detail {d!r} is not one of {', '.join(CATALOG_DETAILS)}.",
                field_path="detail")
    rows = payload.get("sample_rows", 0)
    if rows is None:
        rows = 0
    if not isinstance(rows, int) or isinstance(rows, bool):
        raise Rejection("INVALID_MODEL_OUTPUT",
                        "sample_rows must be an integer.",
                        field_path="sample_rows")
    if rows < 0 or rows > MAX_SAMPLE_ROWS:
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"sample_rows must be between 0 and {MAX_SAMPLE_ROWS}.",
            field_path="sample_rows")
    # `sample_rows` and `detail: ["samples"]` are the same request written two
    # ways, so neither is refused for lacking the other. Rejecting
    # `sample_rows: 5` because "samples" was absent cost a live run a whole
    # generation for a tool call the published schema said was valid -- the
    # same class of defect as the finalize_response null, in a different
    # field. Representation is normalised here; nothing about what was asked
    # for changes.
    if rows and "samples" not in detail:
        detail = detail + ("samples",)
    if "samples" in detail and not rows:
        rows = DEFAULT_SAMPLE_ROWS
    if rows and intent.query_mode != DATA_ANALYSIS:
        raise Rejection(
            "SECURITY_DENIED",
            "Masked sample rows are available only for a data analysis. "
            "Product help reads coverage metadata, not borrower rows.",
            field_path="sample_rows")
    return CatalogRequest(
        intent=intent,
        query=_optional_text(payload, "query", "inspect_catalog"),
        relation_ids=_optional_str_list(payload, "relation_ids",
                                        "inspect_catalog"),
        field_ids=_optional_str_list(payload, "field_ids", "inspect_catalog"),
        detail=detail,
        reporting_periods=_periods(payload, "inspect_catalog"),
        sample_rows=rows,
        cursor=_optional_text(payload, "cursor", "inspect_catalog"))


@dataclass(frozen=True)
class ProductKnowledgeRequest:
    intent: Intent
    query: str
    topics: tuple[str, ...]
    detail: str


def parse_product_knowledge(payload: Any, *,
                            carried: "Intent | None" = None
                            ) -> ProductKnowledgeRequest:
    """Parse a product-knowledge request.

    Reading product facts is not analysis, so this is available in every mode
    -- a product-help question must be able to reach it without declaring a
    data analysis it is not going to run.
    """
    from backend.cockpit_v4.product_knowledge import TOPICS

    if not isinstance(payload, dict):
        raise Rejection("INVALID_MODEL_OUTPUT",
                        "inspect_product_knowledge arguments must be an "
                        "object.")
    intent = parse_intent(payload.get("intent"), carried=carried)
    topics = _optional_str_list(payload, "topics",
                               "inspect_product_knowledge")
    for topic in topics:
        if topic not in TOPICS:
            raise Rejection(
                "INVALID_MODEL_OUTPUT",
                f"topic {topic!r} is not one of {', '.join(TOPICS)}.",
                field_path="topics")
    detail = _optional_text(payload, "detail", "inspect_product_knowledge")
    if detail not in ("", "standard", "full"):
        raise Rejection("INVALID_MODEL_OUTPUT",
                        "detail must be 'standard' or 'full'.",
                        field_path="detail")
    return ProductKnowledgeRequest(
        intent=intent,
        query=_optional_text(payload, "query", "inspect_product_knowledge"),
        topics=topics, detail=detail or "standard")


@dataclass(frozen=True)
class ArtifactRequest:
    intent: Intent
    artifact_id: str
    artifact_kind: str
    columns: tuple[str, ...]
    offset: int
    limit: int
    cursor: str


ARTIFACT_KINDS: tuple[str, ...] = ("result", "thread_turn")


def parse_artifact(payload: Any, *,
                   carried: "Intent | None" = None) -> ArtifactRequest:
    if not isinstance(payload, dict):
        raise Rejection("INVALID_MODEL_OUTPUT",
                        "read_artifact arguments must be an object.")
    intent = parse_intent(payload.get("intent"), carried=carried)
    kind = _require_text(payload, "artifact_kind", "read_artifact")
    if kind not in ARTIFACT_KINDS:
        raise Rejection(
            "INVALID_MODEL_OUTPUT",
            f"artifact_kind must be one of {', '.join(ARTIFACT_KINDS)}.",
            field_path="artifact_kind")
    artifact_id = _require_text(payload, "artifact_id", "read_artifact")
    # Not a path and not a URL. The namespace is checked server-side too;
    # this is the cheap refusal that keeps an obvious traversal out of logs.
    if "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
        raise Rejection(
            "SECURITY_DENIED",
            "artifact_id is an identifier, not a path or URL.",
            field_path="artifact_id")
    offset = payload.get("offset") or 0
    limit = payload.get("limit") or 0
    for name, value in (("offset", offset), ("limit", limit)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise Rejection("INVALID_MODEL_OUTPUT",
                            f"{name} must be a non-negative integer.",
                            field_path=name)
    return ArtifactRequest(
        intent=intent, artifact_id=artifact_id, artifact_kind=kind,
        columns=_optional_str_list(payload, "columns", "read_artifact"),
        offset=int(offset), limit=int(limit),
        cursor=_optional_text(payload, "cursor", "read_artifact"))


# ---- finalize_response -------------------------------------------------

_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")
COVERAGE_STATUSES: tuple[str, ...] = (
    "answered", "partial", "referred", "needs_clarification", "unsupported")


@dataclass(frozen=True)
class EvidenceRef:
    artifact_id: str
    row_key: str
    column_id: str
    scope_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {"artifact_id": self.artifact_id, "row_key": self.row_key,
               "column_id": self.column_id}
        if self.scope_ref:
            out["scope_ref"] = self.scope_ref
        return out


@dataclass(frozen=True)
class NumericClaim:
    """A number in the answer, and where it comes from.

    Two kinds, and exactly one of them applies to any claim.

    A DIRECT claim is a value that appears in one executed result cell, and
    it carries `evidence` naming that cell.

    A DERIVED claim is calculated from cells that exist -- a total across
    rows, a share of a total, a movement, a growth rate -- and it carries a
    `derivation` instead. It is not weaker evidence: the server recomputes it
    from the stored artifact and refuses the answer if the arithmetic does
    not hold. It exists because a total across twelve sectors is a real
    number with no row of its own, and the alternative was inventing one.
    """

    claim_id: str
    #: OPTIONAL, and normally EMPTY.
    #:
    #: The analyst names the cell or the arithmetic; CreditProbe computes the
    #: number from that and owns how it is written. An analyst that sends a
    #: value is offering a cross-check and it is still verified -- what it
    #: may no longer do is have a correct analysis refused because a figure
    #: it reproduced by hand differed in the fifteenth decimal place.
    #:
    #: When present it is a lossless decimal STRING, never a float: a float
    #: display is an approximation and this value is quoted to a credit
    #: officer.
    decimal_value: str
    unit: str
    #: The cell this value was read from. Empty for a derived claim.
    evidence: EvidenceRef
    #: What the analyst ASKED for. -1 means it asked for nothing. For every
    #: governed class the class answers instead, so this field changes
    #: nothing there; it is kept because a unit nothing could classify has
    #: no business rule to consult and this is the only signal available.
    #: Zero is a real precision -- money shows no decimals -- so "absent"
    #: cannot be spelled 0.
    display_precision: int = -1
    #: The arithmetic that produced this value, over cells that exist.
    #: Empty for a direct claim.
    derivation: dict[str, Any] | None = None

    @property
    def is_derived(self) -> bool:
        return bool(self.derivation)

    @property
    def asserts_a_value(self) -> bool:
        """True when the analyst sent a number to be checked against."""
        return bool(self.decimal_value)

    def precision(self) -> int:
        """The precision this claim will actually be written at.

        A declared precision is a REQUEST. For a governed class -- money,
        percentages, probabilities, movements, ratios, counts -- the class
        answers it, and the request changes nothing. Only a unit nothing
        could classify takes the analyst's word for it, because there no
        business rule exists to take instead.
        """
        from backend.cockpit_v4 import display as _display

        return _display.resolve_decimals(self.unit, self.display_precision)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "claim_id": self.claim_id, "unit": self.unit,
            "evidence": self.evidence.to_dict(),
            "display_precision": self.precision()}
        if self.decimal_value:
            body["decimal_value"] = self.decimal_value
        if self.derivation:
            body["derivation"] = self.derivation
        return body


@dataclass(frozen=True)
class CoverageItem:
    subquestion: str
    status: str
    evidence_refs: tuple[EvidenceRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"subquestion": self.subquestion, "status": self.status,
                "evidence_refs": [e.to_dict() for e in self.evidence_refs]}


@dataclass(frozen=True)
class FinalResponse:
    intent: Intent
    disposition: str
    narrative: str
    coverage: tuple[CoverageItem, ...]
    numeric_claims: tuple[NumericClaim, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    tables: tuple[dict[str, Any], ...]
    charts: tuple[dict[str, Any], ...]
    limitations: tuple[str, ...]
    suggested_questions: tuple[dict[str, Any], ...]
    clarification_question: str
    clarification_options: tuple[str, ...]
    referral_owner: str
    referral_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_dict(), "disposition": self.disposition,
            "narrative": self.narrative,
            "coverage": [c.to_dict() for c in self.coverage],
            "numeric_claims": [n.to_dict() for n in self.numeric_claims],
            "evidence_refs": [e.to_dict() for e in self.evidence_refs],
            "tables": [dict(t) for t in self.tables],
            "charts": [dict(c) for c in self.charts],
            "limitations": list(self.limitations),
            "suggested_questions": [dict(s) for s in self.suggested_questions],
            "clarification_question": self.clarification_question,
            "clarification_options": list(self.clarification_options),
            "referral_owner": self.referral_owner,
            "referral_reason": self.referral_reason}


def _parse_evidence(raw: Any, path: str) -> EvidenceRef:
    if not isinstance(raw, dict):
        raise Rejection("ANSWER_VALIDATION", f"{path} must be an object.",
                        field_path=path)
    return EvidenceRef(
        artifact_id=_require_text(raw, "artifact_id", path),
        row_key=_require_text(raw, "row_key", path, allow_empty=True),
        column_id=_require_text(raw, "column_id", path, allow_empty=True),
        scope_ref=str(raw.get("scope_ref") or ""))


def parse_final(payload: Any, *,
                carried: "Intent | None" = None) -> FinalResponse:
    if not isinstance(payload, dict):
        raise Rejection("ANSWER_VALIDATION",
                        "finalize_response arguments must be an object.")
    # §25: the ANSWER is where a reader-facing reading of the question
    # belongs, and it states those fields FLAT. A nested `intent` object is
    # still read when an older caller sends one, but no schema offers it.
    stated = flat_intent(payload)
    nested = payload.get("intent")
    intent = parse_intent(stated or nested, carried=carried)
    disposition = _require_text(payload, "disposition", "finalize_response")
    if disposition not in DISPOSITIONS:
        raise Rejection(
            "ANSWER_VALIDATION",
            f"disposition must be one of {', '.join(DISPOSITIONS)}.",
            field_path="disposition")

    coverage_raw = _optional_list(payload, "coverage", "finalize_response")
    coverage: list[CoverageItem] = []
    for i, raw in enumerate(coverage_raw):
        path = f"coverage[{i}]"
        if not isinstance(raw, dict):
            raise Rejection("ANSWER_VALIDATION", f"{path} must be an object.",
                            field_path=path)
        status = _require_text(raw, "status", path)
        if status not in COVERAGE_STATUSES:
            raise Rejection(
                "ANSWER_VALIDATION",
                f"{path}.status must be one of "
                f"{', '.join(COVERAGE_STATUSES)}.",
                field_path=f"{path}.status")
        refs = _optional_list(raw, "evidence_refs", path)
        coverage.append(CoverageItem(
            subquestion=_require_text(raw, "subquestion", path),
            status=status,
            evidence_refs=tuple(
                _parse_evidence(r, f"{path}.evidence_refs[{j}]")
                for j, r in enumerate(refs))))

    claims_raw = _optional_list(payload, "numeric_claims",
                                "finalize_response")
    claims: list[NumericClaim] = []
    seen_claims: set[str] = set()
    for i, raw in enumerate(claims_raw):
        path = f"numeric_claims[{i}]"
        if not isinstance(raw, dict):
            raise Rejection("ANSWER_VALIDATION", f"{path} must be an object.",
                            field_path=path)
        claim_id = _require_text(raw, "claim_id", path)
        if claim_id in seen_claims:
            raise Rejection("ANSWER_VALIDATION",
                            f"{path}.claim_id {claim_id!r} is repeated.",
                            field_path=f"{path}.claim_id")
        seen_claims.add(claim_id)
        # OPTIONAL, and normally absent. CreditProbe computes this value
        # from the evidence or the derivation and owns how it is written;
        # an analyst that sends one is asserting it as a cross-check, and it
        # is still checked. What it may no longer do is REQUIRE the analyst
        # to reproduce a figure the server already holds to the last digit.
        decimal_value = str(raw.get("decimal_value") or "").strip()
        if decimal_value and not _DECIMAL.match(decimal_value):
            raise Rejection(
                "ANSWER_VALIDATION",
                f"{path}.decimal_value, when sent at all, must be a plain "
                f"decimal string such as \"1234.50\"; got "
                f"{decimal_value!r}. It may also be omitted, and normally "
                f"should be: CreditProbe computes and formats the value.",
                field_path=f"{path}.decimal_value")
        # -1 means "not declared": the display policy chooses from the unit.
        # Zero is a real precision now -- money shows no decimals -- so the
        # absent case needs a value that is not a precision.
        precision = raw.get("display_precision", -1)
        if precision is None:
            precision = -1
        if (not isinstance(precision, int) or isinstance(precision, bool)
                or not -1 <= precision <= 12):
            raise Rejection("ANSWER_VALIDATION",
                            f"{path}.display_precision must be 0-12, or "
                            f"omitted so the unit decides.",
                            field_path=f"{path}.display_precision")
        # Exactly one of the two forms. The provider's tool dialect has no
        # way to say "one of these two" in a schema -- oneOf and anyOf are
        # rejected outright -- so the rule is stated in the field
        # descriptions and enforced here, where it can also say which claim
        # broke it.
        derivation = raw.get("derivation")
        has_derivation = isinstance(derivation, dict) and bool(derivation)
        evidence_raw = raw.get("evidence")
        has_evidence = (isinstance(evidence_raw, dict)
                        and bool(str(evidence_raw.get("artifact_id") or "")))
        if has_derivation and has_evidence:
            raise Rejection(
                "ANSWER_VALIDATION",
                f"{path} carries both 'evidence' and 'derivation'. A value "
                f"either appears in one result cell or is calculated from "
                f"cells; send whichever it is, not both.",
                field_path=f"{path}.derivation")
        if not has_derivation and not has_evidence:
            raise Rejection(
                "ANSWER_VALIDATION",
                f"{path} carries neither 'evidence' nor 'derivation'. Name "
                f"the result cell this value was read from, or the "
                f"arithmetic that produced it.",
                field_path=f"{path}.evidence")
        if has_derivation:
            try:
                derivation_mod.parse(derivation)
            except derivation_mod.DerivationError as exc:
                raise Rejection("ANSWER_VALIDATION", f"{path}: {exc}",
                                field_path=f"{path}.derivation") from exc
        claims.append(NumericClaim(
            claim_id=claim_id, decimal_value=decimal_value,
            unit=_require_text(raw, "unit", path),
            evidence=(_parse_evidence(evidence_raw, f"{path}.evidence")
                      if has_evidence else EvidenceRef("", "", "")),
            display_precision=int(precision),
            derivation=dict(derivation) if has_derivation else None))

    refs_raw = _optional_list(payload, "evidence_refs", "finalize_response")
    tables_raw = _optional_list(payload, "tables", "finalize_response")
    charts_raw = _optional_list(payload, "charts", "finalize_response")
    suggestions_raw = _optional_list(payload, "suggested_questions",
                                     "finalize_response")

    final = FinalResponse(
        intent=intent, disposition=disposition,
        narrative=_require_text(payload, "narrative", "finalize_response"),
        coverage=tuple(coverage), numeric_claims=tuple(claims),
        evidence_refs=tuple(_parse_evidence(r, f"evidence_refs[{i}]")
                            for i, r in enumerate(refs_raw)),
        tables=tuple(dict(x) for x in tables_raw if isinstance(x, dict)),
        charts=tuple(dict(x) for x in charts_raw if isinstance(x, dict)),
        limitations=_optional_str_list(payload, "limitations",
                                       "finalize_response"),
        suggested_questions=tuple(
            dict(x) for x in suggestions_raw if isinstance(x, dict)),
        # Every one of these is optional in MEANING: no clarification, no
        # referral. `null`, `""` and a missing key are three spellings of the
        # same fact and are stored as one.
        clarification_question=_optional_text(
            payload, "clarification_question", "finalize_response"),
        clarification_options=_optional_str_list(
            payload, "clarification_options", "finalize_response"),
        referral_owner=_optional_text(payload, "referral_owner",
                                      "finalize_response"),
        referral_reason=_optional_text(payload, "referral_reason",
                                       "finalize_response"))

    if disposition == "clarification" and not final.clarification_question:
        raise Rejection(
            "ANSWER_VALIDATION",
            "A clarification must carry the question you want answered.",
            field_path="clarification_question")
    if disposition == "referral":
        if not final.referral_owner or final.referral_owner not in OWNERS:
            raise Rejection(
                "ANSWER_VALIDATION",
                f"A referral must name an owner from {', '.join(OWNERS)}.",
                field_path="referral_owner")
    return final


# ---- provider tool schemas --------------------------------------------

def _load(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))


def _defs() -> dict[str, Any]:
    return _load("shared_defs.schema.json")["$defs"]


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    """Resolve `$ref` into the provider payload.

    The application schemas use `$defs` for readability. Provider tool
    schemas are a restricted dialect and `$ref` support is not something to
    assume, so the wire copy is flattened here and the application copy stays
    readable. Server-side validation does not depend on either.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return _inline(defs[ref.split("/")[-1]], defs)
        return {k: _inline(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def _no_nested_intent(schema: dict[str, Any]) -> dict[str, Any]:
    """`intent` is not a field of any tool. It cannot be, and that is the
    point.

    Making it optional was the previous attempt, and it was not enough: an
    optional field is still a field a model can fill in wrongly, and a live
    Mac run filled it in with a STRING twice, in two separate rounds. Each
    round made the parser more forgiving instead of removing the
    restatement.

    The run owns its intent from its first second -- the domain, the release
    and the mode are server decisions, and `envelope.classify` makes them
    before any provider call. What the ANSWER adds is reader-facing: what it
    understood, what it assumed, what it mapped. Those are flat optional
    fields of `finalize_response` and appear nowhere else.

    This is a belt: the schema files no longer define `intent` at all, and
    this strips it from anything that still does, so no future edit can put
    a nested object back on a tool by accident.
    """
    out = dict(schema)
    properties = dict(out.get("properties") or {})
    properties.pop("intent", None)
    out["properties"] = properties
    out["required"] = [name for name in out.get("required", [])
                       if name != "intent"]
    return out


_DESCRIPTIONS = {
    TOOL_INSPECT: ("Read exact catalog metadata for the authorized "
                   "corporate_cockpit release: relation names and grains, "
                   "field definitions, units, valid joins, coverage by "
                   "reporting {{PERIOD}} and missingness. Returns what is "
                   "there; an empty search is an empty search."),
    TOOL_EXECUTE: ("Submit your own exact SQL or Python. CreditProbe "
                   "validates and executes it unchanged, or rejects it with "
                   "the reason. It never edits, shortens or repairs your "
                   "code, and never computes a substitute answer."),
    TOOL_READ: ("Read an authorized persisted result artifact or completed "
                "thread turn. Exact stored values only; this computes "
                "nothing new."),
    TOOL_PRODUCT: ("Read CreditProbe's own product knowledge: what the "
                   "product is, what each of the seven functionalities does "
                   "and owns, TAC, the four Early Warning intelligence "
                   "layers, how modules relate, boundaries and worked "
                   "examples -- each with the deck slide it came from. Facts "
                   "only; the answer is yours to write."),
    TOOL_FINALIZE: ("Deliver the user-facing response: answer, partial "
                    "answer, referral, clarification or unsupported. This is "
                    "the terminal action and costs no extra model call."),
}


#: The fields an action turn's `finalize_response` may carry.
#:
#: An ACTION turn on an analytical run has one job: choose and author the
#: next action. It cannot answer, because it has executed nothing and every
#: number in a V4 answer is bound to a result that ran -- so the answer half
#: of this contract (narrative claims, evidence refs, tables, charts,
#: follow-ups) is not something it could legally fill in.
#:
#: Publishing it anyway cost about nine kilobytes on every action turn AND
#: described, in detail, an answer-shaped response to a model whose turn was
#: supposed to be a decision. The live failure was exactly that shape: an
#: essay, the output allowance reached, nothing run.
#:
#: What remains is every terminal an action turn CAN legitimately take --
#: it needs something the book does not hold, the request belongs to another
#: owner, it cannot be supported, it failed safely -- and the fields those
#: need. Same tool name, same parser, a strict subset of the same schema:
#: nothing here accepts a shape the full contract would reject.
_ACTION_FINALIZE_FIELDS = (
    "disposition", "narrative", "understood_request", "response_language",
    "clarification_question", "clarification_options", "referral_owner",
    "referral_reason", "blocking_ambiguities", "resolved_assumptions",
    "canonical_mappings", "excluded_parts", "public_rationale",
    "limitations",
)

#: The terminals an action turn may reach with nothing executed.
_ACTION_DISPOSITIONS = ("clarification", "referral", "unsupported",
                        "safe_failure")

_ACTION_FINALIZE_NOTE = (
    " On this turn nothing has been executed yet, so this tool can only "
    "STOP the run: ask the user a question, refer it, or say it cannot be "
    "supported. To answer with numbers, run the analysis first -- the full "
    "answer contract is offered on the turn after a result exists.")


def _action_finalize(schema: dict[str, Any]) -> dict[str, Any]:
    """The stop-early subset of the finalize contract. Never a superset."""
    properties = {name: body
                  for name, body in (schema.get("properties") or {}).items()
                  if name in _ACTION_FINALIZE_FIELDS}
    disposition = dict(properties.get("disposition") or {})
    if "enum" in disposition:
        kept = [d for d in disposition["enum"] if d in _ACTION_DISPOSITIONS]
        # Narrowing only. If the full contract ever stops offering one of
        # these, this stops offering it too rather than inventing it.
        disposition["enum"] = kept or list(disposition["enum"])
    properties["disposition"] = disposition
    return {**{k: v for k, v in schema.items() if k != "properties"},
            "properties": properties,
            "required": [r for r in (schema.get("required") or [])
                         if r in properties]}


def provider_tools(*, withhold: tuple[str, ...] = (),
                   catalog: Any = None, stage: str = "",
                   only: tuple[str, ...] | None = None,
                   max_steps: int = 0,
                   plan: dict[str, Any] | None = None,
                   plan_note: str = ""
                   ) -> list[dict[str, Any]]:
    """The tool definitions, in the provider's wire shape.

    `catalog` supplies the PERIOD VOCABULARY. Every schema description that
    needs to name a period carries a token rather than a calendar, because
    the alternative was what shipped: a worked example reading "period not
    specified: using the latest populated quarter 2026Q2 against 2026Q1" on
    every tool, handed to a run reading a monthly book. The analyst believed
    the contract over the catalogue -- correctly, since the contract is what
    it has to fill in -- and answered that the book was quarterly.

    With no catalogue the tokens resolve to neutral words and NO calendar is
    named, which is the only safe thing to say when there is no book open.

    `withhold` removes a tool from THIS request only. It exists for exactly
    one thing: a broad product question whose answer is already covered by
    the synopsis in the starting context does not need
    `inspect_product_knowledge` offered on its first action, and offering it
    is what turned "Who are you?" into two generations. Nothing is withheld
    for more than one action — see `product_knowledge.coverage`.

    `finalize_response` can never be withheld: withholding it would leave a
    run with no way to terminate.

    `stage="analytical_action"` publishes the STOP-EARLY subset of that
    contract instead of the full one -- see `_ACTION_FINALIZE_FIELDS`. It is
    a narrowing, never a widening, and the full contract comes back for the
    turn that writes the answer.

    `only` names the EXACT surface for one turn, in contract order, and is
    how `action_state` publishes a state's legal tools. It overrides
    `withhold` -- naming what may be used is a stronger statement than
    naming what may not -- and an unknown name in it is a programming error
    rather than something to route around. It may legitimately produce a
    single-tool request: a run whose governed metadata is fully resolved has
    exactly one legal transition, and offering it five tools to choose
    between is what ran the live run out of output before it had decided
    anything.
    """
    if only is not None:
        wanted = tuple(only)
        unknown = [name for name in wanted if name not in TOOL_NAMES]
        if unknown:
            raise ValueError(f"unknown tool(s) in only: {sorted(unknown)}")
        if not wanted:
            raise ValueError("a request with no tools cannot take an action")
        blocked = {name for name in TOOL_NAMES if name not in wanted}
    else:
        blocked = {name for name in withhold if name != TOOL_FINALIZE}
        if blocked - set(TOOL_NAMES):
            raise ValueError(f"unknown tool(s) in withhold: "
                             f"{sorted(blocked - set(TOOL_NAMES))}")
    defs = _defs()
    files = {TOOL_INSPECT: "inspect_catalog.schema.json",
             TOOL_PRODUCT: "inspect_product_knowledge.schema.json",
             TOOL_EXECUTE: "execute_analysis.schema.json",
             TOOL_READ: "read_artifact.schema.json",
             TOOL_FINALIZE: "finalize_response.schema.json"}
    tools = []
    for name in TOOL_NAMES:
        if name in blocked:
            continue
        schema = _no_nested_intent(_inline(_load(files[name]), defs))
        description = _DESCRIPTIONS[name]
        if name == TOOL_FINALIZE and stage == "analytical_action":
            schema = _action_finalize(schema)
            description += _ACTION_FINALIZE_NOTE
        if name == TOOL_EXECUTE and max_steps > 0:
            # The schema states the mode's own limit. A static 8 offered a
            # Standard run (limit 6) batches the parser then refused whole.
            schema = {**schema, "properties": {
                **schema["properties"],
                "steps": {**schema["properties"]["steps"], "maxItems": max_steps}}}
        if name == TOOL_EXECUTE and plan:
            # `plan_compiler`: offered only for models whose profile asks for
            # it. Either `plan` or `steps`; the server writes the SQL for a
            # plan, so neither field is required on its own.
            from backend.cockpit_v4 import plan_compiler
            schema = {**schema, "properties": {"sensitivity": plan_compiler.sensitivity_schema(),
                                                  "plan": plan, **schema["properties"]},
                      "required": []}
            description += (
                " PREFER `plan` over writing SQL: name governed measures, "
                "dimensions, filters and the period, and CreditProbe writes and "
                "runs the SQL. Send `steps` only for something a plan cannot "
                "express. Use `sensitivity` only for a Lifetime-PD absolute "
                "percentage-point ECL sensitivity; CreditProbe computes it at "
                "facility grain. " + (f" Measures: {plan_note}" if plan_note else ""))
        tools.append({"name": name,
                      "description": _speak(description, catalog),
                      "input_schema": _speak(schema, catalog)})
    return tools


def _speak(value: Any, catalog: Any) -> Any:
    """Resolve every `{{TOKEN}}` in a schema or a description."""
    from backend.cockpit_v4 import semantics as sem

    if isinstance(value, str):
        return sem.substitute(value, catalog) if "{{" in value else value
    if isinstance(value, dict):
        return {k: _speak(v, catalog) for k, v in value.items()}
    if isinstance(value, list):
        return [_speak(v, catalog) for v in value]
    return value


def application_schema(tool: str) -> dict[str, Any]:
    files = {TOOL_INSPECT: "inspect_catalog.schema.json",
             TOOL_PRODUCT: "inspect_product_knowledge.schema.json",
             TOOL_EXECUTE: "execute_analysis.schema.json",
             TOOL_READ: "read_artifact.schema.json",
             TOOL_FINALIZE: "finalize_response.schema.json",
             "event": "event.schema.json", "error": "error.schema.json",
             "state_machine": "state_machine.json"}
    return _load(files[tool])


__all__ = ["ArtifactRequest", "BATCHABLE", "CATALOG_DETAILS", "COCKPIT",
           "COVERAGE_STATUSES", "CatalogRequest", "CoverageItem",
           "DATA_ANALYSIS", "DISPOSITIONS", "EvidenceRef",
           "ExecutionSubmission", "FinalResponse", "Intent",
           "MAX_BATCHED_READS", "MAX_CODE_BYTES", "MAX_SAMPLE_ROWS",
           "NO_EXECUTION_MODES", "NumericClaim", "OWNERS", "PRODUCT_HELP",
           "QUERY_MODES", "Rejection", "Step", "THEORY_CONCEPT",
           "TOOL_CONTRACT_VERSION", "TOOL_EXECUTE", "TOOL_FINALIZE",
           "TOOL_INSPECT", "TOOL_NAMES", "TOOL_PRODUCT", "TOOL_READ",
           "UNSUPPORTED",
           "application_schema", "parse_artifact", "parse_catalog",
           "parse_execution", "parse_final", "parse_intent",
           "parse_product_knowledge", "parse_steps",
           "provider_tools", "units_display"]
