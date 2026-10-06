"""
Field-level missingness, from the full authorized data. Specification section 5.

Not from ten preview rows
-------------------------
Section 5 is explicit that profiles are built from the actual authorized data,
and section 7.4-F that they come from here rather than from Sonnet or from the
sample rows. Ten rows cannot tell you that a field is 40% null, and a preview
that happens to show ten populated rows of a mostly-empty column is worse than
no preview: it invites a confident analysis of almost nothing.

Two denominators, kept apart
----------------------------
The overall missing fraction and the fraction missing among APPLICABLE rows are
different numbers whenever `not_applicable` occurs, and reporting only the
first makes a field look broken when it is merely inapplicable. Both are
published, and `compact` includes the second only when it differs.

A global rate must not conceal a quarter
----------------------------------------
A field 5% missing overall may be 100% missing in the quarter the user selected.
`by_quarter` carries the per-quarter rate and `compact` surfaces any quarter
that is entirely empty, because that is the case that silently ruins an answer.

Computed once per release, not per query.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.cockpit_agentic import CATALOG_VERSION
from backend.cockpit_agentic import fields as F
from backend.cockpit_agentic.contracts import DataCoverageProfile, FieldProfile
from backend.cockpit_agentic.generate import Release

#: Below this many rows a per-quarter rate is suppressed rather than published,
#: so a small cohort cannot be characterised from the profile alone.
SMALL_GROUP_FLOOR = 5


def _missing_mask(frame: pd.DataFrame, column: str) -> pd.Series:
    series = frame[column]
    mask = series.isna()
    if series.dtype == object:
        mask = mask | series.astype("string").fillna("").str.strip().eq("")
    return mask


def profile_release(release: Release) -> DataCoverageProfile:
    """Every queryable field's coverage, measured over the whole release."""
    profiles: dict[str, FieldProfile] = {}
    row_counts: dict[str, int] = {}

    for relation, frame in release.frames.items():
        row_counts[relation] = int(len(frame))
        if frame.empty:
            continue
        has_quarter = "reporting_quarter" in frame.columns
        applicable_col = ("missing_reason" if "missing_reason" in frame.columns
                          else None)
        not_applicable = (
            frame[applicable_col].fillna("").eq("not_applicable")
            if applicable_col else pd.Series(False, index=frame.index))
        withheld = (frame[applicable_col].fillna("").eq("withheld")
                    if applicable_col else pd.Series(False, index=frame.index))

        for column in frame.columns:
            missing = _missing_mask(frame, column)
            total = int(len(frame))
            na_count = int((missing & not_applicable).sum())
            wh_count = int((missing & withheld).sum())
            missing_count = int(missing.sum())
            observed = total - missing_count
            applicable = total - na_count
            by_quarter: dict[str, float] = {}
            if has_quarter:
                grouped = frame.assign(_m=missing).groupby("reporting_quarter")
                for quarter, group in grouped:
                    if len(group) < SMALL_GROUP_FLOOR:
                        continue
                    by_quarter[str(quarter)] = round(
                        float(group["_m"].mean()), 6)

            earliest = latest = ""
            if "source_period_end" in frame.columns:
                periods = pd.to_datetime(frame["source_period_end"],
                                         errors="coerce").dropna()
                if len(periods):
                    earliest = str(periods.min().date())
                    latest = str(periods.max().date())
            carried = 0.0
            if "value_origin" in frame.columns:
                carried = float(
                    frame["value_origin"].fillna("").eq("carried_forward").mean())

            profiles[f"{relation}.{column}"] = FieldProfile(
                field_name=column, relation=relation, total_rows=total,
                observed=observed, null_count=missing_count,
                invalid_count=0, not_applicable=na_count, withheld=wh_count,
                missing_rate_overall=round(missing_count / total, 6),
                missing_rate_among_applicable=round(
                    (missing_count - na_count) / applicable, 6)
                if applicable else 0.0,
                by_quarter=by_quarter,
                earliest_source_period=earliest, latest_source_period=latest,
                stale_carried_forward_rate=round(carried, 6))

    # Business-field profiles for the tall representations, keyed by metric and
    # question id rather than by raw column -- section 5 requires both views.
    qualitative = release.frames.get(F.QUALITATIVE)
    if qualitative is not None and not qualitative.empty:
        for question_id, group in qualitative.groupby("question_id"):
            missing = _missing_mask(group, "answer_value")
            profiles[f"{F.QUALITATIVE}.answer_value[{question_id}]"] = FieldProfile(
                field_name=f"answer_value where question_id={question_id}",
                relation=F.QUALITATIVE, total_rows=int(len(group)),
                observed=int((~missing).sum()), null_count=int(missing.sum()),
                missing_rate_overall=round(float(missing.mean()), 6),
                missing_rate_among_applicable=round(float(missing.mean()), 6),
                by_quarter={str(q): round(float(g["answer_value"].isna().mean()), 6)
                            for q, g in group.groupby("reporting_quarter")
                            if len(g) >= SMALL_GROUP_FLOOR},
                stale_carried_forward_rate=round(float(
                    group["answer_status"].eq("carried_forward").mean()), 6))

    return DataCoverageProfile(
        dataset_release_id=release.dataset_release_id,
        catalog_version=CATALOG_VERSION,
        computed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        reporting_quarters=list(release.calendar.slots),
        populated_quarters=list(release.calendar.populated),
        missing_quarters=list(release.calendar.missing),
        fields=profiles, row_counts=row_counts)


def compact(profile: DataCoverageProfile, *, threshold: float = 0.0,
            limit: int = 0) -> dict[str, Any]:
    """What goes into the context packet.

    Fields with no missingness at all are summarised as a count rather than
    listed: the useful signal is which fields ARE incomplete, and listing eight
    hundred zeroes buries it. Any field that is entirely empty in some quarter
    is always listed, whatever its overall rate.
    """
    listed: dict[str, list[dict[str, Any]]] = {}
    complete = 0
    for key, field_profile in profile.fields.items():
        relation = field_profile.relation
        entry = field_profile.compact()
        interesting = (field_profile.missing_rate_overall > threshold
                       or "quarters_fully_missing" in entry
                       or field_profile.stale_carried_forward_rate > 0)
        if not interesting:
            complete += 1
            continue
        listed.setdefault(relation, []).append(entry)

    truncated = 0
    if limit:
        # Bounded for the context packet. What is kept is chosen by how much it
        # could mislead: a field entirely absent from some quarter first,
        # then the largest gaps. What is dropped is COUNTED and said, never
        # silently omitted -- section 7.4 permits reducing optional detail, not
        # hiding that it was reduced.
        for relation, entries in listed.items():
            entries.sort(key=lambda e: (0 if "quarters_fully_missing" in e
                                        else 1, -e["miss"]))
        flat = [(relation, entry)
                for relation, entries in listed.items() for entry in entries]
        flat.sort(key=lambda pair: (0 if "quarters_fully_missing" in pair[1]
                                    else 1, -pair[1]["miss"]))
        kept = flat[:limit]
        truncated = len(flat) - len(kept)
        listed = {}
        for relation, entry in kept:
            listed.setdefault(relation, []).append(entry)

    return {
        "dataset_release_id": profile.dataset_release_id,
        "computed_from": "the full authorized release, not the sample rows",
        "computed_at": profile.computed_at,
        "row_counts": dict(profile.row_counts),
        "populated_quarters": list(profile.populated_quarters),
        "missing_quarters": list(profile.missing_quarters),
        "fields_with_full_coverage": complete,
        "fields_with_gaps": {relation: entries
                             for relation, entries in sorted(listed.items())},
        "further_fields_with_gaps_not_listed": truncated,
        "truncation_note": (
            "" if not truncated else
            f"{truncated} further fields have some missingness and are not "
            f"listed here. Those listed are the ones that could most mislead: "
            f"every field entirely absent from some quarter, then the largest "
            f"overall gaps. Ask for a specific field's coverage if you need "
            f"it."),
        "denominator_note": (
            "'miss' is the fraction of ALL rows where the field is empty. "
            "'miss_applicable' appears only when it differs, and excludes rows "
            "whose missing_reason is not_applicable. "
            "'quarters_fully_missing' lists reporting quarters where the field "
            "is empty in every row -- a low overall rate does not mean the "
            "quarter you selected has data."),
        "small_group_note": (
            f"Per-quarter rates are suppressed for groups smaller than "
            f"{SMALL_GROUP_FLOOR} rows."),
    }


def outline(profile: DataCoverageProfile) -> dict[str, Any]:
    """Coverage at a glance. Stage A's share of section F.

    Row counts, which quarters are populated, and HOW MANY fields have gaps --
    per relation, as counts. Not the field-by-field missingness table, which is
    six thousand tokens and is only useful to someone about to choose columns.

    The counts are honest about their own limits: a gate decision must not be
    "the Cockpit cannot answer this because coverage is poor". Coverage decides
    how an analysis is qualified, not who owns the question, and the note says
    so.
    """
    with_gaps: dict[str, int] = {}
    fully_missing: dict[str, int] = {}
    complete = 0
    for field_profile in profile.fields.values():
        relation = field_profile.relation
        entry = field_profile.compact()
        if (field_profile.missing_rate_overall > 0
                or "quarters_fully_missing" in entry
                or field_profile.stale_carried_forward_rate > 0):
            with_gaps[relation] = with_gaps.get(relation, 0) + 1
            if "quarters_fully_missing" in entry:
                fully_missing[relation] = fully_missing.get(relation, 0) + 1
        else:
            complete += 1
    return {
        "dataset_release_id": profile.dataset_release_id,
        "computed_from": "the full authorized release, not the sample rows",
        "computed_at": profile.computed_at,
        "row_counts": dict(profile.row_counts),
        "populated_quarters": list(profile.populated_quarters),
        "missing_quarters": list(profile.missing_quarters),
        "fields_with_full_coverage": complete,
        "fields_with_gaps_by_relation": dict(sorted(with_gaps.items())),
        "fields_absent_from_some_quarter_by_relation":
            dict(sorted(fully_missing.items())),
        "outline_note": (
            "Counts only. The field-by-field missing rates are not in this "
            "packet and no rate here may be quoted to the user. Coverage "
            "qualifies an analysis; it does not decide who owns the question, "
            "so do not refuse a request as unanswerable on the strength of "
            "these counts."),
    }


__all__ = ["SMALL_GROUP_FLOOR", "compact", "outline", "profile_release"]
