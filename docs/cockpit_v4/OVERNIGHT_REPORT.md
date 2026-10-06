# Cockpit V4 — overnight build, test and fix

Branch `claude/cockpit-single-agent-v4-h8fsbq`. No pull request opened, no
merge performed, no V3 module modified.

**Verdict, stated first: NOT READY for live commissioning.** Everything that
can be established without a live provider credential has been established,
and the single blocker that stopped every live request has been found and
fixed — but a fix for a provider rejection is only proven by a provider that
stops rejecting it, and no paid call was made. See §25.

---

## 1. What was asked and what this session did

An overnight build-test-fix pass: fix the provider blocker first, then test
whether CreditProbe can answer senior-credit-risk questions correctly,
meaningfully and usefully; exercise the collaboration surface; inject
failures; measure performance; run real browser tests; and report with an
explicit readiness verdict.

Five commits, each self-contained:

| Commit | What it settled |
| --- | --- |
| `a80103b` | The provider 400 blocker, the compatibility test that keeps it fixed, the error taxonomy, the payload snapshot, and the twenty-question benchmark across three evidence layers |
| `6015b47` | Save, investigate, comment, share, the notification outbox, and the five thread scenarios |
| `b52162a` | The answer actions in the UI, the wider page, the greeting fix, and a sharper legacy-endpoint guard |
| `df8faf0` | Responsive measurement at five viewports and the three layout fixes the numbers demanded |
| `dad9f28` | Ten injected failures, the three taxonomy defects they exposed, and the performance profile |

## 2. The blocker: why every live request failed before inference

The live diagnostic was:

```
Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error',
'message': 'tools.0.custom.input_schema: input_schema does not support
oneOf, allOf, anyOf, or dependentSchemas'}}
```

`tools.0` is `inspect_catalog`. Its schema carried an `allOf` added in the
previous round to DOCUMENT the relationship between `sample_rows` and
`detail: ["samples"]`. It had a title and a description and no schema
semantics at all. The Anthropic tool dialect rejects the keyword regardless
of what is inside it, and rejects it before any inference — so the run cost
nothing and produced nothing, every time.

The dependency now lives in the field descriptions, which is where a reader
was going to look for it anyway.

## 3. Keeping it fixed

`tests/cockpit_v4/test_provider_schema_compatibility.py` (40 tests) walks the
exact output of `provider_tools()` — not the source schema files — for the
full tool set and for all sixteen `withhold` combinations the runtime can
build, and fails on any keyword outside the supported subset:

```
oneOf allOf anyOf not if then else $ref $defs definitions
dependentSchemas dependentRequired dependencies patternProperties
propertyNames unevaluatedProperties unevaluatedItems contains prefixItems
```

`$ref` appears in the application schemas for readability and is inlined for
the wire; the test reads the wire copy, so that stays true by construction
rather than by discipline.

## 4. The error taxonomy

A malformed request reported as an outage sends an operator hunting for a
provider that is working perfectly. Two codes were added,
`PROVIDER_REQUEST_INVALID` and `TOOL_SCHEMA_INVALID`, both operator-class,
carrying the failing tool index, the schema path, the rejected keywords and a
sanitized provider message with credentials stripped. The run stops at
`understanding`, not at `publishing`.

Three further defects surfaced during failure injection and were fixed:

- `_classify` re-read every exception's message including ones that already
  carried a code, so a rejected credential, a rate limit and a tool-schema
  rejection all reported as `PROVIDER_UNAVAILABLE`. An already-classified
  failure is now returned untouched.
- A run that used its provider-attempt allowance raised `BudgetExceeded`
  inside the same handler and was likewise reported as an outage. Budget
  refusals now pass through with `CALL_LIMIT`, settled at zero because
  nothing was sent.
- Every failed run emitted **two** terminal events — the orchestrator's at
  the stage it stopped, and the worker's at "publishing". The second is the
  exact misreading the taxonomy work set out to remove. The outcome now
  records that the terminal event was emitted and the worker does not repeat
  it.

An operator-class stop also mints a reference now. `PROVIDER_AUTH`,
`STORAGE_UNAVAILABLE` and the rest were reaching the reader with nothing to
quote. For a storage failure the reference is minted in memory and the event
is attempted rather than assumed, since the store is the thing that may be
gone.

## 5. The twenty-question benchmark: three layers, kept apart

`tests/cockpit_v4/uat_question_bank.py`, `tests/cockpit_v4/uat_sql.py`,
`tests/cockpit_v4/test_overnight_benchmark.py`.

- **Layer A — the oracle.** `oracle()` computes each expected answer with
  pandas straight from the pinned Parquet and imports nothing from the query
  path, the catalog, the validator or the executor. Nothing is hard-coded:
  every figure is computed at test time from the release, so the bank travels
  to a different release and still means something.
- **Layer B — the independent analyst.** Twenty SQL statements authored
  against the catalogue by a reader of each question, executed through the
  REAL materialized, file-access-disabled V4 session.
- **Layer C — the product.** The same statement carried through the real
  worker: intake, tool contracts, validator, bind proof, parameter binding,
  DuckDB, artifact store, finalizer, event stream.

**All twenty agree across all three layers.** 63 tests: 20 A↔B
reconciliations, 20 end-to-end runs checked against the oracle row for row,
20 rubric scorings, and the artifact writer.

Evidence: `docs/cockpit_v4/evidence/overnight_analytical_benchmark.json`.

Two disagreements were found while the bank was being written, both times the
oracle was wrong and the oracle was fixed. Neither expected answer was ever
adjusted to match the product.

## 6. Question coverage

| Band | Questions | Shape |
| --- | --- | --- |
| Data pull | Q01–Q05 | EAD by sector, top-10 ECL borrowers, Stage 2 shares, covenant breaches and waivers, downgrades with before/after grades |
| Diagnostic | Q06–Q10 | Stage 2 movement over a year, ECL growing faster than EAD, the ECL bridge, downgrade ∩ stage migration, collateral deterioration with rising ECL |
| Remediation | Q11–Q15 | The five borrowers behind the worst sector, a prioritised review list, a three-way intersection, a What-If referral, the year's worst rating-and-ECL movers |
| Multi-part | Q16–Q20 | Top five with comparison and a judgement, a single-sector deep read, a three-period sector read, the portfolio KPI table, deterioration on two of four measures |

Reconciliation properties that hold on the pinned release:
`v4-uat-20q-v1`, latest quarter 2026Q2.

- Q01: eleven sectors, total EAD 20,720.34, top sector Information Technology
  at 25.2% of the book.
- Q03: Stage 2 shares sum to exactly 1.0.
- Q08: the eleven sector contributions sum **exactly** to the book ECL delta
  of −69.7039. A bridge whose parts do not sum to the whole is not a bridge.
- Q13: legitimately empty. The answer says so rather than relaxing a
  condition to produce rows.
- Q19: portfolio EAD 20,720.34, ECL 119.65, coverage 0.577%, Stage 2 share
  1.31%; largest sector contributor Information Technology at 25.47, largest
  borrower BRW0034 at 15.84.

## 7. The answer-quality rubric, and what it refuses to score

Six dimensions at the weights in the brief. Four are properties of what the
PRODUCT published and are scored from the run. Two are properties of the
analyst's own prose and are **left unscored**, because the analyst turn is
scripted in this module and a rubric that grades the harness is worse than no
rubric at all.

| Dimension | Weight | Scored | Basis |
| --- | --- | --- | --- |
| Numerical correctness | 40 | yes | every published figure reconciles to the pandas oracle |
| Answers what was asked | 20 | yes | coverage names each subquestion and resolves to evidence |
| Insight and interpretation | 15 | **no** | needs a live analyst turn |
| Evidence and traceability | 10 | yes | each claim's artifact, row and column resolve to the cell it quotes |
| Visualisation judgement | 10 | yes | a chart is published exactly when the question warrants one |
| Clarity of writing | 5 | **no** | needs a live analyst turn |

**All twenty score 80/80 on the scorable 80 points.** The artifact records the
80, the 20 withheld, and why.

The rubric is also *enforced*, not only measured: a figure the artifact does
not hold is sent back to the analyst instead of published, and a query that
does not bind is refused with DuckDB's own diagnostic before "validated" is
announced.

## 8. Visualisation policy

Stated per question with a reason, not applied mechanically. Seven bar charts
(ranked comparisons), one waterfall (Q08, because the parts sum exactly to
the whole movement, which is what a bridge is for), and twelve questions with
no chart — a five-row named list, a three-KPI comparison or a possibly-empty
intersection is a table, and charting it would be decoration. The test asserts
both directions: a chart appears when warranted and does **not** appear when
it is not.

## 9. Threads T01–T05

Driven through the real worker, asserted against the bytes that would have
gone to a provider.

| | Scenario | What it proves |
| --- | --- | --- |
| T01 | "Show me the borrowers behind that" | the previous question *and* its answer are what "that" resolves against |
| T02 | "And a year ago?" | the measure and grouping persist; the new question reaches the analyst unmodified |
| T03 | A thread seeded from an attention card | the segment, quarters and metric are on the THREAD, so every turn in it carries them |
| T04 | A change of subject mid-thread | history is offered as exact record, labelled as outranking any summary, never as an answer to reuse |
| T05 | Reopening a thread | real persisted turns with their published answers, and a stranger gets 404 |

One product change fell out of T03: the attention seed now says *in the prompt
itself* that it is a record of what the dashboard showed and not an answer to
reuse. That sentence had been sitting in a code comment, where the analyst
could not read it.

## 10. Save, investigate, comment, share

V4's own storage, routes and authorization. None of it calls the previous
Cockpit's investigation API; a sharpened guard now forbids the legacy PATH and
a companion test walks every `fetch(` in the V4 components requiring
`API_PREFIX` in the target.

- A saved analysis keeps the **published** response, not a re-rendering, so it
  cannot drift from the words the reader saw and approved.
- An unfinished run cannot be saved (409 `NOT_FINISHED`).
- Investigations gather saved work, carry a short status lifecycle, and refuse
  an unknown status by name.
- Comments attach to saved analyses and investigations.
- Shares record an internal audience.
- Every read is tenant-checked in SQL: a wrong tenant sees nothing and never
  learns the row exists.

## 11. The notification outbox — and what it will not claim

This is the part to read twice.

`Notifier` records every notification and delivers only what a **configured
transport** actually accepts.

- With no transport — the default, and the state of any build nobody has given
  one — a notification lands as `RECORDED` with `delivered: false` and a
  reason saying so.
- Recipients are **gated, not filtered**. An address outside `RecipientPolicy`
  is written as `REFUSED` for the audit and is never handed to a transport.
  The empty allow-list this build ships with therefore refuses everyone.
- A transport that raises is recorded `FAILED`, never hopefully queued.
- There is no code path that sets `delivered: true` without a transport having
  returned a receipt, and the row names the transport so an audit can tell a
  test double from a real channel.
- In the UI, `deliveryWording()` has exactly one branch that says "Sent", and
  it reads `delivered`. A row that merely says `SENT` without `delivered`
  still does not render as sent — pinned by its own test.

**No email, chat or webhook message was sent to anybody during this session.**

## 12. The provider payload

`tests/cockpit_v4/test_provider_payload.py` (25 tests) snapshots the assembled
request for an analytical question, a product question and a concept question,
and asserts: the model id, the ordered system blocks, the user's question
present unmodified, no credentials anywhere, no V3 or Sonnet tool offered, no
catalogue dump, and that the whole thing fits the model's context.

`docs/cockpit_v4/evidence/provider_payload.json` records 30,028 system bytes
and four tools for the EAD-by-sector case, with no unsupported schema keyword.

## 13. Failure injection

Ten cases. Each checked for four things: terminal state reached, right code
rather than a catch-all, **nothing published that reads as an answer**, and an
operator reference where an operator is the one who can act.

| Case | Terminal | Code | Answer published | Reference |
| --- | --- | --- | --- | --- |
| Provider unreachable | FAILED | `PROVIDER_UNAVAILABLE` | no | — |
| Provider rejected the request (400) | FAILED | `TOOL_SCHEMA_INVALID` | no | yes |
| Credential rejected | FAILED | `PROVIDER_AUTH` | no | yes |
| Provider rate limit | FAILED | `PROVIDER_RATE_LIMIT` | no | — |
| Model output fails the contract | FAILED | `CALL_LIMIT` | no | — |
| SQL does not bind | UNSUPPORTED | — | no | — |
| Query reaches outside the release | UNSUPPORTED | — | no | — |
| Artifact store unavailable mid-run | FAILED | `STORAGE_UNAVAILABLE` | no | yes |
| The run never finalizes | FAILED | `CALL_LIMIT` | no | — |
| The user cancelled | CANCELLED | `CANCELLED_BY_USER` | no | — |

**No injected failure published anything shaped like an answer.** A rate limit
is correctly *not* operator-class: it is a wait, not a misconfiguration.

Evidence: `docs/cockpit_v4/evidence/failure_injection.json`.

## 14. Performance

What the product costs **around** the analyst turn. The scripted analyst
returns immediately, so these are CreditProbe's own milliseconds — the only
part of latency a change to this codebase can move. Provider latency is not in
any of them, and the artifact says so.

| Measurement | Median | p90 |
| --- | --- | --- |
| Bind proof (`EXPLAIN`, heaviest query in the bank) | 6.4 ms | 11.1 ms |
| Both landing dashboards, computed cold | 28.5 ms | 33.4 ms |
| One analytical run end to end | 41.4 ms | 57.7 ms |
| The heaviest question (Q20) end to end | 78.7 ms | 91.4 ms |
| Product Help end to end | 19.8 ms | 448.5 ms |

The Product Help p90 is the first run in the process, which pays for module
import and the first store connection; its median is the steady state.
Evidence: `docs/cockpit_v4/evidence/performance.json`.

## 15. Layout, measured rather than asserted

The previous round widened the Cockpit's own container and the page still
rendered a 1128px ribbon on a 1728px display. A real Chromium at five
viewports found out why in one measurement: the constraint was never the
Cockpit's container. It was the application shell's 1200px content cap and its
36px-a-side padding. At 390px those two took a 375px window down to 91px of
usable column.

Three contained fixes, none touching a V3 page's rendering:

1. The shell's content column stays at 1200px unless a page asks for more.
   Only the Cockpit asks, through `useWideContent` — the root route serves
   whichever Cockpit generation is enabled, so guessing from the pathname
   would widen the wrong one.
2. Horizontal padding is modest below `sm`, unchanged from `lg` up.
3. The navigation rail collapses itself below 767px, to the icon state the
   sidebar already renders properly. The stored preference is untouched.

| Viewport | Window | Content area | Cockpit | Column fill | Window fill |
| --- | --- | --- | --- | --- | --- |
| Mac 16-inch | 1728 | 1501 | 1429 | 95.2% | 86.9% |
| Desktop | 1440 | 1213 | 1141 | 94.1% | 84.2% |
| Laptop | 1280 | 1053 | 981 | 93.2% | 82.3% |
| Tablet | 834 | 607 | 559 | 92.1% | 72.8% |
| Phone | 390 | 315 | 283 | 89.8% | 80.8% |

Two ratios are measured rather than one, because two different owners can
cause a narrow page. Neither can hide behind the other, and a failure prints
the measured ancestor chain so the next reader does not have to guess which
element stopped being wide.

## 16. The greeting

"Good evening, Local UAT" greeted a deployment profile as though it were a
colleague. The demo principal no longer carries that string as a name at all:
it travels as `profile_label`, the session response keeps the two apart, and
the greeting reads only `display_name`. A build that does not know who is at
the keyboard greets nobody. A browser test asserts the heading never contains
a profile label.

## 17. Browser evidence

**42 of 42 real-Chromium tests pass** against the real Next.js UI and the real
V4 API, with every request the page makes recorded. The central claim is
checkable rather than asserted: the Cockpit page uses the V4 run API and never
touches `/api/v1/investigations`, `/api/v1/agentic/officer`, `/api/v1/ask/*`
or the V2/V3 diagnostics.

The analyst is a stub in this suite, which the evidence file states on its own
face: *"This proves the wiring, the run lifecycle and the rendering. It is NOT
a live Opus validation."*

A `V4_BROWSER_ONLY` filter was added for diagnosing a subset; it refuses to
write the evidence artifact, so a filtered run can never be mistaken for a
full one.

## 18. The two dashboards

Unchanged from the previous round and re-verified: segment-level items only in
Segments Requiring Attention, borrower-level and book-level ECL movements in
ECL Highlights, no card in both, both opening the same right-hand drawer, and
`model_calls: 0` on the feed. The ranking is deterministic and no paid model
call builds either dashboard.

## 19. Regression matrix

| Suite | Result |
| --- | --- |
| V4 backend (`tests/cockpit_v4`) | **663 passed**, 0 failed |
| Frontend (`node --test`) | **476 passed**, 0 failed |
| Real browser (Chromium) | **42 passed**, 0 failed |
| TypeScript (`tsc --noEmit`) | clean |

New this session: 40 provider-schema, 25 provider-payload, 63 benchmark, 23
collaboration and threads, 13 failure injection, 6 performance.

### The rest of the repository is not green, and was not before this work

An earlier draft of this section said the only problems elsewhere were two
collection errors. That was wrong: it was measured with `-x`, which stops at
the first error and hid everything behind it. Measured properly, with JUnit
XML because this repository's pytest configuration suppresses the summary
line whenever there are errors:

| Scope (excluding `cockpit_v4`, `brain`, `legacy`) | Tests | Passed | Failed | Errors | Skipped |
| --- | --- | --- | --- | --- | --- |
| `main` | 9,032 | 6,157 | 409 | 176 | 2,290 |
| this branch | 9,668 | 6,764 | 412 | 176 | 2,316 |

The branch carries more tests because it holds the whole Cockpit V2 → V3 → V4
lineage on top of the `main` merge-base.

Comparing the failing test IDs rather than the counts: **zero tests fail on
`main` that pass here**, and exactly three fail here that do not fail on
`main`:

- `tests/presentation/test_decimal_contract.py::test_no_display_path_bypasses_the_contract`
- `tests/proof/test_zero_tolerance.py::TestEngineGuarantees::test_float_debris`
- `tests/release/test_product_copy.py::TestNothingOnScreenNamesAVendor::test_no_rendered_string_says_demo`

All three fail identically at `c322076`, the commit this session started from,
so none is caused by this work. They are repository-wide guards that scan
source text, and they are tripped by two earlier commits on this branch:
`423f949` (Cockpit V2) put the literal "Synthetic demonstration data" into
`frontend/src/components/ask/cockpit-v2.tsx`, and `0e90274` (the first V4
commit) put `:.4f` formatting into `backend/cockpit_v4/budgets.py`. Neither
file was modified this round.

They are real findings and they should be fixed — the decimal guard in
particular is the kind of rule worth keeping honest — but fixing them means
editing Cockpit V2 code and the budget ledger's operator messages, which is
outside what this session was asked to touch and not something to do
unattended at night. **Flagged for a decision rather than silently absorbed.**

Everything this session added or changed is in the four green suites above.

## 20. What was deliberately NOT done

- **No paid live-provider call.** No API key was requested, read or used.
- **No email, chat or webhook sent to anyone.**
- **No V3 module modified.** The three shell changes are additive and leave
  every other page's rendering identical.
- **No pull request opened, no branch merged, no second branch created.**
- **No test weakened.** No expected answer was adjusted to match the product,
  no question removed, no numerical assertion softened into prose, no
  validation disabled, no benchmark answer hard-coded, and no question
  special-cased by its text anywhere in production code.

## 21. Where a test was changed, and why

Two, both sharpened rather than relaxed:

- The legacy-endpoint guard matched the bare word `/investigations` and so
  fired on V4's own investigations under V4's own prefix. It now forbids the
  legacy PATH, and a new companion test walks every `fetch(` with balanced
  parens requiring `API_PREFIX` — which is the property that actually keeps a
  shim out, and which the substring check never had.
- The responsive assertions moved from one ratio to two, which is strictly
  more demanding: the Cockpit's fill of its content area is now checked
  separately from the shell's fill of the window.

## 22. Known limitations

- The analyst turn is scripted in every automated suite. **Answer quality —
  insight and clarity — is not measured anywhere in this report.**
- The benchmark's Layer B SQL stands in for what the model would write. That
  the model would *choose* these analyses is untested.
- The tablet viewport gives the content area 72.8% of the window because the
  navigation rail stays expanded above 767px. That is a deliberate threshold,
  not a defect, but it is the weakest of the five measurements.
- Performance is measured on this container, with a scripted analyst and a
  synthetic release of this size. It is a floor and a relative baseline, not a
  capacity statement about production hardware or a production book.
- Three repository-wide guard tests fail on this branch and not on `main`
  (§19). They predate this session and are tripped by Cockpit V2 copy and by
  the budget ledger's decimal formatting. They are unfixed and flagged.

## 23. What a live session should test first

In this order, because each one gates the next:

1. One `inspect_catalog` call. If the 400 is gone, §2 is confirmed in the only
   way it can be.
2. "Who are you?" — must finish in **one** generation.
3. Q01 (EAD by sector) — compare the published figures against
   `uat_question_bank.oracle("Q01")`.
4. Q08 (the ECL bridge) — the contributions must sum exactly to −69.7039.
5. Q13 — must return "none" rather than relaxing a condition.
6. Q11 and Q16 — the two that need a judgement, which is exactly what the
   rubric could not score here.

## 24. Cost expectation for that session

Six questions, Standard mode, at the query-mode-aware ceilings already in the
build: $1.50 per Product Help run and $3.00 per DATA_ANALYSIS run, with the
ledger reserving before each call and refusing rather than overspending.
A realistic bound for the sequence in §23 is **under $12**, and no single run
can exceed its ceiling.

## 25. Verdict

**NOT READY for live commissioning.**

What *is* established, and established properly:

- The blocker that failed every live request is understood, fixed at its
  cause, and pinned by a test that covers every tool-set variant the runtime
  can build.
- Twenty senior-credit-risk questions reconcile across a pandas oracle,
  independently authored SQL, and the real pipeline — every figure, row for
  row.
- Nothing the product publishes is unsupported by stored evidence, and ten
  injected failures all stop rather than improvising.
- The collaboration surface works and the outbox cannot claim a delivery that
  did not happen.
- The UI is correct and measured at five real viewports in a real browser.

What is **not** established, and cannot be from here:

- That the provider accepts the corrected request. The fix addresses the exact
  keyword in the exact tool the live error named, and the compatibility test
  now walks the whole surface — but a rejection is only proven cured by a
  provider that stops rejecting.
- That Opus chooses the right analysis, writes correct SQL for these questions
  unaided, and writes prose worth a senior credit officer's time. The scripted
  analyst proves the machinery carries a correct analysis to a correct answer.
  It proves nothing about judgement, and 20 of the rubric's 100 points are
  withheld for exactly that reason.

Overnight mock and local tests passing is not commissioning. The gate is §23,
run against a real credential, with the figures checked against the same
oracle that is already in the repository.
