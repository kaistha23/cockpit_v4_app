"""
Which release this is, proved rather than declared.

The defect this exists for
--------------------------
Every analytical object in V4 -- a run, an artifact, a numeric claim, a
table, a saved analysis, a seeded investigation -- carries a `release_id`.
An id is a NAME, and a name is not evidence. Two releases can share one, a
release can be rebuilt under the same id with different bytes, and a stored
artifact from yesterday's build satisfies "same release_id" against today's
perfectly.

So an object carries a HEADER: the id AND a fingerprint of the published
bytes, plus the facts a reader needs to know what the numbers mean -- the
country, the currency, the scale, the latest populated quarter, and whether
this is client data. Evidence validation compares fingerprints, and an
artifact built from different bytes is refused however it is labelled.

What the fingerprint covers
---------------------------
The manifest and every published relation file, by name, size and SHA-256 of
its contents, folded in sorted order. It is therefore a fact about what is on
disk, not about what a manifest says is on disk, which is the distinction a
silent rebuild turns on.

Fail closed
-----------
A release that does not say what it is denominated in does not get a guess.
`header()` returns the missing fields as `unverified`, and a caller that
needs denomination refuses rather than assuming a currency of any
nationality. A wrong currency on a credit figure is not a formatting
problem.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any

#: The V4 demonstration and UAT release. Saudi-native: SAR, million, Saudi
#: synthetic borrowers and sectors. Used when no release id is configured.
#: It is a DEFAULT, not a fallback -- an explicitly configured release is
#: always honoured, and a configured release that is missing is an error
#: rather than an excuse to quietly load this one.
DEFAULT_RELEASE_ID = "v4-saudi-20q-v1"

#: What a header must carry before a denominated figure may be published.
REQUIRED = ("release_id", "release_fingerprint", "reporting_currency",
            "amount_scale", "country")

_LOCK = threading.Lock()
_FINGERPRINTS: dict[str, str] = {}


def fingerprint(release_id: str) -> str:
    """SHA-256 over the published bytes of one release.

    Memoized per process: a release is immutable once published, and hashing
    a hundred megabytes of Parquet on every run would make the guarantee too
    expensive to keep. `forget()` clears it for a test that deliberately
    rebuilds.
    """
    with _LOCK:
        cached = _FINGERPRINTS.get(release_id)
    if cached is not None:
        return cached

    # A DOMAIN release records its digest when it is published, and its bytes
    # do not live where the pre-domain releases live. Hashing the empty
    # directory it is not in returns the SHA-256 of nothing --
    # `e3b0c442...` -- which is a valid-looking fingerprint that identifies
    # no release, matches every other book's header, and was being stamped on
    # every published answer of both books.
    from backend.cockpit_v4 import lake as lake_mod

    try:
        recorded = lake_mod.fingerprint(release_id)
    except Exception:  # noqa: BLE001 - not a domain release
        recorded = ""
    if recorded:
        with _LOCK:
            _FINGERPRINTS[release_id] = recorded
        return recorded

    from backend.cockpit_agentic import store as v3_store

    directory = v3_store.release_dir(release_id)
    digest = hashlib.sha256()
    if directory.exists():
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(str(path.stat().st_size).encode())
            digest.update(_file_digest(path))
    value = digest.hexdigest()
    with _LOCK:
        _FINGERPRINTS[release_id] = value
    return value


def forget(release_id: str = "") -> None:
    """Drop the memoized fingerprint. For tests that rebuild on purpose."""
    with _LOCK:
        if release_id:
            _FINGERPRINTS.pop(release_id, None)
        else:
            _FINGERPRINTS.clear()


def _file_digest(path) -> bytes:  # noqa: ANN001
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.digest()


@dataclass(frozen=True)
class Header:
    """The immutable execution header pinned to every analytical object."""

    release_id: str
    release_fingerprint: str
    domain_id: str = ""
    tenant_id: str = ""
    country: str = ""
    reporting_currency: str = ""
    amount_scale: str = ""
    #: The last period this release actually holds, under a name that does
    #: not decide what a period IS. `latest_populated_quarter` is still
    #: served beside it because saved analyses and older readers index on
    #: that key, but nothing in this build writes a quarter into it.
    latest_populated_period: str = ""
    reporting_frequency: str = "quarterly"
    not_client_data: bool = True
    unverified: tuple[str, ...] = field(default_factory=tuple)

    @property
    def latest_populated_quarter(self) -> str:
        """The name this field had before there were monthly books. Kept so
        an older reader of a saved header is not broken; never a quarter."""
        return self.latest_populated_period

    @property
    def denominated(self) -> bool:
        """True when a currency figure from this release may be published."""
        return not self.unverified

    @property
    def money_unit(self) -> str:
        return f"{self.reporting_currency} {self.amount_scale}".strip()

    def to_dict(self) -> dict[str, Any]:
        body = {
            "release_id": self.release_id,
            "release_fingerprint": self.release_fingerprint,
            "domain_id": self.domain_id,
            "tenant_id": self.tenant_id,
            "country": self.country,
            "reporting_currency": self.reporting_currency,
            "amount_scale": self.amount_scale,
            "latest_populated_period": self.latest_populated_period,
            "latest_populated_quarter": self.latest_populated_period,
            "reporting_frequency": self.reporting_frequency,
            "not_client_data": self.not_client_data,
        }
        if self.unverified:
            body["unverified"] = list(self.unverified)
        return body

    def matches(self, other: Any) -> bool:
        """Same release AND same bytes.

        A dict that carries no fingerprint at all is not a match: it was
        written before headers existed or by something that does not stamp
        them, and either way it cannot be shown to be this release.
        """
        if isinstance(other, Header):
            other = other.to_dict()
        if not isinstance(other, dict):
            return False
        their_print = str(other.get("release_fingerprint") or "")
        if not their_print:
            return False
        return (str(other.get("release_id") or "") == self.release_id
                and their_print == self.release_fingerprint)


def header(*, release_id: str, catalog: Any = None,
           release_summary: dict[str, Any] | None = None,
           tenant_id: str = "") -> Header:
    """Build the header for a loaded release. Nothing here is guessed."""
    summary = dict(release_summary or {})
    currency = str(summary.get("reporting_currency")
                   or getattr(catalog, "reporting_currency", "") or "").strip()
    scale = str(summary.get("amount_scale")
                or getattr(catalog, "amount_scale", "") or "").strip()
    country = str(summary.get("geography_name") or "").strip()
    calendar = getattr(catalog, "calendar", None)
    populated = list(getattr(calendar, "populated", ()) or ())
    # WHAT THIS BOOK'S PERIOD IS, read from the release rather than defaulted.
    #
    # `reporting_frequency` defaulted to "quarterly" and nothing ever set it,
    # so every published answer from a MONTHLY book travelled with a header
    # saying the book was quarterly -- into the thread header, into a saved
    # analysis, into the export footer and into every shared link.
    frequency = str(summary.get("reporting_frequency")
                    or getattr(catalog, "reporting_frequency", "")
                    or "").strip().lower()
    if not frequency:
        frequency = ("monthly" if populated and len(str(populated[-1])) == 7
                     and "-" in str(populated[-1]) else "quarterly")

    missing = []
    if not currency:
        missing.append("reporting_currency")
    if not scale:
        missing.append("amount_scale")
    if not country:
        missing.append("country")

    return Header(
        release_id=release_id,
        release_fingerprint=fingerprint(release_id),
        domain_id=str(summary.get("domain_id") or ""),
        tenant_id=tenant_id or str((summary.get("tenants") or [""])[0]
                                   if summary.get("tenants") else ""),
        country=country,
        reporting_currency=currency,
        amount_scale=scale,
        latest_populated_period=str(populated[-1]) if populated else "",
        reporting_frequency=frequency,
        not_client_data=bool(summary.get("not_client_data", True)),
        unverified=tuple(missing))


__all__ = ["DEFAULT_RELEASE_ID", "Header", "REQUIRED", "fingerprint",
           "forget", "header"]
