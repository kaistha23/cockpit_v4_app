"""
Compile AND EXECUTE every worked example against the LIVE releases.

WHY a script and not a unit test: unit tests run under the fixture conftest,
which pins a different release (sector_name, product_type, no stage/region).
The examples teach the model the LIVE vocabulary, so only the live lake can
say whether they are true. Read-only: the lake is opened, never written.

Exit 1 on any compile error, execution error, or empty result.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

if os.environ.get("COCKPIT_V4_TEST_RELEASE"):
    sys.exit("refusing to run under the test fixtures (COCKPIT_V4_TEST_RELEASE is set)")

from backend.cockpit_v4 import catalog as cat  # noqa: E402
from backend.cockpit_v4 import examples as ex  # noqa: E402
from backend.cockpit_v4 import lake  # noqa: E402
from backend.cockpit_v4 import plan_compiler as pc  # noqa: E402


def main() -> int:
    print(f"lake: {lake.root()}")
    books, sessions = {}, {}
    failures = 0
    for example in ex.load():
        domain = example["domain"]
        if domain not in books:
            catalog = cat.build(domain_id=domain)
            sessions[domain] = cat.open_session(catalog=catalog)
            books[domain] = pc.build_book(domain_id=domain, catalog=catalog,
                                          session=sessions[domain])
            print(f"{domain}: {catalog.dataset_release_id}")
        try:
            steps, _ = pc.compile_plan(example["plan"], books[domain])
            counts = []
            for step in steps:
                rows = sessions[domain].connection.execute(step["code"]).fetchall()
                counts.append(len(rows))
                if not rows:
                    raise RuntimeError("query returned no rows")
            print(f"OK    {example['id']:<18} {domain:<10} rows={counts}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {example['id']:<18} {domain:<10} {type(exc).__name__}: {str(exc)[:200]}")
    print(f"{len(ex.load()) - failures}/{len(ex.load())} examples compile and execute")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
