"""
The machine-readable catalog, and the compact serialization Opus receives.
Specification sections 3.4, 4 and 7.4-D/E.

Two audiences, one source
-------------------------
`Catalog` is built once per dataset release from `fields.py`. It answers two
different questions from the same data:

* the SQL validator asks "does `cockpit_facility_quarter.pd_12_month` exist?"
  and, when it does not, "what DOES exist that is near it?" -- `resolve` and
  `alternatives`;
* the context builder asks "serialize the COMPLETE dictionary small enough to
  send" -- `compact`.

The second is the one section 7.4 is strict about. The complete compact
dictionary of ALL authorized fields must go to Opus; the ten-row preview and
the optional history are what shrink when the packet is too big, never the
schema. `compact` therefore drops empty keys, references the twenty-four
common keys ONCE rather than repeating them on ten relations, and describes
the two-hundred-cell macro pivot by its rule while still resolving every one
of its columns -- but it never omits a business field.

What the catalog is not
-----------------------
It is not a gateway. A field is here because this domain declares it. Nothing
in `fields.py` reaches Early Warning, Credit Scoring, Scorecard Validation,
What-if or Lenses data, and a source column added to an underlying table later
is not exposed by being there: the allowlist is this catalog.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

from backend.cockpit_agentic import CATALOG_VERSION, DOMAIN, UNTRUSTED_NOTE
from backend.cockpit_agentic import fields as F
from backend.cockpit_agentic.calendar import Calendar
from backend.cockpit_agentic.contracts import CatalogAlternative, CockpitFieldSpec

#: The convenience pivot's relation name. Queryable, and every one of its two
#: hundred columns resolves, but described by rule in the compact packet.
MACRO_PIVOT = "cockpit_macro_pivot"

QUERYABLE_RELATIONS: tuple[str, ...] = F.RELATIONS + (MACRO_PIVOT,)

#: What each relation holds, in one line, without naming its columns.
#:
#: This is the OUTLINE the stage-A gate packet carries. It is deliberately
#: subject matter rather than schema: enough for Opus to decide whether a
#: question belongs to the Cockpit at all, and nowhere near enough to write a
#: query from. The complete dictionary -- every column, type, unit, definition,
#: aggregation rule and join -- is stage B's `compact`, and it is only built
#: once the gate has said this really is a Cockpit data question.
SUBJECT_AREAS: dict[str, str] = {
    F.CALENDAR: "the reporting calendar: which quarters this release has and "
                "what each one means",
    F.FACILITY_QUARTER: "the credit book itself -- one row per facility "
                        "position per quarter, with exposure, staging, PD, "
                        "LGD, EAD, ECL, arrears and pricing",
    F.IFRS9_DETAIL: "the stored IFRS 9 term structure: per-horizon "
                    "parameters and results by scenario and model run",
    F.BORROWER_FINANCIAL: "borrower financial statements -- balance sheet, "
                          "income statement and cash flow, at BORROWER grain",
    F.RATING_RATIO: "borrower ratings and the financial ratios behind them, "
                    "at BORROWER grain",
    F.QUALITATIVE: "the qualitative assessment questionnaire answered per "
                   "borrower per quarter",
    F.COLLATERAL: "collateral assets and their valuations, at ASSET grain",
    F.COLLATERAL_ALLOCATION: "which collateral asset secures which facility "
                             "position, and for how much",
    F.COVENANT: "covenant obligations, their tests and their outcomes",
    F.MACRO_WINDOW: "macroeconomic history and forecasts around each "
                    "reporting quarter, by geography and scenario",
    MACRO_PIVOT: "the same macro data as one wide row per anchor quarter, "
                 "geography and scenario",
}


def _collateral_families() -> tuple[dict[str, Any], ...]:
    """The 108 collateral summary columns, by name, with nine shared meanings.

    Section 4.9 requires the actual column names to reach Opus, and they do --
    all 108 of them. What is NOT repeated 108 times is the prose: the meaning
    of `_allocated_net_value_rcy` is the same whichever of the twelve types it
    is measuring, so it is stated once and the twelve names are listed against
    it.
    """
    out: list[dict[str, Any]] = []
    for suffix, definition, dtype, unit in F.COLLATERAL_SUMMARY_SUFFIXES:
        out.append({
            "family": f"collateral_{suffix}",
            "pattern": f"<collateral_type>_{suffix}",
            "definition": definition,
            "type": dtype,
            "unit": unit,
            "aggregation": ("additive" if suffix.startswith("allocated")
                            else "not_additive"),
            "names": [f"{kind}_{suffix}" for kind in F.COLLATERAL_TYPES],
        })
    return tuple(out)


def _ratio_families() -> tuple[dict[str, Any], ...]:
    """The forty ratios and their two companion families.

    The canonical forty are defined once in `ratio_definitions` and listed here
    by name rather than defined twice. Their `_source_value` and `_status`
    companions share one meaning each.
    """
    return (
        {"family": "ratios",
         "pattern": "<ratio_name>",
         "definition": ("The transparently derived value of the ratio, on the "
                        "basis in ratio_period_basis. Each is defined by name "
                        "in the ratio_definitions block of this catalog."),
         "type": "float", "unit": "see ratio_definitions",
         "aggregation": "not_additive",
         "names": list(F.RATIO_NAMES)},
        {"family": "ratio_source_values",
         "pattern": "<ratio_name>_source_value",
         "definition": ("The value as SUPPLIED by the source for that ratio, "
                        "where the source supplies one. Kept separate from the "
                        "derived value so the two can be compared rather than "
                        "silently merged; a bank's own DSCR or liquidity ratio "
                        "is never overwritten by a generic recomputation."),
         "type": "float", "unit": "see ratio_definitions",
         "aggregation": "not_additive",
         "names": [f"{n}_source_value" for n in F.RATIO_NAMES]},
        {"family": "ratio_status",
         "pattern": "<ratio_name>_status",
         "definition": ("Whether that ratio is a source figure, derived, "
                        "unavailable because an input is missing, invalid "
                        "because its denominator is zero or negative, or "
                        "incomparable because the period bases do not match. "
                        "Division by zero yields 'invalid_denominator', never "
                        "infinity or zero."),
         "type": "string",
         "enum": ["source", "derived", "unavailable", "invalid_denominator",
                  "period_basis_mismatch"],
         "aggregation": "enum",
         "names": [f"{n}_status" for n in F.RATIO_NAMES]},
    )


#: Generated column groups, listed by real name with one shared definition.
FAMILIES: dict[str, tuple[dict[str, Any], ...]] = {
    F.FACILITY_QUARTER: _collateral_families(),
    F.RATING_RATIO: _ratio_families(),
}


class UnknownField(LookupError):
    """A name that is not in the catalog. The validator's UNRESOLVED_FIELD."""


class UnknownRelation(LookupError):
    """A relation that is not in this domain. The validator's
    UNRESOLVED_RELATION, and the same message whether the relation belongs to
    another module or does not exist -- a refusal must not map the rest of the
    application."""


@dataclass(frozen=True)
class Catalog:
    """One release's authorized catalog."""

    dataset_release_id: str
    calendar: Calendar
    tenant_id: str = ""
    reporting_currency: str = "INR"
    amount_scale: str = "crore"
    version: str = CATALOG_VERSION

    # -- resolution ----------------------------------------------------

    @staticmethod
    def relations() -> tuple[str, ...]:
        return QUERYABLE_RELATIONS

    @staticmethod
    def require_relation(relation: str) -> str:
        name = str(relation or "").strip().lower()
        if name not in QUERYABLE_RELATIONS:
            raise UnknownRelation(
                f"{relation!r} is not a relation of the {DOMAIN} domain. The "
                f"readable relations are: {', '.join(QUERYABLE_RELATIONS)}.")
        return name

    @staticmethod
    def resolve(relation: str, column: str) -> CockpitFieldSpec:
        """The field, or UnknownField. The single name-checking choke point."""
        rel = Catalog.require_relation(relation)
        spec = F.find(rel, str(column or "").strip())
        if spec is None:
            raise UnknownField(
                f"Column {column!r} is not defined in {rel}.")
        return spec

    @staticmethod
    def columns(relation: str) -> tuple[str, ...]:
        return tuple(sorted(F.all_column_names(Catalog.require_relation(relation))))

    @staticmethod
    def alternatives(relation: str, column: str, *,
                     limit: int = 6) -> tuple[CatalogAlternative, ...]:
        """Fields that DO exist and are near the name that did not.

        Reported as facts. CreditProbe does not choose among them and does not
        rewrite the query -- section 7.6A. Opus reads these and authors the
        repair.

        Matching is deliberately generous about the ways a model mis-names a
        field: `pd_12_month` should surface `pd_pit_12m` and `pd_ttc_12m`, and
        a plain `close` string-similarity pass alone does not always find them.
        """
        rel = Catalog.require_relation(relation)
        wanted = str(column or "").strip().lower()
        names = sorted(F.all_column_names(rel))
        tokens = {t for t in wanted.replace("-", "_").split("_") if t}

        scored: list[tuple[float, str]] = []
        for name in names:
            low = name.lower()
            ratio = difflib.SequenceMatcher(None, wanted, low).ratio()
            name_tokens = set(low.split("_"))
            shared = tokens & name_tokens
            # A shared leading token ("pd", "ecl", "lgd") is worth more than a
            # shared unit fragment, and a substring hit in either direction is
            # a strong signal that the model meant this family.
            bonus = 0.22 * len(shared)
            if tokens and next(iter(sorted(tokens))) in name_tokens:
                bonus += 0.05
            if wanted and (wanted in low or low in wanted):
                bonus += 0.35
            # "12_month" vs "12m", "pct" vs "percent" and similar.
            squashed_want = wanted.replace("_", "")
            squashed_name = low.replace("_", "")
            if squashed_want and squashed_want in squashed_name:
                bonus += 0.25
            scored.append((ratio + bonus, name))

        scored.sort(key=lambda p: (-p[0], p[1]))
        out: list[CatalogAlternative] = []
        for score, name in scored:
            if score < 0.45 or len(out) >= limit:
                continue
            spec = F.find(rel, name)
            if spec is None:                       # pragma: no cover
                continue
            out.append(CatalogAlternative(field_name=spec.name,
                                          meaning=spec.definition,
                                          unit=spec.unit, dtype=spec.dtype))
        return tuple(out)

    @staticmethod
    def relation_alternatives(relation: str, *,
                              limit: int = 4) -> tuple[str, ...]:
        return tuple(difflib.get_close_matches(
            str(relation or "").lower(), QUERYABLE_RELATIONS, n=limit,
            cutoff=0.35)) or QUERYABLE_RELATIONS[:limit]

    # -- serialization for the model -----------------------------------

    def outline(self) -> dict[str, Any]:
        """The domain at a glance. Stage A's share of the dictionary.

        Section 7.4 requires the COMPLETE compact dictionary in the packet that
        plans an analysis. It does not require it in the packet that decides
        whether there is an analysis to plan. Deciding that a question is
        "who are you?" needs to know that this module is a quarterly IFRS 9
        credit book with ten subject areas over twenty quarters; it does not
        need seven hundred and fifty-one field definitions, forty ratio
        formulas or a join graph.

        So this returns SUBJECT MATTER and SIZE -- the domain's name, what each
        relation is about, how many columns it has, which quarters exist -- and
        no column names, types, units, definitions, aggregation rules, joins or
        enumerations. It is not a smaller dictionary; it is a different kind of
        statement, and it says so in the packet so that nothing downstream can
        mistake it for the dictionary and plan from it.
        """
        areas = []
        for rel in QUERYABLE_RELATIONS:
            grain = (F.GRAIN.get(rel, "") if rel != MACRO_PIVOT else
                     "reporting_quarter (anchor) x country_or_region x "
                     "scenario_id")
            # The headline grain only: everything after the "--" is the
            # detailed warning about how joining it goes wrong, and that
            # belongs with the dictionary that lets you write the join.
            areas.append({
                "relation": rel,
                "about": SUBJECT_AREAS.get(rel, ""),
                "grain": grain.split(" -- ")[0].strip(),
                "columns": (len(F.all_column_names(rel))
                            if rel != MACRO_PIVOT
                            else len(F.MACRO_PIVOT_FIELDS)
                            + len(F.COMMON_KEYS)),
            })
        summary = F.summary()
        return {
            "domain_id": DOMAIN,
            "domain_name": "Cockpit -- the quarterly IFRS 9 credit book for "
                           "this tenant",
            "catalog_version": self.version,
            "dataset_release_id": self.dataset_release_id,
            "reporting_currency": self.reporting_currency,
            "amount_scale": self.amount_scale,
            "subject_areas": areas,
            "declared_fields": summary["declared_fields"],
            "ratio_definitions": summary["ratios"],
            "qualitative_questions": summary["qualitative_questions"],
            "macro_factors": summary["macro_factors"],
            "reporting_quarters": list(self.calendar.slots),
            "populated_quarters": list(self.calendar.populated),
            "missing_quarters": list(self.calendar.missing),
            "outline_note": (
                "THIS IS AN OUTLINE, NOT THE FIELD DICTIONARY. It names no "
                "column and defines no field. Use it to decide who owns this "
                "request. Do not write a query from it, do not assert that a "
                "field exists or does not exist, and do not tell the user what "
                "the data contains at column level: the complete dictionary is "
                "assembled only if this request turns out to be a Cockpit data "
                "analysis, and you will have it before you plan anything."),
        }

    def compact(self, *, include_pivot_columns: bool = False
                ) -> dict[str, Any]:
        """The COMPLETE authorized dictionary, small enough to send.

        `include_pivot_columns` lists all two hundred macro pivot columns
        instead of describing them by rule. It is off by default because the
        rule is exact and reversible -- factor id, underscore, one of twenty
        suffixes -- and spending two hundred entries on a mechanical expansion
        is exactly the bloat section 4.11 warns against. Every one of those
        columns still resolves through `resolve`.
        """
        relations: dict[str, Any] = {}
        for rel in F.RELATIONS:
            own = [s for s in F.BY_RELATION.get(rel, ())]
            families = FAMILIES.get(rel, ())
            claimed = {n for fam in families for n in fam["names"]}
            relations[rel] = {
                "grain": F.GRAIN[rel],
                "fields": [s.compact() for s in own if s.name not in claimed],
            }
            if families:
                # A generated family is listed by its REAL column names with
                # one shared definition, rather than repeating near-identical
                # prose once per column. Every name below resolves through
                # `resolve`; nothing is omitted and no placeholder is sent.
                relations[rel]["column_families"] = [dict(f) for f in families]

        pivot: dict[str, Any] = {
            "grain": ("reporting_quarter (anchor) x country_or_region x "
                      "scenario_id -- one row per anchor, geography and "
                      "scenario, with two hundred value cells"),
            "column_rule": {
                "pattern": "<factor_id>_<offset_suffix>",
                "factor_ids": list(F.MACRO_FACTOR_IDS),
                "offset_suffixes": list(F.MACRO_OFFSET_SUFFIXES),
                "meaning": ("lag4..lag1 are the four quarters before the "
                            "anchor, current is the anchor itself, and "
                            "lead1..lead15 are FORECASTS made at the anchor. "
                            "Ten factors x twenty offsets = two hundred value "
                            "cells per row. They are not two hundred factors "
                            "and they add no reporting quarter."),
                "example": "real_gdp_growth_yoy_lead4",
                "units": {fid: unit for fid, _m, unit in F.MACRO_FACTORS},
            },
        }
        if include_pivot_columns:
            pivot["fields"] = [s.compact() for s in F.MACRO_PIVOT_FIELDS]
        relations[MACRO_PIVOT] = pivot

        return {
            "domain_id": DOMAIN,
            "catalog_version": self.version,
            "dataset_release_id": self.dataset_release_id,
            "reporting_currency": self.reporting_currency,
            "amount_scale": self.amount_scale,
            "rcy_note": ("A column ending _rcy is stated in the reporting "
                         f"currency, {self.reporting_currency}, in "
                         f"{self.amount_scale}."),
            # Serialized ONCE and referenced, rather than repeated on every
            # relation. Section 7.4: shared definitions may be referenced
            # structurally inside the same payload.
            "common_keys": {
                "applies_to": list(F.RELATIONS),
                "note": ("Every relation below also carries these keys. "
                         "tenant_id and dataset_release_id are enforced by the "
                         "query principal: you do not need to filter on them, "
                         "and filtering on them cannot widen what you can "
                         "see."),
                "fields": [k.compact() for k in F.COMMON_KEYS],
            },
            "relations": relations,
            "joins": [dict(j) for j in F.JOINS],
            "enumerations": {
                "rating_scale": {
                    "grades_weakest_last": list(F.RATING_SCALE),
                    "rank": dict(F.RATING_RANK),
                    "note": ("Nineteen grades. rank 1 = AAA, rank 19 = C, and "
                             "a LARGER rank is a WEAKER grade, so an improving "
                             "rating is a falling rank. There is no CCC+, no "
                             "CCC- and no D; default_flag is a separate field "
                             "and grade C is not mechanically default."),
                },
                "qualitative_questions": [
                    {"id": qid, "field": field, "question": text}
                    for qid, field, text in F.QUALITATIVE_QUESTIONS],
                "qualitative_grades_weakest_first": list(F.QUALITATIVE_GRADES),
                "collateral_types": list(F.COLLATERAL_TYPES),
                "macro_factors": [{"id": fid, "meaning": meaning, "unit": unit}
                                  for fid, meaning, unit in F.MACRO_FACTORS],
                "macro_scenarios": list(F.MACRO_SCENARIOS),
            },
            "ratio_definitions": [
                {"n": n, "field": name, "definition": definition, "unit": unit}
                for n, name, definition, unit in F.RATIO_DEFINITIONS],
            "ratio_note": (
                "These forty definitions are DATA SEMANTICS: what a stored or "
                "derived number means, so you can tell whether two of them are "
                "comparable. They are not an analysis you are required to "
                "perform and not a template to imitate. Each ratio also has a "
                "<name>_source_value holding the source's own figure where it "
                "differs, and a <name>_status saying whether it is source, "
                "derived, unavailable or invalid. A zero or negative "
                "denominator yields 'invalid_denominator', never infinity or "
                "zero."),
            "calendar": self.calendar.compact(),
            "untrusted_data_note": UNTRUSTED_NOTE,
        }

    # -- reporting -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The full machine-readable catalog, for the docs and the tests."""
        return {
            "domain_id": DOMAIN,
            "catalog_version": self.version,
            "dataset_release_id": self.dataset_release_id,
            "reporting_currency": self.reporting_currency,
            "amount_scale": self.amount_scale,
            "calendar": self.calendar.to_dict(),
            "relations": {
                rel: {
                    "grain": F.GRAIN.get(rel, ""),
                    "columns": [
                        {"name": s.name, "type": s.dtype, "unit": s.unit,
                         "definition": s.definition,
                         "aggregation": s.aggregation,
                         "nullable": s.nullable,
                         "enumeration": list(s.enumeration),
                         "value_origin": s.value_origin,
                         "availability": s.availability,
                         "missing_reason": s.missing_reason,
                         "generated_from": s.generated_from}
                        for s in F.fields_of(rel)],
                } for rel in QUERYABLE_RELATIONS},
            "joins": [dict(j) for j in F.JOINS],
            "summary": F.summary(),
        }

    def sizes(self) -> dict[str, int]:
        """How big the compact serialization is, in characters and rough
        tokens. The context builder counts before it calls; this is where the
        number comes from."""
        import json

        blob = json.dumps(self.compact(), separators=(",", ":"))
        return {"characters": len(blob), "approx_tokens": len(blob) // 4}


def build(*, dataset_release_id: str, calendar: Calendar, tenant_id: str = "",
          reporting_currency: str = "INR",
          amount_scale: str = "crore") -> Catalog:
    return Catalog(dataset_release_id=dataset_release_id, calendar=calendar,
                   tenant_id=tenant_id, reporting_currency=reporting_currency,
                   amount_scale=amount_scale)


__all__ = ["Catalog", "MACRO_PIVOT", "QUERYABLE_RELATIONS", "SUBJECT_AREAS",
           "UnknownField", "UnknownRelation", "build"]
