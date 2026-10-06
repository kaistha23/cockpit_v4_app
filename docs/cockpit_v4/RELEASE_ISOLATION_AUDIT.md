# Release isolation audit — was the Saudi work contaminating?

**Yes. It was contaminating, in three places, and the worst one was silent.**

Audited from `56be9ce`; fixed at `9474f4a`.

---

## 1. Finding

### The contamination

`56be9ce` added a module-level `CURRENCY = "SAR"` to `precision.py` and wired
it into `service.load_release` as the fallback for a release whose manifest
does not declare a currency.

`v4-uat-20q-v1` does not declare one — the shared release writer has never
recorded currency — so selecting it reported **SAR million over INR
data**. Measured before the fix:

```
v4-uat-20q-v1   manifest reporting_currency=None  ->  catalog says SAR million
v4-saudi-20q-v1 manifest reporting_currency='SAR' ->  catalog says SAR million
```

Both releases reported the same thing, which is the signature of a constant
rather than a release-driven value. Nothing looked wrong, because **a silent
manifest is not a wrong manifest** — there was no error to notice.

Two further places carried the same assumption:

| Where | Effect |
| --- | --- |
| `execute_tool.claim_guide` | The worked example shipped `"SAR million"` with **every result packet**, whatever release was loaded. It would steer the analyst into declaring the wrong unit on every amount — and the unit is what the precision policy reads. |
| `scripts/cockpit_v4/stub_server.py` | The canned answer had `"SAR million"` baked in, so pointed at the INR release the browser suite published Saudi money over Indian data. |

### What was already isolated

- Catalog and coverage caches — keyed by `release_id`
- Attention cache — keyed by `(release_id, tenant_id)`
- Runs, artifacts, threads — all carry `release_id`
- The data itself — `saudi.localize()` runs at seed time against one
  release's frames and cannot reach another
- The canonical/display maths — classifies by the claim's unit string, never
  by a global

### The honest part

The *pre-existing* fallback was `"INR"`/`"crore"` — equally a hard-coded
default, which merely happened to match the data. Restoring it would have
reintroduced the same defect wearing a different flag. Both are gone.

## 2. Files changed

| File | Change |
| --- | --- |
| `backend/cockpit_v4/precision.py` | Removed `CURRENCY`/`AMOUNT_SCALE`/`MONEY_UNIT`. Added `money_unit(catalog)`. Added `crore`/`lakh` to the scale vocabulary. |
| `backend/cockpit_v4/service.py` | New `denomination()`: manifest → release data → nothing. No literal fallback. |
| `backend/cockpit_v4/execute_tool.py` | `StepResult.money_unit` from the run's catalog; `claim_guide` takes it as a parameter. |
| `scripts/cockpit_v4/stub_server.py` | Canned answer reads the selected release's unit. |
| `scripts/cockpit_v4/browser_evidence.py` | Passes `V4_RELEASE` to the browser suite. |
| `tests/cockpit_v4/browser/cockpit_v4.browser.mjs` | Currency assertions follow the selected release. |
| `tests/cockpit_v4/test_domain_and_numerics.py` | Asserts a *coherent* pair from the selected release, not a nationality. |
| `tests/cockpit_v4/test_saudi_localization.py` | Pinned to `v4-saudi-20q-v1` by id. Source scan exempts the two currency modules, which are held to a stricter rule instead. |
| `tests/cockpit_v4/test_math_pipeline.py` | The Saudi EAD regression declares which release it is about. |
| `tests/cockpit_v4/test_release_isolation.py` | **New.** 28 checks across both releases. |

Untouched since `56be9ce`: `derivation.py`, `finalization.py`,
`contracts.py`, the contract schemas.

## 3. `COCKPIT_V4_RELEASE_ID=v4-uat-20q-v1`

| Property | Result |
| --- | --- |
| Selectable | ✅ |
| Currency / scale | **INR crore** — from its own data, not a default |
| Data fingerprint | `724fe14954f7ea61` — identical to the pre-Saudi-round record |
| Relations | 11 |
| Latest quarter | 2026Q2, **11 sectors**, total EAD **20,720.3435** |
| Borrower names | Unchanged (*Aravali Chemicals*, *Bhavani Agro*, …) |
| Saudi names present | **0** |
| Claim guide unit | `INR crore` |
| V4 suite | **811 passed, 1 skipped** |
| Browser | Currency assertions pass; no SAR shown |

## 4. `COCKPIT_V4_RELEASE_ID=v4-saudi-20q-v1`

| Property | Result |
| --- | --- |
| Currency / scale | **SAR million** — from its manifest |
| Data fingerprint | `bdb0be4662d63207` |
| Latest quarter | 2026Q2, **12 sectors**, total EAD **40,599.1737** |
| Borrower names | Fictional GCC |
| `amounts_converted` | `false` |
| Claim guide unit | `SAR million` |
| V4 suite | **812 passed** |
| Browser | 46/46 |

## 5. The mathematical fix is unchanged

`git diff 56be9ce..HEAD` over `derivation.py`, `finalization.py`,
`contracts.py` and `contracts/` is **empty**.

Preserved and re-verified on both releases:

- Canonical/display separation
- `Decimal` quantization, `ROUND_HALF_UP`
- Derived claims, independently recomputed
- Answer evidence validation
- Table and chart validation
- M01–M15 publication

And proven release-neutral rather than assumed:

```
INR crore | SAR million | USD million | EUR bn
  40599.17    accepted     (canonical rounded to 2dp)
  40599.18    refused      (rounded the wrong way)
  40599.1699  refused      (merely rounds to the right figure)
```

## 6. Final HEAD

`9474f4a`

| Suite | Saudi release | UAT release |
| --- | --- | --- |
| V4 backend | 812 passed | 811 passed, 1 skipped |
| Release isolation | 28 passed | 28 passed |
| Frontend | 476 passed | — |
| Browser | 46/46 | currency checks pass |

### V3 and unrelated modules

Measured, not argued: the wider backend suite (excluding `cockpit_v4`, and
the `brain`/`legacy` collection errors that predate this work) was run before
and after, and the failing test IDs compared.

| | Tests | Passed | Failed | Errors | Skipped |
| --- | --- | --- | --- | --- | --- |
| Branch baseline | 9,668 | 6,764 | 412 | 176 | 2,316 |
| Branch now | 9,668 | 6,764 | 412 | 176 | 2,316 |

**Zero new failures, zero newly passing** — the two sets of failing IDs are
identical. Those 412 are the repository's existing condition (`main` itself
runs 409 over the same scope) and are not this round's to close.

**Known intermittent, unrelated to this work:** one browser test, *"the
seeded context load appears in the process panel"*, fails in roughly one full
run in three and passes in isolation and on re-run. It touches the process
panel, not currency. Not introduced by this round; recorded rather than
hidden.

## 7. Mac pull and start

```bash
cd ~/path/to/IPM_V2
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull --ff-only origin claude/cockpit-single-agent-v4-h8fsbq
# expect HEAD = 9474f4a
```

**Your existing release needs no reseed.** Its data is untouched and it now
reports its own INR crore.

```bash
# Keep using your existing UAT book
export COCKPIT_V4_RELEASE_ID=v4-uat-20q-v1
./scripts/cockpit_v4/START_COCKPIT_V4.command
```

To try the Saudi book instead — it is a **separate** release and building it
does not alter the UAT one:

```bash
python3 scripts/cockpit_v4/seed_release.py --release v4-saudi-20q-v1
export COCKPIT_V4_RELEASE_ID=v4-saudi-20q-v1
./scripts/cockpit_v4/START_COCKPIT_V4.command
```

Verify which book is loaded before trusting a figure:

```bash
curl -s localhost:8414/api/v1/cockpit-v4/diagnostics | python3 -m json.tool | grep -i release
```

Live UAT needs `ANTHROPIC_API_KEY` exported in the shell that starts the API.
Nothing in this session asked for a key, used one, or made a paid call.
