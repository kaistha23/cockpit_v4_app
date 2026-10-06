# TODO: bank formulae for IFRS 9 metrics (Cockpit V4)

## Purpose

Cockpit V4 cannot yet use the bank's own definitions. Today every average is a plain `AVG(col)`, unweighted (`backend/cockpit_v4/plan_compiler.py`, `build_book`, ~L127-200, e.g. `AVG({col})` at ~L161). "Average PD" and "average LGD" are therefore simple facility means. This file collects the actual formulas from the product owner. **Nothing here is decided by engineering.** "Candidate — not adopted" lines are the usual industry options, listed only to tick or replace.

## How to fill in

1. For each item, tick one candidate or write the bank's formula in the table.
2. Map each variable to a catalog column (see Appendix A). If the column does not exist, write "MISSING" and name the source.
3. State weighting, period convention and edge cases. Do not leave them implicit.
4. Add the owner / policy reference.
5. Change `Status` to `provided`. Engineering moves it to `implemented` once code and tests land, and fills in the location.

## Status legend

| Status | Meaning |
|---|---|
| pending | Nothing supplied yet. Engineering must not guess. |
| provided | Formula supplied by the product owner, not yet in code. |
| implemented | In code with tests. "Implementation location" filled in. |

## What is blocked until filled

| Blocked item | Where | Needs sections |
|---|---|---|
| Governed formula families (weighted ratios, stage mix, coverage, movement) | `backend/cockpit_v4/attention_v2.py`: `_corporate_families` (~L126), `_retail_families` (~L209) | 1, 2, 3, 5, 7, 8 |
| Metric generation for averages (replace unweighted `AVG`) | `plan_compiler.build_book` (~L127-200) | 1 |
| `compile_sensitivity` aggregation (currently requires `held_constant` = `lgd_pct`, `ead_sar_mn`, `macro_overlay`; driver `pd_lifetime`; denominator `sector_total_recognised_ecl`) | `plan_compiler.compile_sensitivity` (~L444) | 2, 6 |
| Gold SQL for corpus items marked `pending_formula` | corpus items flagged `pending_formula` (no such flag exists in the repo yet; location to be confirmed) | 1-9 |
| Business-term wording for derived measures | `backend/cockpit_v4/semantics.py` (`_CORPORATE_MEASURES`, `_RETAIL_MEASURES`) | all |
| Analyst guidance on workflows | `backend/cockpit_v4/prompts/analyst.md` | 9 |

## Per-item template (copy for new items)

| Field | Entry |
|---|---|
| Formula | |
| Variables → catalog columns | |
| Weighting basis | |
| Period convention | corporate: `corp_*_quarter.reporting_quarter` (quarterly). Retail: `retail_account_month.reporting_month` (monthly). Stock measures (EAD, ECL, balance, stage counts): quarter-end month or average of months? Flow measures (write-off, recovery, transfers, cures): sum of the 3 months? |
| Edge cases (NULL, zero denominator, caps) | |
| Source / owner | |
| Status | pending |
| Implementation location | |

---
## 1. Portfolio aggregation of ratios

### 1.1 pd_pit_12m (average PD)

Candidates:
  - [ ] candidate — not adopted: EAD-weighted: Σ(x·EAD)/ΣEAD
  - [ ] candidate — not adopted: Balance-weighted (`drawn_sar_mn` / `balance_sar_mn`)
  - [ ] candidate — not adopted: Simple facility mean (current behaviour)
  - [ ] candidate — not adopted: Other: ____

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `pd_pit_12m`, weight: `ead_sar_mn` / `drawn_sar_mn` / `balance_sar_mn` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 1.2 pd_lifetime

Candidates:
  - [ ] candidate — not adopted: EAD-weighted: Σ(x·EAD)/ΣEAD
  - [ ] candidate — not adopted: Balance-weighted (`drawn_sar_mn` / `balance_sar_mn`)
  - [ ] candidate — not adopted: Simple facility mean (current behaviour)
  - [ ] candidate — not adopted: Other: ____

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `pd_lifetime` (no `pd_lifetime_*` variants exist) |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 1.3 lgd_pct (average LGD)

Candidates:
  - [ ] candidate — not adopted: EAD-weighted: Σ(x·EAD)/ΣEAD
  - [ ] candidate — not adopted: Balance-weighted (`drawn_sar_mn` / `balance_sar_mn`)
  - [ ] candidate — not adopted: Simple facility mean (current behaviour)
  - [ ] candidate — not adopted: Other: ____
  - [ ] candidate — not adopted: ECL-implied: ΣECL/Σ(EAD·PD)

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `lgd_pct` (percent, not 0-1) |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 1.4 ECL coverage

Candidates:
  - [ ] candidate — not adopted: Σecl_sar_mn / Σead_sar_mn
  - [ ] candidate — not adopted: Σecl_sar_mn / Σ gross carrying amount (define GCA)

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `ecl_sar_mn`, `ead_sar_mn`, `ecl_coverage_pct` (corporate facility only) |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 1.5 Collateral coverage_pct and ltv_pct

Candidates:
  - [ ] candidate — not adopted: EAD-weighted: Σ(x·EAD)/ΣEAD
  - [ ] candidate — not adopted: Balance-weighted (`drawn_sar_mn` / `balance_sar_mn`)
  - [ ] candidate — not adopted: Simple facility mean (current behaviour)
  - [ ] candidate — not adopted: Other: ____
  - [ ] candidate — not adopted: Weighted by `allocated_value_sar_mn`

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `corp_collateral_quarter.coverage_pct`, `ltv_pct`; retail `collateral_coverage_pct` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 1.6 utilisation_pct

Candidates:
  - [ ] candidate — not adopted: Σdrawn / Σlimit (recomputed)
  - [ ] candidate — not adopted: Simple mean of recorded `utilisation_pct`

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `drawn_sar_mn`, `limit_sar_mn`, `balance_sar_mn`, `utilisation_pct` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

## 2. Core IFRS 9 ECL

### 2.1 12-month (Stage 1) vs lifetime (Stage 2/3) ECL

Candidates:
  - [ ] candidate — not adopted: ECL = PD12m × LGD × EAD (× DF)
  - [ ] candidate — not adopted: ECL = Σt marginal PD(t) × LGD(t) × EAD(t) × DF(t)

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `ecl_12m_sar_mn`, `ecl_lifetime_sar_mn`, `stage` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 2.2 Marginal vs cumulative PD

Candidates:
  - [ ] candidate — not adopted: Marginal PD from `pd_lifetime` curve differences
  - [ ] candidate — not adopted: Cumulative PD used directly

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `pd_lifetime`, `pd_pit_12m`; term structure columns: MISSING? |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 2.3 Discounting at EIR

Candidates:
  - [ ] candidate — not adopted: Discount each period at EIR
  - [ ] candidate — not adopted: No discounting (undiscounted proxy)

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | EIR: MISSING? |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 2.4 Stage 3 at PD = 1

Candidates:
  - [ ] candidate — not adopted: PD = 1, ECL = EAD × LGD
  - [ ] candidate — not adopted: Use recorded `pd_pit_12m` as is

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `stage`, `default_flag`, `lgd_pct` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 2.5 Macro scenario weights

Candidates:
  - [ ] candidate — not adopted: Probability-weighted base / upside / downside
  - [ ] candidate — not adopted: Single scenario

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | Scenario weights and per-scenario ECL: MISSING? |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 2.6 Management overlay (`macro_overlay`)

Candidates:
  - [ ] candidate — not adopted: Added on top of modelled ECL
  - [ ] candidate — not adopted: Allocated to stages / sectors pro rata ECL
  - [ ] candidate — not adopted: Allocated pro rata EAD

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `macro_overlay`: not a facility column in the catalog (only a name accepted in `held_constant`) |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 2.7 Relation of ecl_12m_sar_mn / ecl_lifetime_sar_mn / ecl_sar_mn

Candidates:
  - [ ] candidate — not adopted: ecl_sar_mn = ecl_12m if stage 1 else ecl_lifetime (as described in schema)
  - [ ] candidate — not adopted: ecl_sar_mn includes overlay
  - [ ] candidate — not adopted: ecl_sar_mn excludes overlay

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `ecl_12m_sar_mn`, `ecl_lifetime_sar_mn`, `ecl_sar_mn`, `stage` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

## 3. Staging and SICR rules

### 3.1 SICR trigger (sicr_flag)

Candidates:
  - [ ] candidate — not adopted: Relative PD increase vs origination (multiple ___)
  - [ ] candidate — not adopted: Absolute PD threshold ___
  - [ ] candidate — not adopted: Rating notch downgrade ≥ ___
  - [ ] candidate — not adopted: Any combination

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `sicr_flag`, `pd_pit_12m`, `pd_lifetime`, `rating_current`, `rating_previous`, `rating_notches_moved` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 3.2 30+ DPD backstop

Candidates:
  - [ ] candidate — not adopted: dpd_days > 30
  - [ ] candidate — not adopted: dpd_days ≥ 30

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `dpd_days` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 3.3 Watchlist and forbearance triggers

Candidates:
  - [ ] candidate — not adopted: Watchlist → Stage 2
  - [ ] candidate — not adopted: Forborne → Stage 2 (probation ___ months)

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | Watchlist / forbearance columns: MISSING? |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 3.4 Default definition (90+ DPD / default_flag)

Candidates:
  - [ ] candidate — not adopted: dpd_days > 90
  - [ ] candidate — not adopted: dpd_days ≥ 90
  - [ ] candidate — not adopted: Unlikeliness-to-pay triggers

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `dpd_days`, `default_flag` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 3.5 Cure and probation

Candidates:
  - [ ] candidate — not adopted: Cure when cured and clean for ___ months
  - [ ] candidate — not adopted: Cure immediately on clearing arrears

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `cure_flag`, `quarters_in_stage` (corporate), `stage` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

## 4. Stage migration and transition matrices

### 4.1 Count vs EAD basis

Candidates:
  - [ ] candidate — not adopted: Count of facilities / accounts
  - [ ] candidate — not adopted: EAD-weighted share
  - [ ] candidate — not adopted: Both

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `stage`, `ead_sar_mn`, `facility_id`, `account_id` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 4.2 Cohort definition

Candidates:
  - [ ] candidate — not adopted: Opening-period population only
  - [ ] candidate — not adopted: Opening + new originations

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `reporting_quarter` / `reporting_month`, `origination_month` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 4.3 New, repaid, written-off facilities

Candidates:
  - [ ] candidate — not adopted: Separate rows (New / Repaid / Written-off)
  - [ ] candidate — not adopted: Excluded from matrix
  - [ ] candidate — not adopted: Exited bucket = Stage 4

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `write_off_sar_mn`, `ead_sar_mn` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

## 5. Ratios

### 5.1 Coverage by stage

Candidates:
  - [ ] candidate — not adopted: Σecl / ΣEAD by stage
  - [ ] candidate — not adopted: Σecl / ΣGCA by stage

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `ecl_sar_mn`, `ead_sar_mn`, `stage` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 5.2 Stage 2 share

Candidates:
  - [ ] candidate — not adopted: Σ EAD(stage 2) / Σ EAD
  - [ ] candidate — not adopted: Facility count share

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `stage`, `ead_sar_mn` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 5.3 NPL ratio

Candidates:
  - [ ] candidate — not adopted: Stage 3 EAD / total EAD
  - [ ] candidate — not adopted: 90+ DPD EAD / total EAD
  - [ ] candidate — not adopted: default_flag EAD / total EAD

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `stage`, `dpd_days`, `default_flag`, `ead_sar_mn` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 5.4 Cost of risk

Candidates:
  - [ ] candidate — not adopted: ECL charge (annualised) / average gross loans
  - [ ] candidate — not adopted: (ΔECL + write-offs − recoveries) / average EAD

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `ecl_sar_mn`, `write_off_sar_mn`, `recovery_sar_mn`, `ead_sar_mn` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

## 6. Sensitivity

### 6.1 ΔECL for +1pp PD

Candidates:
  - [ ] candidate — not adopted: Facility level: Σ(EAD × ΔPD × LGD), then sum
  - [ ] candidate — not adopted: Aggregate product: ΣEAD × avg ΔPD × avg LGD
  - [ ] candidate — not adopted: Lifetime vs 12m PD driver (code currently requires `pd_lifetime`)

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `pd_lifetime`, `ead_sar_mn`, `lgd_pct`, `macro_overlay` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_ <br>Today: `compile_sensitivity`, Stage 2 only, sector and quarter explicit |

### 6.2 Caps and floors

Candidates:
  - [ ] candidate — not adopted: PD capped at 100%
  - [ ] candidate — not adopted: Stage 1 sensitivity uses PD12m
  - [ ] candidate — not adopted: Shock applied multiplicatively

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `pd_lifetime` upper bound 1; shock currently absolute pp only |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 6.3 Denominator for ΔECL as % of ECL

Candidates:
  - [ ] candidate — not adopted: Sector total recognised ECL (current)
  - [ ] candidate — not adopted: Stage 2 ECL only
  - [ ] candidate — not adopted: Total book ECL

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `ecl_sar_mn` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

## 7. ECL movement walk

### 7.1 Opening to closing walk

Candidates:
  - [ ] candidate — not adopted: Opening → transfers (1↔2↔3) → originations → derecognitions → remeasurement → write-offs → recoveries → closing
  - [ ] candidate — not adopted: Remeasurement as plug
  - [ ] candidate — not adopted: Remeasurement computed explicitly

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `ecl_sar_mn`, `write_off_sar_mn`, `recovery_sar_mn`, `stage`, `origination_month` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 7.2 Order of steps and attribution

Candidates:
  - [ ] candidate — not adopted: Transfers at opening PD/LGD
  - [ ] candidate — not adopted: Transfers at closing PD/LGD

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | _TBD_ |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 7.3 Write-off and recovery treatment

Candidates:
  - [ ] candidate — not adopted: Recoveries as P&L credit outside the ECL walk
  - [ ] candidate — not adopted: Recoveries reduce ECL stock

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `write_off_sar_mn`, `recovery_sar_mn` (flows) |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

## 8. Retail specifics

### 8.1 DPD buckets

Candidates:
  - [ ] candidate — not adopted: Use recorded `delinquency_bucket` (coarse)
  - [ ] candidate — not adopted: Use `delinquency_bucket_fine` (1-9, ...)
  - [ ] candidate — not adopted: Recompute from `dpd_days` with bank boundaries: ____

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `dpd_days`, `delinquency_bucket`, `delinquency_bucket_fine` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 8.2 Roll rates

Candidates:
  - [ ] candidate — not adopted: Balance-based: bucket n at t → bucket n+1 at t+1 / bucket n at t
  - [ ] candidate — not adopted: Account-count based

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `delinquency_bucket`, `balance_sar_mn`, `account_id`, `reporting_month` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 8.3 Vintage curves

Candidates:
  - [ ] candidate — not adopted: Stage 2 / default rate by `months_on_book`
  - [ ] candidate — not adopted: By `vintage_year` cohort, EAD-weighted
  - [ ] candidate — not adopted: Cumulative vs marginal

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `vintage_year`, `months_on_book`, `origination_month`, `default_flag`, `ead_sar_mn` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

## 9. Analysis workflows

### 9.1 What is driving Stage 2 growth

Candidates:
  - [ ] candidate — not adopted: Stage 2 EAD change = net transfers in/out + new originations − repayments
  - [ ] candidate — not adopted: By sector, by rating move

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `stage`, `ead_sar_mn`, `sector`, `sicr_flag` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 9.2 What is driving ECL growth

Candidates:
  - [ ] candidate — not adopted: Decomposition: volume (ΔEAD) vs rate (ΔECL/EAD)
  - [ ] candidate — not adopted: Stage mix vs within-stage coverage vs overlay

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | `ecl_sar_mn`, `ead_sar_mn`, `stage`, `sector` |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

### 9.3 Volume vs rate effect formula

Candidates:
  - [ ] candidate — not adopted: Volume = ΔEAD × prior coverage; Rate = Δcoverage × current EAD
  - [ ] candidate — not adopted: Midpoint / Shapley split

| Field | Entry |
|---|---|
| Formula | _TBD_ |
| Variables → catalog columns | _TBD_ |
| Weighting basis | _TBD_ |
| Period convention | _TBD_ (corporate quarterly / retail monthly; stock vs flow) |
| Edge cases (NULL, zero denominator, caps) | _TBD_ |
| Source / owner | _TBD_ |
| Status | pending |
| Implementation location | _TBD_  |

## 10. Worked examples

Empty template. Add one block per example. These become gold SQL and expected plan shapes.

| # | Question | Method | Formula | Expected plan / SQL shape | Sample numbers (inputs → expected output) | Status |
|---|---|---|---|---|---|---|
| 1 | _e.g. average LGD for Stage 2 in 2025Q4_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | pending |
| 2 | _e.g. ΔECL for +1pp PD, Stage 2, sector X_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | pending |

---

## Appendix A. Catalog columns (verified against `backend/cockpit_v4/schema.py` and `semantics.py`)

Corporate: `corp_facility_quarter` (period `reporting_quarter`, quarterly). Retail: `retail_account_month` (period `reporting_month`, monthly).

| Column | Corporate facility | Retail account | Relation / note |
|---|---|---|---|
| `ead_sar_mn` | yes | yes | EAD. Not `limit` or `drawn`/`balance`. Retail customer relation carries `total_ead_sar_mn` (same exposure, summed) |
| `drawn_sar_mn` | yes | no | Corporate drawn balance |
| `limit_sar_mn` | yes | yes | Committed limit |
| `balance_sar_mn` | no | yes | Retail balance |
| `pd_pit_12m` | yes | yes | Point-in-time 12m PD (0-1). Borrower also has `pd_ttc_12m` |
| `pd_lifetime` | yes | yes | Lifetime PD (0-1). The only name; no `pd_lifetime_*` suffixed column exists |
| `lgd_pct` | yes | yes | Percent, not 0-1 |
| `ecl_12m_sar_mn` | yes | yes | Stage 1 basis |
| `ecl_lifetime_sar_mn` | yes | yes | Stage 2/3 basis |
| `ecl_sar_mn` | yes | yes | Recognised: 12m in Stage 1, lifetime in Stages 2/3. Retail customer carries `total_ecl_sar_mn` (summing both double-counts) |
| `ecl_coverage_pct` | yes | not seen | Recognised ECL as % of EAD (corporate facility) |
| `stage` | yes | yes | 1, 2, 3 |
| `sicr_flag` | yes | yes | Trigger, not the stage |
| `dpd_days` | yes | yes | Recorded, not derived |
| `default_flag` | yes | yes | Recorded, not derived from DPD |
| `cure_flag` | yes | yes | Cured this period |
| `coverage_pct` | collateral only | no | `corp_collateral_quarter`; NOT ECL coverage. Retail uses `collateral_coverage_pct` |
| `ltv_pct` | collateral only | collateral only | `corp_collateral_quarter`, `retail_collateral_month` |
| `utilisation_pct` | yes | yes | Recorded drawn/limit % |
| `write_off_sar_mn` | yes | yes | Flow, this period |
| `recovery_sar_mn` | yes | yes | Flow, this period |
| `vintage_year` | no | yes | `retail_account_month`; also `months_on_book`, `origination_month` |
| `delinquency_bucket` | no | yes | Coarse banding of `dpd_days`; `delinquency_bucket_fine` splits the first 30 days (do not sum fine within coarse) |
| `macro_overlay` | **not a column** | **not a column** | Only a token in `sensitivity.held_constant` (`plan_compiler.py` ~L272, L472). Legacy `backend/data_loader.py` has an aggregate "Macro Overlay". Source needed |

Relations: relationships recorded in the catalog. Facility columns repeat per quarter/month. Borrower-grain or customer-grain figures need explicit aggregation.
