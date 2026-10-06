"""
Which book this request is about. Decided once, in one place.

The order matters, and it is not a preference list
--------------------------------------------------
1. INSIDE A THREAD, the thread's domain wins -- always, and without consulting
   anything else. A conversation is held in one book. A follow-up six turns
   later must reach the same evidence as the question that opened it, and a
   domain re-derived from whatever the home page currently shows is not that.
2. Otherwise the request's own selection, which is the Corporate/Retail
   control on Cockpit Home.
3. Otherwise the configured default.

A selection that DISAGREES with the thread it is sent to is not quietly
overruled and not quietly obeyed: it is reported, so the caller can offer to
start a thread in the other book rather than contaminating this one.

Readiness is per domain
-----------------------
One book being unpublished does not make the other unavailable, and it
absolutely does not make the other a substitute for it. `statuses()` answers
for each separately and names the reason when a book cannot be asked
anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.cockpit_v4 import catalog as cat
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import lake


class DomainPinned(PermissionError):
    """A request asked one book of a thread held in another."""

    def __init__(self, *, thread_domain: str, asked: str) -> None:
        self.thread_domain = thread_domain
        self.asked = asked
        super().__init__(
            f"This conversation is pinned to {dom.LABELS[thread_domain]} and "
            f"the question was sent as {dom.LABELS[asked]}. Evidence from two "
            f"books in one transcript cannot be told apart afterwards, so "
            f"nothing was substituted. Start a "
            f"{dom.SHORT_LABELS[asked]} conversation for this question.")


def analysis_domains(*, tenant_id: str = lake.DEFAULT_TENANT
                     ) -> tuple[str, ...]:
    """The books the ANALYTICAL path can answer questions about, right now.

    Established, not declared. This used to be a hard-coded tuple holding
    only Corporate, because the execution path was built from one pinned
    analytical release and a Retail thread would have run its SQL against
    corporate relations. That is fixed: the analytical runtime is opened per
    run, from the book the run was accepted in.

    It is still computed rather than asserted, because "the code supports
    this book" and "this runtime can open it" are different claims. A domain
    whose release is not published in a deployment is not askable there, and
    saying otherwise would put the question in a thread that cannot answer
    it.
    """
    ready: list[str] = []
    for domain_id in dom.DOMAIN_IDS:
        if analysis_supported(domain_id, tenant_id=tenant_id):
            ready.append(domain_id)
    return tuple(ready)


def analysis_supported(domain_id: str, *,
                       tenant_id: str = lake.DEFAULT_TENANT) -> bool:
    """Whether a question in this book can actually be executed.

    Opens the book. A cheaper check -- "is the release published?" -- would
    pass for a release whose session cannot be materialized, and the run
    would then fail after the reader had been told it was accepted.
    """
    from backend.cockpit_v4 import analytical_runtime as arun

    try:
        arun.for_domain(dom.parse(domain_id), tenant_id=tenant_id)
    except (arun.AnalyticalRuntimeUnavailable, DomainUnavailable,
            dom.UnknownDomain, cat.CrossDomainAccess, lake.ReleaseNotFound):
        return False
    except Exception:  # noqa: BLE001 - an unopenable book is not askable
        return False
    return True


class AnalysisNotWiredForDomain(RuntimeError):
    """This book cannot be asked in this runtime. Said, never faked."""

    def __init__(self, domain_id: str, *,
                 tenant_id: str = lake.DEFAULT_TENANT) -> None:
        self.domain_id = dom.parse(domain_id)
        ready = analysis_domains(tenant_id=tenant_id)
        available = (", ".join(dom.LABELS[d] for d in ready) if ready
                     else "no book in this runtime")
        super().__init__(
            f"{dom.LABELS[self.domain_id]} questions cannot be answered in "
            f"this runtime: its analytical release could not be opened, and "
            f"running the question against another book would answer from "
            f"the wrong book without saying so. What can be asked here: "
            f"{available}. Publish it with "
            f"{provision_command(self.domain_id)}, or browse "
            f"{dom.SHORT_LABELS[self.domain_id]} in Data Builder.")


class DomainUnavailable(RuntimeError):
    """This book has no published release in this runtime. Said, not swapped."""

    def __init__(self, domain_id: str, reason: str) -> None:
        self.domain_id = domain_id
        self.reason = reason
        super().__init__(
            f"The {dom.LABELS[domain_id]} domain is not available in this "
            f"runtime: {reason} Nothing was substituted for it.")


def scope_for(domain_id: str, *, tenant_id: str = lake.DEFAULT_TENANT,
              release_id: str = "") -> dom.DomainScope:
    """Everything one request needs to know about its book.

    `release_id` names a PUBLISHED release to open instead of the one this
    domain currently points at. It exists for reading history: a thread
    pinned to a superseded release has to be openable against the release
    it was pinned to, or the id stored on it is decoration.

    It is not a way to choose a book for new work. `resolve` below never
    passes it, so a new run still takes the domain's current release, and
    the routes refuse a caller that tries to name one.
    """
    domain_id = dom.parse(domain_id)
    release_id = release_id or dom.DEFAULT_RELEASES[domain_id]
    try:
        catalog = cat.build(domain_id=domain_id, release_id=release_id,
                            tenant_id=tenant_id)
    except lake.ReleaseNotFound as exc:
        raise DomainUnavailable(
            domain_id,
            f"release {release_id!r} is not published.") from exc
    return dom.DomainScope(
        domain_id=domain_id,
        release_id=catalog.dataset_release_id,
        release_fingerprint=catalog.release_fingerprint,
        country=lake.COUNTRY_NAME,
        currency=catalog.reporting_currency,
        amount_scale=catalog.amount_scale,
        reporting_frequency=catalog.calendar.frequency,
        periods=tuple(catalog.calendar.slots),
        relations=catalog.relations())


def resolve(*, thread_domain: str = "", requested: Any = None,
            tenant_id: str = lake.DEFAULT_TENANT) -> dom.DomainScope:
    """The book this request is about, by the order at the top of this module."""
    if thread_domain:
        pinned = dom.parse(thread_domain)
        asked = dom.parse(requested, default=pinned)
        if asked != pinned:
            raise DomainPinned(thread_domain=pinned, asked=asked)
        return scope_for(pinned, tenant_id=tenant_id)
    return scope_for(dom.parse(requested), tenant_id=tenant_id)


@dataclass(frozen=True)
class Availability:
    """What each book can currently do, and why not when it cannot."""

    statuses: tuple[dom.DomainStatus, ...]

    def __getitem__(self, domain_id: str) -> dom.DomainStatus:
        for status in self.statuses:
            if status.domain_id == domain_id:
                return status
        raise KeyError(domain_id)

    @property
    def ready_domains(self) -> tuple[str, ...]:
        return tuple(s.domain_id for s in self.statuses if s.ready)

    @property
    def default(self) -> str:
        """The book to open on. The configured one when it is ready.

        When it is not, the first book that IS ready -- which is a different
        thing from substituting one for the other: nothing here answers a
        Corporate question with Retail data, it only decides which control is
        selected when the page opens.
        """
        if dom.DEFAULT_DOMAIN in self.ready_domains:
            return dom.DEFAULT_DOMAIN
        return self.ready_domains[0] if self.ready_domains else \
            dom.DEFAULT_DOMAIN

    def to_dict(self) -> dict[str, Any]:
        return {
            "domains": [s.to_dict() for s in self.statuses],
            "default_domain": self.default,
            "ready": list(self.ready_domains),
        }


def availability(*, tenant_id: str = lake.DEFAULT_TENANT) -> Availability:
    statuses: list[dom.DomainStatus] = []
    for domain_id in dom.DOMAIN_IDS:
        release_id = dom.DEFAULT_RELEASES[domain_id]
        try:
            scope = scope_for(domain_id, tenant_id=tenant_id)
        except DomainUnavailable as exc:
            statuses.append(dom.DomainStatus(
                domain_id=domain_id, release_id=release_id, ready=False,
                reason=exc.reason,
                detail={"provision_command": provision_command(domain_id)}))
            continue
        except cat.CrossDomainAccess as exc:
            statuses.append(dom.DomainStatus(
                domain_id=domain_id, release_id=release_id, ready=False,
                reason=str(exc)))
            continue
        manifest = lake.read_manifest(release_id)
        statuses.append(dom.DomainStatus(
            domain_id=domain_id, release_id=release_id, ready=True,
            scope=scope,
            detail={"row_counts": manifest.get("row_counts", {}),
                    "entity_counts": manifest.get("entity_counts", {}),
                    "relation_count": len(manifest.get("relations", [])),
                    "field_count": sum(len(r.get("fields", []))
                                       for r in manifest.get("relations",
                                                             []))}))
    return Availability(tuple(statuses))


def provision_command(domain_id: str = "") -> str:
    """The one V4-native command that publishes a domain."""
    base = "python3 scripts/cockpit_v4/seed_domains.py"
    return f"{base} --domain {domain_id}" if domain_id else base


__all__ = ["AnalysisNotWiredForDomain", "Availability", "DomainPinned",
           "DomainUnavailable", "analysis_domains", "analysis_supported",
           "availability", "provision_command", "resolve", "scope_for"]
