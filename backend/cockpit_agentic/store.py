"""
Where a release lives on disk, and the guard that keeps it there.

Runtime isolation, not just a branch
------------------------------------
A Git branch isolates code. It does not isolate a database, a Parquet lake, a
cache or a seed target, and a V3 build that wrote into the shared analytics
directory would corrupt every other module's data while every test still
passed. So every V3 path carries the configured namespace, and `check_target`
refuses to write anywhere that does not -- before the first byte, not after.

The release is a pinned, immutable snapshot: a directory named for the release
id, holding one Parquet file per relation and a manifest. Nothing rewrites a
published release in place, because a request pins one for its whole lifetime
and section 9.6 forbids silently mixing two.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backend.cockpit_agentic import (CATALOG_VERSION, DATA_VERSION, DOMAIN,
                                     NOT_CLIENT_DATA, ORIGIN, namespace)
from backend.cockpit_agentic.calendar import Calendar
from backend.cockpit_agentic.generate import Release

MANIFEST = "manifest.json"

#: Directories a V3 build must never write into, whatever the configuration
#: says. These belong to the rest of the product.
FORBIDDEN = ("data/curated", "data/raw", "metadata", "data/analytics")


class UnsafeTarget(RuntimeError):
    """A write destination outside the V3 namespace. Refused."""


class ReleaseNotFound(LookupError):
    """The pinned release is not on disk. Said, never substituted."""


def root() -> Path:
    """The V3 lake. Always inside the configured namespace."""
    from backend.config import settings

    return Path(settings.analytics_dir).parent / namespace()


def check_target(path: Path | str, *, require_flag: bool = True) -> Path:
    """Refuse a destination that is not ours, before anything is written."""
    from backend.config import settings

    if require_flag and not settings.cockpit_agentic_v3:
        raise UnsafeTarget(
            "Cockpit Agentic V3 is switched off in this runtime. Set "
            "COCKPIT_AGENTIC_V3=true before building or seeding a release.")
    target = Path(path).resolve()
    text = str(target).replace("\\", "/")
    space = namespace()
    if space not in text:
        raise UnsafeTarget(
            f"{target} does not contain the V3 namespace {space!r}. A build "
            f"that can write outside its own namespace is not isolated, "
            f"whatever branch it is on.")
    for forbidden in FORBIDDEN:
        if f"/{forbidden}/" in f"{text}/" and space not in forbidden:
            raise UnsafeTarget(
                f"{target} is inside {forbidden}, which belongs to the rest of "
                f"the product. V3 writes only under {space!r}.")
    return target


def release_dir(dataset_release_id: str) -> Path:
    return root() / str(dataset_release_id)


def write(release: Release, *, require_flag: bool = True,
          overwrite: bool = False) -> dict[str, Any]:
    """Publish a release. One Parquet file per relation, plus a manifest."""
    directory = check_target(release_dir(release.dataset_release_id),
                             require_flag=require_flag)
    if directory.exists():
        if not overwrite:
            raise UnsafeTarget(
                f"release {release.dataset_release_id!r} already exists. A "
                f"published release is immutable: build a new release id, or "
                f"pass overwrite=True deliberately.")
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)

    written: dict[str, dict[str, Any]] = {}
    for relation, frame in release.frames.items():
        path = directory / f"{relation}.parquet"
        frame.to_parquet(path, index=False)
        written[relation] = {"rows": int(len(frame)),
                             "columns": int(frame.shape[1]),
                             "bytes": int(path.stat().st_size)}

    manifest = {
        "dataset_release_id": release.dataset_release_id,
        "domain_id": DOMAIN,
        "origin": ORIGIN,
        "not_client_data": NOT_CLIENT_DATA,
        "data_version": DATA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "namespace": namespace(),
        # Recorded so a principal whose tenant does not appear here is TOLD so,
        # rather than being handed an empty result. A zero-row answer is not
        # proof that the portfolio is empty (section 7.7).
        "tenants": sorted(
            str(t) for t in release.frames[
                "cockpit_facility_quarter"]["tenant_id"].dropna().unique()),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "calendar": release.calendar.to_dict(),
        "relations": written,
    }
    (directory / MANIFEST).write_text(json.dumps(manifest, indent=2,
                                                 default=str))
    return manifest


def read_manifest(dataset_release_id: str) -> dict[str, Any]:
    path = release_dir(dataset_release_id) / MANIFEST
    if not path.exists():
        raise ReleaseNotFound(
            f"release {dataset_release_id!r} is not published in this runtime. "
            f"Run scripts/build_cockpit_agentic_v3.py to build it.")
    return json.loads(path.read_text())


def load_calendar(dataset_release_id: str) -> Calendar:
    manifest = read_manifest(dataset_release_id)
    block = manifest["calendar"]
    return Calendar(dataset_release_id=dataset_release_id,
                    slots=tuple(block["reporting_slots"]),
                    populated=tuple(block["populated_quarters"]),
                    data_cutoff=block.get("data_cutoff_at") or {})


def relation_path(dataset_release_id: str, relation: str) -> Path:
    path = release_dir(dataset_release_id) / f"{relation}.parquet"
    if not path.exists():
        raise ReleaseNotFound(
            f"{relation!r} is not published in release "
            f"{dataset_release_id!r}.")
    return path


def read_relation(dataset_release_id: str, relation: str) -> pd.DataFrame:
    return pd.read_parquet(relation_path(dataset_release_id, relation))


def releases() -> list[str]:
    base = root()
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir()
                  if (p / MANIFEST).exists())


def report() -> dict[str, Any]:
    from backend.config import settings

    return {
        "enabled": bool(settings.cockpit_agentic_v3),
        "namespace": namespace(),
        "root": str(root()),
        "releases": releases(),
        "forbidden_directories": list(FORBIDDEN),
        "note": ("A Git branch is not runtime isolation. Every V3 path "
                 "carries the namespace and a write outside it is refused "
                 "before anything happens."),
    }


__all__ = ["FORBIDDEN", "MANIFEST", "ReleaseNotFound", "UnsafeTarget",
           "check_target", "load_calendar", "read_manifest", "read_relation",
           "relation_path", "release_dir", "releases", "report", "root",
           "write"]
