# Analytical semantics, binding, and analytical budgets

Three live defects, one round. Each stopped a real DATA_ANALYSIS run, and none
of them was about the analysis being wrong.

## 1. A resolution is not an ambiguity

### What happened

A live run recorded, correctly and honestly:

> Exposure read as reported EAD (ead_reported), not gross carrying amount.
> Period not specified: using the latest populated quarter 2026Q2 against 2026Q1.
> ECL read as booked ECL (ecl_reported).

and then could not execute. `Intent.may_execute` required an **empty**
`ambiguities` list, so writing down a resolution refused the analysis the
resolution had made possible. Careful behaviour was penalised; the only way to
run was to say nothing.

### The contract now

| Field | What belongs in it | Stops execution |
|---|---|---|
| `blocking_ambiguities` | Two defensible readings that would produce materially different numbers, and nothing else. | **Yes — the only field that does** |
| `resolved_assumptions` | A choice the analyst made and is declaring. "Period not specified: latest populated quarter 2026Q2 against 2026Q1." | No |
| `canonical_mappings` | A term this domain already defines. "Exposure at default = ead_reported." | No |

`may_execute` reads `blocking_ambiguities` only. The refusal message, when one
is warranted, now says where a resolution belongs instead of merely refusing.

Resolutions are shown rather than hidden: each one becomes a
`Resolved: …` or `Assumed: …` line in the process trace, and a blocking
ambiguity becomes `Needs a decision: …`. Transparency was never the problem.

## 2. Canonical Cockpit semantics

`backend/cockpit_v4/semantics.py`, built from the catalogue and carried in the
starting context as `cockpit_semantics`. Every entry names the relation and
field it resolves to, so a reader can check it against `inspect_catalog`.
A term whose field is absent from a release is dropped rather than offered.

| Term | Resolves to | Why this and not the other |
|---|---|---|
| exposure at default, EAD | `ead_reported` | `ead_pit` / `ead_ttc` are model parameters, not the reported figure |
| ECL, expected credit loss | `ecl_reported` | the booked figure; `ecl_modelled` is pre-overlay and `ecl_overlay` is the adjustment |
| 12-month ECL / lifetime ECL | `ecl_12m_reported` / `ecl_lifetime_reported` | named explicitly by the question |
| ECL coverage | `ecl_coverage_ratio` | the recorded ratio; computing ECL/EAD is a different figure and must be described as one |
| gross carrying amount | `gross_carrying_amount` | a balance-sheet measure, distinct from EAD |
| drawn balance | `drawn_balance` | distinct from both |
| Stage / Stage 1, 2, 3 | `ifrs9_stage` = 1, 2, 3 | the recorded stage; `sicr_flag` is the trigger, not the stage |
| PD | `pd_pit_12m` | point-in-time 12-month is the reporting default; lifetime, TTC and at-origination must be named |
| LGD | `lgd_pit` | `lgd_ttc` and `lgd_downturn` must be named |
| sector, segment | `sector_name` | the only segment dimension in this release — **there is no subsegment** |
| borrower, customer | `borrower_id` (+ `borrower_name`) | one borrower may hold several facilities |
| facility | `facility_id` | the grain of `cockpit_facility_quarter` |
| days past due | `days_past_due` | recorded, not derived |
| collateral coverage | `collateral_coverage_ratio` | allocated net value over the coverage denominator, as recorded |
| rating | `risk_rating` (+ `rating_rank`) | borrower grain; a lower rank is a stronger grade |

### The one term that genuinely needs a question

**`exposure`**, bare and unqualified. The catalogue records `ead_reported`,
`gross_carrying_amount` and `drawn_balance`, and they are materially different
figures. That is a `blocking_ambiguity` and is worth one targeted question
with `clarification_options` the reader can click.

**"Exposure at default" is not that case.** `ambiguous_terms_in()` checks the
longer canonical term first, so "total exposure at default by sector" resolves
and runs.

## 3. Period resolution

Deterministic, from this release's own calendar, and computed rather than
assumed:

| Phrase | Resolves to |
|---|---|
| latest quarter | the latest **populated** reporting quarter |
| previous quarter | the populated quarter before it |
| latest-quarter change, quarter on quarter | latest vs previous populated |
| over the latest year, year on year | latest vs the same quarter one year earlier |

"Populated" matters: a quarter the calendar defines but holds no rows for is
not a reporting period, and comparing against one produces a movement that is
entirely an artefact of coverage.

On `v4-uat-20q-v1` that is **2026Q2**, against **2026Q1** and **2025Q2**.

## 4. The binder

### What happened

A live run showed "Query validated", then failed "at the bind check". Both
statements were true and the order was wrong.

`validate_batch` checked the SQL's structure and that its relations were
authorized, announced success, and only then — inside the executor — asked
DuckDB whether the query resolved at all.

### The binder failure itself

Reproduced exactly against the pinned release:

```
Invalid Input Error: Values were not provided for the following prepared
statement parameters: 1
```

`execute_analysis` publishes a `parameters` object on every step, so a model
following the schema may write `WHERE reporting_quarter = ?` and put the value
there. **Nothing carried that object to the engine.** `v3_sql.execute` calls
`connection.execute(sql)` with no parameters, and `v3_sql.bind` runs
`EXPLAIN {sql}` the same way. DuckDB saw a placeholder with no value and
refused — a bind failure caused by a contract the application advertised and
did not honour.

### The fix

`backend/cockpit_v4/sqlbind.py`:

- `parameter_argument()` converts the step's `parameters` into what DuckDB
  wants: a **list** in order for positional `?` (numbered from "1", so
  dictionary order cannot decide which value lands in which filter), the
  mapping itself for named `$name`. A mismatch — a placeholder with no value,
  a value with no placeholder, mixed styles, non-numeric keys for positional
  parameters — is named here rather than left as a DuckDB message to decode.
- `prove_bindable()` runs `EXPLAIN` with those parameters. `EXPLAIN` runs the
  parser, the binder and the optimizer and executes nothing: an EXPLAIN of an
  unguarded cross join over the whole book returns in about three
  milliseconds.
- `validate_batch` proves every step that can be bound now, and returns which
  were proven and which bind against earlier results. The event reads
  **"Query checked: N step(s) passed the structure, authorization and
  join-grain checks; M also bound against the live tables, executing
  nothing"**, and it is only emitted after the proof. It used to say
  "validated and bound", which claimed the whole batch was bound when only
  the non-deferred steps were, and printed the declared grain and units as
  though the server had agreed with them.
- `_execute_sql` passes the same parameters to the engine, so the contract is
  honoured end to end.

What the binder catches that a structure check cannot: an unresolvable column,
a `GROUP BY` position out of range, an `ORDER BY` term that does not exist, a
type the function will not take.

### Bind failure versus execution failure

A query that never bound did not run, and the trace does not say it did.

| | Message | `phase` | `executed` |
|---|---|---|---|
| Bind | "… did not bind and was not run." | `bind` | `false` |
| Runtime | "… failed while running." | `runtime` | `true` |

The operator detail persists the DuckDB exception type, the binder's own
sanitized message, the unresolved name, the submission ordinal, the stage,
the submitted SQL and its parameters. A submission refused at the binder is
**recorded as a numbered submission with status `rejected`**, so "which query,
on which attempt" is answerable for the one that never ran.

Paths and connection strings are stripped from every message before it
reaches a model or a reader.

CreditProbe repairs nothing. The exact diagnostic goes back to the analyst,
which is the only repair mechanism V4 has.

## 5. Analytical deadlines and cost

The mode is not knowable at intake — the question arrives as text — so a run
starts on the product-help allowance and **widens once, upward only, when the
analyst declares a DATA_ANALYSIS**. A Product Help run keeps the tight bound
it should have.

| Query mode | Standard | Deep |
|---|---|---|
| PRODUCT_HELP, THEORY, REFERRAL | 60 s · $1.00 | 120 s · $2.00 |
| DATA_ANALYSIS | **120 s · $1.50** | **240 s · $3.00** |

The cost figures are derived, not chosen. At the verified price card
($15/Mtok input, $75/Mtok output) one analytical generation with a ~12k-token
assembled request and a 4,096-token response costs about $0.18 + $0.31 =
$0.49 worst case. A realistic analysis is metadata, submission, one repair and
a finalization — four generations, about $1.96 worst case, and well under that
once responses settle at actual usage. $1.50 covers the common
three-generation path with the reservation headroom the ledger needs; $3.00
covers the four-generation path with a repair.

Nothing else moved. `execution_submissions`, `analysis_rounds`,
`generation_attempts`, `catalog_calls`, `total_steps`, `answer_corrections`
and `reserved_output_tokens` are unchanged, and a test asserts it: this is a
time and money change, not a quiet loosening of how much work a run may do.

The supervisor settles on the run's stored `deadline_at`, so that is pushed
out too — widening the ledger alone would leave a 120-second analysis killed
by a 60-second watchdog.

The effective deadline and ceiling appear in the run budget, the process trace
and the diagnostics, because a panel showing the intake value while the run
works to a different one is telling the reader something untrue.

## 6. The final-answer reserve

Audited. The reservation is taken at the verified price card, against the
actual counted input, settled at **actual** usage after the call, and held
pending only while genuinely open — there was no double counting to remove.

What was wrong: a single `reserved_output_tokens` was reserved for every
generation, and when it did not fit, the run stopped. A real run ended at
COST_LIMIT with $0.71 committed because a further ~$0.30 did not fit under
$1.00 — while a shorter answer was affordable and nobody offered one.

`Ledger.affordable_output_tokens()` now reduces the response allowance to what
the remaining budget can carry, and the cap **sent to the provider** is
reduced to match, so the reservation stays a true projection of the exposure
rather than an optimistic one. The run fails closed only when even
`MIN_RESPONSE_TOKENS` (1,024) will not fit: "cannot afford 4,096 tokens" is
not the same fact as "cannot afford an answer".

A request that does not fit the context window is still reported as
`INPUT_CONTEXT_LIMIT`, not as a cost failure — the more specific diagnosis.

## 7. The first failure survives the terminal stop

A run that hits a binder failure, retries, and then runs out of time stops as
`DEADLINE_EXPIRED`. The terminal code is the bound that ran out; it is rarely
the interesting fact.

The orchestrator keeps the **first** analytical failure and appends it to the
terminal message and its operator detail:

> The 120-second standard deadline passed. The first analytical failure in
> this run was: step s1 of submission 1 did not bind — Binder Error:
> Referenced column "no_such_column" not found in FROM clause!

Everything else remains in the event log, which never lost it.
