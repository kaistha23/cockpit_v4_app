"""
Optional memory maintenance. Never on the answer path.

Off by default. The first help response therefore needs no second model, no
second credential and no summary call -- which is the whole reason "Who are
you?" can complete in one generation.

When it is enabled, one summarization job is scheduled after older
unsummarized history passes a threshold, not after every question. It runs
after the answer is published, on its own quota, and it cannot reopen a
settled run or read new portfolio data.

The corruption rule
-------------------
V3 recovered a "corrupted" list field by rejoining single characters, which
turned a legitimate rating of "B" into evidence of damage. Recovery here
requires the stored schema version and provenance to establish the known
defect. Otherwise the field is QUARANTINED and the exact source turns are
used instead. A short string is not proof of anything.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from backend.cockpit_v4 import events as ev

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA_VERSION = "v4.1"

#: Completed exchanges beyond the recent window before a job is worth running.
DEFAULT_THRESHOLD = 8

#: The known V3 encoding defect: a scalar string stored as a list of its own
#: single characters. Recovery is permitted ONLY for a summary that declares
#: the schema version where that defect existed.
DEFECTIVE_SCHEMA_VERSIONS = frozenset({"v3.0", "v3.1"})


def maybe_schedule(store: Any, thread_id: str, *, cfg: Any) -> bool:
    """Schedule at most one job. Returns whether one was started."""
    if not getattr(cfg, "memory_enabled", False):
        return False
    if not getattr(cfg, "memory_model", ""):
        logger.info("V4 memory is enabled but COCKPIT_V4_MEMORY_MODEL is not "
                    "set; no summarization was attempted.")
        return False
    summary = store.get_summary(thread_id)
    covered = int((summary or {}).get("covered_through_ordinal") or 0)
    total = store.turn_count(thread_id)
    if total - covered < DEFAULT_THRESHOLD:
        return False
    thread = threading.Thread(
        target=_run_job, args=(store, thread_id, cfg, covered, total),
        daemon=True, name=f"v4-memory-{thread_id[:8]}")
    thread.start()
    return True


def _run_job(store: Any, thread_id: str, cfg: Any, covered: int,
             total: int) -> None:
    """One bounded summarization. Its failure changes nothing user-visible."""
    try:
        turns = store.recent_turns(thread_id, limit=total)
        body = summarize(turns[covered:], covered_through=total)
        store.put_summary(thread_id=thread_id, covered_through=total,
                          body=body, schema_version=SUMMARY_SCHEMA_VERSION)
    except Exception as exc:  # noqa: BLE001
        logger.info("V4 memory job for thread %s failed: %s", thread_id, exc)


def summarize(turns: list[dict[str, Any]], *,
              covered_through: int) -> dict[str, Any]:
    """A structured record of what happened, with turn ids as citations.

    Deliberately extractive rather than generative in this build: it cites
    turn ids and carries forward exact corrections. A summary that outranks a
    user's own correction is worse than no summary.
    """
    corrections: list[str] = []
    conclusions: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for turn in turns:
        answer = turn.get("answer") or {}
        turn_id = turn.get("turn_id", "")
        for item in answer.get("coverage") or []:
            if item.get("status") == "needs_clarification":
                unresolved.append(str(item.get("subquestion") or ""))
        if answer.get("disposition") in ("answer", "partial_answer"):
            conclusions.append({
                "turn_id": turn_id,
                "question": str(turn.get("question") or "")[:300],
                "disposition": answer.get("disposition"),
                "claims": [c.get("claim_id")
                           for c in (answer.get("numeric_claims") or [])]})
    return {"schema_version": SUMMARY_SCHEMA_VERSION,
            "covered_through_turn": covered_through,
            "source_turn_ids": [t.get("turn_id") for t in turns],
            "corrections": corrections, "conclusions": conclusions,
            "unresolved_questions": [u for u in unresolved if u]}


def validate_string_list(value: Any, *, field_name: str,
                         schema_version: str = "") -> list[str]:
    """Accept a list of strings. Never expand a scalar into characters.

    A scalar becomes a one-item list only where the field contract permits
    that normalization, and this function is called only for those fields.
    Strict reference fields (artifact ids, turn ids) are not routed here.
    """
    if isinstance(value, list):
        if all(isinstance(v, str) for v in value):
            if _looks_character_expanded(value) and \
                    schema_version in DEFECTIVE_SCHEMA_VERSIONS:
                # The known defect, established by the stored schema version
                # rather than guessed from length.
                return ["".join(value)]
            return list(value)
        raise ValueError(f"{field_name} must contain strings only.")
    if isinstance(value, str):
        return [value]
    raise ValueError(f"{field_name} must be an array of strings.")


def _looks_character_expanded(value: list[str]) -> bool:
    return len(value) > 3 and all(len(v) == 1 for v in value)


def quarantine(field_name: str, reason: str) -> dict[str, Any]:
    return {"field": field_name, "status": "quarantined", "reason": reason,
            "fallback": "the exact source turns were used instead."}


__all__ = ["DEFAULT_THRESHOLD", "DEFECTIVE_SCHEMA_VERSIONS",
           "SUMMARY_SCHEMA_VERSION", "maybe_schedule", "quarantine",
           "summarize", "validate_string_list"]
