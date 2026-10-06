# The Cockpit home feed: method

Two sections on the Cockpit V4 home page — **Segments requiring attention**
and **Latest-quarter ECL highlights** — computed by
`backend/cockpit_v4/attention.py` from the pinned release, deterministically,
with no model call.

Independently re-implemented in pandas by
`tests/cockpit_v4/attention_oracle.py`, which imports nothing from the engine.
`test_attention_feed.py` asserts the two agree item for item and figure for
figure.

## Two dashboards, two questions

They are separate feeds answering separate questions, and nothing appears in
both.

| | Question | Scope of every card |
|---|---|---|
| **Segments requiring attention** | What deteriorated, by segment, between two reporting quarters? | `segment` only |
| **Latest-quarter ECL highlights** | What moved in ECL this quarter? | `segment`, `borrower` or `portfolio` |

Every item carries an explicit `scope`, declared rather than inferred from its
dimension. `check_composition()` refuses a feed in which the segment list
holds anything that is not segment-scoped, in which an item id appears in both
lists, or in which the same headline appears twice.

The invariant lives in the engine because the defect was a rendering decision:
a tab that merged the two feeds put "Information Technology carries the most
ECL in the book", a single borrower and a book-wide stage-mix line into the
segment list, and then showed all of them again below. A rule that only lives
in a component is one the next component can break again.

The tab is gone. Both dashboards are on the page; merging them served no
purpose that separating them does not serve better.

## What it is, and is not

| | |
|---|---|
| Question | "What deteriorated in the **recorded** book?" |
| Grain | sector × reporting quarter |
| Sources | `cockpit_facility_quarter`, `cockpit_rating_ratio_quarter`, `cockpit_covenant_quarter` — all inside the pinned release, read through the same read-only DuckDB session an analysis uses |
| Model calls | **zero**, asserted by a test |
| Not | Early Warning. No live signal, no behavioural score, no EWS output, no prediction. Cockpit reports movement between two reporting dates; anything emerging or forward-looking belongs to Early Warning and is not read here. |

## Candidate indicators

Every indicator declares a direction, a unit, and the movement that counts as
a full-severity move. `full_move` is a **scale**, not a threshold: it is what
makes a stage-share move, a PD move and a rating move comparable.

| Indicator | Family | Worse when | Value | `full_move` |
|---|---|---|---|---|
| `stage2_share` | stage_migration | rises | Stage-2 EAD ÷ sector EAD | 0.10 |
| `stage3_share` | stage_migration | rises | Stage-3 EAD ÷ sector EAD | 0.05 |
| `ecl_coverage` | ecl | rises | ECL ÷ sector EAD | 0.01 |
| `ecl_amount` | ecl | rises | reported ECL | 2% of book EAD |
| `weighted_pd` | credit_quality | rises | Σ(EAD × PD) ÷ Σ EAD, over rows with a PD | 0.02 |
| `rating_rank` | credit_quality | rises | Σ(EAD × rating rank) ÷ Σ EAD, borrower grain | 1.0 notch |
| `uncovered_share` | collateral | rises | EAD with collateral cover < 1× ÷ sector EAD | 0.10 |
| `covenant_breach_share` | covenant | rises | EAD in breach ÷ EAD with an **observed** covenant test | 0.10 |
| `past_due_share` | arrears | rises | EAD with days past due > 0 ÷ sector EAD | 0.10 |
| `concentration_share` | concentration | rises | sector EAD ÷ book EAD | 0.03 |

Two comparison bases are evaluated for every indicator: the **prior quarter**
and the **same quarter one year earlier**. Both produce candidates; the
de-duplication below keeps the stronger framing of one issue.

Leverage and DSCR deterioration are in the specification's candidate list and
are **not** implemented: `cockpit_borrower_financial_quarter` and
`cockpit_rating_ratio_quarter` carry `debt_to_ebitda` and `dscr` at borrower
grain, but weighting them to a sector needs a coverage rule this release's
financial-input coverage does not yet support cleanly. Adding one is a
matter of another entry in `INDICATORS`; claiming it while it is absent would
be worse than saying so.

## Missing data

A null on either side of a comparison is **not** zero. The candidate is
dropped and the reason is recorded (`missing_value`), and the dropped ledger
is returned on the operator endpoint.

Three indicators additionally declare an **observation base**: how much of the
sector the measure was actually observed over. The candidate is dropped as
`insufficient_observation` when that share falls below the floor in either
period.

| Indicator | Observation base | Floor |
|---|---|---|
| `weighted_pd` | EAD with a PD | 50% of sector EAD |
| `rating_rank` | EAD with a rating | 50% of sector EAD |
| `covenant_breach_share` | EAD with an observed covenant test | 20% of sector EAD |

This is not theoretical. The pinned release records covenant headroom in
**alternate quarters**: 2026Q1 has 144 covenant rows and no headroom and no
breach date on any of them. Counting those rows in the base made the breach
share read 0% in Q1 and 48% in Q2 — and the first version of this feed put
that fake movement at the top of four of its five cards. The base now counts
only observed tests, so Q1 has no value at all and the quarter-on-quarter
comparison is correctly dropped while the year-on-year one still stands.

## Materiality gates

All expressed as fractions of the book's own EAD, so the feed behaves the same
on a larger portfolio without being re-tuned.

| Gate | Rule | Drop reason |
|---|---|---|
| Money | `exposure_at_risk` ≥ 5 bps of book EAD | `below_materiality` |
| Sector size | sector EAD ≥ 50 bps of book EAD **in both periods** | `sector_below_minimum_exposure` |
| Base | ≥ 2 facilities in both periods | `below_minimum_facility_count` |

`exposure_at_risk` is what translates a share, a ratio and an amount into one
comparable quantity:

```
share / ratio indicator:  adverse_delta × sector EAD at the latest quarter
amount indicator:         adverse_delta
```

The sector-size and facility-count gates are what stop a relative spike off a
tiny base. A sector with two facilities whose Stage-2 share goes 0% → 100% is
a 100% move and, on a 4-million book of a 20,000-million portfolio, not a finding.

## Score

```
adverse_delta = (value_new − value_old) × direction      (≤ 0 → dropped)

relative   = adverse_delta     / (adverse_delta     + full_move)
money      = exposure_at_risk  / (exposure_at_risk  + 2% of book EAD)
confidence = min(1, min(facilities_old, facilities_new) / 5)

score = 100 × (0.5 × relative + 0.5 × money) × confidence
```

`x / (x + reference)` rather than `min(1, x / reference)`: the hard version
gave every large movement exactly 100, so the five biggest issues in the book
ranked on a tie-break instead of on size. The smooth form is 0 at no movement,
0.5 at the reference movement, and approaches 1 without reaching it — always
monotone, always discriminating.

Severity labels are presentation only, and the score travels with them:
`high` ≥ 50, `moderate` ≥ 25, otherwise `low`.

## De-duplication and breadth

1. **One candidate per (segment, family).** The highest score wins. This is
   what stops "ECL up", "ECL coverage up" and "ECL outgrew exposure" — three
   views of one movement — taking three of the five slots for one sector.
2. **Four selection passes**, each walking the remaining candidates in score
   order:
   1. a segment not yet shown **and** an issue type not yet shown;
   2. a segment not yet shown;
   3. an issue type not yet shown;
   4. whatever is left.

Pass 1 buys breadth on both axes. Passes 2–4 make breadth a preference and
never a reason to drop a real issue or to pad the list.

## Tie-breaking

Total and reproducible, in order: `score`, `exposure_at_risk`,
`adverse_delta`, segment name (A→Z), indicator id (A→Z), comparison basis.
`test_ties_break_the_same_way_every_time` shuffles the input and asserts the
output is unchanged.

## Fewer than five

A legitimate outcome, reported as one:

> *N of 5 slots are filled. The remaining movements in this release did not
> clear the materiality floor, and nothing is shown that did not.*

## ECL highlights

A separate list with its own families, considered in a fixed priority order,
at most one per family, first five taken:

| # | Highlight | Family | Rule |
|---|---|---|---|
| 1 | Largest ECL increase by sector | `ecl_move` | max(ECL_new − ECL_old) where positive |
| 2 | Largest ECL coverage increase | `coverage` | max(Δ ECL/EAD) where positive |
| 3 | Largest single contributor to book ECL | `contribution` | max(sector ECL) at the latest quarter |
| 4 | Largest single borrower in book ECL | `borrower` | max(borrower ECL) at the latest quarter — **scope `borrower`** |
| 5 | Stage-2 share of the book, and its move | `stage_mix` | always reported, in whichever direction it went — **scope `portfolio`** |
| 6 | Largest ECL reduction by sector | `improvement` | min(ECL_new − ECL_old) where negative |

Coverage is listed separately from the absolute move deliberately: it isolates
the part of an ECL movement that exposure changing does not explain.

## Evidence

Every item carries a machine-readable record: release, reporting period,
comparison period, segment and its dimension, metric, unit, numerator and
denominator fields and their values on both sides, old value, new value,
delta, exposure at risk, facility counts, sector exposure, reporting currency,
and the ranking reason with each component of the score.

`GET /api/v1/cockpit-v4/attention/{item_id}` returns that plus the whole
method — every indicator, every gate, the formula, the book totals, the
executed SQL, and the dropped-candidate ledger for that segment. The drawer
shows the business-friendly subset; the trace panel shows the record.

## Possible drivers

Other indicators that moved adversely for the same segment over the same
period, plus the largest ECL contributor in the segment. Every line is
labelled **coincides with** or **associated with**.

Nothing here establishes cause. Two aggregates moving in the same quarter is
an association, and a dashboard that writes "ECL rose *because* ratings fell"
has asserted a causal claim off a correlation of two aggregates.
`test_drivers_are_association_never_causation` and a browser test both check
the wording.

## Drill-down

This release records **sector → borrower → facility**. It has **no subsegment
column**. The drilldown block on every item says so explicitly and offers
borrower level instead:

```json
{"available": ["borrower", "facility"],
 "unavailable": ["subsegment"],
 "note": "This release records sector, borrower and facility. It has no
          subsegment level, so there is no subsegment breakdown to show —
          drill to borrowers instead."}
```

Inventing a plausible-looking hierarchy would be worse than the gap.

## Caching and performance

Keyed by `(release_id, tenant_id)`. A new release is a new key, which is the
whole of the invalidation rule; a different tenant can never be served another
tenant's computation. `?refresh=true` recomputes. Measured in-process against
the real release:

| | |
|---:|---|
| First request, cold session | 446 ms |
| Server-side computation | 64 ms |
| Cached request, median / p95 | 2.4 ms / 3.1 ms |
| Forced refresh, median | 31 ms |
| Item detail, median | 2.4 ms |
| Investigate Further, median | 4.9 ms |
| Model calls | 0 |

The local UAT target is **under 2 seconds for the first request and under
100 ms cached**; `test_the_feed_is_fast_enough_to_render_a_page` asserts the
computation stays under 2 s.

## Failure

`AttentionUnavailable` becomes a **503 naming the component**, with an error
reference:

```json
{"error_code": "ATTENTION_UNAVAILABLE",
 "component": "segment_attention_feed",
 "error_reference": "att-…",
 "message": "…"}
```

The section renders *Segment attention feed unavailable* with that reference.
Ask, the run API and the health endpoint are untouched — a dashboard that
cannot compute is not a backend that is down, and both an API test and a
browser test assert Ask still works while the feed is failing.

## What the pinned release currently produces

`v4-saudi-20q-v1`, latest quarter **2026Q2**, against 2026Q1 and 2025Q2:

| Score | Family | Item |
|---:|---|---|
| 79.9 | covenant | Information Technology: more exposure sits under a covenant breach — 12.32% → 47.82% (+35.49 pp) year on year |
| 64.8 | credit_quality | Construction: internal ratings moved down — 7.87 → 9.34 notches (+1.47) year on year |
| 56.8 | collateral | Manufacturing: less of the exposure is collateralised — 52.41% → 80.71% (+28.30 pp) year on year |
| 42.4 | arrears | Chemicals: more exposure is past due — 9.25% → 22.18% (+12.93 pp) quarter on quarter |
| 8.6 | concentration | Power and Utilities: concentration in the book increased — 16.38% → 16.85% (+0.47 pp) quarter on quarter |

Five sectors, five issue types. 22 candidates survived to scoring; 198 were
dropped, 158 of them because the measure improved rather than deteriorated —
this book is broadly getting better, and the feed says so rather than
manufacturing five alarms.
