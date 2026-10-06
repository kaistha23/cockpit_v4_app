#!/usr/bin/env python3
"""Publish an isolated V4 synthetic release. Never overwrites another one.

V4 does not need its own data domain -- it uses the V3 corporate_cockpit
generator unchanged, which is the point: the domain is preserved and only the
orchestration changed. What V4 needs is its OWN namespace, so a V4 build
cannot write over a release a running V3 or demo instance is serving.

Usage:
    python3 scripts/cockpit_v4/seed_release.py --release v4-uat-20q-v1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _refresh_evidence(args, target, store, profile, saudi) -> int:
    """Rewrite docs evidence from a published release, changing no data."""
    import pandas as pd

    if args.no_evidence:
        print(f"  {args.release} is not the demonstration release the docs "
              f"describe; nothing was rewritten")
        return 0

    manifest = store.read_manifest(args.release)
    frames = {path.stem: pd.read_parquet(path)
              for path in sorted(target.glob("*.parquet"))}
    evidence = ROOT / "docs" / "cockpit_v4" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    summary = {
        "dataset_release_id": args.release,
        "reporting_periods": manifest["calendar"]["reporting_slots"],
        "rows": {name: int(len(f)) for name, f in frames.items()},
        "columns": {name: int(f.shape[1]) for name, f in frames.items()},
        "not_client_data": manifest.get("not_client_data"),
    }
    if not args.no_localize:
        summary.update(saudi.manifest_overrides())
    (evidence / "release_summary.json").write_text(
        json.dumps(summary, indent=2, default=str))
    (evidence / "release_manifest.json").write_text(
        json.dumps({"release": args.release, "namespace": args.namespace,
                    "relations": manifest["relations"]},
                   indent=2, default=str))
    print(f"  evidence refreshed for {args.release}; data untouched")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default="v4-saudi-20q-v1")
    parser.add_argument("--namespace", default="cockpit_v4",
                        help="Runtime namespace. Never the V3 one.")
    parser.add_argument("--borrowers", type=int, default=120)
    parser.add_argument("--facilities", type=int, default=280)
    parser.add_argument("--last-quarter", default="2026Q2")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--refresh-evidence", action="store_true",
                        help="Rewrite the evidence files for an already "
                             "published release. Does not touch the data.")
    parser.add_argument("--no-evidence", action="store_true",
                        help="Do not rewrite docs/cockpit_v4/evidence. Set "
                             "automatically for any release other than the "
                             "canonical demonstration one.")
    parser.add_argument("--no-localize", action="store_true",
                        help="Publish the generator's own currency and names "
                             "unchanged. For comparison only; the V4 "
                             "demonstration is Saudi.")
    args = parser.parse_args()

    # The evidence documents under docs/ describe THE demonstration release.
    # Seeding any other one used to rewrite them, so a probe release of
    # twenty borrowers would be published in the docs as the book -- a
    # reader of release_summary.json would have been told something false
    # about the release they were actually running.
    from backend.cockpit_v4 import release as release_mod

    if args.release != release_mod.DEFAULT_RELEASE_ID:
        args.no_evidence = True

    if args.namespace.strip() in ("", "cockpit_agentic_v3"):
        print("Refusing to seed into the V3 namespace. V4 uses its own.",
              file=sys.stderr)
        return 2

    # These are read by backend.config at import time, so they are set first.
    os.environ["COCKPIT_AGENTIC_V3"] = "true"
    os.environ["COCKPIT_AGENTIC_V3_NAMESPACE"] = args.namespace

    from backend.cockpit_agentic import generate, profile, store, validate_data
    from backend.cockpit_v4 import saudi

    target = store.release_dir(args.release)
    if args.namespace not in str(target):
        print(f"Refusing: {target} is not inside the V4 namespace.",
              file=sys.stderr)
        return 2
    if target.exists() and not args.overwrite:
        if args.refresh_evidence:
            # The release itself is untouched -- only the evidence FILES
            # under docs/ are rewritten from what is already published. This
            # exists so a published release never has to be rebuilt just to
            # correct a description of it.
            return _refresh_evidence(args, target, store, profile, saudi)
        print(f"Release {args.release} already exists at {target}. A "
              f"published release is immutable; pass --overwrite "
              f"deliberately or choose a new id.")
        return 0

    print(f"Building {args.release}: {args.borrowers} borrowers, "
          f"{args.facilities} facilities, ending {args.last_quarter}")
    release = generate.build_release(
        dataset_release_id=args.release, last_quarter=args.last_quarter,
        borrowers=args.borrowers, facilities=args.facilities)

    # The V4 demonstration book is a SAUDI corporate portfolio. The generator
    # is shared with V3 and is left alone; the release is localized after it
    # is built, which is why nothing here can change a V3 release.
    #
    # No amount is converted. Multiplying fictional figures by an exchange
    # rate would manufacture economic meaning that was never in them, with an
    # implied rate a reader could ask about and nobody could defend. The
    # numbers are read as Saudi amounts in SAR million.
    if not args.no_localize:
        release.frames = saudi.localize(release.frames)
        print(f"  localized: {saudi.CURRENCY} {saudi.AMOUNT_SCALE}, "
              f"{saudi.GEOGRAPHY_NAME}, fictional GCC borrower names")

    conformance = generate.conform(release)
    report = validate_data.validate(release)
    failures = [c for c in report.get("checks", [])
                if not c.get("ok") and c.get("severity") == "error"]
    if failures:
        print("Refusing to publish a release that fails an integrity gate:")
        for check in failures:
            print(f"  - {check.get('name')}: {check.get('detail')}")
        return 1

    coverage = profile.profile_release(release)
    manifest = store.write(release, overwrite=args.overwrite)

    # The manifest is where a V4 runtime reads its currency from, and the
    # shared writer does not record one -- so `service.load_release` fell
    # back to the generator's SAR and a Saudi demonstration reported million.
    if not args.no_localize:
        manifest.update(saudi.manifest_overrides())
        (target / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str))
        audit = saudi.audit(release.frames)
        if audit["leaks"]:
            print("Refusing to publish: the localized release still carries "
                  "India-specific money labels:")
            for leak in audit["leaks"]:
                print(f"  - {leak}")
            return 1
        print(f"  currencies: {', '.join(audit['currencies'])}")
        print(f"  countries:  {', '.join(audit['countries'])}")
        print(f"  borrowers:  {len(audit['borrower_names'])} fictional "
              f"GCC names, e.g. {audit['borrower_names'][0]}")
    print(f"  published to {target}")
    print(f"  relations: {len(manifest['relations'])}")
    print(f"  quarters:  {len(release.calendar.slots)} "
          f"({release.calendar.slots[0]}..{release.calendar.slots[-1]})")

    if args.no_evidence:
        print(f"  evidence not rewritten: {args.release} is not the "
              f"demonstration release the docs describe")
        return 0

    evidence = ROOT / "docs" / "cockpit_v4" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "release_manifest.json").write_text(
        json.dumps({"release": args.release, "namespace": args.namespace,
                    "relations": manifest["relations"],
                    "conformance": conformance},
                   indent=2, default=str))
    # `Release.summary()` reads the shared generator's own constants, which
    # are V3's and are left alone. Without this the published evidence for a
    # Saudi release still declared INR crore.
    summary = release.summary()
    if not args.no_localize:
        summary.update(saudi.manifest_overrides())
    (evidence / "release_summary.json").write_text(
        json.dumps(summary, indent=2, default=str))
    (evidence / "coverage_summary.json").write_text(
        json.dumps(profile.outline(coverage), indent=2, default=str))
    print(f"  evidence written to {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
