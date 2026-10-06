"""
Answer validation: check the evidence, render the numbers, rewrite nothing.

What "validated" means here, precisely
--------------------------------------
Every `numeric_claim` is checked against the evidence this run produced, in
one of two ways.

A DIRECT claim names an artifact, a row and a column, and its value must
match what is stored there.

A DERIVED claim names an operation and the cells it consumes, and the value
is RECOMPUTED here from the stored artifact. This is the stricter of the two:
a direct claim is compared against a cell, a derived claim has its whole
arithmetic redone. It exists because a total across twelve sectors, or the
share carried by the largest four, is a real number with no row of its own --
and a validator that only accepted pointers to physical cells left the
analyst no move except inventing a row called "all sectors", which is exactly
what a live run did before being refused twice. The narrative's PROSE is not claimed to be
semantically verified -- no validator reads English and certifies that a
sentence is true. What is enforced is narrower and honest: a portfolio number
in the narrative must arrive through a `{{claim.id}}` placeholder bound to
evidence, so a figure cannot appear in the answer without an execution behind
it.

The renderer substitutes validated values at the declared unit and precision.
That is deterministic formatting of an evidence-bound value, which is
presentation. It is the ONLY transformation applied to the analyst's words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.cockpit_v4 import derivation as deriv
from backend.cockpit_v4 import precision as prec
from backend.cockpit_v4.values import normalize as vnorm
from backend.cockpit_v4.contracts import (DATA_ANALYSIS, FinalResponse,
                                          NumericClaim, Rejection)
from backend.cockpit_v4.states import (ACTION_FORMAT_EXHAUSTED,
                                       ANSWER_FORMAT_EXHAUSTED,
                                       ANSWER_VALIDATION, CALL_LIMIT,
                                       COST_LIMIT, DEADLINE_EXPIRED,
                                       EXECUTION_LIMIT, OUTPUT_LIMIT,
                                       PROVIDER_UNAVAILABLE, ROUND_LIMIT)

PLACEHOLDER = re.compile(r"\{\{claim\.([A-Za-z0-9_.:-]{1,64})\}\}")

#: WHY the written answer never arrived, in one sentence a reader can act
#: on. The rows are published either way; this says what is missing from
#: them and, where the reader can do something about it, what.
#:
#: It lives HERE, beside `result_only_response`, because two components
#: publish this way: the orchestrator, when its own budget check stops the
#: run, and the SUPERVISOR, when it settles a run whose worker is blocked.
#: Keyed by the error code either of them is settling under. Membership is
#: also the eligibility list -- a code that is not here does not publish a
#: result-only answer.
RESULT_ONLY_REASON: dict[str, str] = {
    ANSWER_FORMAT_EXHAUSTED: (
        "Every attempt at the written answer was cut off before it was "
        "complete, so none of them was published. A narrower question "
        "usually produces one."),
    ACTION_FORMAT_EXHAUSTED: (
        "The analysis could not be carried further after this result."),
    OUTPUT_LIMIT: (
        "The written answer was longer than this run could publish."),
    DEADLINE_EXPIRED: (
        "The run reached its time allowance before the answer was written."),
    COST_LIMIT: (
        "The run reached its cost ceiling before the answer was written."),
    CALL_LIMIT: (
        "The run used its model-call allowance before the answer was "
        "written."),
    # A NETWORK FAULT IS NOT A BUDGET, and the rows survive it either way.
    #
    # `budgets.spend_transport_retry` used to raise CALL_LIMIT when a second
    # transport failure arrived, so the live M06 turn 3 published its five
    # rows under "the run used its model-call allowance" having used 4 of
    # its 12 generations. It settles under its own code now, and it has to
    # be here or that same run would publish nothing at all.
    PROVIDER_UNAVAILABLE: (
        "The provider could not be reached to write the answer. The "
        "analysis below had already run and is unaffected."),
    EXECUTION_LIMIT: (
        "The run used its execution allowance before the answer was "
        "written."),
    ROUND_LIMIT: (
        "The run used its analysis rounds before the answer was written."),
    # A WRITTEN ANSWER THAT FAILED ITS CHECKS IS STILL NOT A REASON TO
    # WITHHOLD THE ROWS. This was deliberately excluded once, on the ground
    # that the analyst HAD written an answer and what that run settles into
    # was not this channel's business. The distinction does not survive
    # contact with a reader: the rejected narrative is discarded either way,
    # what publishes here is the stored result with a server-written caveat
    # and no narrative at all, and the alternative on offer is a red box
    # over a query that ran correctly.
    ANSWER_VALIDATION: (
        "The written answer could not be reconciled with the evidence, and "
        "the one correction this run allows was already used. The rows "
        "below are the query's own output and were not affected."),
}

#: A bare number in narrative prose. Used to WARN, never to reject prose --
#: "20 quarters", "IFRS 9" and "12-month" are legitimate and are not
#: portfolio claims.
_BARE_NUMBER = re.compile(r"(?<![\w.{])(\d[\d,]*\.?\d*)(?![\w}])")
_ALLOWED_BARE = {"9", "12", "20", "19", "1", "2", "3", "4", "5", "10", "15",
                 "0", "40", "2021", "2022", "2023", "2024", "2025", "2026"}

#: A sentence that PROPOSES A POLICY CHANGE. Both halves are required --
#: the policy, and something being done to it -- because "tighten", "raise"
#: and "recommend" on their own are the ordinary vocabulary of a credit
#: write-up and a check that fires on them refuses correct analysis.
_POLICY_ACTION = re.compile(
    r"(?i)\bpolic(?:y|ies)\b(?=[^.!?]*\b(?:tighten|loosen|raise|lower|"
    r"reduce|increase|cut|suspend|withdraw|amend|change|revise|cap|freeze|"
    r"introduce|relax|restrict)\w*\b)"
    r"|\b(?:tighten|loosen|raise|lower|reduce|increase|cut|suspend|withdraw|"
    r"amend|change|revise|cap|freeze|introduce|relax|restrict)\w*\b"
    r"(?=[^.!?]*\bpolic(?:y|ies)\b)")

#: A sentence that CALLS A NUMBER A LIMIT. Narrow on purpose: the check it
#: gates refuses an answer, and a credit write-up says "reduce" and
#: "exposure" in every other sentence without proposing anything.
_POLICY_LIMIT = re.compile(
    r"(?i)\b(?:limit|threshold|cap|ceiling|floor|covenant|"
    r"polic(?:y|ies))\b")

#: A written figure, with or without thousands separators.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

#: WORDS THAT ASSERT A DIRECTION, and nothing else.
#:
#: CLOSURE-02. The verbs only, in their unambiguous senses. "deteriorated"
#: and "improved" are judgements -- ECL deteriorating is ECL RISING and
#: coverage improving is coverage rising, so the same word means opposite
#: arithmetic on two measures. "widened" and "narrowed" describe a gap
#: between two numbers rather than either number. Bare "up" and "down" are
#: prepositions half the time ("broken down by sector"). None of them is
#: here, because a check that refuses an answer must be sure.
_MOVED_UP = frozenset({
    "rose", "rise", "rises", "rising", "risen", "increased", "increases",
    "increasing", "increase", "grew", "grown", "grows", "growing",
    "climbed", "climbs", "climbing", "higher"})
_MOVED_DOWN = frozenset({
    "fell", "fall", "falls", "falling", "fallen", "declined", "declines",
    "declining", "decline", "dropped", "drops", "dropping", "drop",
    "decreased", "decreases", "decreasing", "decrease", "eased", "eases",
    "easing", "ease", "shrank", "shrunk", "shrinks", "shrink", "lower"})

#: The two prepositions that fix WHICH figure is the start and which the
#: end. "from A to B" and "to B from A" both say the same thing and both
#: are unambiguous; "B, up from A" is not this pattern and is left alone.
_FROM_TO = re.compile(r"(?i)\b(from|to)\b")

#: Language that asserts an order. Matched against the narrative to decide
#: whether a published chart is making a ranking claim.
_SUPERLATIVE = re.compile(
    r"\b(top|largest|biggest|highest|greatest|smallest|lowest|"
    r"rank(?:ed|ing)?|leading|worst|best|most)\b")


def _affix_before(token: str) -> re.Pattern[str]:
    """A unit token sitting immediately in front of a placeholder."""
    edge = r"\b" if token[-1:].isalnum() else ""
    return re.compile(rf"(?i){edge}{re.escape(token)}\s*$")


def _affix_after(token: str) -> re.Pattern[str]:
    """A unit token sitting immediately after a placeholder."""
    edge = r"\b" if token[-1:].isalnum() else ""
    return re.compile(rf"(?i)^\s*{re.escape(token)}{edge}")


def substitute(narrative: str, values: dict[str, str],
               units: dict[str, str]) -> tuple[str, list[str]]:
    """Put each claim's published text into the prose, ONCE, with ONE unit.

    `format_value` writes the unit into the string -- `SAR 78 million`,
    `48.31%`, `446 accounts`. An analyst writing prose naturally writes it
    too, and the result reached readers:

        "SAR SAR 78 million million across 446 accounts accounts"
        "an overall coverage of 48.31%%"

    The prose is not wrong about the unit; it is right about it twice. So
    the duplicate is TAKEN OUT here rather than sent back: CreditProbe owns
    the numeric string, this is the moment that string is written, and a
    re-ask would spend a model turn and a slice of the deadline to correct
    a presentation detail the server can settle itself.

    Only a token `format_value` is about to emit for THAT claim's unit is
    removed, and only where it touches the placeholder. A sentence that
    mentions millions elsewhere keeps its word, and `SAR {{claim.x}}` whose
    claim is a percentage keeps its `SAR` -- that is a claim carrying the
    wrong unit, which is a different fault and not one to paper over.

    Returns the rendered prose and one note per repair.
    """
    from backend.cockpit_v4 import display as disp

    out: list[str] = []
    notes: list[str] = []
    cursor = 0
    for match in PLACEHOLDER.finditer(narrative):
        claim_id = match.group(1)
        head = narrative[cursor:match.start()]
        cursor = match.end()
        if claim_id not in values:
            out.append(head)
            out.append(match.group(0))
            continue

        prefixes, suffixes = disp.written_affixes(units.get(claim_id, ""))
        for token in prefixes:
            trimmed = _affix_before(token).sub("", head)
            if trimmed != head:
                head, _ = trimmed, notes.append(
                    f"the narrative wrote {token.upper()!r} in front of "
                    f"{{{{claim.{claim_id}}}}}, which already publishes it; "
                    f"the duplicate was removed.")
                break
        out.append(head)
        out.append(values[claim_id])

        rest = narrative[cursor:]
        for token in suffixes:
            trimmed = _affix_after(token).sub("", rest)
            if trimmed != rest:
                cursor += len(rest) - len(trimmed)
                notes.append(
                    f"the narrative wrote {token!r} after "
                    f"{{{{claim.{claim_id}}}}}, which already publishes it; "
                    f"the duplicate was removed.")
                break
    out.append(narrative[cursor:])
    return "".join(out), notes


@dataclass
class ValidationReport:
    ok: bool
    rendered_narrative: str = ""
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    claim_values: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": list(self.problems),
                "warnings": list(self.warnings),
                "claims_checked": len(self.claim_values)}


def _cell(value: Any, unit: str, disp: Any) -> Any:
    """One published cell, as a reader sees it.

    A null and a non-numeric value pass through unchanged. A number whose
    unit was resolved is written in that unit. A number whose unit nobody
    could name is still written for a person -- no unit asserted, but not
    sixteen digits either, because machine precision reaching a reader is a
    defect whether or not we know what the number measures.
    """
    if value is None:
        return value
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    if unit:
        return disp.format_value(number, unit)
    return disp.format_unitless(number)


def _format(claim: NumericClaim) -> str:
    """Last resort: a claim that never reached the canonical registry.

    Reached only when a claim was neither settled nor rejected, which the
    validator does not allow. It renders whatever the analyst sent rather
    than raising, because an answer that is already being refused should not
    also crash while being described.
    """
    if not claim.decimal_value:
        return ""
    try:
        value = Decimal(claim.decimal_value)
    except InvalidOperation:
        return claim.decimal_value
    return prec.format_value(value, claim.unit, claim.precision())


#: What a chart can say. Below the floor there is nothing to compare; above
#: the ceiling there is nothing a reader can take in, and the honest form of
#: a ninety-row result is a table.
MIN_CHART_POINTS = 2
MAX_CHART_POINTS = 25

#: A distribution nobody can draw quartiles through. Four values is the
#: smallest set with a meaningful first and third quartile between them.
MIN_BOX_OBSERVATIONS = 4

#: The two forms whose rows are not points. A heatmap's rows are CELLS of a
#: grid, a box plot's rows are OBSERVATIONS inside a box; in both, counting
#: rows measures something a reader does not read one at a time.
HEATMAP = "heatmap"
BOX = "box"

#: Forms that state an order BY POSITION: the reader takes the top bar to be
#: the largest. Only these make a ranking claim, and only these are held to
#: `_check_ordering`.
RANKING_KINDS = frozenset({"bar", "grouped_bar"})

#: Forms whose x axis is a SEQUENCE -- time, or an ordered band. Their order
#: is the axis's, not the measure's, so a ranking test means nothing on them.
SEQUENCE_KINDS = frozenset({"line", "step_line", "area", "combo",
                            "waterfall"})

#: Forms that divide ONE WHOLE. Magnitude is read off area or length, not
#: off position, so an unsorted delinquency mix under "the highest share is
#: in 1-29" is perfectly readable -- the reader sees the biggest slice
#: wherever it sits.
COMPOSITION_KINDS = frozenset({"pie", "donut", "stacked_bar",
                               "stacked_bar_100"})

#: Forms that show a DISTRIBUTION or a relationship rather than an order.
SHAPE_KINDS = frozenset({"scatter", "bubble", "histogram", HEATMAP, BOX})

#: Every form `finalize_response` accepts, matching the `kind` enum in
#: `shared_defs.schema.json`. Validated here as well because the JSON
#: schema is enforced by the provider and nothing in Python ever looked:
#: `parse_final` took each chart as a raw dict, so a kind no renderer knows
#: reached `chart_svg`, fell through its `else`, and was silently drawn as
#: a bar.
#:
#: The set is what a credit-risk team actually draws. `bar`, `line`,
#: `waterfall`, `scatter`, `heatmap` and `box` were here; the rest existed
#: only as the shapes analysts had to flatten into bars: a delinquency mix
#: is a stacked bar, a band mix is a 100% stacked bar, rate-over-volume is a
#: combo, a DPD distribution is a histogram, a vintage comparison is a
#: grouped bar.
CHART_KINDS = tuple(sorted(
    RANKING_KINDS | SEQUENCE_KINDS | COMPOSITION_KINDS | SHAPE_KINDS))


@dataclass
class Finalizer:
    """Checks one final response against the evidence this run produced."""

    store: Any
    tenant_id: str
    release_id: str
    limits: Any
    #: Artifact ids this run created. Evidence outside them is not this run's.
    run_artifacts: set[str] = field(default_factory=set)
    #: claim_id -> the canonical value and the display form it was checked
    #: against. Rendering reads THIS, not the analyst's string, so the
    #: published figure is CreditProbe's rounding of CreditProbe's
    #: arithmetic rather than whatever the model happened to type.
    canonical: dict[str, Any] = field(default_factory=dict)
    #: claim_id -> the stored text of a non-numeric cell (a sector name, a
    #: rating grade). Rounding a category is meaningless, so these never
    #: reach the precision policy and are published exactly as stored.
    text: dict[str, str] = field(default_factory=dict)
    #: The claims this run validated. Read when rendering a table, so a
    #: column's unit is one the answer has already been held to rather than
    #: one the renderer chose for it.
    _claims: tuple = ()
    #: The run's release header. Evidence is checked against it: an artifact
    #: from a DIFFERENT build of the same release id is not this run's
    #: evidence, and an id alone cannot tell them apart.
    header: Any = None
    #: WHICH BOOK'S POLICY governs this answer. Empty on a run with no book
    #: -- a product question has no credit policy to cite -- and the
    #: citation check below does nothing when it is.
    domain_id: str = ""
    #: What the server resolved the question's own words to: dicts
    #: {term, field, value, say} from the run envelope. EMPTY by default, so
    #: every construction that predates the entity check behaves as before.
    #: The orchestrator sets it before each validation.
    resolved_entities: tuple = ()
    #: The run's question, read ONLY to tell a comparison over a field from
    #: a question about one value of it.
    question: str = ""
    #: Output column names produced by a governed plain average, whose label
    #: reads "Simple average of X (unweighted)". Empty on a legacy build.
    unweighted_measures: frozenset = frozenset()

    def validate(self, final: FinalResponse, *,
                 executed: bool) -> ValidationReport:
        problems: list[str] = []
        warnings: list[str] = []
        values: dict[str, str] = {}
        self._claims = tuple(final.numeric_claims)

        for claim in final.numeric_claims:
            problem = self._check_claim(claim)
            if problem:
                problems.append(problem)
            else:
                values[claim.claim_id] = self._render(claim)

        referenced = set(PLACEHOLDER.findall(final.narrative))
        declared = {c.claim_id for c in final.numeric_claims}
        for missing in sorted(referenced - declared):
            problems.append(
                f"the narrative references {{{{claim.{missing}}}}} but no "
                f"numeric_claim with that id was supplied.")
        for unused in sorted(declared - referenced):
            warnings.append(
                f"numeric claim {unused!r} was supplied but never referenced "
                f"in the narrative.")

        if final.disposition in ("answer", "partial_answer") \
                and final.intent.query_mode == DATA_ANALYSIS:
            if executed and not final.numeric_claims and not final.tables:
                # A full ANSWER that reports nothing bound to the evidence is
                # asserting a result it cannot support. A PARTIAL answer that
                # says plainly what it could not establish is the honest
                # outcome, not an invalid one -- provided it declares the
                # limitation rather than leaving the gap silent.
                if final.disposition == "answer" or not final.limitations:
                    problems.append(
                        "this data analysis response carries no numeric "
                        "claim and no table, so nothing in it is bound to "
                        "the executed evidence. Either bind the figures, or "
                        "send a partial answer that states what could not be "
                        "established.")
            stripped = PLACEHOLDER.sub("", final.narrative)
            for raw in _BARE_NUMBER.findall(stripped):
                cleaned = raw.replace(",", "").rstrip(".")
                if cleaned in _ALLOWED_BARE or len(cleaned) <= 2:
                    continue
                warnings.append(
                    f"the narrative contains the bare figure {raw!r}. A "
                    f"portfolio number should arrive through a claim "
                    f"placeholder so it is bound to evidence.")

        if not executed and final.numeric_claims:
            problems.append(
                "numeric claims were supplied but no analysis was executed in "
                "this run, so there is no artifact for them to reference.")

        for i, chart in enumerate(final.charts):
            problem = self._check_chart(chart, i)
            if problem:
                # An invalid optional chart is DROPPED with a warning. It does
                # not cost another analysis round.
                warnings.append(problem)

        if len(final.charts) > self.limits.charts:
            warnings.append(
                f"{len(final.charts)} charts were supplied and the limit is "
                f"{self.limits.charts}; the extra charts were dropped.")

        for i, table in enumerate(final.tables):
            problems.extend(self._check_table(table, i))

        problems.extend(self._check_ordering(final))
        problems.extend(self._check_movement(final))
        problems.extend(self._check_policy_citation(final))
        entity_problems, entity_warnings = self._check_entity_scope(final)
        problems.extend(entity_problems)
        warnings.extend(entity_warnings)
        problems.extend(self._check_average_disclosure(final))

        rendered = final.narrative
        if not problems:
            # A unit the analyst typed beside a placeholder is removed here,
            # not sent back: the server writes the numeric string, so the
            # server settles how many times its unit appears in it.
            rendered, repairs = substitute(
                final.narrative, values,
                {c.claim_id: c.unit for c in final.numeric_claims})
            warnings.extend(repairs)

        return ValidationReport(
            ok=not problems, rendered_narrative=rendered, problems=problems,
            warnings=warnings, claim_values=values)

    def _render(self, claim: NumericClaim) -> str:
        """The published text of a claim, from the canonical value."""
        if claim.claim_id in self.text:
            return self.text[claim.claim_id]
        verdict = self.canonical.get(claim.claim_id)
        if verdict is None:
            return _format(claim)
        return prec.format_value(verdict.canonical, claim.unit,
                                 verdict.precision)

    def release_problem(self, artifact_id: str) -> str:
        """Is this artifact from the same release, and the same bytes?

        A release id is a name. Two builds of one id have the same name and
        different numbers, and an artifact stored before a rebuild satisfies
        "same release_id" against the rebuild perfectly. The fingerprint is
        what the two cannot share.
        """
        if self.header is None:
            return ""
        record = self.store.get_artifact(artifact_id,
                                         tenant_id=self.tenant_id)
        if record is None:
            return ""
        if str(record.get("release_id") or "") != self.header.release_id:
            return (f"artifact {artifact_id!r} was computed from release "
                    f"{record.get('release_id')!r} and this run is answering "
                    f"from {self.header.release_id!r}. A figure from one "
                    f"release is not evidence for another.")
        stored = str((record.get("scope") or {}).get(
            "release_fingerprint") or "")
        if stored and stored != self.header.release_fingerprint:
            return (f"artifact {artifact_id!r} carries release "
                    f"{self.header.release_id!r} but was computed from a "
                    f"different build of it. Same name, different numbers.")
        return ""

    def _artifacts(self, ids) -> dict[str, Any]:
        """The stored artifacts for a derivation, tenant-checked."""
        out: dict[str, Any] = {}
        for artifact_id in ids:
            if artifact_id not in self.run_artifacts:
                continue
            record = self.store.get_artifact(artifact_id,
                                             tenant_id=self.tenant_id)
            if record is not None:
                out[artifact_id] = record
        return out

    def _settle(self, claim: NumericClaim, computed, *, label: str) -> str:
        """Record the canonical value, checking any figure the analyst sent.

        Two paths, and the difference is the whole point of this round.

        The analyst sent NOTHING: there is no figure to disagree with. The
        value CreditProbe computed is the value, and the display policy says
        how it is written. A correct analysis cannot be refused here.

        The analyst sent a figure: it is a cross-check and it is checked as
        strictly as before. Wrong arithmetic still fails.
        """
        precision = claim.precision()
        if not claim.asserts_a_value:
            canonical = prec.plain(computed)
            self.canonical[claim.claim_id] = prec.Verdict(
                True, canonical=canonical,
                display=prec.plain(prec.quantize(canonical, precision)),
                precision=precision)
            return ""
        verdict = prec.check(claim.decimal_value, computed, unit=claim.unit,
                             declared_precision=precision, label=label)
        if not verdict.ok:
            return verdict.problem
        self.canonical[claim.claim_id] = verdict
        return ""

    def _check_derived(self, claim: NumericClaim) -> str:
        """Recompute the claim. Its arithmetic is redone, not taken on trust."""
        label = f"claim {claim.claim_id!r}"
        try:
            derivation = deriv.parse(claim.derivation)
        except deriv.DerivationError as exc:
            return f"{label}: {exc}"

        for artifact_id in derivation.artifact_ids:
            if artifact_id not in self.run_artifacts:
                return (f"{label} references artifact {artifact_id!r}, which "
                        f"this run did not produce.")
            if self.store.get_artifact(artifact_id,
                                       tenant_id=self.tenant_id) is None:
                return (f"{label} references artifact {artifact_id!r}, which "
                        f"is not available to you.")
            wrong_release = self.release_problem(artifact_id)
            if wrong_release:
                return f"{label}: {wrong_release}"

        unit_wrong = deriv.unit_problem(derivation, claim.unit)
        if unit_wrong:
            return f"{label}: {unit_wrong}"

        # The same entity check, over the columns the arithmetic reads.
        # A `count` over `facility_id` is not a count of borrowers however
        # the claim is labelled.
        wrong_entity = self._wrong_entity(
            claim, [o.column_id for o in derivation.operands if o.column_id])
        if wrong_entity:
            return wrong_entity

        try:
            computed = deriv.compute(derivation,
                                     self._artifacts(derivation.artifact_ids),
                                     label=label)
        except deriv.DerivationError as exc:
            return f"{exc}"

        problem = self._settle(claim, computed, label=label)
        if problem:
            return f"{problem} (derivation: {derivation.operation})"
        return ""

    @staticmethod
    def _wrong_entity(claim: NumericClaim, columns: list[str]) -> str:
        """A COUNT OF ONE THING SOURCED FROM A COUNT OF ANOTHER.

        CLOSURE-01. A live answer to "for each borrower, what is the
        average utilisation of their facilities, and how many facilities
        does each have?" returned one row per borrower -- 5,412 of them --
        and published

            "2 borrowers carry facilities in 2026Q2"

        from a claim whose unit was `borrowers`, whose operation was
        `identity`, and whose cell was `facility_count` at row r0. The
        first borrower happened to hold two facilities. A per-row count of
        FACILITIES became a portfolio count of BORROWERS, and nothing
        stopped it: both are COUNT class, `identity` preserves the unit, so
        the unit machinery saw two counts and agreed.

        What it did not check is WHAT IS BEING COUNTED. The unit names the
        entity; so does the column. When both name one, and they differ,
        the claim is reading a number about one thing and calling it a
        number about another.

        Deliberately narrow, and not an L18 rule. It says nothing when the
        unit names no entity (`SAR million`, `percent`), nothing when the
        column names none (`n`, `total`), and nothing when a phrase could
        be read two ways -- `facilities per borrower` names both and is
        therefore proof of nothing.
        """
        from backend.cockpit_v4 import display as disp

        wanted = disp.entity_of(claim.unit)
        if not wanted:
            return ""
        for column in columns:
            got = disp.entity_of(column)
            if got and got != wanted:
                return (
                    f"claim {claim.claim_id!r} is declared in "
                    f"{claim.unit!r} -- a count of one thing per "
                    f"{wanted} -- and reads column {column!r}, which "
                    f"counts one thing per {got}. A count of one thing is "
                    f"not a count of another. Either count the {wanted} "
                    f"rows with a 'count' derivation over a column that "
                    f"identifies a {wanted}, naming the rows you mean, or "
                    f"run a query that returns the figure, or do not state "
                    f"it.")
        return ""

    def _is_comparison(self, field_name: str, values: set[str]) -> bool:
        """Same heuristic as the plan-time intent check, imported not copied."""
        from backend.cockpit_v4.intent_check import _comparison_words

        return len(values) >= 2 or bool(_comparison_words(
            field_name.lower()).search(self.question.lower()))

    def _check_entity_scope(self, final: FinalResponse
                            ) -> tuple[list[str], list[str]]:
        """A HEADLINE NUMBER ABOUT THE WRONG ENTITY, OR ABOUT ALL OF THEM.

        The question said "Riyadh" and the server resolved it to
        region=Riyadh, yet the claim cited the Makkah row (or an all-region
        total) and the answer was published as if it were Riyadh's. Nothing
        else catches it: the number is real, the cell exists, the unit is
        right. The entity is the one thing the reader asked for.

        Does nothing without resolved entities, and nothing when the
        question is a comparison over that field (two or more values
        resolved, or comparison wording): there, many rows are the point.
        It is a PROBLEM only when claims sit on rows carrying the field and
        NONE is on the named value: "why is X building" answers rightly cite
        other entities as comparisons beside X's own row.
        When no cited artifact carries the field at all, but another
        artifact of this run does with several distinct values, it is a
        WARNING only: the answer may be quoting across entities.
        """
        problems: list[str] = []
        warnings: list[str] = []
        by_field: dict[str, set[str]] = {}
        for entity in self.resolved_entities or ():
            f, v = str(entity.get("field") or ""), entity.get("value")
            if f and v not in (None, ""):
                by_field.setdefault(f, set()).add(str(v))
        if not by_field:
            return problems, warnings
        records: dict[str, Any] = {}

        def record(artifact_id: str) -> Any:
            if artifact_id not in records:
                try:
                    records[artifact_id] = self.store.get_artifact(
                        artifact_id, tenant_id=self.tenant_id)
                except Exception:  # noqa: BLE001
                    records[artifact_id] = None
            return records[artifact_id]

        for f, wanted in by_field.items():
            if self._is_comparison(f, wanted):
                continue
            want = next(iter(wanted))
            carried = False
            on_entity = False
            seen: list = []
            for claim in final.numeric_claims:
                ref = claim.evidence
                if claim.is_derived or ref is None or not ref.artifact_id:
                    continue
                rec = record(ref.artifact_id)
                if not rec:
                    continue
                col = next((c for c in rec["columns"]
                            if str(c).lower() == f.lower()), None)
                if col is None:
                    continue
                carried = True
                index = deriv._index_of(ref.row_key, rec["rows"]) \
                    if rec["rows"] else -1
                if index < 0:
                    continue
                got = rec["rows"][index].get(col)
                if got is not None:
                    seen.append((claim, got))
                    if vnorm(got) == vnorm(want):
                        on_entity = True
            if seen and not on_entity:
                claim, got = seen[0]
                problems.append(
                    f"claim {claim.claim_id!r} reports {f}={got} but "
                    f"the question asked about {f}={want}, and no claim is "
                    f"bound to the {want} row. Bind the headline claim to "
                    f"the {want} row (other {f} values may stay as "
                    f"comparisons).")
            if carried:
                continue
            for artifact_id in sorted(self.run_artifacts):
                rec = record(artifact_id)
                if not rec:
                    continue
                col = next((c for c in rec["columns"]
                            if str(c).lower() == f.lower()), None)
                if col is not None and len({str(r.get(col))
                                            for r in rec["rows"]}) > 1:
                    warnings.append(
                        f"the question asked about {f}={want} but the "
                        f"cited evidence has no {f} column while artifact "
                        f"{artifact_id!r} spans several {f} values; the "
                        f"answer may be quoting across entities.")
                    break
        return problems, warnings

    _DISCLOSURE = ("simple average", "unweighted", "not weighted",
                   "un-weighted", "simple (unweighted)")

    def _check_average_disclosure(self, final: FinalResponse) -> list[str]:
        """AN UNWEIGHTED AVERAGE PRESENTED AS THE BANK'S AVERAGE.

        Governed plain-average metrics are SQL AVG(col): every row counts
        equally whatever its balance. The bank's weighting convention is
        not yet defined (docs/cockpit_v4/TODO_FORMULAE_IFRS9.md), so a
        figure from one must say it is a simple, unweighted average, or the
        answer must ask which weighting is wanted. Does nothing when no
        measure is flagged unweighted.
        """
        if not self.unweighted_measures:
            return []
        used: list[str] = []
        for claim in final.numeric_claims:
            cols: list[str] = []
            if claim.is_derived:
                try:
                    parsed = deriv.parse(claim.derivation)
                    cols = [str(o.column_id) for o in parsed.operands
                            if o.column_id]
                except deriv.DerivationError:
                    cols = []
            elif claim.evidence is not None and claim.evidence.column_id:
                cols = [str(claim.evidence.column_id)]
            for col in cols:
                if col in self.unweighted_measures and col not in used:
                    used.append(col)
        if not used:
            return []
        text = " ".join([final.narrative, *final.limitations,
                         *final.intent.resolved_assumptions,
                         final.clarification_question or ""]).lower()
        if any(w in text for w in self._DISCLOSURE):
            return []
        return [
            f"state that {col} is a simple (unweighted) average, in the "
            f"narrative or resolved_assumptions (the bank's weighting "
            f"convention is not yet defined), or ask which weighting is "
            f"wanted." for col in used]

    def _check_claim(self, claim: NumericClaim) -> str:
        if claim.is_derived:
            return self._check_derived(claim)
        ref = claim.evidence
        if ref.artifact_id not in self.run_artifacts:
            return (f"claim {claim.claim_id!r} references artifact "
                    f"{ref.artifact_id!r}, which this run did not produce.")
        record = self.store.get_artifact(ref.artifact_id,
                                         tenant_id=self.tenant_id)
        if record is None:
            return (f"claim {claim.claim_id!r} references artifact "
                    f"{ref.artifact_id!r}, which is not available to you.")
        wrong_release = self.release_problem(ref.artifact_id)
        if wrong_release:
            return f"claim {claim.claim_id!r}: {wrong_release}"
        if ref.column_id and ref.column_id not in record["columns"]:
            return (f"claim {claim.claim_id!r} names column "
                    f"{ref.column_id!r}, which is not in artifact "
                    f"{ref.artifact_id!r}. Its columns are "
                    f"{record['columns']}.")
        if not ref.column_id:
            return ""
        wrong_entity = self._wrong_entity(claim, [str(ref.column_id)])
        if wrong_entity:
            return wrong_entity
        found = _locate(record["rows"], ref.row_key, ref.column_id)
        if found is _MISSING:
            return (f"claim {claim.claim_id!r} names row {ref.row_key!r}, "
                    f"which is not in artifact {ref.artifact_id!r}.")
        if found is None:
            return (f"claim {claim.claim_id!r} points at a NULL cell in "
                    f"{ref.artifact_id!r}. A null is not zero: report it as "
                    f"missing or name the reason.")
        try:
            stored = Decimal(str(found))
        except (InvalidOperation, ValueError):
            # A non-numeric cell -- a sector name, a rating grade. Compared
            # as text, because rounding a category is meaningless.
            if claim.asserts_a_value and str(found) != claim.decimal_value:
                return (f"claim {claim.claim_id!r} asserts "
                        f"{claim.decimal_value!r} and the artifact holds "
                        f"{found!r}.")
            self.text[claim.claim_id] = str(found)
            return ""
        return self._settle(claim, stored,
                            label=f"claim {claim.claim_id!r}")

    # -- rendering ------------------------------------------------------

    def _units_for(self, artifact_id: str, columns: list[str],
                   catalog: Any) -> dict[str, str]:
        """What unit each published column is in. Asked, never assumed.

        Two sources, in order, and no third:

          1. A NUMERIC CLAIM already bound to this artifact and column. The
             analyst declared that unit, the validator checked it against
             the arithmetic, and it is therefore a unit this answer has
             already been held to.
          2. The CATALOGUE, when the column name is a field it knows.

        A column neither source can name is published as a plain number.
        That is the honest outcome: a column called `total` could be an
        amount, a count of them or a ratio, and printing a currency beside
        it because the query produced floats is how a reader is shown a
        denomination nobody computed.
        """
        out: dict[str, str] = {}
        for claim in self._claims:
            ref = claim.evidence
            if ref.artifact_id == artifact_id and ref.column_id:
                out.setdefault(str(ref.column_id), claim.unit)
            # AN OPERAND IS IN THE CLAIM'S UNIT ONLY WHERE THE ARITHMETIC
            # PRESERVES IT.
            #
            # H-LIVE-04. This used to read the claim's unit onto EVERY
            # operand column of its derivation, so the live answer to "What
            # is ECL coverage of exposure?" published `balance_total` as
            # 19,619.22% and `limit_total` as 35,932.22%: their only
            # appearance in any claim was as the denominator of a coverage
            # percentage, and a ratio's denominator is not measured in
            # percent. `ecl_total` and `ead_total` escaped only because
            # direct claims had already named them in SAR million and
            # `setdefault` keeps the first answer.
            #
            # Which operations preserve a unit is declared beside the
            # operation table in `derivation`, from the arithmetic each one
            # does. Nothing here knows anything about `balance_total`.
            if not claim.derivation:
                continue
            try:
                parsed = deriv.parse(claim.derivation)
            except deriv.DerivationError:
                continue
            for operand in deriv.operands_in_the_result_unit(parsed):
                if operand.artifact_id != artifact_id or not operand.column_id:
                    continue
                out.setdefault(str(operand.column_id), claim.unit)
        if catalog is not None:
            from backend.cockpit_v4 import display as disp

            relations = []
            record = self.store.get_artifact(artifact_id,
                                             tenant_id=self.tenant_id)
            if record is not None:
                relations = list((record.get("scope") or {}).get(
                    "relations", ()))
            for column in columns:
                if column in out:
                    continue
                for relation in relations:
                    unit = disp.unit_for_field(catalog, relation, column)
                    if unit:
                        out[column] = unit
                        break
        return out

    #: What a reader is told when the numbers arrive without their write-up.
    #:
    #: Written HERE, by the server, and never by the model -- the model is
    #: precisely the thing that failed. It says what happened in the order a
    #: reader needs it: the analysis ran, the rows are real, the explanation
    #: is what is missing.
    RESULT_ONLY_NARRATIVE = (
        "**The analysis ran and its result is below.** CreditProbe could "
        "not write the accompanying explanation, so what follows is the "
        "query's own output with no commentary on it.\n\n"
        "Every figure here was computed and stored by CreditProbe and is "
        "shown exactly as it was recorded. Nothing in this response was "
        "written by the analyst.")

    def result_only_response(self, *, reason: str, purposes: Any = None,
                             catalog: Any = None, intent: Any = None,
                             rejected: FinalResponse | None = None
                             ) -> dict[str, Any]:
        """The computed result, published without a written answer.

        The defect this exists for
        --------------------------
        A live run executed its query, stored three result artifacts, and
        then could not write the answer about them. The reader was shown
        "This request stopped. Reason: ANSWER_FORMAT_EXHAUSTED" and nothing
        else -- no rows, no export, and on a refresh no record that the
        exchange had happened at all.

        The rows were never lost. They were in the artifact store the whole
        time, individually serveable, with their ids known to the
        orchestrator. What they did not have was an ADDRESS: the only
        channel a result can reach a reader through is the written answer
        object, and failing to produce that object is exactly what had
        happened.

        So this builds the object from the stored artifacts instead, with a
        server-written caveat in place of the narrative and NO numeric
        claims -- a claim is a thing the analyst asserted and had checked,
        and there is no analyst here to assert one. Every number comes from
        `render_tables`, which reads the artifact and formats it under the
        one display policy, so nothing published this way has passed
        through a model.

        THE CHARTS SURVIVE, when there are any. `rejected` is the answer
        that failed, and this used not to receive it at all: the parameter
        was absent, `charts` was a hard-coded `[]`, and a reader whose
        narrative was refused lost every picture the analyst had drawn
        along with it. Four live answers came back as bare tables that way,
        one of them after the reader had asked for a line chart by name.

        A chart is safe to keep where a claim is not. `_check_chart` judges
        a chart against its ARTIFACT -- the columns exist, the kind is
        known, the shape is readable -- and never against the narrative, so
        a chart that passed is still passing. `render_charts` then reads
        every value back out of the artifact store. Nothing the model wrote
        reaches the reader through this path; the drawing is the analyst's
        choice of WHICH stored rows to show, which is the same kind of
        choice the tables already carry.

        Returns `{}` when there is nothing to publish, which is the correct
        answer for a run that never executed anything.
        """
        artifacts = sorted(self.run_artifacts)
        if not artifacts:
            return {}
        titles = dict(purposes or {})

        tables: list[dict[str, Any]] = []
        for artifact_id in artifacts:
            record = self.store.get_artifact(artifact_id,
                                             tenant_id=self.tenant_id)
            if record is None or not record.get("columns"):
                continue
            step_id = str((record.get("scope") or {}).get("step_id") or "")
            tables.append({
                "artifact_id": artifact_id,
                "title": (titles.get(step_id) or titles.get(artifact_id)
                          or "Result"),
                "columns": list(record["columns"])})
        if not tables:
            return {}

        # Rendered by the SAME code path a published answer uses, so a
        # reader sees one table format whatever produced it.
        shell = FinalResponse(
            intent=intent, disposition="partial_answer",
            narrative=self.RESULT_ONLY_NARRATIVE, coverage=(),
            numeric_claims=(), evidence_refs=(), tables=tuple(tables),
            charts=(), limitations=(), suggested_questions=(),
            clarification_question="", clarification_options=(),
            referral_owner="", referral_reason="")
        rendered = self.render_tables(shell, catalog)

        # The analyst's own charts, re-checked and re-rendered from the
        # artifacts. A rejected NARRATIVE says nothing about whether a
        # picture of the stored rows was sound.
        charts: list[dict[str, Any]] = []
        if rejected is not None and rejected.charts:
            try:
                charts = self.render_charts(
                    self.surviving_charts(rejected), catalog)
            except Exception:  # noqa: BLE001 - a chart is never worth the rows
                charts = []

        body: dict[str, Any] = {
            "intent": intent.to_dict() if intent is not None else {},
            "disposition": "partial_answer",
            "narrative": self.RESULT_ONLY_NARRATIVE,
            "coverage": [], "numeric_claims": [], "evidence_refs": [],
            "tables": rendered, "charts": charts,
            "limitations": [
                "The written answer could not be produced, so these rows "
                "are published without an explanation of them.",
                reason],
            "suggested_questions": [],
            "clarification_question": "", "clarification_options": [],
            "referral_owner": "", "referral_reason": "",
            "evidence_bound": False,
            "executed": True,
            # The flag the reader's caveat banner hangs on, and the one
            # thing that distinguishes this from an answer somebody wrote.
            "result_only": True,
            "result_only_reason": reason,
        }
        if self.header is not None:
            body["release"] = self.header.to_dict()
        return body

    def render_tables(self, final: FinalResponse,
                      catalog: Any = None) -> list[dict[str, Any]]:
        """The rows a reader sees, built here from the stored artifact.

        The analyst chose the table: which result, which columns, what to
        call it. Every VALUE in it comes from the artifact CreditProbe
        executed and stored, formatted by the one display policy. No number
        in a published table has passed through the model.

        Ordering is the artifact's own, which the query produced at full
        precision. Sorting formatted strings would put SAR 9,000 million
        above SAR 40,599 million.
        """
        from backend.cockpit_v4 import display as disp

        out: list[dict[str, Any]] = []
        for table in final.tables:
            artifact_id = str(table.get("artifact_id") or "")
            record = (self.store.get_artifact(artifact_id,
                                              tenant_id=self.tenant_id)
                      if artifact_id in self.run_artifacts else None)
            body = dict(table)
            if record is None:
                out.append(body)
                continue
            columns = [str(c) for c in (table.get("columns")
                                        or record["columns"])]
            units = self._units_for(artifact_id, columns, catalog)
            rows = []
            for index, row in enumerate(record["rows"]):
                rows.append({
                    "row_id": deriv.row_id_for(index),
                    "canonical": {c: row.get(c) for c in columns},
                    "display": {c: _cell(row.get(c), units.get(c, ""), disp)
                                for c in columns},
                })
            body.update({"columns": columns, "column_units": units,
                         "rows": rows, "row_count": len(rows),
                         "rendered_by": "creditprobe"})
            out.append(body)
        return out

    def render_charts(self, charts: list[dict[str, Any]],
                      catalog: Any = None) -> list[dict[str, Any]]:
        """The same contract for a chart: the server supplies every point.

        The analyst decided a chart is useful and which series to show. The
        values are the artifact's, at full precision for ordering and scale,
        with the reader's form carried beside each point for axis labels and
        tooltips.
        """
        from backend.cockpit_v4 import display as disp

        out: list[dict[str, Any]] = []
        for chart in charts:
            artifact_id = str(chart.get("artifact_id") or "")
            record = (self.store.get_artifact(artifact_id,
                                              tenant_id=self.tenant_id)
                      if artifact_id in self.run_artifacts else None)
            body = dict(chart)
            if record is None:
                out.append(body)
                continue
            label = str(chart.get("x_column") or "")
            series = [str(c) for c in (chart.get("y_columns") or []) if c]
            units = self._units_for(artifact_id,
                                    [c for c in [label, *series] if c],
                                    catalog)
            # The unit the analyst DECLARED on the chart is authoritative for
            # its own axis: a chart is allowed to plot a measure no claim
            # happens to cite. The catalogue still fills in what it can.
            declared = str(chart.get("unit") or "")
            points = []
            for index, row in enumerate(record["rows"]):
                values = {c: row.get(c) for c in series}
                points.append({
                    "row_id": deriv.row_id_for(index),
                    "label": row.get(label) if label else None,
                    "values": values,
                    "display": {
                        c: _cell(values[c], units.get(c, declared), disp)
                        for c in series},
                })
            body.update({"points": points,
                         "series_units": {c: units.get(c, declared)
                                          for c in series},
                         "rendered_by": "creditprobe"})
            kind = str(chart.get("kind") or "").lower()
            if kind == HEATMAP:
                body["matrix"] = self._matrix(chart, record, units, declared,
                                              disp)
            elif kind == BOX:
                body["boxes"] = self._boxes(chart, record, units, declared,
                                            disp)
            relations = list((record.get("scope") or {}).get("relations", ()))
            self._attach_axes(body, kind, label, series, units, declared,
                              record, relations, catalog, disp)
            out.append(body)
        return out

    @staticmethod
    def _attach_axes(body: dict[str, Any], kind: str, x_column: str,
                     series: list[str], units: dict[str, str], declared: str,
                     record: dict[str, Any], relations: list[str],
                     catalog: Any, disp: Any) -> None:
        """Put the scale a reader reads the chart against onto the payload.

        The charts had no axes because the browser is not allowed to invent
        a number and nobody had built the place a tick could come from. This
        is that place: the tick VALUES by a 1/2/5 rule, each tick's STRING
        by `display.py`, and the label by the catalogue's own business name
        -- so an axis says "Exposure at default", not `ead_sar_mn`.

        A form with no axis gets none. A pie divides a whole and has no
        scale; a category axis names its positions and carries no ticks,
        because the categories ARE the points and a second copy of those
        strings is a second copy free to drift.
        """
        from backend.cockpit_v4 import axis as axis_mod

        if kind in COMPOSITION_KINDS - {"stacked_bar", "stacked_bar_100"}:
            return  # pie, donut

        def label_for(column: str) -> str:
            return axis_mod.field_label(catalog, relations, column)

        def unit_for(column: str) -> str:
            return units.get(column, declared)

        rows = record["rows"]

        def column_values(column: str) -> list[Any]:
            return [row.get(column) for row in rows]

        if kind == HEATMAP:
            matrix = body.get("matrix") or {}
            body["x_axis"] = axis_mod.category_axis(
                label_for(str(matrix.get("column_axis") or x_column)),
                str(matrix.get("column_axis") or x_column))
            row_axis = str(matrix.get("row_axis") or "")
            body["y_axis"] = axis_mod.category_axis(
                label_for(row_axis), row_axis)
            return

        if kind == BOX:
            # A box plot lays its categories down the page and its measure
            # across, so the axes are the transpose of every other form's.
            measure = series[0] if series else ""
            spread = [value
                      for box in (body.get("boxes") or [])
                      for value in (box.get("minimum"), box.get("maximum"))]
            body["y_axis"] = axis_mod.category_axis(
                label_for(x_column), x_column)
            built = axis_mod.measure_axis(
                label_for(measure), measure, unit_for(measure), spread, disp,
                include_zero=False)
            if built is not None:
                body["x_axis"] = built
            return

        # Everything else: categories or a sequence across, a measure up.
        built_x = None
        if kind in ("scatter", "bubble"):
            built_x = axis_mod.measure_axis(
                label_for(x_column), x_column, unit_for(x_column),
                column_values(x_column), disp, include_zero=False)
        if built_x is not None:
            body["x_axis"] = built_x
        elif x_column:
            # A scatter whose x column holds no numbers is not a scatter,
            # but it still has positions and they still have names. Saying
            # what they are beats publishing no axis at all.
            body["x_axis"] = axis_mod.category_axis(
                label_for(x_column), x_column)

        if kind == "stacked_bar_100":
            # The axis of a 100% stack is the share, not the measure: the
            # segments are re-scaled to the whole, so labelling the height
            # in SAR would put a denomination on a proportion.
            body["y_axis"] = {
                "kind": axis_mod.MEASURE, "label": "Share of total",
                "column": "", "unit": "PCT",
                "ticks": [{"value": value,
                           "display": disp.format_value(Decimal(value), "PCT")}
                          for value in (0, 25, 50, 75, 100)]}
            return

        def measure_for(columns: list[str], *, stacked: bool,
                        zero_based: bool) -> dict[str, Any] | None:
            if not columns:
                return None
            spread = ([axis_mod.stack_total(row, columns) for row in rows]
                      if stacked
                      else [row.get(c) for row in rows for c in columns])
            unit = unit_for(columns[0])
            return axis_mod.measure_axis(
                # A chart with two measures on one scale cannot be named
                # after whichever series is first; the scale they share can.
                label_for(columns[0]) if len(columns) == 1
                else axis_mod.unit_label(unit, disp),
                columns[0] if len(columns) == 1 else "",
                unit, spread, disp, include_zero=zero_based)

        if kind == "combo":
            # The one form this product draws on two scales, because that is
            # what it is FOR: a rate over the volumes it is a rate of. Both
            # scales are published, named, so a reader can see which mark
            # belongs to which -- an unnamed second axis is the chart
            # mistake this is otherwise indistinguishable from.
            bars = measure_for(series[:1], stacked=False, zero_based=True)
            if bars is not None:
                body["y_axis"] = bars
            line = measure_for(series[1:2], stacked=False, zero_based=False)
            if line is not None:
                body["y_axis_secondary"] = line
            return

        # A bar encodes magnitude by LENGTH, so its axis must include zero
        # or a 3% difference is drawn as a doubled bar. A line or an area
        # encodes by position over a sequence and makes no such claim; a
        # waterfall bridges to a total and is meaningless off zero.
        built = measure_for(
            series, stacked=kind in ("stacked_bar", "waterfall"),
            zero_based=kind == "waterfall" or kind not in SEQUENCE_KINDS)
        if built is not None:
            body["y_axis"] = built

    @staticmethod
    def _matrix(chart: dict[str, Any], record: dict[str, Any],
                units: dict[str, str], declared: str, disp: Any
                ) -> dict[str, Any]:
        """A from/to result as the grid it is.

        A rating migration is `rating_from`, `rating_to` and a measure. Laid
        out flat it is forty-nine rows a reader compares by scrolling; laid
        out as a grid it is one picture, and the diagonal -- everything that
        did not move -- is visible without reading a single number. A live
        answer published the flat form because the contract had no second
        axis to put the rows on.

        Every cell is keyed `row|column`, which is what the renderer looks
        up. A pair the result does not contain is simply absent: an empty
        cell means "no borrowers made that move", and writing a zero there
        would assert something the query never said.
        """
        column_axis = str(chart.get("x_column") or "")
        row_axis = str(chart.get("series_column") or "")
        measure = next((str(c) for c in (chart.get("y_columns") or []) if c),
                       "")
        unit = units.get(measure, declared)

        rows_seen: list[str] = []
        columns_seen: list[str] = []
        cells: dict[str, Any] = {}
        display: dict[str, Any] = {}
        for row in record["rows"]:
            down = str(row.get(row_axis, ""))
            across = str(row.get(column_axis, ""))
            if down not in rows_seen:
                rows_seen.append(down)
            if across not in columns_seen:
                columns_seen.append(across)
            value = row.get(measure) if measure else None
            cells[f"{down}|{across}"] = value
            display[f"{down}|{across}"] = _cell(value, unit, disp)

        # ONE ORDERED AXIS FOR BOTH SIDES when the two name the same things,
        # which a migration always does: from-A must sit above to-A or the
        # diagonal is not a diagonal and the picture says nothing.
        #
        # The order is the RESULT'S, as it is for every published table --
        # first appearance, which is what the query's ORDER BY produced.
        # Sorting the labels instead would put a rating axis in alphabetical
        # order, `A, A+, A-, AA, BBB`, and a diagonal drawn through that
        # means nothing. The analyst ordered the query; this draws it.
        axis = list(rows_seen)
        axis.extend(c for c in columns_seen if c not in rows_seen)
        square = set(rows_seen) == set(columns_seen)
        return {"row_axis": row_axis, "column_axis": column_axis,
                "measure": measure, "unit": unit,
                "rows": axis if square else rows_seen,
                "columns": axis if square else columns_seen,
                "square": square, "cells": cells, "display": display}

    @staticmethod
    def _boxes(chart: dict[str, Any], record: dict[str, Any],
               units: dict[str, str], declared: str, disp: Any
               ) -> list[dict[str, Any]]:
        """The five numbers a box plot draws, computed HERE.

        The same rule as every other published figure: the server owns the
        number. Sending the raw observations and letting a browser compute
        quartiles would put arithmetic a reader acts on into the client,
        where it is neither checked nor reproducible from the trace.

        Quartiles by the linear-interpolation convention, which is what
        `numpy.percentile` and every spreadsheet produce, so a reader
        checking the box against their own tooling gets the same numbers.
        """
        group = str(chart.get("x_column") or "")
        measure = next((str(c) for c in (chart.get("y_columns") or []) if c),
                       "")
        unit = units.get(measure, declared)

        grouped: dict[str, list[Decimal]] = {}
        for row in record["rows"]:
            key = str(row.get(group, "")) if group else ""
            try:
                value = Decimal(str(row.get(measure)))
            except (InvalidOperation, ValueError, TypeError):
                continue
            grouped.setdefault(key, []).append(value)

        def quantile(ordered: list[Decimal], fraction: float) -> Decimal:
            if len(ordered) == 1:
                return ordered[0]
            position = Decimal(str(fraction)) * (len(ordered) - 1)
            low = int(position)
            high = min(low + 1, len(ordered) - 1)
            return (ordered[low]
                    + (ordered[high] - ordered[low]) * (position - low))

        out: list[dict[str, Any]] = []
        for key, values in grouped.items():
            ordered = sorted(values)
            five = {"minimum": ordered[0], "q1": quantile(ordered, 0.25),
                    "median": quantile(ordered, 0.5),
                    "q3": quantile(ordered, 0.75), "maximum": ordered[-1]}
            out.append({
                "label": key, "count": len(ordered), "unit": unit,
                **{name: str(value) for name, value in five.items()},
                "display": {name: _cell(value, unit, disp)
                            for name, value in five.items()}})
        return out

    def _check_table(self, table: dict[str, Any], index: int) -> list[str]:
        """A published table must project columns the artifact really has.

        A table carries no values of its own: it names an artifact and the
        columns to show, and the reader is served the stored rows. That is
        why a table cannot misreport a number -- but it CAN name a column
        that does not exist, which renders as an empty column and reads as
        missing data rather than as a mistake in the answer.
        """
        problems: list[str] = []
        artifact_id = str(table.get("artifact_id") or "")
        if not artifact_id:
            return problems
        if artifact_id not in self.run_artifacts:
            return [f"tables[{index}] references artifact {artifact_id!r}, "
                    f"which this run did not produce."]
        record = self.store.get_artifact(artifact_id,
                                         tenant_id=self.tenant_id)
        if record is None:
            return [f"tables[{index}] references artifact {artifact_id!r}, "
                    f"which is not available to you."]
        available = set(record["columns"])
        missing = [c for c in (table.get("columns") or [])
                   if str(c) not in available]
        if missing:
            problems.append(
                f"tables[{index}] names columns {missing}, which are not in "
                f"artifact {artifact_id!r}. Its columns are "
                f"{record['columns']}.")
        return problems

    def _check_ordering(self, final: FinalResponse) -> list[str]:
        """A ranking claimed in words must be a ranking in the evidence.

        Checked only where the answer both CLAIMS an order ("the largest",
        "top five") and publishes a RANKING chart, because a chart of ranked
        bars is the reader's ranking. The test is monotonicity on the charted
        measure, in either direction -- an ascending chart under "the
        smallest" is as correct as a descending one under "the largest". It
        catches the specific error of a query that forgot its ORDER BY under
        an answer that says "the biggest", which no other check would see.

        ONLY A RANKING CHART MAKES A RANKING CLAIM. This paragraph has
        always said "a chart of ranked bars"; the code tested every kind,
        and that is a defect a live run paid for twice. A line over time is
        ordered by PERIOD, so a delinquency trend that rises and then falls
        is not monotonic and never will be -- and the narrative had only to
        contain "most", "highest" or "worst", which is most of the
        vocabulary of a delinquency write-up. Two answers were refused, the
        single correction was spent on a rejection that reproduces
        identically, and the reader was handed rows with no explanation.
        A sequence is not a rank, and neither is a distribution or a flow.
        """
        if not final.charts:
            return []
        words = final.narrative.lower()
        if not _SUPERLATIVE.search(words):
            return []
        problems: list[str] = []
        # SURVIVING charts, not every chart the analyst sent: one that is
        # about to be dropped as a warning must not hard-reject the answer
        # it was only decorating.
        for index, chart in enumerate(self.surviving_charts(final)):
            if str(chart.get("kind") or "").lower() not in RANKING_KINDS:
                continue
            artifact_id = str(chart.get("artifact_id") or "")
            if artifact_id not in self.run_artifacts:
                continue
            record = self.store.get_artifact(artifact_id,
                                             tenant_id=self.tenant_id)
            if record is None:
                continue
            for column in (chart.get("y_columns") or [])[:1]:
                values = []
                for row in record["rows"]:
                    cell = row.get(column)
                    if cell is None:
                        values = []
                        break
                    try:
                        values.append(Decimal(str(cell)))
                    except InvalidOperation:
                        values = []
                        break
                if len(values) < 3:
                    continue
                descending = all(a >= b for a, b in zip(values, values[1:]))
                ascending = all(a <= b for a, b in zip(values, values[1:]))
                if not (descending or ascending):
                    problems.append(
                        f"chart {index + 1} "
                        f"({str(chart.get('title') or chart.get('kind'))!r}) "
                        f"claims a ranking and charts {column!r}, but "
                        f"artifact {artifact_id!r} is not ordered by it. "
                        f"Order the query by the measure you are ranking, "
                        f"draw it as a sequence rather than a ranking, or "
                        f"drop the ranking language.")
        return problems

    def _check_movement(self, final: FinalResponse) -> list[str]:
        """A MOVEMENT STATEMENT CHECKED AGAINST ITS OWN TWO FIGURES.

        CLOSURE-02. A live answer wrote, of past-due exposure, that it had
        gone "from 8.08% of exposure to 8.42%" and then that "it has
        actually eased slightly". The numbers were right and the sentence
        contradicted them.

        This does NOT read the analysis. It makes no judgement about
        whether a move is material, what caused it, or whether easing is
        good. It takes one sentence that names a start figure and an end
        figure THROUGH THE CLAIMS THE SERVER COMPUTED, and refuses it when
        the direction word says the opposite of the arithmetic. The
        interpretation stays the model's; the arithmetic stays the
        server's.

        NARROW BY CONSTRUCTION, on the same discipline as
        `_check_policy_citation`. It fires only on a sentence that:

          * carries EXACTLY TWO claim placeholders, both numeric and both
            in the same unit -- two measures moving in one sentence are two
            statements and this cannot tell which word belongs to which;
          * puts one of them after "from" and the other after "to", which
            is the one English structure that says unambiguously which
            figure is the start ("B, up from A" does not match and is left
            alone);
          * contains direction words of exactly ONE sense.

        Anything else is silent.
        """
        if not final.narrative or not self.canonical:
            return []
        problems: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", final.narrative):
            problem = self._movement_problem(sentence)
            if problem:
                problems.append(problem)
        return problems

    def _movement_problem(self, sentence: str) -> str:
        found = list(PLACEHOLDER.finditer(sentence))
        if len(found) != 2:
            return ""
        words = {w.lower() for w in re.findall(r"[A-Za-z]+", sentence)}
        up, down = words & _MOVED_UP, words & _MOVED_DOWN
        if bool(up) == bool(down):
            return ""

        ends: dict[str, Any] = {}
        units: dict[str, str] = {}
        for match in found:
            claim_id = match.group(1)
            verdict = self.canonical.get(claim_id)
            if verdict is None or verdict.canonical is None:
                return ""
            prepositions = _FROM_TO.findall(sentence[:match.start()])
            if not prepositions:
                return ""
            which = prepositions[-1].lower()
            if which in ends:
                return ""
            ends[which] = verdict.canonical
            units[claim_id] = next(
                (c.unit for c in self._claims if c.claim_id == claim_id), "")
        if set(ends) != {"from", "to"}:
            return ""
        if len({u.strip().lower() for u in units.values()}) != 1:
            return ""

        start, end = ends["from"], ends["to"]
        if start == end:
            return ""
        went_up = end > start
        if went_up == bool(up):
            return ""
        said = sorted(up or down)[0]
        return (
            f"the answer says {said!r} of a movement from "
            f"{start} to {end}, which went "
            f"{'up' if went_up else 'down'}. Those are CreditProbe's own "
            f"figures for the two claims in that sentence: describe the "
            f"movement they show, or name the measure that did move the "
            f"way you meant.")

    def _check_policy_citation(self, final: FinalResponse) -> list[str]:
        """A policy action names the clause it changes.

        The same discipline as a numeric claim with no evidence, and for the
        same reason. "We should tighten the non-salaried cut-off" reads as
        governance advice whether or not any such cut-off exists, whether or
        not the analyst knows what it currently is, and whether or not the
        clause it would change is the one it names. A reader cannot tell the
        difference from the prose, which is precisely what makes confident
        policy language dangerous in a way confident numbers are not: a
        wrong number can be checked.

        DELIBERATELY NARROW. The trigger is the word "policy" in the same
        sentence as a change verb, not a list of words that appear in every
        credit write-up. A check that fires on "recommend" or "tighten"
        alone would refuse ordinary analysis for using ordinary English,
        which is the failure mode `_check_ordering` cost this product two
        live answers for.

        The OTHER book's clause is refused unconditionally. `CP-1.2` in a
        Retail answer is a rule that does not govern the exposure being
        discussed, and quoting it is worse than quoting nothing: it is a
        citation that looks checked and is not.
        """
        if not self.domain_id or not final.narrative:
            return []
        from backend.cockpit_v4 import credit_policy as cp

        problems: list[str] = []
        foreign = cp.foreign_citations(final.narrative, self.domain_id)
        if foreign:
            other = ("Corporate" if self.domain_id == "retail"
                     else "Retail")
            problems.append(
                f"the answer cites {', '.join(foreign)}, which belongs to "
                f"the {other} credit policy and does not govern this book. "
                f"Cite this book's own clause, or drop the citation.")

        sentences = [x for x in re.split(r"(?<=[.!?])\s+", final.narrative)
                     if _POLICY_ACTION.search(x)]
        cited = cp.citations(final.narrative, self.domain_id)
        if sentences and not cited:
            problems.append(
                "the answer proposes a policy change and cites no clause of "
                "this book's credit policy. The clauses this question "
                "reaches are attached to this turn: name the one the change "
                "would alter and quote the rule in force, or state the "
                "finding without proposing an action.")
        if not cited:
            problems += self._unbound_thresholds(final.narrative, cp)
        return problems

    def _unbound_thresholds(self, narrative: str, cp: Any) -> list[str]:
        """A POLICY THRESHOLD QUOTED WITH NO CLAUSE NAMED.

        H-LIVE-06. A live answer wrote "well inside the CP-1.1 single
        obligor limit of SAR 25,000 million". That one was cited and is
        governed; `finalize_response` warned only that the bare figure was
        not bound to artifact evidence, which it could not be -- a policy
        threshold is not a cell in a result and the claim contract has
        nowhere to put it. So the warning was noise on a correct answer and
        would have been the same noise on an uncited one, and the answer
        published either way.

        A threshold is bound to the PACK, not to an artifact. The pack is in
        front of every turn that writes an answer, and naming the clause is
        the binding. This refuses the case where neither happened: a figure
        this book's policy sets, presented as a limit, with no clause of
        this book named anywhere in the answer.

        DELIBERATELY NARROW, on the same discipline as `_POLICY_ACTION`.
        The sentence must call the number a limit, and the number must be
        one this book's pack actually sets. An ordinary sentence about a
        ratio of 1.2 is not a covenant citation, and a portfolio figure
        that happens to equal a threshold is not either unless the
        sentence says it is one.
        """
        thresholds: set[Decimal] = set()
        try:
            for entry in (cp.synopsis(self.domain_id).get(
                    "thresholds_a_portfolio_number_meets") or ()):
                for value in (entry.get("thresholds") or {}).values():
                    try:
                        thresholds.add(Decimal(str(value)))
                    except (InvalidOperation, ValueError):
                        continue
        except Exception:  # noqa: BLE001 - a book with no policy pack
            return []
        if not thresholds:
            return []
        problems: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", narrative):
            if not _POLICY_LIMIT.search(sentence):
                continue
            for written in _NUMBER.findall(sentence):
                try:
                    value = Decimal(written.replace(",", ""))
                except (InvalidOperation, ValueError):
                    continue
                if value not in thresholds:
                    continue
                problems.append(
                    f"the answer presents {written} as a policy limit and "
                    f"names no clause of this book's credit policy. That "
                    f"figure is one this book's pack sets, and the pack is "
                    f"attached to this turn: cite the clause it comes from "
                    f"and quote the rule in force. A threshold is bound to "
                    f"the clause that sets it, not to a result cell, and an "
                    f"uncited one reads as governance whether or not it is.")
                break
            if problems:
                break
        return problems

    def _check_chart(self, chart: dict[str, Any], index: int) -> str:
        kind = str(chart.get("kind") or "").lower()
        if kind not in CHART_KINDS:
            return (f"chart {index} asks for {kind!r}, which is not a form "
                    f"CreditProbe draws ({', '.join(CHART_KINDS)}); the "
                    f"chart was dropped. It used to be drawn as a bar "
                    f"whatever it said.")
        artifact_id = str(chart.get("artifact_id") or "")
        if artifact_id not in self.run_artifacts:
            return (f"chart {index} references artifact {artifact_id!r}, "
                    f"which this run did not produce; the chart was dropped.")
        record = self.store.get_artifact(artifact_id, tenant_id=self.tenant_id)
        if record is None:
            return (f"chart {index} references an artifact not available to "
                    f"you; the chart was dropped.")
        columns = set(record["columns"])
        missing = [c for c in
                   [chart.get("x_column"), chart.get("series_column"),
                    *(chart.get("y_columns") or [])]
                   if c and c not in columns]
        if missing:
            return (f"chart {index} names columns {missing} that are not in "
                    f"its artifact; the chart was dropped.")
        kind = str(chart.get("kind") or "").lower()
        if kind == HEATMAP and not chart.get("series_column"):
            return (f"chart {index} is a heatmap with no series_column, so "
                    f"it has only one axis and no cells to fill; the chart "
                    f"was dropped. A heatmap needs x_column for the columns, "
                    f"series_column for the rows and y_columns[0] for the "
                    f"value in each cell.")
        return self._chart_shape_problem(chart, index, record)

    def _chart_shape_problem(self, chart: dict[str, Any], index: int,
                             record: dict[str, Any]) -> str:
        """Is this result the shape a chart can actually say something about?

        §26: the analyst decides whether a picture helps; CreditProbe checks
        the decision against the result it would be drawn from. Two things
        make a chart useless whatever was intended, and both are countable.

        ONE POINT is not a comparison. A single scalar drawn as one bar tells
        a reader nothing they did not read in the sentence above it.

        TOO MANY POINTS is not a comparison either. Twelve sectors ranked by
        exposure is a chart; ninety borrowers with their covenant status is a
        list, and drawing it produces a wall of bars nobody reads and a
        label column nobody can align. This is the live case in §29 -- a
        "show me the customers behind this" answer arrived with a bar per
        borrower, which is a worse way to read the same table.

        Note what this does NOT do: infer intent from the question, or from
        the column names, or from the grain. A top-ten borrower ranking is
        ten points and passes, because ten points IS a readable comparison
        whatever the rows are called.

        WHAT COUNTS AS A POINT DEPENDS ON THE FORM. For a bar or a line it
        is a distinct x value, and the arithmetic above is right. For a
        MATRIX it is not: a rating migration over seven grades is forty-nine
        rows and seven categories, and a reader takes in a 7x7 grid at a
        glance. Counting its cells dropped it as "past the 25 a reader can
        take in" -- which is how a live rating migration arrived as a flat
        48-row table. For a BOX PLOT the rows ARE the distribution and are
        supposed to be many; what a reader counts is the boxes.
        """
        label = str(chart.get("x_column") or "")
        rows = record["rows"]
        kind = str(chart.get("kind") or "").lower()
        if kind == HEATMAP:
            # The larger of the two axes: a 7x3 grid is as readable as 7x7,
            # and it is the longer axis that runs out of room first.
            series = str(chart.get("series_column") or "")
            points = max(len({str(row.get(label)) for row in rows}),
                         len({str(row.get(series)) for row in rows}))
        elif kind == BOX:
            points = (len({str(row.get(label)) for row in rows}) if label
                      else 1)
            if not label:
                # One ungrouped distribution is a legitimate single box, and
                # "a chart needs two points to compare anything" is about
                # comparing categories, not about summarising rows.
                return ("" if len(rows) >= MIN_BOX_OBSERVATIONS else
                        f"chart {index} summarises {len(rows)} row(s); a box "
                        f"plot needs at least {MIN_BOX_OBSERVATIONS} to have "
                        f"quartiles worth drawing. The chart was dropped.")
        else:
            points = (len({str(row.get(label)) for row in rows}) if label
                      else len(rows))
        if points < MIN_CHART_POINTS:
            return (f"chart {index} would have {points} point(s); a chart "
                    f"needs at least {MIN_CHART_POINTS} to compare anything. "
                    f"The table says it better and the chart was dropped.")
        if points > MAX_CHART_POINTS:
            return (f"chart {index} would have {points} points, past the "
                    f"{MAX_CHART_POINTS} a reader can take in. A result this "
                    f"long is a table -- rank it and show the top rows if a "
                    f"picture is wanted. The chart was dropped.")
        return ""

    def surviving_charts(self, final: FinalResponse) -> list[dict[str, Any]]:
        kept = [c for i, c in enumerate(final.charts)
                if not self._check_chart(c, i)]
        return kept[:self.limits.charts]

    def validate_suggestions(self, final: FinalResponse, catalog: Any
                             ) -> list[dict[str, Any]]:
        """Keep only suggestions whose fields and quarters actually exist.

        Checked from catalog metadata, never by running a sample analysis:
        a suggestion is not worth a query.
        """
        kept = []
        calendar = getattr(catalog, "calendar", None)
        quarters = set(getattr(calendar, "slots", ()) or ())
        for suggestion in final.suggested_questions:
            fields = [str(f) for f in (suggestion.get("required_fields") or [])]
            ok = True
            for field_id in fields:
                relation, _, column = str(field_id).partition(".")
                if not column:
                    ok = False
                    break
                try:
                    catalog.resolve(relation, column)
                except Exception:  # noqa: BLE001
                    ok = False
                    break
            for quarter in (suggestion.get("required_quarters") or []):
                if quarters and str(quarter) not in quarters:
                    ok = False
                    break
            if ok:
                kept.append(dict(suggestion))
        return kept


def _close(asserted: Decimal, computed: Decimal) -> bool:
    """Exact, or within the last place of a rounded display value.

    Not a licence to be approximately right. A derived figure is compared at
    a relative 1e-9, which absorbs a decimal string the analyst rounded and
    nothing wider -- a dropped row moves these by whole millions.
    """
    if asserted == computed:
        return True
    if computed == 0:
        return abs(asserted) <= deriv.DEFAULT_TOLERANCE
    return abs(asserted - computed) / abs(computed) <= deriv.DEFAULT_TOLERANCE


_MISSING = object()


def _locate(rows: list[dict[str, Any]], row_key: str, column: str) -> Any:
    """Find the referenced cell.

    `row_key` may be a published row id ("r0"), a bare index, a
    `column=value` key, or a value that appears in exactly that row. The
    vocabulary is deliberately the SAME one the derivation resolver accepts:
    the result packet publishes "r0", and a direct claim citing "r0" being
    refused while a derivation citing "r0" was accepted would be a trap of
    our own making.
    """
    if not rows:
        return _MISSING
    index = deriv._index_of(row_key, rows)
    if index < 0:
        return _MISSING
    return rows[index].get(column, _MISSING)


def correction_packet(final: FinalResponse, report: ValidationReport, *,
                      store: Any, tenant_id: str,
                      run_artifacts: set[str]) -> dict[str, Any]:
    """Everything needed to fix the BINDING, and nothing that reruns anything.

    The live EAD run failed validation twice over row names, and each
    rejection sent back prose. Prose is enough to know something is wrong and
    not enough to fix it: the analyst was never told what the row ids
    actually were. This packet answers that directly -- here is the artifact
    you may cite, here are its columns, here are its row ids, here is how a
    calculated number must be expressed -- so the repair is a rewrite of the
    evidence binding rather than another guess.
    """
    artifacts = []
    for artifact_id in sorted(run_artifacts):
        record = store.get_artifact(artifact_id, tenant_id=tenant_id)
        if record is None:
            continue
        rows = record["rows"]
        artifacts.append({
            "artifact_id": artifact_id,
            "columns": record["columns"],
            "row_count": len(rows),
            "row_ids": [deriv.row_id_for(i) for i in range(len(rows))],
            "rows": [{"row_id": deriv.row_id_for(i), **row}
                     for i, row in enumerate(rows)],
        })
    return {
        "what_to_do": (
            "Send finalize_response again with the SAME analysis and "
            "corrected evidence. The query already ran and its result is "
            "below; do not run it again, do not read the catalogue again, "
            "and do not ask the user anything."),
        "problems": list(report.problems),
        "rejected_claims": [
            {"claim_id": c.claim_id, "claimed_value": c.decimal_value,
             "unit": c.unit,
             "kind": "derived" if c.is_derived else "direct"}
            for c in final.numeric_claims],
        "authorized_artifacts": artifacts,
        # A CORRECTION REPLACES THE WHOLE ANSWER. The packet used to say
        # nothing about charts, so a corrected answer came back with none:
        # the reader lost every chart by getting a better sentence. The
        # charts are echoed by title and kind -- not by their points, which
        # the analyst already has -- so resending them is a copy, not a
        # reconstruction.
        "charts": {
            "resend_them": (
                "This correction replaces your whole answer, charts "
                "included. Send the same charts again unless a problem "
                "above names one, in which case fix that one and send the "
                "rest unchanged."),
            "you_sent": [
                {"title": str(chart.get("title") or ""),
                 "kind": str(chart.get("kind") or ""),
                 "artifact_id": str(chart.get("artifact_id") or "")}
                for chart in final.charts],
        },
        "direct_value": (
            "A number that appears in one result cell: send 'evidence' with "
            "artifact_id, row_key (one of the result's row_ids, e.g. 'r0') "
            "and column_id."),
        "row_scope": (
            "Each operand says WHICH cells one way and not both: "
            "rows='all' for every row of that result -- a total, or the "
            "denominator of a share -- or row_ids naming the ones you mean. "
            "rows='all' is refused on a result that was clipped at the "
            "preview cap."),
        "calculated_value": (
            "A number calculated from the result -- a total, a share, a "
            "difference, a growth rate: send 'derivation' instead of "
            "'evidence'. Do NOT invent a row such as 'all sectors' or 'top "
            "4 sectors'; name the real row ids and let the operation add "
            "them up."),
        "operations": deriv.describe(),
    }


def rejection(report: ValidationReport) -> Rejection:
    return Rejection(
        ANSWER_VALIDATION,
        "The response was not published. " + " ".join(report.problems)
        + " Correct the response itself; no new analysis is available for "
          "this correction.",
        field_path="finalize_response",
        detail={"problems": report.problems})


__all__ = ["Finalizer", "MAX_CHART_POINTS", "MIN_CHART_POINTS",
           "PLACEHOLDER", "ValidationReport",
           "correction_packet", "rejection"]
