"""
One domain's authorized catalogue, and the SQL session that can read it.

Two things live here because they are one argument
--------------------------------------------------
The catalogue says what a domain contains. The session materialises exactly
that and then shuts the door. Splitting them across modules would let the two
lists drift, and the whole isolation claim is that they cannot.

Why not V3's
------------
V3's catalogue is a module of constants reached through static methods, bound
to the corporate book, and its session builder writes
`domain_id = 'corporate_cockpit'` into every predicate. Both are correct for
one book. Neither can describe a second one, and teaching them to would mean
editing V3.

The isolation argument, in order
--------------------------------
1. A Catalog is constructed FOR a domain and holds that domain's relations.
   `require_relation` refuses anything else by name -- including, explicitly,
   a relation that exists in the other domain, which is refused with a
   sentence saying so rather than an unhelpful "unknown".
2. The session materialises only the catalogue's relations, filtered on
   tenant, release AND domain, while file access still works.
3. File access is then disabled and the configuration locked, BEFORE any
   model-authored SQL is admitted. From there the connection can reach
   nothing but those tables.

The order is the security argument. A check that runs after the door is open
is a check that can be skipped.
"""

from __future__ import annotations

import difflib
import threading
import time
from dataclasses import dataclass
from typing import Any

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import schema as schema_mod

MAX_CACHED_SESSIONS = 4

#: How each book's relations join. Stated, because a model guessing a join
#: key across a six-hundred-thousand-row retail book produces a cross product
#: and a timeout rather than an error anyone can read.
JOINS: dict[str, list[dict[str, Any]]] = {
    dom.CORPORATE: [
        {"left": "corp_facility_quarter", "right": "corp_borrower_quarter",
         "on": ["borrower_id", "reporting_quarter"],
         "cardinality": "many facility rows to one borrower row",
         "note": "Every facility belongs to a borrower in the same quarter.",
         "warning": ("A borrower's own figures -- revenue, EBITDA, debt, "
                     "leverage -- repeat once per facility after this join. "
                     "Aggregate the facility side first, or de-duplicate on "
                     "borrower_id, before summing anything from the borrower "
                     "side.")},
        {"left": "corp_collateral_quarter", "right": "corp_facility_quarter",
         "on": ["facility_id", "reporting_quarter"],
         "cardinality": "many collateral items to one facility",
         "note": "Security is held against a facility.",
         "warning": ("A facility with three pledged assets appears three "
                     "times. Summing ead_sar_mn across this join counts the "
                     "same exposure once per asset. Sum the ALLOCATED "
                     "collateral value against a de-duplicated facility "
                     "total.")},
        {"left": "corp_covenant_quarter", "right": "corp_facility_quarter",
         "on": ["facility_id", "reporting_quarter"],
         "cardinality": "many covenant tests to one facility",
         "note": "A covenant tests a facility.",
         "warning": ("A facility with four covenant tests appears four "
                     "times. Untested is not compliant: a facility with no "
                     "covenant row is absent from an inner join rather than "
                     "counted as passing.")},
    ],
    dom.RETAIL: [
        {"left": "retail_account_month", "right": "retail_customer_month",
         "on": ["customer_id", "reporting_month"],
         "cardinality": "many accounts to one customer",
         "note": "Every account belongs to a customer in the same month.",
         "warning": ("retail_customer_month already carries "
                     "total_ead_sar_mn and total_ecl_sar_mn summed across "
                     "that customer's accounts. Joining and summing them "
                     "again counts each customer once per account held.")},
        {"left": "retail_behaviour_month", "right": "retail_account_month",
         "on": ["account_id", "reporting_month"],
         "cardinality": "one behaviour row to one account",
         "note": "Behavioural variables describe an account.",
         "warning": ("One row each way, so this join repeats nothing. It is "
                     "stated because the two relations share account_id and "
                     "reporting_month and joining on account_id alone would "
                     "cross every month with every other.")},
        {"left": "retail_collateral_month", "right": "retail_account_month",
         "on": ["account_id", "reporting_month"],
         "cardinality": "one collateral row to one SECURED account",
         "note": "Secured accounts only; unsecured accounts have no row.",
         "warning": ("An inner join here silently drops every unsecured "
                     "account, so a portfolio total computed across it is a "
                     "secured-only total. Use a left join, or say that the "
                     "figure is secured lending.")},
    ],
}


class CrossDomainAccess(PermissionError):
    """A query reaching for the other book. Refused, and named as such."""


@dataclass(frozen=True)
class Calendar:
    """The release's own periods, and what kind of period they are.

    `frequency` is REQUIRED to come from the release. It defaulted to one
    module constant for both books, so a quarterly Corporate catalogue
    described itself as monthly to every consumer that asked -- the analyst
    instruction, the tool schemas, the attention feed and the export footer
    among them.
    """

    slots: tuple[str, ...]
    frequency: str

    @property
    def latest(self) -> str:
        return self.slots[-1] if self.slots else ""

    @property
    def previous(self) -> str:
        return self.slots[-2] if len(self.slots) > 1 else ""

    @property
    def year_ago(self) -> str:
        """The same period one year earlier, whatever a year is here."""
        step = 4 if self.frequency == "quarterly" else 12
        return (self.slots[-(step + 1)]
                if len(self.slots) > step else "")

    @property
    def periods_per_year(self) -> int:
        return 4 if self.frequency == "quarterly" else 12

    def last(self, count: int) -> tuple[str, ...]:
        return self.slots[-count:] if count > 0 else ()

    # -- the shape catalogue consumers already read ----------------------
    #
    # A V4 release publishes only completed months, so every slot is
    # populated and none is missing. These exist because the tools that read
    # a calendar ask for them by name, and answering "all of them" honestly
    # is better than each consumer discovering the attribute is absent.

    @property
    def populated(self) -> tuple[str, ...]:
        return self.slots

    @property
    def missing(self) -> tuple[str, ...]:
        return ()


@dataclass(frozen=True)
class Catalog:
    """What one domain's release contains, and nothing another one does."""

    domain_id: str
    dataset_release_id: str
    release_fingerprint: str
    calendar: Calendar
    tenant_id: str = lake.DEFAULT_TENANT
    reporting_currency: str = lake.CURRENCY
    amount_scale: str = lake.AMOUNT_SCALE
    catalog_version: str = "v4.1"
    #: THE RELEASE'S OWN SHAPE, read from its manifest by `build`.
    #:
    #: A published release records its full relation and field list, so it
    #: describes itself and needs no help from the code that reads it. This
    #: used to be answered by `schema_mod` keyed on the DOMAIN, which meant
    #: today's schema was imposed on whatever release was opened: the
    #: session builder selected today's columns from an older parquet and
    #: DuckDB refused to bind them, so a book published before a column was
    #: added stopped opening at all. A thread pinned to a release has to be
    #: readable against the shape that release was published with, or the
    #: release id on it is decoration.
    #:
    #: `build` fills it. Empty means a caller constructed a Catalog without
    #: one, and the domain's current schema is the only answer available --
    #: which is the old behaviour, kept for that case alone.
    specs: tuple[schema_mod.Relation, ...] = ()

    # -- what is here ----------------------------------------------------

    def _shape(self) -> tuple[schema_mod.Relation, ...]:
        return self.specs or schema_mod.relations(self.domain_id)

    def relations(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._shape())

    def require_relation(self, relation: str) -> str:
        name = str(relation or "").strip().lower()
        if name in self.relations():
            return name
        # A relation that belongs to the OTHER book is refused with the
        # reason, because "unknown relation" would send an analyst hunting
        # for a typo in a name that is spelled perfectly.
        try:
            owner = schema_mod.domain_of_relation(name)
        except schema_mod.UnknownRelation:
            owner = ""
        if owner and owner != self.domain_id:
            raise CrossDomainAccess(
                f"{name!r} is a relation of the {dom.LABELS[owner]} domain "
                f"and this analysis is pinned to {dom.LABELS[self.domain_id]}."
                f" A question about the other book is a question for a "
                f"thread in that book. The relations here are: "
                f"{', '.join(self.relations())}.")
        # NAMED AGAINST THE RELEASE, not the domain. A relation this book
        # gained after the pinned release was published is not a typo and
        # not a missing feature -- it is a column that did not exist yet,
        # and saying so is the difference between a reader hunting for a
        # mistake and a reader reading history.
        raise schema_mod.UnknownRelation(
            f"{relation!r} is not a relation of "
            f"{self.dataset_release_id!r}, the "
            f"{dom.LABELS[self.domain_id]} release this analysis is pinned "
            f"to. Its relations are: {', '.join(self.relations())}.")

    def resolve(self, relation: str, column: str) -> schema_mod.Field:
        return self.spec(relation).field(column)

    def columns(self, relation: str) -> tuple[str, ...]:
        return self.spec(relation).columns

    def spec(self, relation: str) -> schema_mod.Relation:
        name = self.require_relation(relation)
        for spec in self._shape():
            if spec.name == name:
                return spec
        # Unreachable: `require_relation` already matched against the same
        # list. Kept loud rather than returning None, because a silent
        # miss here would be a catalogue describing a relation it cannot
        # find.
        raise schema_mod.UnknownRelation(
            f"{name!r} is listed by {self.dataset_release_id!r} and has no "
            f"specification in it.")

    def outline(self) -> dict[str, Any]:
        """This book at a glance: subject matter and size, not the dictionary.

        Shaped as `{"relations": [...]}` because that is what the context
        assembler reads. Each entry carries the relation's own name, so a
        packet built from two books can never attribute one book's grain to
        the other's relation.
        """
        return {
            "domain_id": self.domain_id,
            "domain_label": dom.LABELS[self.domain_id],
            "dataset_release_id": self.dataset_release_id,
            "reporting_frequency": self.calendar.frequency,
            "reporting_periods": list(self.calendar.slots),
            "latest_period": self.calendar.latest,
            "relations": [{"relation": spec.name, "name": spec.name,
                           "grain": spec.grain, "about": spec.description,
                           "description": spec.description,
                           "period_column": spec.period_column,
                           "key_columns": list(spec.key_columns),
                           "columns": len(spec.fields)}
                          for spec in self._shape()],
            "joins": self.joins(),
        }

    def joins(self) -> list[dict[str, Any]]:
        """How this book's relations connect. Facts, not suggestions.

        FILTERED TO THIS RELEASE. The join graph is the one thing about a
        book's shape that no manifest records -- it is authored here, and
        `lake.publish` does not write it down -- so for a release published
        before a join existed this constant is today's opinion about an
        older book. Filtering to the relations and columns the release
        actually holds is what keeps that opinion from becoming a false
        statement: a join naming `corp_facility_quarter` is not a fact
        about a release whose facilities are in `corp_facility_month`.
        """
        available = set(self.relations())
        kept: list[dict[str, Any]] = []
        for join in JOINS.get(self.domain_id, []):
            left, right = str(join.get("left")), str(join.get("right"))
            if left not in available or right not in available:
                continue
            on = [str(c) for c in (join.get("on") or ())]
            if any(c not in self.columns(left) or c not in self.columns(right)
                   for c in on):
                continue
            kept.append(join)
        return kept

    def alternatives(self, relation: str, column: str, *,
                     limit: int = 6) -> tuple[str, ...]:
        """Columns that DO exist and are near the one that did not.

        Facts, not a rewrite. CreditProbe does not choose among them; the
        analyst reads them and authors its own repair.
        """
        names = list(self.columns(relation))
        wanted = str(column or "").strip().lower()
        tokens = {t for t in wanted.replace("-", "_").split("_") if t}
        scored: list[tuple[float, str]] = []
        for name in names:
            low = name.lower()
            ratio = difflib.SequenceMatcher(None, wanted, low).ratio()
            shared = tokens & set(low.split("_"))
            bonus = 0.22 * len(shared)
            if wanted and (wanted in low or low in wanted):
                bonus += 0.2
            scored.append((ratio + bonus, name))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return tuple(name for score, name in scored[:limit] if score > 0.25)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "domain_label": dom.LABELS[self.domain_id],
            "dataset_release_id": self.dataset_release_id,
            "release_fingerprint": self.release_fingerprint,
            "reporting_currency": self.reporting_currency,
            "amount_scale": self.amount_scale,
            "reporting_frequency": self.calendar.frequency,
            "reporting_periods": list(self.calendar.slots),
            "latest_period": self.calendar.latest,
            "relations": [spec.to_dict()
                          for spec in self._shape()],
        }


def build(*, domain_id: str, release_id: str = "",
          tenant_id: str = lake.DEFAULT_TENANT) -> Catalog:
    """Open a domain's published release, read-only."""
    domain_id = dom.parse(domain_id)
    release_id = release_id or dom.DEFAULT_RELEASES[domain_id]
    manifest = lake.read_manifest(release_id)
    published = str(manifest.get("domain_id") or "")
    if published != domain_id:
        # A release built for one book cannot serve another, whatever it is
        # named. Substituting it would answer a Retail question with
        # Corporate numbers and say nothing about having done so.
        raise CrossDomainAccess(
            f"Release {release_id!r} was published for the "
            f"{dom.LABELS.get(published, published)} domain and was asked "
            f"for as {dom.LABELS[domain_id]}. Nothing was substituted.")
    # THE RELEASE'S OWN RELATIONS, from the manifest that was written when
    # it was published. Same argument as the calendar below and the same
    # defect if it is skipped: a release read through a schema it was not
    # published with is a release being described as something else.
    #
    # A manifest with no relations block is older than the block; the
    # domain's current schema is then the only answer there is, and saying
    # so here is better than an empty catalogue.
    published_shape = [schema_mod.relation_from_dict(spec)
                       for spec in (manifest.get("relations") or [])
                       if isinstance(spec, dict) and spec.get("relation")]
    return Catalog(
        domain_id=domain_id,
        dataset_release_id=release_id,
        specs=tuple(published_shape),
        release_fingerprint=str(manifest.get("release_fingerprint") or ""),
        # The DOMAIN decides the frequency, and the manifest records what
        # the release was actually built with. Falling back to a module
        # constant is what let a quarterly book call itself monthly.
        calendar=Calendar(tuple(manifest.get("reporting_periods") or ()),
                          str(manifest.get("reporting_frequency")
                              or schema_mod.frequency(domain_id))),
        tenant_id=tenant_id,
        reporting_currency=str(manifest.get("reporting_currency")
                               or lake.CURRENCY),
        amount_scale=str(manifest.get("amount_scale") or lake.AMOUNT_SCALE))


# ---- the session -------------------------------------------------------

@dataclass
class Session:
    connection: Any
    catalog: Catalog
    relations: tuple[str, ...]
    built_seconds: float = 0.0

    @property
    def domain_id(self) -> str:
        return self.catalog.domain_id

    @property
    def tenant_id(self) -> str:
        return self.catalog.tenant_id

    @property
    def dataset_release_id(self) -> str:
        return self.catalog.dataset_release_id

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception:  # noqa: BLE001 - closing twice is not an error
            pass


_SESSIONS: dict[tuple[str, ...], Session] = {}
_SESSIONS_LOCK = threading.RLock()


def open_session(*, catalog: Catalog, reuse: bool = True) -> Session:
    """A session for this tenant, domain and release, built or reused.

    The cache key carries all three. A key that carried only the tenant and
    the release would let a Corporate session serve a Retail question the
    moment two releases shared an id -- and the fingerprint is in it because
    two builds of one id hold different numbers.
    """
    key = (catalog.tenant_id, catalog.domain_id, catalog.dataset_release_id,
           catalog.release_fingerprint)
    if not reuse:
        return _build_session(catalog)
    with _SESSIONS_LOCK:
        cached = _SESSIONS.get(key)
        if cached is not None:
            return cached
    session = _build_session(catalog)
    with _SESSIONS_LOCK:
        _SESSIONS[key] = session
        while len(_SESSIONS) > MAX_CACHED_SESSIONS:
            _, evicted = _SESSIONS.popitem()
            evicted.close()
    return session


def _build_session(catalog: Catalog) -> Session:
    import duckdb

    started = time.monotonic()
    manifest = lake.read_manifest(catalog.dataset_release_id)
    tenants = manifest.get("tenants") or []
    if tenants and catalog.tenant_id not in tenants:
        raise PermissionError(
            f"Release {catalog.dataset_release_id!r} holds no data for "
            f"tenant {catalog.tenant_id!r}. This is an access or "
            f"configuration mismatch, not an empty portfolio.")

    connection = duckdb.connect(database=":memory:")
    connection.execute("SET threads TO 2")
    # RAISED FROM 512MB, DELIBERATELY AND ON A MEASUREMENT.
    #
    # The whole release is materialised into in-memory tables below, so the
    # base tables are a fixed cost paid before a single query runs. On the
    # enriched Retail book -- 1.9 million rows across four relations --
    # `duckdb_memory()` reported 486 MiB of IN_MEMORY_TABLE against a
    # 488 MiB effective limit. Queries still returned, because DuckDB
    # streams and spills rather than failing outright, but a group-by that
    # wants a hash table had nothing left to build it in, and a limit a
    # workload sits exactly on is one that fails on the day the book gains
    # a relation.
    #
    # 1.5GB is not a licence to grow: it is the measured footprint plus
    # working room for the widest group-by an analyst can author. The
    # figure to watch is IN_MEMORY_TABLE.
    connection.execute("SET memory_limit = '1536MB'")

    built: list[str] = []
    for relation in catalog.relations():
        path = lake.relation_path(catalog.dataset_release_id, relation)
        columns = ", ".join(f'"{c}"' for c in catalog.columns(relation))
        # The tenant, release and DOMAIN filters are part of the table, not
        # something the analyst has to remember to write.
        connection.execute(
            f'CREATE TABLE "{relation}" AS SELECT {columns} '
            f"FROM read_parquet('{path}') "
            f"WHERE tenant_id = '{catalog.tenant_id}'"
            f"  AND dataset_release_id = '{catalog.dataset_release_id}'"
            f"  AND domain_id = '{catalog.domain_id}'")
        built.append(relation)

    # From here the session can reach nothing but those tables.
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    return Session(connection=connection, catalog=catalog,
                   relations=tuple(built),
                   built_seconds=round(time.monotonic() - started, 3))


def reset_sessions() -> None:
    with _SESSIONS_LOCK:
        for session in _SESSIONS.values():
            session.close()
        _SESSIONS.clear()


__all__ = ["Calendar", "Catalog", "CrossDomainAccess", "MAX_CACHED_SESSIONS",
           "Session", "build", "open_session", "reset_sessions"]
