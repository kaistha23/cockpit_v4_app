"""
`inspect_catalog`: exact authorized metadata, retrieved selectively.

The V3 behaviour this replaces
------------------------------
V3 attached the whole field dictionary -- 991 addressable relation.column
definitions, every ratio, the expanded macro pivot and a full missingness
table -- to every planning call, whatever the question was. "Who are you?"
paid for the entire corporate data domain.

V4 sends a compact INDEX in the starting context (relation names, one-line
grains, subject families, available quarters) and nothing else. When the
analyst needs a definition it asks for it, and this returns the COMPLETE
metadata for exactly what was asked: definition, unit, grain, aggregation
behaviour, valid joins, coverage and missingness. Selective does not mean
partial -- a field returned with half its definition is worse than one not
returned at all, because the analyst cannot tell it is missing something.

What this tool must never do: choose a field, infer what "exposure" means, or
answer with a plausible schema when the search found nothing. An empty search
is an empty search.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_agentic import catalog as v3_catalog
from backend.cockpit_agentic import fields as v3_fields
from backend.cockpit_v4.contracts import CatalogRequest, Rejection

#: The soft output target for one page. Larger requests paginate rather than
#: silently drop fields.
OUTPUT_TARGET_TOKENS = 4_000
MAX_FIELDS_PER_PAGE = 60
#: Columns a sample may show when none were named. A sample is about shape.
MAX_SAMPLE_COLUMNS = 12
#: Mirrors the contract's bound, so a sample cannot exceed it by another path.
MAX_SAMPLE_ROWS = 10


@dataclass
class MetadataReceipt:
    """Proof that a specific field's metadata was actually delivered.

    `execute_analysis` checks these: a query naming a field whose definition
    the analyst never read is not refused, but the mismatch is reported, so a
    field used on a guess is visible in the trace rather than invisible in the
    numbers.
    """

    receipt_id: str
    field_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    catalog_version: str

    def to_dict(self) -> dict[str, Any]:
        return {"receipt_id": self.receipt_id,
                "field_ids": list(self.field_ids),
                "relation_ids": list(self.relation_ids),
                "catalog_version": self.catalog_version}


@dataclass
class CatalogService:
    """Reads the pinned release's catalog. Never writes, never infers."""

    catalog: Any
    scope: Any
    coverage: Any = None
    session: Any = None
    receipts: dict[str, MetadataReceipt] = field(default_factory=dict)
    #: What this RUN already holds. The point of tracking it is not to refuse
    #: a repeat but to be able to SAY it is a repeat: a response that does not
    #: distinguish "here is what you asked for" from "you already had this"
    #: leaves the model to infer whether it made progress, and a model that
    #: cannot tell asks again.
    known_fields: set[str] = field(default_factory=set)
    known_relations: set[str] = field(default_factory=set)
    known_details: set[str] = field(default_factory=set)

    # -- helpers ---------------------------------------------------------

    @property
    def catalog_version(self) -> str:
        return str(getattr(self.catalog, "catalog_version", "")
                   or v3_catalog.__dict__.get("CATALOG_VERSION", "v3"))

    def _authorized(self, relation: str) -> bool:
        permits = getattr(self.scope, "permits", None)
        return bool(permits(relation)) if callable(permits) else True

    def _relations(self) -> tuple[str, ...]:
        names = tuple(self.catalog.relations())
        return tuple(n for n in names if self._authorized(n))

    #: Words that match almost every definition and so rank nothing.
    _STOP = frozenset({"the", "and", "for", "with", "per", "this", "that",
                       "from", "what", "which", "how", "are", "is", "by",
                       "of", "in", "on", "at", "to", "a", "an"})

    def _matching_fields(self, relations: Any, terms: list[str],
                         limit: int = 12) -> list[dict[str, Any]]:
        words = [t for t in terms if t not in self._STOP]
        if not words:
            return []
        scored = []
        for relation in relations:
            for column in self.catalog.columns(relation):
                try:
                    spec = (column if hasattr(column, "name")
                            else self.catalog.resolve(relation, column))
                except Exception:  # noqa: BLE001 - an unreadable field is not a match
                    continue
                haystack = " ".join((spec.name.replace("_", " "),
                                     str(getattr(spec, "label", "") or ""),
                                     str(getattr(spec, "definition", "") or ""))).lower()
                hits = sum(1 for w in words if w in haystack)
                if hits:
                    name_hit = any(w in spec.name.lower() for w in words)
                    scored.append((hits + (0.5 if name_hit else 0), relation, spec))
        scored.sort(key=lambda item: (-item[0], item[1], item[2].name))
        return [{"field_id": f"{relation}.{spec.name}",
                 "label": str(getattr(spec, "label", "") or ""),
                 "definition": str(getattr(spec, "definition", "") or "")[:160],
                 "unit": str(getattr(spec, "unit", "") or "")}
                for _, relation, spec in scored[:limit]]

    def _field_dict(self, spec: Any, relation: str) -> dict[str, Any]:
        """One field's COMPLETE metadata. Not a fragment of a definition."""
        coverage = self._field_coverage(relation, spec.name)
        out = {
            "field_id": f"{relation}.{spec.name}",
            "relation": relation,
            "column": spec.name,
            "label": getattr(spec, "label", ""),
            "definition": spec.definition,
            "dtype": spec.dtype,
            "unit": getattr(spec, "unit", ""),
            # A V4 field states its aggregation as a DERIVED property, from
            # its unit, rather than repeating it on every one of its eighty
            # column definitions. An empty string here would tell the analyst
            # "not stated" about a column the catalogue knows is additive.
            "aggregation": (getattr(spec, "aggregation", "")
                            or getattr(spec, "additive", "not_additive")),
            "nullable": bool(getattr(spec, "nullable", True)),
            "value_origin": getattr(spec, "value_origin", ""),
            "availability": getattr(spec, "availability", ""),
            "missing_reason": getattr(spec, "missing_reason", ""),
            "source_name": getattr(spec, "source_name", ""),
            "lineage": getattr(spec, "lineage", ""),
        }
        enumeration = tuple(getattr(spec, "enumeration", ()) or ())
        if enumeration:
            out["allowed_values"] = list(enumeration)
        if getattr(spec, "currency_scoped", False):
            out["currency"] = getattr(self.catalog, "reporting_currency", "")
            out["amount_scale"] = getattr(self.catalog, "amount_scale", "")
        if getattr(spec, "generated_from", ""):
            out["generated_from"] = spec.generated_from
        if coverage:
            out["coverage"] = coverage
        return out

    def _field_coverage(self, relation: str, column: str
                        ) -> dict[str, Any] | None:
        """Missingness from the measured profile, never from ten sample rows."""
        if self.coverage is None:
            return None
        lookup = getattr(self.coverage, "field", None)
        if callable(lookup):
            try:
                found = lookup(relation, column)
            except Exception:  # noqa: BLE001
                return None
            if found is None:
                return None
            if isinstance(found, dict):
                return found
            to_dict = getattr(found, "to_dict", None)
            return to_dict() if callable(to_dict) else None
        return None

    def _grain(self, relation: str) -> str:
        outline = getattr(self.catalog, "outline", None)
        if callable(outline):
            try:
                doc = outline()
            except Exception:  # noqa: BLE001
                return ""
            for entry in doc.get("relations", []) or []:
                if isinstance(entry, dict) and entry.get("name") == relation:
                    return str(entry.get("grain") or entry.get("purpose") or "")
        return ""

    # -- the tool --------------------------------------------------------

    def _expansion_refusal(self, request: CatalogRequest, detail: set[str],
                           relations: tuple[str, ...],
                           requested_relations: tuple[str, ...]
                           ) -> dict[str, Any] | None:
        """Refuse a request that would page through the whole catalogue.

        The live loop's fuel. A request for `fields` across eleven relations
        expanded to 991 field definitions, returned the first 60, and said
        931 remained -- so the model asked again, and again, and the deadline
        went on catalogue paging. A partial dump with an invitation to
        continue is worse than a refusal: it looks like progress.

        An explicit list of field ids is always served, however long, because
        that is a request someone actually made.
        """
        # Only field EXPANSION is refused. Asking for a relation's joins or
        # its coverage names the relation legitimately and returns a handful
        # of facts, not hundreds.
        if "fields" not in detail:
            return None
        if request.field_ids or not requested_relations:
            return None
        total = sum(len(self.catalog.columns(r)) for r in requested_relations)
        if total <= MAX_FIELDS_PER_PAGE:
            return None
        return {
            "status": "needs_scope",
            "catalog_version": self.catalog_version,
            "requested": {"relation_ids": list(requested_relations),
                          "detail": sorted(set(request.detail))},
            "returned": [],
            "coverage_complete_for_request": False,
            "reason": (
                f"Expanding {len(requested_relations)} relation(s) would be "
                f"{total} field definitions. Name the field ids you need in "
                f"field_ids, or narrow to one relation, or use "
                f"detail=['discovery'] with a query to find the canonical "
                f"ids. Nothing was returned and nothing was abbreviated."),
            "relation_sizes": {r: len(self.catalog.columns(r))
                               for r in requested_relations},
            "authorized_relations": list(relations),
        }

    @staticmethod
    def _underspecified(request: CatalogRequest, detail: set[str],
                        relations: tuple[str, ...]) -> dict[str, Any] | None:
        """A request with no scope is not a request for everything."""
        scoped = bool(request.relation_ids or request.field_ids
                      or request.query)
        if scoped or detail & {"coverage", "relationships", "samples"}:
            return None
        return {
            "status": "needs_scope",
            "requested": {"detail": sorted(detail)},
            "returned": [],
            "coverage_complete_for_request": False,
            "reason": ("Specify relation(s), field(s), or a query. An "
                       "unscoped request is not a request for the whole "
                       "catalogue, and none of it was returned."),
            "authorized_relations": list(relations),
            "next": ("detail=['discovery'] with a query finds canonical ids; "
                     "field_ids returns exact definitions."),
        }

    def inspect(self, request: CatalogRequest) -> dict[str, Any]:
        """Answer one catalog request. Every branch returns what is there."""
        detail = set(request.detail) or {"discovery"}
        out: dict[str, Any] = {
            "status": "ok",
            "catalog_version": self.catalog_version,
            "release_id": getattr(self.scope, "dataset_release_id", ""),
            "reporting_currency": getattr(self.catalog, "reporting_currency",
                                          ""),
            "amount_scale": getattr(self.catalog, "amount_scale", ""),
        }

        relations = self._relations()
        requested_relations = tuple(
            r for r in request.relation_ids if r in relations)

        refusal = (self._underspecified(request, detail, relations)
                   or self._expansion_refusal(request, detail, relations,
                                              requested_relations))
        if refusal is not None:
            refusal.setdefault("catalog_version", self.catalog_version)
            refusal["added_new_information"] = False
            return refusal
        unknown_relations = tuple(
            r for r in request.relation_ids if r not in relations)
        if unknown_relations:
            # Named, with the authorized alternatives -- and NO substitution.
            out["unknown_relations"] = {
                "requested": list(unknown_relations),
                "authorized_relations": list(relations),
                "note": ("These relations are not in the authorized domain. "
                         "Nothing was substituted for them."),
            }

        if "discovery" in detail:
            terms = [t.strip().lower()
                     for t in request.query.replace(",", " ").split()
                     if len(t.strip()) > 2]
            listed = []
            for relation in relations:
                grain = self._grain(relation)
                if terms and not any(
                        t in relation.lower() or t in grain.lower()
                        for t in terms):
                    continue
                listed.append({"relation": relation, "grain": grain,
                               "column_count": len(
                                   self.catalog.columns(relation))})
            # Fields too, not just relations: "days past due" or "sector" is
            # a column, and a search that only read relation names and grains
            # sent the analyst to guess column names instead -- the invented
            # columns that failed to bind on live runs. Ranked by how many
            # query words a field's name, label or definition contains; a
            # search result the analyst asked for, not a substitution.
            matching = self._matching_fields(relations, terms)
            if matching:
                out["matching_fields"] = matching
            if not listed and terms and not matching:
                # An empty search is an empty search. It is NOT an invitation
                # to return the whole catalog and let the model guess.
                out["discovery"] = []
                out["discovery_note"] = (
                    f"No authorized relation matched {request.query!r}. The "
                    f"authorized relations are listed under "
                    f"'authorized_relations'.")
                out["authorized_relations"] = list(relations)
            else:
                out["discovery"] = listed or [
                    {"relation": r, "grain": self._grain(r),
                     "column_count": len(self.catalog.columns(r))}
                    for r in relations]

        delivered_fields: list[str] = []
        if "fields" in detail or request.field_ids:
            wanted: list[tuple[str, str]] = []
            unresolved: list[str] = []
            for field_id in request.field_ids:
                relation, _, column = field_id.partition(".")
                if not column:
                    unresolved.append(field_id)
                    continue
                if relation not in relations:
                    unresolved.append(field_id)
                    continue
                try:
                    self.catalog.resolve(relation, column)
                except Exception:  # noqa: BLE001
                    unresolved.append(field_id)
                    continue
                wanted.append((relation, column))
            for relation in requested_relations:
                if not request.field_ids:
                    wanted.extend((relation, c)
                                  for c in self.catalog.columns(relation))

            start = 0
            if request.cursor:
                try:
                    start = max(0, int(request.cursor))
                except ValueError as exc:
                    raise Rejection(
                        "INVALID_MODEL_OUTPUT",
                        "cursor must be the offset returned by a previous "
                        "page.", field_path="cursor") from exc
            page = wanted[start:start + MAX_FIELDS_PER_PAGE]
            entries = []
            for relation, column in page:
                try:
                    spec = self.catalog.resolve(relation, column)
                except Exception:  # noqa: BLE001
                    unresolved.append(f"{relation}.{column}")
                    continue
                entries.append(self._field_dict(spec, relation))
                delivered_fields.append(f"{relation}.{column}")
            out["fields"] = entries
            if start + MAX_FIELDS_PER_PAGE < len(wanted):
                out["next_cursor"] = str(start + MAX_FIELDS_PER_PAGE)
                out["omitted"] = {
                    "remaining_fields": len(wanted) - (
                        start + MAX_FIELDS_PER_PAGE),
                    "note": ("This page is complete for the fields it lists. "
                             "Request the next page with the cursor; nothing "
                             "was abbreviated."),
                }
            if unresolved:
                out["unresolved_fields"] = {
                    "requested": unresolved,
                    "note": ("These field ids do not exist in the authorized "
                             "catalog. No similar field was substituted. Use "
                             "discovery to find the exact canonical id."),
                }
                for relation, column in (
                        (f.partition('.')[0], f.partition('.')[2])
                        for f in unresolved if '.' in f):
                    alternatives = getattr(self.catalog, "alternatives", None)
                    if callable(alternatives) and relation in relations:
                        try:
                            out.setdefault("alternatives", {})[
                                f"{relation}.{column}"] = list(
                                    alternatives(relation, column))[:8]
                        except Exception:  # noqa: BLE001
                            pass

        if "relationships" in detail:
            joins = getattr(self.catalog, "joins", None)
            out["relationships"] = (
                joins() if callable(joins)
                else _declared_joins(requested_relations or relations))

        if "coverage" in detail:
            from backend.cockpit_v4 import semantics as sem

            calendar = getattr(self.catalog, "calendar", None)
            noun = sem.period_noun(self.catalog)
            slots = list(getattr(calendar, "slots", ()) or ())
            out["coverage"] = {
                "reporting_frequency": sem.frequency(self.catalog),
                "period_noun": noun,
                # The neutral key is always present so a reader that does
                # not know which book it is holding still finds the periods;
                # the noun-specific key is what the analyst's own book calls
                # them, and only THIS book's noun appears.
                "reporting_periods": slots,
                f"reporting_{noun}s": slots,
                f"populated_{noun}s": list(
                    getattr(calendar, "populated", ()) or ()),
                f"missing_{noun}s": list(
                    getattr(calendar, "missing", ()) or ()),
                "note": (f"{len(slots)} calendar slots are not {len(slots)} "
                         f"observations for every row: a facility, an "
                         f"account or a borrower that originated part-way "
                         f"through the window legitimately has fewer "
                         f"{noun}s, and a coverage figure read as though it "
                         f"had all of them is wrong by exactly that gap."),
            }

        if "samples" in detail and request.sample_rows:
            out["samples"] = self._samples(
                requested_relations or tuple(
                    sorted({f.partition(".")[0] for f in request.field_ids
                            if f.partition(".")[0] in relations})),
                request.sample_rows,
                columns=tuple(request.field_ids))

        receipt = MetadataReceipt(
            receipt_id=f"mr-{uuid.uuid4().hex[:12]}",
            field_ids=tuple(delivered_fields),
            relation_ids=tuple(requested_relations or ()),
            catalog_version=self.catalog_version)
        self.receipts[receipt.receipt_id] = receipt
        out["metadata_receipt_id"] = receipt.receipt_id
        out.update(self._account(request, detail, out, delivered_fields,
                                 requested_relations))
        return out

    def _account(self, request: CatalogRequest, detail: set[str],
                 out: dict[str, Any], delivered_fields: list[str],
                 requested_relations: tuple[str, ...]) -> dict[str, Any]:
        """Say what was asked for, what came back, and what is still missing.

        Monotonic and self-describing, so the analyst never has to infer
        whether its request succeeded. `already_known` is the important one:
        without it, a second identical call looks exactly like the first.
        """
        asked_fields = [f for f in request.field_ids]
        unresolved = set(
            (out.get("unresolved_fields") or {}).get("requested", ()))
        returned = list(delivered_fields)
        # What the RUN already had before this call. A field can be both
        # returned and already known -- that is exactly the case worth
        # naming, because otherwise a second identical call looks like the
        # first one.
        already = sorted(set(asked_fields) & self.known_fields)
        missing = sorted(unresolved)

        new_fields = set(returned) - self.known_fields
        new_relations = set(requested_relations) - self.known_relations
        new_details = (detail & {"relationships", "coverage", "samples"}
                       ) - self.known_details
        added = bool(new_fields or new_relations or new_details
                     or (detail & {"discovery"} and "discovery"
                         not in self.known_details))

        self.known_fields |= set(returned)
        self.known_relations |= set(requested_relations)
        self.known_details |= (detail & {"discovery", "relationships",
                                         "coverage", "samples"})

        complete = not missing and not out.get("next_cursor")
        account: dict[str, Any] = {
            "requested": {
                "detail": sorted(detail),
                "relation_ids": list(request.relation_ids),
                "field_ids": asked_fields,
                **({"query": request.query} if request.query else {}),
                **({"sample_rows": request.sample_rows}
                   if request.sample_rows else {}),
            },
            "returned": {
                "field_ids": returned,
                "relations": list(requested_relations),
                "detail": sorted(
                    d for d in detail
                    if d in out or d == "fields" and returned),
            },
            "already_known": already,
            "still_missing": missing,
            "coverage_complete_for_request": complete,
            "added_new_information": added,
            "known_so_far": {
                "field_count": len(self.known_fields),
                "relations": sorted(self.known_relations),
            },
        }
        if not added:
            account["no_new_information"] = (
                "No new catalogue information was added. The requested "
                "fields are already available in this conversation. Continue "
                "with the analysis, or request a different, specific "
                "metadata item.")
        return account

    def _samples(self, relations: tuple[str, ...], rows: int, *,
                 columns: tuple[str, ...] = ()) -> dict[str, Any]:
        """A few masked rows of the requested columns. Never a table.

        Narrow on purpose: `SELECT *` on the widest relation is 198 columns
        of borrower data to answer a question about shape. When field ids were
        named, only those columns are read; otherwise a small bounded slice of
        the relation's own columns, so the response cannot become a dump by
        another route.
        """
        if not relations:
            return {"status": "needs_scope",
                    "reason": ("Name the relation or the field ids to sample. "
                               "No sample was read.")}
        if self.session is None:
            return {"status": "unavailable",
                    "reason": ("No execution session is open, so no sample "
                               "can be read. This is not an empty table.")}
        from backend.cockpit_v4 import sql as v4_sql

        out: dict[str, Any] = {"note": (
            "Masked sample rows of the requested columns only. These are "
            "examples of SHAPE, never a population profile: do not read a "
            "total, a rate or a distribution off them.")}
        rows = max(1, min(int(rows), MAX_SAMPLE_ROWS))
        for relation in relations[:2]:
            available = list(self.catalog.columns(relation))
            wanted = [c.partition(".")[2] for c in columns
                      if c.partition(".")[0] == relation
                      and c.partition(".")[2] in available]
            selected = wanted[:MAX_SAMPLE_COLUMNS] or \
                available[:MAX_SAMPLE_COLUMNS]
            projection = ", ".join(f'"{c}"' for c in selected)
            try:
                result = v4_sql.execute(
                    f"SELECT {projection} FROM {relation} LIMIT {rows}",
                    self.session, deadline_seconds=5.0, max_rows=rows)
                out[relation] = {
                    "purpose": "column shape and example values",
                    "scope": {"relation": relation,
                              "columns": selected,
                              "row_limit": rows,
                              "columns_requested": bool(wanted)},
                    "columns": [c["name"] for c in result.columns],
                    "rows": result.rows[:rows]}
            except Exception as exc:  # noqa: BLE001
                out[relation] = {"status": "unavailable",
                                 "reason": str(exc)[:200]}
        return out

    def receipt_fields(self, receipt_ids: tuple[str, ...]) -> set[str]:
        found: set[str] = set()
        for receipt_id in receipt_ids:
            receipt = self.receipts.get(receipt_id)
            if receipt is not None:
                found.update(receipt.field_ids)
        return found


def _declared_joins(relations: tuple[str, ...]) -> list[dict[str, Any]]:
    """The joins the domain declares, with the grain warning each carries.

    These are the repetition traps section 12 names. Stating them is not the
    same as enforcing them -- `execute_tool` does the static check -- but an
    analyst that has read them writes fewer of them.
    """
    declared = [
        {"left": v3_fields.BORROWER_FINANCIAL,
         "right": "cockpit_facility_quarter",
         "on": ["borrower_id", "reporting_quarter"],
         "cardinality": "one borrower row to many facility rows",
         "warning": ("A borrower's balance sheet repeats once per facility "
                     "after this join. Aggregate the facility side first, or "
                     "de-duplicate on borrower_id, before summing anything "
                     "from the borrower side.")},
        {"left": v3_fields.COLLATERAL,
         "right": v3_fields.COLLATERAL_ALLOCATION,
         "on": ["collateral_id", "reporting_quarter"],
         "cardinality": "one asset to many facility allocations",
         "warning": ("A shared asset appears once per allocation. Sum the "
                     "ALLOCATED value, never the whole-asset value, across "
                     "facilities.")},
        {"left": "cockpit_facility_quarter", "right": v3_fields.IFRS9_DETAIL,
         "on": ["facility_id", "reporting_quarter"],
         "cardinality": "one facility to many scenario/horizon rows",
         "warning": ("Stored scenario and horizon detail multiplies the "
                     "facility row. Booked ECL is not the sum of scenario "
                     "ECL.")},
        {"left": "cockpit_facility_quarter", "right": v3_fields.COVENANT,
         "on": ["facility_id", "reporting_quarter"],
         "cardinality": "one facility to many covenant tests",
         "warning": ("A borrower-scope covenant binds once, not once per "
                     "facility. Untested is not compliant.")},
    ]
    if not relations:
        return declared
    wanted = set(relations)
    return [j for j in declared
            if j["left"] in wanted or j["right"] in wanted] or declared


__all__ = ["CatalogService", "MAX_FIELDS_PER_PAGE", "MetadataReceipt",
           "OUTPUT_TARGET_TOKENS"]
