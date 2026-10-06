#!/usr/bin/env python3
"""Publish the two V4 analytical domains. Immutable, fingerprinted, Saudi.

    python3 scripts/cockpit_v4/seed_domains.py
    python3 scripts/cockpit_v4/seed_domains.py --domain retail
    python3 scripts/cockpit_v4/seed_domains.py --verify

A published release is final. This refuses to rewrite one unless
`--overwrite` is passed deliberately, because an analysis saved last week
names a release id and expects the numbers it saw.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from backend.cockpit_v4 import domains as dom
    from backend.cockpit_v4 import invariants, lake
    from backend.cockpit_v4.generate import corporate, retail

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", choices=list(dom.DOMAIN_IDS) + ["all"],
                        default="all")
    parser.add_argument("--overwrite", action="store_true",
                        help="Rewrite a published release. Deliberate only.")
    parser.add_argument("--verify", action="store_true",
                        help="Check published bytes against their fingerprint.")
    args = parser.parse_args()

    builders = {dom.CORPORATE: corporate.build, dom.RETAIL: retail.build}
    wanted = (list(dom.DOMAIN_IDS) if args.domain == "all"
              else [args.domain])

    if args.verify:
        failed = False
        for domain_id in wanted:
            release_id = dom.DEFAULT_RELEASES[domain_id]
            if not lake.exists(release_id):
                print(f"  {domain_id:<10} {release_id}  NOT PUBLISHED")
                failed = True
                continue
            ok = lake.verify(release_id)
            print(f"  {domain_id:<10} {release_id}  "
                  f"{'verified' if ok else 'FINGERPRINT MISMATCH'}  "
                  f"{lake.fingerprint(release_id)[:16]}")
            failed = failed or not ok
        return 1 if failed else 0

    for domain_id in wanted:
        release_id = dom.DEFAULT_RELEASES[domain_id]
        if lake.exists(release_id) and not args.overwrite:
            print(f"  {domain_id:<10} {release_id}  already exists -- "
                  f"immutable, nothing written")
            continue
        print(f"  {domain_id:<10} building {release_id} ...")
        build = builders[domain_id](release_id=release_id)

        # The gate runs BEFORE publication. A book with a negative ECL in it
        # is one a model will report faithfully and a reader will believe.
        findings = invariants.check(build)
        if findings:
            print(f"  REFUSING to publish {release_id}: it failed its own "
                  f"gates.")
            for finding in findings[:12]:
                print(f"    - {finding}")
            return 1

        manifest = lake.publish(build, overwrite=args.overwrite)
        rows = sum(manifest["row_counts"].values())
        print(f"  {domain_id:<10} published {release_id}")
        noun = {"quarterly": "quarters",
                "monthly": "months"}.get(manifest["reporting_frequency"],
                                         "periods")
        print(f"             {len(manifest['reporting_periods'])} {noun} "
              f"{manifest['reporting_periods'][0]}..{manifest['latest_period']}")
        print(f"             {len(manifest['relations'])} relations, "
              f"{rows:,} rows, "
              f"{sum(len(r['fields']) for r in manifest['relations'])} fields")
        print(f"             {manifest['geography_name']} · "
              f"{manifest['reporting_currency']} "
              f"{manifest['amount_scale']} · "
              f"{manifest['reporting_frequency']}")
        print(f"             fingerprint "
              f"{manifest['release_fingerprint'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
