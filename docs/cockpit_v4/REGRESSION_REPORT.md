# Regression report

## Method

The V3 suite is the baseline. `backend/cockpit_agentic/` is **byte-identical**
to the base commit on this branch — `git diff 015de742890e HEAD --
backend/cockpit_agentic backend/api backend/llm backend/config.py` is empty —
so any difference in its results is environmental, and this report establishes
that rather than assuming it.

Failure **IDs and their material messages** are both compared. A new failure
hiding under an old failing test name is exactly what comparing IDs alone
misses.

## Environment

| | |
|---|---|
| Revision | `claude/cockpit-single-agent-v4-h8fsbq`, base `015de742890e895026ed8558822f4bff87ddd447` |
| Python | 3.11.15 |
| Platform | Linux container (hosted session), not the user's Mac |
| Data namespace | `cockpit_v4` for V4, `cockpit_agentic_v3` for the V3 run |
| Provider credential | **absent** — no paid call in any run below |

The container started with **no Python dependencies installed at all**.
Everything below was installed during this session to make the suites
runnable: `pytest fastapi pydantic duckdb pandas pyarrow httpx jsonschema
anthropic python-dotenv sqlalchemy openpyxl uvicorn psycopg[binary]
python-multipart python-docx`. None of that is a code change.

## V3 suite — the regression baseline

```
COCKPIT_AGENTIC_V3_NAMESPACE=cockpit_agentic_v3 \
  python3 -m pytest tests/cockpit_agentic -q
```

| Run | tests collected | passed | failures | errors | skipped |
|---|---:|---:|---:|---:|---:|
| Before dependency installs | 590 | 556 | 0 | 8 | 26 |
| After `psycopg[binary]` | 590 | 556 | 0 | 8 | 26 |
| After `python-multipart` | 590 | 556 | 0 | 8 | 26 |
| **After `python-docx`** | **590** | **564** | **0** | **0** | **26** |

The 8 errors were the same 8 tests throughout — all in
`tests/cockpit_agentic/test_thread_and_api.py`, all failing **on setup**, and
each run's message named the next missing dependency in a chain:
`psycopg` → `python-multipart` → `docx`. Each is imported while constructing
the full product FastAPI application, which those 8 tests need and no other
V3 test does.

Installing the third one cleared all eight. **V3 is fully green on the V4
branch: 590 collected, 0 failures, 0 errors, 26 skipped.**

The 26 skips are V3's own pre-existing skips and were identical in every run.

Re-run at handoff on the final commit: **564 passed, 26 skipped, 0 failed**
in 107s — identical to the baseline above.

**Conclusion: V4 introduces no V3 regression.** That is supported by two
independent facts — the V3 sources are unchanged, and the V3 suite passes
completely once this container has the dependencies the suite has always
needed.

The previously reported "413 failures and 231 setup errors" is a historical
report from a different environment. It is not used as a pass criterion here,
and this environment did not reproduce it.

## V4 suite

```
COCKPIT_AGENTIC_V3_NAMESPACE=cockpit_v4 python3 -m pytest tests/cockpit_v4 -q
```

| tests collected | passed | failures | errors | skipped | elapsed |
|---:|---:|---:|---:|---:|---:|
| 492 | 492 | 0 | 0 | 0 | ~16 s |

Frontend unit suite — the whole thing, not only the Cockpit:

```
cd frontend && npm test
```

| suites | tests | pass | fail |
|---:|---:|---:|---:|
| 38 | 470 | 470 | 0 |

The Cockpit V4 components alone are 62 of those:

```
cd frontend && node --test --experimental-strip-types \
  "src/components/cockpit-v4/*.test.ts"
```

covering `reducer.test.ts`, `client.test.ts`, `live-trace.test.ts`,
`markdown.test.ts`, `attention-client.test.ts` and `greeting.test.ts`.

## Defects found and fixed while testing

Each was found by a test that failed for the right reason, not by review:

| # | Defect | Consequence had it shipped |
|---|---|---|
| 1 | A common table expression was read as an unauthorized relation. | Every legitimate multi-step SQL analysis refused. Found by the Stage-2 case. |
| 2 | An idempotent retry was rejected as a concurrent run, because the concurrency check ran before the idempotency lookup. | A dropped 202 would come back as a 409 the caller could not clear. |
| 3 | `_ps` skipped a header that `ps -o field=` does not print, so every process looked dead. | The **stop script would refuse to stop anything**. Found by the reused-pid test. |
| 4 | `NO_PROGRESS` ended the run instead of rejecting that submission. | Work that had already succeeded was thrown away. |
| 5 | A corrupt state database raised a raw `sqlite3.DatabaseError`. | An unclassified internal failure instead of the declared `STORAGE_UNAVAILABLE`, sending an operator to the wrong place. |
| 6 | The loopback demo principal used a hard-coded tenant that the seeded release does not contain. | Every analysis would read zero rows — "the portfolio is empty" rather than "wrong tenant". Found by the real-socket run. |

### Second round — the launcher/frontend wiring

| # | Defect | Consequence had it shipped |
|---|---|---|
| 7 | The launcher set `NEXT_PUBLIC_COCKPIT_V4_API` but not `NEXT_PUBLIC_API_URL`. | The Cockpit talked to the V4 API while the header, the status badge and every landing-page widget talked to `http://127.0.0.1:8000` — a backend the V4 instance never starts. |
| 8 | The V4 API served no `/api/v1/health`, so pointing the shell at it produced a 404. | The header rendered **"Backend offline"** while V4 was answering every request — the status indicator lying about the service next to it. |
| 9 | `wait_for_health` JSON-decoded the UI's root page, which serves HTML. | The decode raised, was swallowed as "not ready yet", and a healthy UI was reported as failed until the timeout expired. |
| 10 | The V4 client resolved an unset address to `""` (same origin). | A silent wrong address rather than a stated one. It now has no default at all. |

### Third round — the Cockpit page was still the legacy one

| # | Defect | Consequence |
|---|---|---|
| 11 | `frontend/src/app/page.tsx` rendered the legacy Cockpit. The V4 component existed and was imported by nothing. | Pressing Ask ran the legacy flow: `POST /api/v1/investigations` and `/api/v1/agentic/officer`, both 404 against the V4 API. **Opus V4 was never reached.** |
| 12 | Shell chrome polled `/demo`, `/auth/me`, `/ai/status` and `/workspace/notifications`. | Four 404s per page load in the V4 API log, obscuring the Cockpit under test. |
| 13 | The replay effect aborted its own async work under React StrictMode's double mount. | A browser refresh silently failed to reconnect to the running run. |
| 14 | The stub's "slow" case blocked 30s inside one `converse`. | Made a correct implementation look broken: V4 observes a cancellation *between* actions and does not abort an in-flight provider call — that is what the deadline and supervisor are for. Fixed in the test, not the product. |

### Fourth round — the false process trace, the tool contract, and Product Help

| # | Defect | Consequence |
|---|---|---|
| 15 | `client.ts` registered only `source.onmessage`. The server sends **named** SSE frames (`event: run.progress`), and a named frame never reaches `onmessage`. | The live process panel was a **false trace**: a run that really executed 15 events for 29.4s displayed "not started" and "Answered in 0s". The panel was reporting on a stream it was not receiving. Fixed by registering a listener for each of the 22 names in `backend/cockpit_v4/events.py`, with a `seq` guard against replays. |
| 16 | `finalize_response` advertised `clarification_question` as `["string","null"]` but the server validator demanded `str`. | A model that followed the published schema had its **answer rejected** on the null. Fixed at the boundary: the validator now accepts absent-as-null for every optional field across all tools, while mandatory fields still fail closed. Prompt wording was not used. |
| 17 | `execute_analysis`'s schema declared `expected_units` an **object**; the parser demanded a **string**. | A model following the schema would have had *every* analysis submission rejected. Found by mechanically diffing each published schema against its parser (`test_tool_contract_agreement.py`). |
| 18 | Elapsed time was computed client-side from wall clock. | A refresh mid-run restarted the timer at 0. The panel now reads `budget.elapsed_seconds` from the run status when the run is settled, and marks it authoritative. |
| 19 | Sub-second stages rendered as "0s". | A real 0.4s stage looked like it had not run. `formatSeconds` keeps one decimal below 10s. |
| 20 | A later successful attempt replaced the failed one in the panel. | The trace hid that anything had gone wrong. `Step.failures` is now incremented and never cleared; the panel keeps the failed substep visible under a succeeded step. |
| 21 | Product Help had no grounded source. | Answers about CreditProbe were the model's guess. A versioned **Product Knowledge Pack** (`backend/cockpit_v4/product_knowledge.json`, pack `2026-09-11.1`, source SHA256 `bdb3ce5d…`) is now ingested from the deck, with a ~924-token synopsis always in base context and a fifth tool, `inspect_product_knowledge`, for selective retrieval. The deck is never attached wholesale. |
| 22 | Keyword retrieval matched substrings: `"kpi"` matched inside **"cockpit"**. | Irrelevant sections retrieved on almost every question. Fixed with a leading word-boundary pattern (a both-sides boundary was also wrong — it made "layers" and "automatically" miss). |
| 23 | Answers were rendered as plain text. | Markdown arrived as raw `**` and `-` characters. Now parsed to a React element tree — no HTML string is produced, so there is nothing to sanitize and no new dependency was added. |
| 24 | `markdown.ts` and `markdown.tsx` shared a stem. `tsc` resolved `./markdown` to the `.ts`; **Turbopack resolved it to the `.tsx` itself**, a self-import. | The page failed to build while every type check passed. Only running the app caught it. Renamed to `markdown-parse.ts`. |

Slide 14 of the deck describes a **multi-agent** design. It is recorded in the
pack as `historical_architecture`, `status: HISTORICAL_ARCHITECTURE`,
`label: NOT_CURRENT_V4_ARCHITECTURE`, `applies_to_current_runtime: false`, and
is deliberately **not retrievable**, so the analyst cannot describe the current
runtime as multi-agent.

### Fifth round — the home feed, the one-call product path, and real input

| # | Defect | Consequence |
|---|---|---|
| 25 | Broad Product Help spent a generation retrieving what the synopsis already carried. The live run `run-53dfe2c6f10c480bb7b2d033273a6427` took 2 generations, 28.048 s and USD 0.19004 to answer "Who are you?". | Every identity question cost twice what it needed to. Fixed deterministically: `product_knowledge.coverage` withholds `inspect_product_knowledge` from the FIRST action unless the question names product detail the synopsis does not carry, and the full set is restored for every action after it. No hard-coded answer, no extra model call, no prompt-only fix. |
| 26 | The covenant breach share read 0% in 2026Q1 and 48% in 2026Q2 across four sectors at once. | The attention feed's first five cards were four variations of a **data gap**: this release records covenant headroom in alternate quarters, and a quarter with covenant rows but no headroom and no breach date is a quarter where the test was not observed, not one with no breaches. The base now counts observed tests only, and three indicators carry an explicit observation floor. Found by the independent pandas oracle disagreeing with the engine. |
| 27 | Every large movement scored exactly 100. | `min(1, x/reference)` saturated, so the five biggest issues in the book ranked on a tie-break rather than on size. Replaced with `x/(x+reference)`, which is monotone everywhere. |
| 28 | Five cards could be five sectors saying the same sentence. | De-duplication was per `(segment, family)` only. A four-pass selection now prefers a new segment AND a new issue type first, so the feed reads as five findings rather than one finding five times. |
| 29 | "Explain External Intelligence" retrieved nothing. | External intelligence is one of the four Early Warning layers, and the layer keywords did not include the layers' own names. A question the specification lists as a retrieval question fell through to the synopsis. |
| 30 | The browser stub's turn counter was keyed by question text. | A second run of the same question started at turn 1 and read a tool result that did not exist, so a working seeded investigation surfaced as `PROVIDER_UNAVAILABLE`. A test defect, not a product one — and it was hiding whether the seeded flow worked at all. Now counted from the assistant turns in the request, which is per-conversation and stateless. |
| 31 | A reader who clicked Investigate Further and typed immediately could land in a new thread. | The seeded thread id was adopted by an effect; `ask` read component state. It now reads the prop directly, so there is no window. |

### Sixth round — analytical execution, and the landing page restored

| # | Defect | Consequence |
|---|---|---|
| 32 | `Intent.may_execute` required an EMPTY `ambiguities` list, and the analyst put its resolutions in it. | "Exposure read as reported EAD", "Period not specified: using the latest populated quarter 2026Q2 against 2026Q1" and "ECL read as booked ECL" each **refused the analysis they had made possible**. Careful behaviour was penalised and the only way to run was to say nothing. Split into `blocking_ambiguities` (the only field that stops execution), `resolved_assumptions` and `canonical_mappings`, with every resolution now visible in the trace. |
| 33 | `steps[].parameters` was published on every step of `execute_analysis` and never reached the engine. | A model following the schema wrote `WHERE reporting_quarter = ?`, and DuckDB refused with *"Values were not provided for the following prepared statement parameters: 1"*. A bind failure caused by a contract the application advertised and did not honour. Reproduced exactly against the pinned release before anything was changed. |
| 34 | "Query validated" was emitted before DuckDB had been asked whether the query bound. | Two true statements in the wrong order. `validate_batch` now proves every step bindable with `EXPLAIN` — which executes nothing — and the event says the query was checked and how much of it was bound. (The wording was later replaced again: it had claimed the whole batch was bound when only the non-deferred steps were, and printed the analyst's declared grain and units as though the server had verified them.) A submission refused at the binder is recorded as a numbered submission with status `rejected`, so the failed SQL is on file. |
| 35 | A failed step read "failed at the bind check" in the same shape as a step that ran and failed. | A query that never bound did not execute, and a trace that implies it did sends an operator to the wrong place. `StepResult` now carries `phase` and `executed`, and the messages differ: "did not bind and was not run" versus "failed while running". |
| 36 | Standard's 60-second deadline and $1.00 ceiling were set for a product question and applied to an analysis. | A live analytical run expired mid-query, and another stopped at COST_LIMIT with $0.71 committed. DATA_ANALYSIS now gets 120s/$1.50 Standard and 240s/$3.00 Deep, adopted when the analyst declares the mode and never at the expense of a Product Help run. The stored `deadline_at` moves too, or the supervisor would kill a 120-second analysis at sixty. |
| 37 | The response reserve was a fixed maximum, and a budget that could not carry it stopped the run. | A shorter answer was affordable and nobody offered one. The allowance is now reduced to what the remaining budget can pay for — with the cap sent to the provider reduced to match, so the reservation stays a true projection — and the run fails closed only below a 1,024-token floor. |
| 38 | A terminal COST_LIMIT or DEADLINE_EXPIRED buried the failure that actually started the trouble. | The first analytical failure is kept and appended to the terminal message and its detail. |
| 39 | The V4 landing page was a narrow centred column with the process panel occupying it before anything had been asked. | Restored to the earlier Cockpit's layout — greeting, "What's on your mind?", one wide Ask box, business prompt chips, the Trace line, Requires attention with its reporting period, ECL highlights, Continue where you left off — on the V4 backend only. No legacy endpoint, component or flow came back with the look. |

### Seventh round — one landing page composition defect

| # | Defect | Consequence |
|---|---|---|
| 40 | The "All" tab merged the segment feed and the ECL feed into the upper section. | Requires attention listed "Information Technology carries the most ECL in the book", "Bhavani Cements is the largest single ECL contributor", "Stage 2 share of the book fell" and "Transport and Logistics had the largest ECL reduction" — a book-wide observation and a single borrower among them — and then showed every one of them again in the ECL section below. Two dashboards answering two questions were rendered as one list and a duplicate. The tab existed only to merge feeds that are both already visible, so it is gone. |
| 41 | Nothing in the engine stopped it. | Composition was a rendering decision, which is how it went wrong. Every item now declares an explicit `scope` (`segment` / `borrower` / `portfolio`), and `check_composition()` refuses a feed whose segment list holds a non-segment item, whose two lists share an id, or whose two lists share a headline. |

Neither changed the ranking. The engine's SQL, gates, formula, tie-breaks,
cache and pandas oracle are untouched, and a test asserts the top five are
still the oracle's top five.

### Eighth round — the catalogue loop

| # | Defect | Consequence |
|---|---|---|
| 42 | `inspect_catalog` expanded `relation_ids` into every column when `field_ids` was empty: 991 definitions, first page of 60, "931 remaining". | The live loop's fuel. A partial dump that advertises more looks like progress, so the analyst paged on — three catalogue calls, a truncated generation, and a 120-second deadline spent without reaching SQL. Expansion beyond one page is now refused with the relation sizes and what to specify; an explicit field-id list is still served and still pages. |
| 43 | An unscoped request returned the full relation list, or an unexplained empty array. | An unscoped request is not a request for the whole catalogue. Refused compactly, under 2 KB, naming the authorized relations. |
| 44 | Responses did not say whether the request had been satisfied. | A model that cannot tell whether it made progress asks again. Every response now carries `requested` / `returned` / `already_known` / `still_missing` / `coverage_complete_for_request` / `added_new_information`. |
| 45 | Nothing bounded repetition; `catalog_calls = 4` bounded only the count. | Four identical calls, four generations. Two consecutive calls adding nothing now produce a typed warning; a third is terminal `NO_PROGRESS`. `catalog_calls` stays at 4 — the fix is on repeating, not on asking, and a test asserts distinct exploration is unaffected. |
| 46 | The starting context carried term→field mappings but no type, unit, grain, period column or join key. | A defensible reason to read the catalogue for a question whose every term was already resolved. `semantics.field_packet()` now supplies those facts for all 16 mapped fields, ≈6.9 KB, and a test fails if any method language appears in it. |
| 47 | `sample_rows` was an independent integer in the published schema and rejected by the parser without `"samples"` in `detail`. | "sample_rows requires 'samples' in detail" — the finalize_response null defect in a different field. The two forms are now the same request, and six schema-valid payloads are asserted to parse. |
| 48 | A sample ran `SELECT *`: 198 columns of borrower data to show shape. | Samples now read the requested columns only, at most 12 unnamed, 10 rows, 2 relations, with their purpose and exact scope returned. An unscoped sample is refused. |

The truncated generation's stop reason was `max_tokens`. The handling was
already correct — nothing executes, the partial turn is rolled out of history,
one counted regeneration — so the fix was upstream: the dumps that inflated
the conversation, and a prompt that now asks for compact tool actions. Neither
the output allowance nor the 120-second deadline was increased.

### Browser suite

```
python3 scripts/cockpit_v4/browser_evidence.py
```

Real Chromium, real Next.js UI, real V4 API, **stubbed analyst**. 35/35 pass.
Every network request the page makes is recorded, so "never calls the legacy
flow" is checked rather than asserted. A screenshot of a rendered answer is
written to `docs/cockpit_v4/evidence/cockpit_v4_answer.png`.

Eleven of the twenty-five cover the home feed end to end: both sections load
from the pinned release, five cards are five different segments, a click opens
the right-side drawer with its "why it appeared" and its numbers, the drawer
offers borrower drill-down and states that the release has no subsegment
level, drivers never claim cause, Investigate Further opens a real V4 thread,
a follow-up runs inside that thread without restating the segment, the seeded
context load appears in the process panel, an ECL highlight does the same, a
reload keeps the investigation, and a failing feed leaves Ask working.

Eight more cover the restored landing page: the time-aware greeting (with a
name only when the session has one), "What's on your mind?", an Ask box that
spans the workspace and sits above the attention feed with no process panel
reserved while idle, prompt chips that ask and can be dismissed, the Trace
line and its explanation, Requires attention with its reporting period and
Segments requiring attention holding only segment-scoped cards with no
merging tab, the two dashboards proven to share no id and no headline, both
opening the same drawer, Continue where you left off listing a conversation that really happened, an Arabic answer
rendering without forcing a horizontal scroll, and the absence of every legacy
component marker.

A full-page screenshot of the restored landing page is written to
`docs/cockpit_v4/evidence/cockpit_v4_landing.png` for review against the
reference the requirement was written from.

## Suite results at handoff

All run in this container, in this order, on the commit being handed off.

| Suite | Command | Result |
|---|---|---:|
| V4 backend | `python3 -m pytest tests/cockpit_v4` | **492 passed**, 0 failed |
| — launcher + frontend wiring subset | `… test_launcher_safety.py test_frontend_wiring.py` | 43 passed |
| — Product Help benchmark | `… test_product_help_benchmark.py` | 81 passed |
| — Product Help semantics / tool policy | `… test_product_help_semantics.py` | 61 passed |
| — spelling, telegraphic and multilingual input | `… test_language_and_intent.py` | 40 passed |
| — attention ranking, oracles, seeding and composition | `… test_attention_feed.py` | 42 passed |
| — analytical execution, binding and budgets | `… test_analytical_execution.py` | 31 passed |
| — catalogue convergence and the no-progress guard | `… test_catalog_convergence.py` | 18 passed |
| — tool schema / parser agreement | `… test_tool_contract_agreement.py` | 33 passed |
| — tool contract agreement | `… test_tool_contract_agreement.py` | 26 passed |
| — event contract parity | `… test_event_contract_parity.py` | 5 passed |
| — "Who are you?" acceptance | `… test_who_are_you_acceptance.py` | 7 passed |
| Frontend unit | `npm test` (`node --test`, 38 suites) | **470 passed**, 0 failed |
| — Cockpit V4 components only | `node --test 'src/components/cockpit-v4/*.test.ts'` | 62 passed |
| Browser (real Chromium) | `python3 scripts/cockpit_v4/browser_evidence.py` | **35/35 passed** |
| V3 regression | `python3 -m pytest tests/cockpit_agentic` | **564 passed**, 26 skipped, 0 failed |
| Acceptance coverage | `python3 scripts/cockpit_v4/acceptance_evidence.py` | 100 / 100 covered |

The V3 result is the material one for isolation: `backend/cockpit_agentic/` is
unchanged on this branch and its suite is green.

Two design rules were also refined because a test showed the original was
wrong, not because a test was inconvenient:

- A **partial answer** that states plainly what it could not establish is now
  accepted with no numeric claim. Only a full `answer` must carry bound
  evidence. Refusing an honest concession was the wrong behaviour.
- `NO_PROGRESS` now returns to the analyst as a submission-level rejection, so
  a supported partial answer is still reachable.

## What was NOT run

- **The 84-question bank.** Not run, per the instruction, and not run
  automatically by anything in this build.
- **Any paid live provider call.** No credential was authorized in this
  environment. Every acceptance case's `real_provider` field reads `NOT RUN`,
  and the browser suite runs against a **stubbed analyst** — it proves the
  delivery path, not the model's answers.
- **Python analysis.** `pyrunner.probe()` reports the capability unavailable
  in this container: the escape self-test found network access was not
  blocked, so the runner refuses to certify itself.
- **The full repository suite.** Only `tests/cockpit_agentic` and
  `tests/cockpit_v4` were run, plus the frontend unit suite. This report makes
  no claim about the rest of the repository.
