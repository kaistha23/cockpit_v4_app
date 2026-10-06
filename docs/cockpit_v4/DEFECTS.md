# Cockpit V4 — open defect log

Defects found on this branch that are **not yet fixed**. Each entry records
what was observed, what was proved, what was *not* proved, and what a fix
has to include. A defect leaves this file only when it has a fix and a
regression test that fails without it.

---

## D-001 · `seed_release.py` rebuilds an existing release instead of refusing

| | |
|---|---|
| **Status** | OPEN — not fixed |
| **Severity** | P2 |
| **Found** | 2026-09-12, incidentally, while running `pytest tests/cockpit_v4/` |
| **Failing test** | `tests/cockpit_v4/test_launcher_safety.py::test_seeding_an_existing_release_does_not_overwrite_it` |
| **Requirement violated** | `MASTER_BUILD_SPEC.md` §669 — *"Launcher cannot stop any unrelated process or overwrite a release."* Acceptance id **V4-AT-006** ("Immutability, checked rather than assumed") |

### What happens

Running the V4 test suite invokes `scripts/cockpit_v4/seed_release.py` for the
already-published release `v4-uat-20q-v1`. Instead of printing *"already
exists … immutable"* and stopping, the script **rebuilds the release**:

```
Building v4-uat-20q-v1: 120 borrowers, 280 facilities, ending 2026Q2
  published to /home/user/IPM_V2/data/cockpit_v...
  relations: 11
  quarters:  20 (2021Q3..2026Q2)
  evidence written to /home/user/IPM_V2/docs/cockpit_v4/evidence
```

The test asserts `"already exists" in out.stdout` and fails on that line.

### Why it matters beyond the red test

The rebuild has a side effect outside the test sandbox: it **rewrites four
tracked files in the repository**.

```
 M docs/cockpit_v4/evidence/coverage_summary.json
 M docs/cockpit_v4/evidence/performance.json
 M docs/cockpit_v4/evidence/release_manifest.json
 M docs/cockpit_v4/evidence/release_summary.json
```

The row counts change materially, so this is not timestamp churn:

| relation | committed | after an accidental rebuild |
|---|---:|---:|
| `cockpit_facility_quarter` | 1,655 | 3,163 |
| `cockpit_ifrs9_detail` | 5,415 | 10,440 |
| `cockpit_borrower_financial_quarter` | 1,010 | 1,936 |
| `cockpit_qualitative_quarter` | 20,200 | 38,720 |
| `cockpit_collateral_allocation` | 4,440 | 9,180 |

So **running the test suite silently changes the committed evidence for the
release that every oracle and benchmark is measured against.** Any number
quoted from those files after a suite run is a number from a different
dataset than the one on the branch. That is the real cost of this defect —
the red test is only how it announces itself.

### What was established

Resolving the guard's own path, read-only, outside pytest:

```python
COCKPIT_AGENTIC_V3=true COCKPIT_AGENTIC_V3_NAMESPACE=cockpit_v4
store.release_dir('v4-uat-20q-v1')
#  -> /home/user/IPM_V2/data/cockpit_v4/v4-uat-20q-v1
#  exists -> True
```

The guard at `seed_release.py:52` is `if target.exists() and not args.overwrite`.
With that resolution it **would** fire. `COCKPIT_V4_TEST_RELEASE` is unset in
this environment, so the test passes the same default id, `v4-uat-20q-v1`.

So the guard's logic and its default path resolution are **not** obviously
wrong, and a fix that only rewrites the condition is likely to be the wrong
fix.

### What was NOT established

**Why the guard does not fire inside the test's subprocess.** This has not
been root-caused and must be before anything is changed. Two candidates, both
unverified:

1. **Environment divergence.** The test launches with
   `env={**os.environ, "COCKPIT_AGENTIC_V3_NAMESPACE": "cockpit_v4"}`, where
   `os.environ` is pytest's, possibly already mutated by a fixture. `root()`
   is `Path(settings.analytics_dir).parent / namespace()`, so anything that
   moves `analytics_dir` moves the guard's target — and a target that does
   not exist is a target the guard waves through.
2. **Settings caching order.** `seed_release.py` sets the namespace env vars
   at lines 41–43 and imports `backend.cockpit_agentic` at line 45, relying on
   `backend.config` reading them at import time. If `backend.config.settings`
   is already materialised in that interpreter, the namespace applied to
   `root()` is the stale one.

Note these pull in opposite directions: (1) says the guard looked in the wrong
place, (2) says the same. The printed output says the rebuild published to a
path under the real `/home/user/IPM_V2/data/cockpit_v...`, which is *not*
obviously consistent with either. That contradiction is exactly why this is
recorded as not-root-caused rather than half-diagnosed.

### What a fix must include

1. A root cause, established by reproducing the guard's path resolution
   **inside the failing subprocess** — not inferred from reading the code.
2. The immutability guard holding regardless of how `analytics_dir`,
   the namespace env, or settings caching are arranged. A published release
   must be refused from any caller.
3. A regression test that **fails without the fix**, asserting both halves:
   the refusal (`returncode == 0`, `"already exists"`, `"immutable"`) **and**
   that `docs/cockpit_v4/evidence/*.json` are byte-identical before and
   after — because the file mutation is the part that actually costs
   something, and the current test does not check it.
4. Consideration of whether the evidence files should be written by a seed
   script at all, or only by an explicit `--overwrite` / publish step.

### Provenance

Found during an accidental run of the V4 suite; the four modified files were
restored with a working-tree `git checkout --`, not a branch reset, and the
rebuild was never committed. The defect itself pre-dates that run and is
present at `b4ac6aa`.

---

## D-002 · The result-only browser journey times out under Midnight, twice in six runs

| | |
|---|---|
| **Status** | OPEN — not root-caused, observability improved |
| **Severity** | P2 |
| **Found** | 2026-09-20, Round H, while regenerating themed browser evidence |
| **Failing test** | `tests/cockpit_v4/browser/cockpit_v4.browser.mjs` — *"a result survives an answer that could not be written"* |

### What happens

The browser journey that exercises the result-only channel — a run whose
query executes and whose write-up then fails, so the rows must reach the
reader without a narrative — waits the full 120 s for
`[data-testid="v4-response"]` and then fails. The test takes **151 s**
instead of its usual **5.8 s**: a stall, not a marginal overrun.

### What was measured, not assumed

Thirteen full browser runs in one session, same code, same machine (the
default-theme runs counted here are the six after the unrelated thread-title
fix earlier in this round; the one before it failed a different test):

| Theme | Full-suite runs | Result |
|---|---|---|
| default (Executive Light) | 6 | 76/76 every time |
| `porcelain` — themed code path, light palette | 1 | 76/76 |
| `midnight` | 6 | **4 × 76/76, 2 × 75/76** |
| `midnight`, this test alone | 2 | passes |
| default, this test alone | 2 | passes |

So it is not the test in isolation, it is not the themed code path as such
(`porcelain` seeds `localStorage` through exactly the same helper), and it
is not run ordering — two default runs back to back both passed 76/76,
which was the first hypothesis and the one that was disproved.

It correlates with `midnight` and with the full suite together. In the two
failures the suite's total wall time roughly doubled (154 s → 301 s), but
no other test was slower by more than a few hundred milliseconds. One test
absorbed all of it.

### What is NOT established

The cause. Specifically, it is not known whether the run never produced an
answer, or produced one the page rendered with no box — and those call for
investigations in completely different halves of the system.
`waitForSelector` waits for VISIBLE, so the old message, "never appeared",
covered both and distinguished neither.

Nothing was widened, skipped or retried to get past this. The timeout is
still 120 s and the test is still required.

### What was done instead

`expect()` now reports, on timeout, whether the selector is absent from the
DOM or present with its box, `display`, `visibility` and `opacity` — and
the `data-theme` in force. The next occurrence will say which half to look
in. It has not reproduced in the five runs since (three full `midnight`,
two isolated), so the improved message has not yet been exercised against
a real failure.

### What a fix must include

1. A reproduction that captures the new diagnostic, so the question above
   is answered before any code changes.
2. If the run did not answer: which stage stalled, from the run's own event
   stream, not from the page.
3. If the answer rendered invisibly: the rule that did it, and why it
   applies under `midnight` and not under `porcelain` — the two differ only
   in palette and in whether `@custom-variant dark` matches.
4. A regression test that fails without the fix. A test that merely passes
   more often is not one.

---

## D-003 · The session cache evicts the session it is about to return

| | |
|---|---|
| **Status** | OPEN — deliberately not fixed in the first-live-UAT repair round |
| **Severity** | P1 by consequence, narrow by reach |
| **Found** | 2026-09-21, while running a targeted batch of five V4 test modules |
| **Failing test** | `tests/cockpit_v4/test_release_history.py::test_a_pinned_thread_replays_its_own_sql_against_its_own_release[v4-saudi-retail-20m-v4]`, in a batch that opens five or more distinct releases. Passes in isolation. |
| **Out of scope because** | It predates this round's work and is not a defect the first live UAT demonstrated. It is logged rather than repaired on instruction. |

### What happens

`backend/cockpit_v4/catalog.py:430-434`:

```python
_SESSIONS[key] = session
while len(_SESSIONS) > MAX_CACHED_SESSIONS:     # = 4
    _, evicted = _SESSIONS.popitem()
    evicted.close()
return session
```

`dict.popitem()` removes the **most recently inserted** entry, and the entry
just inserted is `session` itself. So once four distinct
`(tenant, domain, release, fingerprint)` keys are cached, every subsequent
`open_session` builds a session, caches it, immediately evicts and closes
it, and hands the caller a dead DuckDB connection. The caller's first query
raises:

```
_duckdb.ConnectionException: Connection Error: Connection already closed!
```

### What was proved

Through the real `open_session`, with `_build_session` stubbed so that only
the caching code runs:

```
open release-0: returned session closed = False   cached = 1
open release-1: returned session closed = False   cached = 2
open release-2: returned session closed = False   cached = 3
open release-3: returned session closed = False   cached = 4
open release-4: returned session closed = True    cached = 4
open release-5: returned session closed = True    cached = 4
```

It is pre-existing: stashing every edit and re-running the identical batch
on a clean `ac315dd` reproduces it exactly (`1 failed, 76 passed`).

The sibling cache does it correctly — `analytical_runtime.py:198` is
`_CACHE.pop(next(iter(_CACHE)))`, which pops the **oldest**. `catalog.py` is
the one that pops the newest.

### What was NOT proved

That it can be reached in the deployed product. A deployment serving more
than four distinct `(tenant, domain, release, fingerprint)` combinations
would reach it; the two-book Cockpit with one tenant holds two keys. The
first live UAT was never at risk: the approved queue opens two session keys
against a cap of four.

### What a fix must include

1. Eviction of the **oldest** entry, not the newest, and a note saying why
   the two caches must agree.
2. A test that opens `MAX_CACHED_SESSIONS + 2` distinct sessions through the
   real `open_session` and asserts the returned session is usable.
3. A test that the evicted session IS closed, so the fix does not trade a
   dead connection for a leaked one.
4. Whether `_SESSIONS.get` should move the key to the end — the cache is
   described as an LRU and is not one. Decide it explicitly.
