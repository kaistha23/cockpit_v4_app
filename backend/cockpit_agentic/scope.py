"""
The domain boundary, below the model. Specification section 6.4.

Read-only alone is not a data-domain boundary
---------------------------------------------
Section 6.4 is explicit about this, and it is the sentence this module exists
to satisfy. A read-only connection to the whole warehouse would still let a
generated query name `ews_alerts`. What is needed is a principal that can see
ONLY the allowlisted Cockpit views, for one tenant, over one pinned release,
restricted "even if generated code names another schema, source table, catalog,
search service, cached artifact or exported file".

That is enforced in `sql.py` by building a connection whose entire universe is
ten views over one release's Parquet files, and then disabling file and network
access and locking the configuration so nothing the model writes can widen it.
This module decides WHAT that universe contains.

Metadata is scoped too
----------------------
`visible` filters a listing BEFORE it is described. A refusal says the same
thing whether a relation belongs to another module or does not exist, so the
refusal itself does not map the rest of the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from backend.cockpit_agentic import DOMAIN, UNTRUSTED_NOTE
from backend.cockpit_agentic.catalog import QUERYABLE_RELATIONS


class OutOfScope(PermissionError):
    """A read outside the Cockpit domain. Refused, and refused identically
    whoever asks and whatever lies beyond."""


#: Everything Cockpit may read. Nothing else exists as far as this runtime is
#: concerned -- no EWS, Credit Scoring, Scorecard Validation, What-if or Lenses
#: relation, and no source table that also happens to feed Cockpit ingestion.
ALLOWED: frozenset[str] = frozenset(QUERYABLE_RELATIONS)


@dataclass(frozen=True)
class Scope:
    """One principal's effective read scope, for one pinned release."""

    tenant_id: str
    dataset_release_id: str
    relations: frozenset[str]
    principal_relations: frozenset[str]
    unrestricted_principal: bool

    def permits(self, relation: str) -> bool:
        return str(relation) in self.relations

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain_id": DOMAIN,
            "tenant_id": self.tenant_id,
            "dataset_release_id": self.dataset_release_id,
            "readable_relations": sorted(self.relations),
            "capability_scope": sorted(ALLOWED),
            "principal_is_unrestricted": self.unrestricted_principal,
            "note": ("The Cockpit reads its own twenty-quarter corporate "
                     "domain only. The effective scope is the intersection of "
                     "that capability scope and this principal's permissions, "
                     "and it is enforced by the query principal rather than by "
                     "a filter the model has to remember to write."),
            "untrusted_text": UNTRUSTED_NOTE,
        }


def tenant_of(principal: Any) -> str:
    """The authenticated tenant. Resolved server-side, never from model text."""
    for attribute in ("tenant_id", "tenant", "org_id", "workspace_id"):
        value = getattr(principal, attribute, None)
        if value:
            return str(value)
    # A deployment without multi-tenancy still has exactly one tenant, and
    # naming it explicitly is what lets the artifact and cache keys carry it.
    from backend.config import settings

    return str(settings.cockpit_agentic_v3_default_tenant)


def for_principal(principal: Any, *, dataset_release_id: str) -> Scope:
    granted = frozenset(
        str(r) for r in (getattr(principal, "cockpit_relations", None) or ()))
    # An empty grant means "not narrowed", which is how the rest of the product
    # reads a principal's dataset list. It does not mean "may see nothing", and
    # reading it as the latter would break every ordinary analyst.
    unrestricted = not granted
    effective = ALLOWED if unrestricted else (ALLOWED & granted)
    return Scope(tenant_id=tenant_of(principal),
                 dataset_release_id=str(dataset_release_id),
                 relations=effective, principal_relations=granted,
                 unrestricted_principal=unrestricted)


def permit(relation: str, scope: Scope) -> str:
    """Return the relation name, or refuse. The single choke point.

    The message names only this domain's relations. It is identical whether the
    caller asked for a relation belonging to another module or for one that
    does not exist anywhere, so a probe learns nothing from the difference.
    """
    name = str(relation or "").strip().lower()
    if not scope.permits(name):
        raise OutOfScope(
            f"{relation!r} is not readable from the Cockpit. The Cockpit reads "
            f"its own twenty-quarter corporate domain only: "
            f"{', '.join(sorted(scope.relations))}.")
    return name


def visible(names: Iterable[str], scope: Scope) -> list[str]:
    """Filter a listing BEFORE it is described. Metadata is scoped as well."""
    return sorted(n for n in names if scope.permits(str(n)))


__all__ = ["ALLOWED", "OutOfScope", "Scope", "for_principal", "permit",
           "tenant_of", "visible"]
