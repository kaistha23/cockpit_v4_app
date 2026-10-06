# Cockpit V4 — rounding fixed, and the demonstration made Saudi

Branch `claude/cockpit-single-agent-v4-h8fsbq`. No pull request, no merge, no
V3 module modified, no paid provider call.

**Verdict: MATHEMATICAL QUERY PUBLICATION — READY FOR LIVE UAT.** §19 states
precisely what that covers and what it does not.

---

## 1. HEAD

| | |
| --- | --- |
| Started from | `08f7e67` (verified against remote before any change) |
| Now at | `559f8df` + this report |

## 2. The live rounding root cause

```
asserted   40599.17
canonical  40599.1736630513815
```

`40,599.17` is how a credit officer writes that number, and the analysis
behind it was correct — understood, EAD mapped, quarter resolved, SQL bound,
executed, twelve rows.

**Root cause: `finalization._close()` compared the two at a relative
tolerance of 1e-9.** I introduced that bound last round with a comment saying
it "absorbs a decimal string the analyst rounded". It does not. Rounding five
significant digits to two places moves the figure by **9.02e-8** relative —
ninety times the bound. The comment asserted something the arithmetic never
supported, and a test now measures the gap rather than restating the claim.

Widening the tolerance was the wrong fix and is refused explicitly: at 1e-6 a
figure wrong by more than SAR 0.04 million passes on this book. Presentation
would have been bought with arithmetic.

## 3. `mfg_vs_it_ticket`, diagnosed rather than assumed

The brief is right that this must not be assumed to be rounding. It is a
**ratio of two average ticket sizes**, and two failure modes look identical in
a log:

| Case | Verdict |
| --- | --- |
| Display rounded to 2dp | **Passes** — presentation, not arithmetic |
| Denominator is IT's *total* rather than its *average ticket* | **Still fails** |
| Ratio inverted | **Still fails** |

All three are pinned as tests. A ratio may carry 2–4 decimals where money
carries 2, because a ratio loses real information at two.

## 4. Precision policy

Full policy: `docs/cockpit_v4/DISPLAY_PRECISION.md`.

> **Superseded.** The permitted sets below are what this round shipped. A
> later round narrowed every governed class to exactly its default, because
> an overridable money precision let one answer print `SAR 7,013.12 million`
> in prose above a table reading `SAR 7,013 million`. See
> `DISPLAY_PRECISION.md` for the policy in force.

| Unit class | Permitted | Default |
| --- | --- | --- |
| Money (`SAR million`) | 0, 1, 2, 3 | 2 |
| Percent | 0, 1, 2, 3 | 2 |
| Percentage point | 0, 1, 2, 3 | 2 |
| Ratio | 2, 3, 4 | 2 |
| Count | 0 | 0 |
| Categorical | 0 | — |

Precision is read from the **unit**, never a claim's name. The first thing it
caught was real: a covenant-breach count declaring two decimal places, which
the old validator had no opinion about.

## 5. Canonical / display contract

```
expected_display = quantize(canonical, allowed_precision)   # ROUND_HALF_UP
asserted ∈ { canonical, expected_display }
```

Everything is `Decimal`; no binary float is used to validate money. A number
that merely *rounds* to the right answer (`40599.1699` at 2dp) is refused —
rounding to something correct is not being it. The published figure is
rendered from the canonical verdict, not the analyst's string.

## 6. New Saudi release

`v4-saudi-20q-v1` — 20 quarters, 2021Q3..2026Q2, same fields, same integrity
gates, 11 relations, fingerprint `bdb0be4662d63207…`. The old release is
untouched; the seeder still refuses to overwrite a published release.

`--refresh-evidence` was added for the case this round actually needed:
correcting a *description* of a release without rebuilding its data.

## 7. Saudi data generation

The generator is shared with V3 and is **left alone**. All localization lives
in `backend/cockpit_v4/saudi.py` and runs *after* a release is built, so
nothing here can reach a V3 release.

**Nothing is FX-converted.** Multiplying fictional rupees by a rate would
manufacture economic meaning that was never in them, with an implied rate a
reader could ask about and nobody could defend. The amounts are read as Saudi
amounts. A test asserts localization moves **no number**, column by column,
and that localizing twice changes nothing.

## 8. Currency and scale

- Reporting currency **SAR**, canonical scale **million**, style `SAR 40,599.17 million`.
- The book totals ~SAR 40.6 billion of exposure — a credible mid-size Saudi
  corporate portfolio.
- Scale is read, never assumed: `SAR million` and `SAR bn` differ a
  thousandfold and the pair is checked together.

**The leak that mattered was a default.** The shared release writer records no
currency, so `service.load_release` fell back to `"INR"`/`"crore"` whenever
the manifest was silent. The manifest now carries SAR million and the fallback
is the Saudi one.

## 9. Borrower names

Fictional GCC corporate names, derived by hash from the borrower id so
reseeding reproduces them exactly (e.g. *Al Ahsa Contracting*, *Red Sea
Trading Company*, *Jubail Petrochemical Industries*). 104 distinct names
across 120 borrowers. Bhavani, Yamuna, Aravali, Deccan, Narmada, Sahyadri and
Wardha are asserted gone. No real Saudi company is used.

## 10. Repository INR / crore audit

| Surface | Occurrences |
| --- | --- |
| `backend/cockpit_v4` | 0 user-facing (4 in `saudi.py`, which must name what it replaces; 1 explanatory comment) |
| `frontend/src/components/cockpit-v4` | **0** |
| `scripts/cockpit_v4` | **0** |
| `tests/cockpit_v4` | 0 user-facing (guard definitions only) |
| `docs/cockpit_v4` | **0** |

A test walks every V4 source file and fails on any non-comment line
containing `INR`, `crore` or `₹`. V3 and unrelated modules were **not**
touched (Part 28).

## 11. EAD regression evidence

The live question, against the Saudi book:

- **12 sectors** — the same count the live run returned
- Total **SAR 40,599.17 million**, top sector Information Technology at 17.27%
- Table ✓, ranked bar chart ✓, quarter stated ✓
- **2 generations, no answer-repair round**
- The total is a `sum` derivation over the **twelve real rows** — not a
  pointer to an invented "all sectors" row
- No `INR`/`crore`/`₹` anywhere in the published answer

## 12. Deliberately wrong rounding — still refused

Each sent back for correction, end to end:

| Wrong value | Why |
| --- | --- |
| `40599.18` | rounded the wrong way |
| `40599.1699` | merely rounds to the right figure |
| `40.60` | restated in billions without dividing |
| `0.8361` for `top_share` | a percentage sent as a proportion of one |

Plus, at the unit level: percent vs percentage point, ratio vs percent,
counts with decimals, and a thousandfold scale error.

## 13. M01–M15 publication

**15/15 published**, two generations each, zero catalogue calls.

| | State | Gens | Claims | Chart |
| --- | --- | --- | --- | --- |
| M01–M04 | COMPLETED | 2 | 2 | bar |
| M05 | COMPLETED | 2 | 2 | — |
| M06 | COMPLETED | 2 | 2 | bar |
| M07 | COMPLETED | 2 | 0 | — |
| M08, M09 | COMPLETED | 2 | 2 | bar |
| M10 | COMPLETED | 2 | 1 | — |
| M11, M12 | COMPLETED | 2 | 4 | — |
| M13 | COMPLETED | 2 | 0 | — |
| M14 | COMPLETED | 2 | 2 | — |
| M15 | COMPLETED | 2 | 1 | bar |

**The harness now sends rounded business values**, and a test proves it:
every claim carries no more decimals than it declares. Sending machine
precision made the previous round's tests pass for the wrong reason — it
showed the validator accepts its own arithmetic verbatim and said nothing
about whether `SAR 40,599.17` publishes, which is the exact figure the live
run was refused for.

M07 and M13 return **no rows** on this book — no sector matches those
conditions. That is a finding, not a failure: the answer says so plainly with
the query's own columns, and publishes no chart.

Performance: end-to-end M01 **37.8 ms**, recomputing a 250-cell derivation
**0.18 ms**.

## 14. Attention and ECL feeds

Recomputed against the Saudi release, deterministic ranking preserved, no
model call on page load. All figures SAR; borrower names GCC.

One finding: **no sector's ECL rose** from 2026Q1 to 2026Q2 on this book —
every one fell. The engine correctly omits its "largest riser" highlight. The
test had assumed one always exists; it now asserts the engine does **not**
dress the least-bad fall up as a rise.

## 15. Product Help

Audited and already Saudi-framed: **0** mentions of INR, crore, rupee or
India; 5 mentions of SAR, which are the deck's own. The synthetic-demo caveat
is preserved and asserted. No claim of a real Saudi bank portfolio.

## 16. Browser

**46/46 real Chromium**, read off the *rendered page* rather than the source —
because the source was already clean while the runtime still fell back to INR.

- No page shows `INR`, `crore`, `lakh`, `₹` or `rupee`
- Attention cards, ECL highlights and the drawer read in SAR with GCC names
- A published answer, its table and its chart agree
- No horizontal overflow at 1728 / 1440 / 1280 / 834 / 390
- Wide workspace preserved (1429px of 1728px)

Screenshots: `docs/cockpit_v4/evidence/cockpit_v4_landing.png`,
`cockpit_v4_answer.png`.

## 17. Test counts

| Suite | Result |
| --- | --- |
| V4 backend | **787 passed**, 0 failed |
| Frontend (`node --test`) | **476 passed**, 0 failed |
| Real Chromium | **46 passed**, 0 failed |
| TypeScript | clean |

New this round: 24 display-precision, 17 Saudi-localization, and additions to
the math pipeline (59) and derived claims (24).

## 18. V3 regression

V3 and unrelated modules were not modified. The shared generator, catalog and
store are untouched; every Saudi change is a V4 module operating after a
release is built — which is the property that lets this round relabel a V4
release without touching a V3 one.

Measured rather than argued. The wider backend suite (excluding
`cockpit_v4`, and the `brain`/`legacy` collection errors that predate this
work) was run before and after, and the failing test IDs compared:

| | Tests | Passed | Failed | Errors | Skipped |
| --- | --- | --- | --- | --- | --- |
| Branch baseline, before this round | 9,668 | 6,764 | 412 | 176 | 2,316 |
| Branch now | 9,668 | 6,764 | 412 | 176 | 2,316 |

**Zero new failures, and zero tests newly passing.** Comparing IDs rather
than counts, the two sets are identical — 588 failing before, the same 588
after, with no additions and no removals.

Those 588 are the repository's existing condition, not this round's: `main`
itself runs 409 failures and 176 errors over the same scope, and the three
extra on this branch were traced last round to source-scanning guards tripped
by earlier Cockpit V2 and V4 commits. They remain open and are not this
round's to close.

## 19. Verdict

**MATHEMATICAL QUERY PUBLICATION: READY FOR LIVE UAT.**

Against the acceptance conditions:

- ✅ Realistic rounded M01–M15 answers all publish (15/15)
- ✅ Wrong rounded values fail (four end-to-end, plus unit-level scale errors)
- ✅ All amounts SAR
- ✅ No V4 user-facing INR/crore remains — asserted over source *and* rendered page
- ✅ Saudi release stable, fingerprinted, immutability guard intact
- ✅ Product Help Saudi-appropriate
- ✅ Attention/ECL feeds Saudi-localized

**Not established, and not claimable from here:** that Opus, unprompted,
sends business-rounded values with correctly declared units. The contract is
now stated in the result packet, the tool schema and the correction packet,
and a first attempt that gets it wrong is caught and told the expected display
value — but the analyst turn is scripted, so what is proven is that the
machinery accepts a correct business figure and refuses a wrong one.

Answer prose quality remains unscored, unchanged from previous rounds. This
verdict covers the mathematical publication path and the Saudi localization;
it is not a statement that the whole Cockpit is commissioned.

## 20. Mac pull, reseed, start

```bash
cd ~/path/to/IPM_V2
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull --ff-only origin claude/cockpit-single-agent-v4-h8fsbq

# Build the Saudi release (new id; the old one is left alone)
python3 scripts/cockpit_v4/seed_release.py --release v4-saudi-20q-v1
# expect: "localized: SAR million, Saudi Arabia, fictional GCC borrower names"
#         "currencies: SAR, USD"   "quarters: 20 (2021Q3..2026Q2)"

# Start
./scripts/cockpit_v4/START_COCKPIT_V4.command
#   status: python3 scripts/cockpit_v4/status.py
#   stop:   python3 scripts/cockpit_v4/stop.py
```

Live UAT needs `ANTHROPIC_API_KEY` exported in the shell that starts the API.
**Do not put it in a file in the repository.** Nothing in this session asked
for a key, used one, or made a paid call.

First question, because it is the one that failed:

> What is total exposure at default by sector in the latest quarter?

Expect twelve sectors, a table, a ranked bar chart, and a total of
**SAR 40,599.17 million** — arriving as a sum derivation over the twelve real
rows. Check against `tests/cockpit_v4/math_bank.py::oracle("M01")`, which is
in the repo.

If a figure is still refused, the rejection now names the expected display
value; that message is the fastest diagnosis available.
