"""The final answer is checked against the evidence before anyone sees it.

Specification sections 29, 30 and 31.

What this is for
----------------
A model that has just read a result table and is writing prose about it will
occasionally write 52 where the table says 42. Nothing upstream catches that:
the SQL was valid, the execution succeeded, the review found the evidence
sufficient, and the sentence is fluent. The only place it can be caught is
here, by holding the prose against the rows.

What this module does NOT do
----------------------------
It does not rewrite the prose. That rule is the same one that governs SQL and
Python, for the same reason: a module that edits a claim to match the evidence
has authored a claim, and the next person to read it cannot tell which parts
the analyst wrote. So a failure produces a report -- the exact answer, the
exact errors, the exact evidence -- and Opus rewrites it. Once.

Why once
--------
A rewrite that fails validation twice is not converging, and a loop that lets
it try again is an open-ended loop with a model in it. The second failure ends
the same way a five-submission exhaustion does: render what IS supported, say
plainly what was removed, and stop.

Charts and suggestions are different
------------------------------------
They are optional. An unsupported figure in the prose is a wrong answer; an
unsupported chart is decoration that failed. So charts and suggested questions
are DROPPED rather than sent back, and a dropped chart never restarts
analytical reasoning. Section 30 says this in terms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_agentic import DOMAIN
from backend.cockpit_agentic import contracts as K

# ---- outcome vocabulary, section 29 -----------------------------------

VALID = "VALID"
UNSUPPORTED_NUMERIC_CLAIM = "UNSUPPORTED_NUMERIC_CLAIM"
INVALID_EVIDENCE_REFERENCE = "INVALID_EVIDENCE_REFERENCE"
DOMAIN_LEAK = "DOMAIN_LEAK"
INVALID_CHART = "INVALID_CHART"
INVALID_PRODUCT_CLAIM = "INVALID_PRODUCT_CLAIM"
RESPONSE_CONTRACT_INVALID = "RESPONSE_CONTRACT_INVALID"

CATEGORIES: tuple[str, ...] = (
    VALID, UNSUPPORTED_NUMERIC_CLAIM, INVALID_EVIDENCE_REFERENCE, DOMAIN_LEAK,
    INVALID_CHART, INVALID_PRODUCT_CLAIM, RESPONSE_CONTRACT_INVALID)

#: Categories that send the answer back to Opus. The rest are handled by
#: dropping the offending optional part, which needs no model call.
BLOCKING: frozenset[str] = frozenset({
    UNSUPPORTED_NUMERIC_CLAIM, INVALID_EVIDENCE_REFERENCE, DOMAIN_LEAK,
    INVALID_PRODUCT_CLAIM, RESPONSE_CONTRACT_INVALID})

#: Numbers in prose. Captures thousands separators, decimals and a trailing
#: percent or basis-point unit, because "42 bps" and "0.42%" are the same
#: claim written two ways and both must be checkable.
_NUMBER = re.compile(
    r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
    r"\s*(%|percent|percentage points?|pp|bps|basis points?)?", re.I)

#: Numbers that are never a claim about the portfolio. A quarter label, a year,
#: an ordinal, a count of things the answer itself lists. Checking these would
#: produce noise that trains a reader to ignore the checker.
_NOT_A_MEASUREMENT = re.compile(
    r"(?:^|[^\d])(19|20)\d{2}(?:Q[1-4])?(?:$|[^\d])|^\s*[1-9]\s*$", re.I)

#: How close a prose figure must be to a value in the evidence. Rounding is
#: legitimate -- 0.4237 written as 0.42, 42.4 as 42 -- and a tolerance that
#: refused rounding would make the check unusable. A tolerance that accepted
#: 42 for 52 would make it pointless.
RELATIVE_TOLERANCE = 0.005
ABSOLUTE_TOLERANCE = 0.51


@dataclass
class Finding:
    """One thing wrong with the answer, in terms Opus can act on."""

    category: str
    detail: str
    #: The exact fragment at issue, so the rewrite request does not make Opus
    #: hunt for it.
    fragment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "detail": self.detail,
                "fragment": self.fragment}


@dataclass
class Report:
    """What the check found, and what was done about the optional parts."""

    findings: list[Finding] = field(default_factory=list)
    dropped_charts: list[str] = field(default_factory=list)
    dropped_suggestions: list[str] = field(default_factory=list)
    dropped_references: list[str] = field(default_factory=list)
    numbers_checked: int = 0
    numbers_matched: int = 0

    @property
    def valid(self) -> bool:
        """True when nothing BLOCKING was found. Dropped decoration is not a
        failure: the answer without a chart is still the answer."""
        return not any(f.category in BLOCKING for f in self.findings)

    @property
    def categories(self) -> list[str]:
        seen: list[str] = []
        for finding in self.findings:
            if finding.category not in seen:
                seen.append(finding.category)
        return seen or [VALID]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "categories": self.categories,
            "findings": [f.to_dict() for f in self.findings],
            "dropped_charts": list(self.dropped_charts),
            "dropped_suggestions": list(self.dropped_suggestions),
            "dropped_evidence_references": list(self.dropped_references),
            "numbers_checked": self.numbers_checked,
            "numbers_matched": self.numbers_matched,
        }


# ---- gathering what the evidence actually says -------------------------

def _numbers_in(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    text = str(value)
    found: list[float] = []
    for raw, _unit in _NUMBER.findall(text):
        try:
            found.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return found


def evidence_values(results: list[Any]) -> set[float]:
    """Every number the executed steps actually produced.

    Plus the differences and percentage changes between values in the same
    column, because "ECL fell by 12" is a claim about the evidence that no
    single cell contains. Derivations of derivations are not included: at that
    point the set is large enough to accept nearly anything, and a checker
    that accepts nearly anything is a checker in name only.
    """
    direct: set[float] = set()
    columns: dict[str, list[float]] = {}
    for packet in results:
        for step in getattr(packet, "steps", []):
            for row in getattr(step, "rows", []) or []:
                items = row.items() if isinstance(row, dict) else []
                for name, value in items:
                    for number in _numbers_in(value):
                        direct.add(number)
                        columns.setdefault(str(name), []).append(number)

    derived: set[float] = set()
    for values in columns.values():
        if len(values) > 40:
            # A long series has too many pairs to be evidence of anything.
            continue
        for i, first in enumerate(values):
            for second in values[i + 1:]:
                derived.add(round(second - first, 6))
                derived.add(round(first - second, 6))
                if first:
                    change = (second - first) / abs(first)
                    derived.add(round(change * 100, 6))
                    derived.add(round(change * 10_000, 6))  # basis points
        if values:
            derived.add(round(sum(values), 6))
            derived.add(round(sum(values) / len(values), 6))
            derived.add(float(len(values)))
    return direct | derived


def _supported(number: float, evidence: set[float]) -> bool:
    for known in evidence:
        if abs(number - known) <= ABSOLUTE_TOLERANCE:
            return True
        if known and abs(number - known) / abs(known) <= RELATIVE_TOLERANCE:
            return True
    return False


def _claims_in(text: str) -> list[tuple[str, float]]:
    claims: list[tuple[str, float]] = []
    for match in _NUMBER.finditer(text or ""):
        fragment = match.group(0).strip()
        if _NOT_A_MEASUREMENT.search(f" {fragment} "):
            continue
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        claims.append((fragment, value))
    return claims


# ---- the check ---------------------------------------------------------

def check(envelope: K.AnswerEnvelope, *, results: list[Any],
          catalog: Any = None, registry_facts: Any = None,
          executed: bool = True) -> tuple[K.AnswerEnvelope, Report]:
    """Validate the answer. Returns the envelope with optional parts dropped,
    and the report. Never edits prose."""
    report = Report()
    evidence = evidence_values(results) if executed else set()
    known_ids = {step.artifact_id
                 for packet in results
                 for step in getattr(packet, "steps", [])
                 if getattr(step, "artifact_id", "")}

    # ---- 1. evidence references must exist -------------------------
    unknown = [f for f in envelope.fact_ids if f not in known_ids]
    if unknown:
        report.dropped_references = unknown
        envelope.fact_ids = [f for f in envelope.fact_ids if f in known_ids]
        report.findings.append(Finding(
            INVALID_EVIDENCE_REFERENCE,
            f"{len(unknown)} evidence reference(s) name no result produced for "
            f"this request: {', '.join(unknown[:5])}. Cite only the results "
            f"that were executed.",
            fragment=", ".join(unknown[:5])))

    # ---- 2. every numeric claim must trace to a result --------------
    #
    # Only where something was executed. A theory answer explaining that a
    # 12-month PD covers twelve months has no evidence to trace to, and
    # demanding one would make the check absurd.
    if executed:
        prose = " ".join([envelope.narrative, *envelope.findings,
                          *envelope.hypotheses])
        unsupported: list[str] = []
        for fragment, value in _claims_in(prose):
            report.numbers_checked += 1
            if _supported(value, evidence):
                report.numbers_matched += 1
            else:
                unsupported.append(fragment)
        if unsupported:
            report.findings.append(Finding(
                UNSUPPORTED_NUMERIC_CLAIM,
                f"{len(unsupported)} figure(s) in the answer do not appear in "
                f"the executed results, or in a difference or percentage "
                f"change between them: {', '.join(unsupported[:8])}. Every "
                f"Cockpit figure must come from a result this request "
                f"produced.",
                fragment=", ".join(unsupported[:8])))

    # ---- 3. no other module's data may appear -----------------------
    leaked = _domain_leaks(envelope)
    if leaked:
        report.findings.append(Finding(
            DOMAIN_LEAK,
            f"The answer reports values attributed to {', '.join(leaked)}, "
            f"which the Cockpit cannot read. Report only what the "
            f"{DOMAIN} domain holds.",
            fragment=", ".join(leaked)))

    # ---- 4. product claims must be grounded -------------------------
    if registry_facts is not None:
        ungrounded = _ungrounded_product_claims(envelope, registry_facts)
        if ungrounded:
            report.findings.append(Finding(
                INVALID_PRODUCT_CLAIM,
                "The answer states something about CreditProbe that the "
                "configured product registry does not record: "
                + "; ".join(ungrounded[:4])
                + ". Say instead that it cannot be verified from the "
                  "configured product information.",
                fragment="; ".join(ungrounded[:4])))

    # ---- 5. the envelope must be well formed ------------------------
    contract = _contract_problems(envelope)
    if contract:
        report.findings.append(Finding(
            RESPONSE_CONTRACT_INVALID, "; ".join(contract)))

    # ---- 6. charts: validate, and DROP the ones that fail -----------
    kept_charts = []
    for chart in envelope.charts:
        problem = _chart_problem(chart, evidence, known_ids, executed=executed)
        if problem:
            report.dropped_charts.append(f"{chart.title}: {problem}")
            report.findings.append(Finding(
                INVALID_CHART, f"chart {chart.title!r} was dropped: {problem}",
                fragment=chart.title))
            continue
        kept_charts.append(chart)
    envelope.charts = kept_charts

    # ---- 7. suggestions: validate, and DROP the ones that fail ------
    if catalog is not None and envelope.suggested_questions:
        kept: list[str] = []
        for question in envelope.suggested_questions:
            problem = _suggestion_problem(question, catalog)
            if problem:
                report.dropped_suggestions.append(f"{question}: {problem}")
                continue
            kept.append(question)
        envelope.suggested_questions = kept

    return envelope, report


def _domain_leaks(envelope: K.AnswerEnvelope) -> list[str]:
    """Named claims about another module's data, in the prose.

    Deliberately narrow: it looks for a value attributed to another module,
    not for the module's NAME. "This is an Early Warning question" is a
    correct sentence for a referral to contain, and flagging it would make
    every referral a validation failure.
    """
    prose = " ".join([envelope.narrative, *envelope.findings]).lower()
    leaks: list[str] = []
    patterns = (
        ("Early Warning", r"(ews|early[- ]warning)\s+(score|alert|signal)\s+"
                          r"(?:of|is|was|rose|fell|increased|decreased)?\s*"
                          r"[-\d]"),
        ("Credit Scoring", r"(credit|application|behaviou?ral)\s+score\s+"
                           r"(?:of|is|was)?\s*[-\d]"),
        ("Scorecard Validation", r"\b(psi|csi|gini|ks statistic)\b\s*"
                                 r"(?:of|is|was|=)?\s*[-\d]"),
        ("What-if", r"(simulated|shocked|stressed|hypothetical)\s+"
                    r"\w+\s+(?:of|is|was)?\s*[-\d]"),
    )
    for module, pattern in patterns:
        if re.search(pattern, prose):
            leaks.append(module)
    return leaks


def _ungrounded_product_claims(envelope: K.AnswerEnvelope,
                               registry_facts: Any) -> list[str]:
    """Claims about CreditProbe's own behaviour that the registry does not
    record. Only sentences that assert a MECHANISM -- how something is
    calculated, decided or determined -- because those are the ones a reader
    would act on and the ones a model is most likely to invent."""
    documented = " ".join(str(part).lower() for part in registry_facts)
    problems: list[str] = []
    mechanism = re.compile(
        r"creditprobe\s+(?:calculates|computes|determines|decides|uses|"
        r"applies|derives|models)\s+[^.]{6,160}", re.I)
    for sentence in mechanism.findall(envelope.narrative or ""):
        subject = re.sub(r"^creditprobe\s+\w+\s+", "", sentence.strip(),
                         flags=re.I)
        head = " ".join(subject.split()[:3]).lower().strip(" ,.")
        if head and head not in documented:
            problems.append(sentence.strip()[:120])
    return problems


def _contract_problems(envelope: K.AnswerEnvelope) -> list[str]:
    problems: list[str] = []
    if not (envelope.narrative or "").strip():
        problems.append("the answer has no prose")
    if envelope.kind == "clarification" and not envelope.clarification_question:
        problems.append("a clarification asks nothing")
    if envelope.kind == "referral" and not envelope.referral:
        problems.append("a referral names no destination")
    for table in envelope.tables:
        rows = getattr(table, "rows", []) or []
        columns = getattr(table, "columns", []) or []
        if rows and columns and any(
                isinstance(r, (list, tuple)) and len(r) != len(columns)
                for r in rows):
            problems.append(f"table {getattr(table, 'title', '')!r} has rows "
                            f"that do not match its columns")
    return problems


def _chart_problem(chart: K.AnswerChart, evidence: set[float],
                   known_ids: set[str], *, executed: bool) -> str:
    if not chart.series:
        return "it has no series, so it draws nothing"
    # A citation is not required. The substantive link is the VALUES: a chart
    # whose numbers are all in the executed results is evidence-linked whether
    # or not it names an artifact, and demanding the citation as well would
    # drop correct charts for a bookkeeping reason.
    unknown = [f for f in chart.fact_ids if f not in known_ids]
    if unknown:
        return f"it cites {unknown[0]}, which is not a result of this request"
    if not executed:
        return ""
    for series in chart.series:
        for value in (series.get("values") or []):
            for number in _numbers_in(value):
                if not _supported(number, evidence):
                    return (f"the value {number:g} is not in the executed "
                            f"results")
    return ""


def _suggestion_problem(question: str, catalog: Any) -> str:
    """A suggested question must be answerable. Checks the field names and
    quarters it mentions against the actual catalogue."""
    lowered = f" {str(question).lower()} "
    known_fields = set(getattr(catalog, "field_names", lambda: set())())
    for token in re.findall(r"`([a-z0-9_]+)`", lowered):
        if known_fields and token not in known_fields:
            return f"it names {token}, which is not a field in this domain"
    quarters = set(getattr(catalog, "quarters", ()) or ())
    if quarters:
        for quarter in re.findall(r"\b((?:19|20)\d{2}Q[1-4])\b",
                                  str(question), re.I):
            if quarter.upper() not in quarters:
                return f"it names {quarter}, which is not a reporting quarter here"
    return ""


def rewrite_request(envelope: K.AnswerEnvelope, report: Report) -> str:
    """What Opus is told when its answer did not validate.

    The exact answer, the exact errors, the exact evidence. No proposed
    wording: the whole point is that CreditProbe does not write the sentence.
    """
    lines = [
        "Your answer did not pass evidence validation. It has NOT been "
        "edited and it has not been shown to the user.",
        "",
        "What failed:",
    ]
    for finding in report.findings:
        if finding.category in BLOCKING:
            lines.append(f"  - {finding.category}: {finding.detail}")
    lines += [
        "",
        "Rewrite the ANSWER ONLY. You cannot run SQL, you cannot run Python, "
        "you cannot open a new analysis round, and no counter resets. Work "
        "from the results you already have: state only what they support, and "
        "say plainly what they do not.",
        "",
        "This is the only rewrite available. If the next answer does not "
        "validate, only the supported parts will be shown, with a limitation "
        "saying so.",
    ]
    return "\n".join(lines)


__all__ = ["BLOCKING", "CATEGORIES", "DOMAIN_LEAK", "Finding",
           "INVALID_CHART", "INVALID_EVIDENCE_REFERENCE",
           "INVALID_PRODUCT_CLAIM", "RESPONSE_CONTRACT_INVALID", "Report",
           "UNSUPPORTED_NUMERIC_CLAIM", "VALID", "check", "evidence_values",
           "rewrite_request"]
