"""
`read_artifact`: exact persisted values, checked against who is asking.

Not a calculator. This returns rows that were already produced and stored; a
new aggregate requires `execute_analysis`. That separation is what makes an
evidence reference checkable -- if a number can appear without an execution
that produced it, no amount of reference-checking proves anything.

An unauthorized reference returns a safe denial that does NOT confirm the
artifact exists. "You are not permitted to read art-1234" tells an attacker
that art-1234 is real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.cockpit_v4.contracts import ArtifactRequest

DEFAULT_ROWS = 100
DEFAULT_COLUMNS = 32


@dataclass
class ArtifactService:
    store: Any
    tenant_id: str
    release_id: str
    limits: Any

    def read(self, request: ArtifactRequest) -> dict[str, Any]:
        if request.artifact_kind == "thread_turn":
            return self._read_turn(request)
        return self._read_result(request)

    def _read_result(self, request: ArtifactRequest) -> dict[str, Any]:
        record = self.store.get_artifact(request.artifact_id,
                                         tenant_id=self.tenant_id)
        if record is None:
            return {"status": "not_available",
                    "message": ("No artifact with that reference is available "
                                "to you in this release.")}
        rows = record["rows"]
        columns = record["columns"]
        wanted = [c for c in request.columns if c in columns] or columns
        omitted_columns = [c for c in request.columns if c not in columns]

        limit = request.limit or min(self.limits.preview_rows, DEFAULT_ROWS)
        offset = request.offset
        if request.cursor:
            try:
                offset = max(offset, int(request.cursor))
            except ValueError:
                pass
        window = rows[offset:offset + limit]
        projected = [{k: row.get(k) for k in wanted[:DEFAULT_COLUMNS]}
                     for row in window]

        out: dict[str, Any] = {
            "status": "ok", "artifact_id": record["artifact_id"],
            "kind": record["kind"], "release_id": record["release_id"],
            "scope": record["scope"], "columns": wanted[:DEFAULT_COLUMNS],
            "total_rows": record["row_count"], "offset": offset,
            "returned_rows": len(projected), "rows": projected,
            "executed_code_digest": record["code_digest"],
            "created_at": record["created_at"],
        }
        if record["release_id"] and record["release_id"] != self.release_id:
            # Evidence from a different authorized scope is COMPARISON
            # evidence and is labelled as such. It never silently becomes the
            # current release's calculation.
            out["comparison_evidence"] = True
            out["comparison_note"] = (
                f"This artifact was produced under release "
                f"{record['release_id']}, not the pinned "
                f"{self.release_id}. Present it as a labelled historical "
                f"comparison, never as this release's result.")
        if offset + len(projected) < record["row_count"]:
            out["next_cursor"] = str(offset + len(projected))
            out["omitted_rows"] = record["row_count"] - (
                offset + len(projected))
        if len(wanted) > DEFAULT_COLUMNS:
            out["omitted_columns"] = wanted[DEFAULT_COLUMNS:]
            out["omitted_columns_note"] = (
                "These columns exist in the artifact and were not returned "
                "in this projection. Request them explicitly; do not "
                "conclude they are absent.")
        if omitted_columns:
            out["unknown_columns"] = omitted_columns
        return out

    def _read_turn(self, request: ArtifactRequest) -> dict[str, Any]:
        turn = self.store.get_turn(request.artifact_id,
                                   tenant_id=self.tenant_id)
        if turn is None:
            return {"status": "not_available",
                    "message": ("No completed turn with that reference is "
                                "available to you.")}
        return {"status": "ok", "kind": "thread_turn",
                "turn_id": turn["turn_id"], "ordinal": turn["ordinal"],
                "question": turn["question"], "answer": turn["answer"],
                "created_at": turn["created_at"],
                "note": ("This is the exact recorded turn, which outranks any "
                         "summary of it.")}


__all__ = ["ArtifactService", "DEFAULT_COLUMNS", "DEFAULT_ROWS"]
