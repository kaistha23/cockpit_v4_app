"""
Everything ONE analytical run needs, resolved from that run's own book.

The defect this module exists for
---------------------------------
`worker._drive` opened its SQL session from `self.runtime.catalog` -- one
process-wide catalogue, built from one pinned release at startup. Every run in
the process shared it, so a run's domain could be recorded on its thread, its
dashboard could be computed correctly, its badge could say Retail, and the
analysis would still read corporate relations. Nothing in the answer would
have said so.

The previous round refused retail runs rather than let that happen. This
module is what makes the refusal unnecessary: the analytical runtime is
derived from the RUN, not from the process.

Where the identity comes from
-----------------------------
The persisted run and its thread, and nowhere else. Not the frontend, not the
Home switch as it currently stands, not a startup default, and not whichever
catalogue happened to be cached first. A run settled three minutes after the
reader switched the page to the other book must still read the book it was
asked in.

Caching
-------
Keyed by tenant, domain, release AND fingerprint. A release id is a name; two
builds share it and hold different numbers, so a cache keyed on the name would
serve the old build's catalogue to the new build's question.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domain_resolver as resolver
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake


class AnalyticalRuntimeUnavailable(RuntimeError):
    """This run's book cannot be opened. Said, never substituted."""


@dataclass(frozen=True)
class AnalyticalRuntime:
    """One book, opened for one run. No global state reaches in here."""

    scope: dom.DomainScope
    catalog: cat.Catalog
    session: Any
    #: A V4 book states its own missingness in its schema rather than through
    #: a separately measured profile. Present so every consumer of a "book"
    #: can ask without knowing which kind it holds.
    coverage: Any = None

    @property
    def domain_id(self) -> str:
        return self.scope.domain_id

    @property
    def release_id(self) -> str:
        return self.scope.release_id

    @property
    def release_fingerprint(self) -> str:
        return self.scope.release_fingerprint

    @property
    def tenant_id(self) -> str:
        return self.catalog.tenant_id

    @property
    def money_unit(self) -> str:
        return self.scope.money_unit

    def release_summary(self) -> dict[str, Any]:
        """What the analyst is told about the book it is reading."""
        manifest = lake.read_manifest(self.release_id)
        return {
            "dataset_release_id": self.release_id,
            "domain_id": self.domain_id,
            "domain_label": self.scope.label,
            "release_fingerprint": self.release_fingerprint,
            "origin": manifest.get("origin"),
            "data_version": manifest.get("data_version"),
            "not_client_data": manifest.get("not_client_data"),
            "reporting_currency": self.scope.currency,
            "amount_scale": self.scope.amount_scale,
            "reporting_frequency": self.scope.reporting_frequency,
            "geography": manifest.get("geography"),
            "geography_name": manifest.get("geography_name"),
            "tenants": manifest.get("tenants"),
        }

    def read_scope(self, principal: dict[str, Any]) -> "ReadScope":
        """The effective read scope, derived server-side from the principal.

        There is one way to derive it and this is it. A scope assembled at a
        call site is a scope that can disagree with the session it is used
        against.
        """
        return ReadScope(
            tenant_id=str(principal.get("tenant") or self.tenant_id),
            principal_id=str(principal.get("id") or ""),
            domain_id=self.domain_id,
            dataset_release_id=self.release_id,
            release_fingerprint=self.release_fingerprint,
            relations=tuple(self.catalog.relations()))


@dataclass(frozen=True)
class ReadScope:
    """What this principal may read, in this book, in this release."""

    tenant_id: str
    principal_id: str
    domain_id: str
    dataset_release_id: str
    release_fingerprint: str
    relations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"tenant_id": self.tenant_id, "domain_id": self.domain_id,
                "dataset_release_id": self.dataset_release_id,
                "release_fingerprint": self.release_fingerprint,
                "relations": list(self.relations)}


_CACHE: dict[tuple[str, ...], AnalyticalRuntime] = {}
_LOCK = threading.RLock()
MAX_CACHED = 4


def for_domain(domain_id: str, *, tenant_id: str = lake.DEFAULT_TENANT,
               release_id: str = "",
               release_fingerprint: str = "") -> AnalyticalRuntime:
    """Open a book. Refuses rather than substituting when it cannot.

    `release_id` is the run's OWN record of what it was accepted against,
    and it SELECTS that release rather than merely asserting it. It used to
    be an assertion only: any id but the domain's current one was refused,
    so a thread pinned to a superseded release could not be opened at all
    and every consumer that tried -- the transcript header, the trace, the
    export lineage -- swallowed the refusal and published an empty release
    block beside a release id it had just read. The stored id said one
    thing and the header said nothing.

    Opening a superseded release is READING HISTORY, and it is safe because
    a published release is immutable and describes its own shape. It is not
    a way to choose a book for new work: `resolve` never passes an id, so a
    new run still takes the domain's current release.

    `release_fingerprint` stays an assertion, and the strict one. A release
    whose bytes moved under its own id is not the release this run was
    accepted against, and answering from the new bytes would silently
    change what the reader was told they were asking about.
    """
    domain_id = dom.parse(domain_id)
    try:
        scope = resolver.scope_for(domain_id, tenant_id=tenant_id,
                                   release_id=release_id)
    except resolver.DomainUnavailable as exc:
        raise AnalyticalRuntimeUnavailable(str(exc)) from exc
    except lake.ReleaseNotFound as exc:
        raise AnalyticalRuntimeUnavailable(
            f"This run was accepted against release {release_id!r}, which is "
            f"not published here. Nothing was substituted.") from exc

    if release_fingerprint and release_fingerprint != scope.release_fingerprint:
        raise AnalyticalRuntimeUnavailable(
            f"Release {scope.release_id!r} has been rebuilt since this run "
            f"was accepted: it was {release_fingerprint[:16]} and is now "
            f"{scope.release_fingerprint[:16]}. The numbers are not the same "
            f"numbers, so nothing was substituted.")

    key = (tenant_id, scope.domain_id, scope.release_id,
           scope.release_fingerprint)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

    catalog = cat.build(domain_id=scope.domain_id,
                        release_id=scope.release_id, tenant_id=tenant_id)
    try:
        session = cat.open_session(catalog=catalog)
    except PermissionError as exc:
        raise AnalyticalRuntimeUnavailable(str(exc)) from exc
    runtime = AnalyticalRuntime(scope=scope, catalog=catalog, session=session)

    with _LOCK:
        _CACHE[key] = runtime
        while len(_CACHE) > MAX_CACHED:
            _CACHE.pop(next(iter(_CACHE)))
    return runtime


class LegacyRelease(RuntimeError):
    """This run was accepted against the pre-domain release.

    Not an error and not a fallback: it is the answer to "which book is
    this run's?" for every run recorded before the two domain books existed,
    and for the analytical benchmark suites that still drive that release.
    The caller opens THAT release or refuses -- it does not substitute a
    domain book whose numbers are different numbers.
    """

    def __init__(self, release_id: str) -> None:
        super().__init__(
            f"Run recorded against release {release_id!r}, which is not a "
            f"domain release.")
        self.release_id = release_id


def domain_of_release(release_id: str) -> str:
    """Which book published this release id, or empty if none did.

    The CURRENT defaults first, because that is the common case and costs
    no file read. Then the release's own manifest, which records the domain
    it was published for -- a superseded release still belongs to the book
    that wrote it, and matching only against today's defaults said it
    belonged to neither. That is what made every thread pinned to a
    superseded release look like a legacy release with no book at all.

    An id nothing published still returns empty, and the caller still
    refuses. Reading history is not the same as inventing it.
    """
    for domain_id, known in dom.DEFAULT_RELEASES.items():
        if release_id == known:
            return domain_id
    try:
        published = str(lake.read_manifest(release_id).get("domain_id") or "")
    except Exception:  # noqa: BLE001 - not published here
        return ""
    return published if published in dom.DEFAULT_RELEASES else ""


def for_run(record: Any, *, store: Any = None) -> AnalyticalRuntime:
    """The book THIS run was accepted in, read from what was persisted.

    The RELEASE decides, because the release names the bytes. A domain id is
    a label on a run and a release id is a statement about which numbers it
    was accepted against; where the two disagree the run is refused rather
    than served either one.
    """
    domain_id = str(getattr(record, "domain_id", "") or "")
    release_id = str(getattr(record, "release_id", "") or "")
    fingerprint = str(getattr(record, "release_fingerprint", "") or "")

    if store is not None:
        pinned = store.thread_domain(getattr(record, "thread_id", "")) or {}
        domain_id = domain_id or pinned.get("domain_id", "")
        if not release_id:
            release_id = pinned.get("release_id", "") or ""
            fingerprint = fingerprint or pinned.get("release_fingerprint", "")

    published = domain_of_release(release_id) if release_id else ""
    if release_id and not published:
        # A release that belongs to neither book. Its own runtime or nothing.
        raise LegacyRelease(release_id)

    if published and domain_id and published != domain_id:
        raise AnalyticalRuntimeUnavailable(
            f"This run is labelled {domain_id!r} and was accepted against "
            f"release {release_id!r}, which is the "
            f"{dom.LABELS[published]} book. The label and the bytes "
            f"disagree, so nothing was opened and nothing was substituted.")

    # A run recorded with neither a release nor a domain is corporate: that
    # is the only book the runtime had when such a row could be written.
    domain_id = published or domain_id or dom.DEFAULT_DOMAIN
    return for_domain(
        domain_id,
        tenant_id=str(getattr(record, "tenant_id", "")
                      or lake.DEFAULT_TENANT),
        release_id=release_id,
        release_fingerprint=fingerprint)


def reset() -> None:
    with _LOCK:
        for runtime in _CACHE.values():
            try:
                runtime.session.close()
            except Exception:  # noqa: BLE001
                pass
        _CACHE.clear()
    cat.reset_sessions()


__all__ = ["AnalyticalRuntime", "AnalyticalRuntimeUnavailable",
           "LegacyRelease", "MAX_CACHED", "ReadScope", "domain_of_release",
           "for_domain", "for_run", "reset"]
