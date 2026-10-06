# UAT results

**Read the labels.** Every result below says how it was obtained. Nothing here
is a live-provider measurement, because no paid run was authorized in this
environment.

| Label | Meaning |
|---|---|
| MODEL MOCK | the analyst is scripted; the runner, store, catalog and event stream are real |
| REAL DATABASE | real DuckDB against the published `v4-saudi-20q-v1` release |
| REAL HTTP | a real ASGI client over the real router |
| REAL SOCKET | a real uvicorn process on a real port, real SSE frames read incrementally |
| REAL PROCESS | real OS processes and ports |
| ORACLE | the expected number computed with pandas straight from the Parquet files, never through the code path under test |
| NOT RUN | no coverage in this build |

## Acceptance gates

| Gate | Status | Evidence |
|---|---|---|
| **G0** safe foundation | **PASS** | base `015de742890e` verified against the remote; V3 sources byte-identical; separate worktree, branch, namespace, release, state DB, ports; diagnosis separates OBSERVED/REPRODUCED/HYPOTHESIS/NOT AVAILABLE |
| **G1** observable help | **PASS (mock) · live BLOCKED** | "Who are you?" completes in **one** generation with no metadata, SQL, Sonnet or summary; progress is persisted and visible. Live G1 needs a credential this environment does not have. |
| **G2** analytical correctness | **PASS** | EAD-by-sector and the four-quarter Stage-2 comparison both match independent oracles, including the sector that *entered* stage 2 |
| **G3** bounded recovery | **PASS** | tool-history ordering proven on the stored canonical history; a real model-authored repair after a truthful diagnostic; every counter exercised against the live loop |
| **G4** reliable delivery | **PASS** | persist-before-publish; reconnect replays without a second paid run; supervisor settles a dead worker as INTERRUPTED; answer retrievable after a delivery failure |
| **G5** security and evidence | **PASS** | domain, tenant and artifact isolation; six SQL escape attempts refused; secrets redacted on the way in; numeric claims bound to exact stored values; no code path can trim a plan |
| **G6** usability | **PASS (mock, in a real browser)** | hideable panel with per-substep detail and auto-expansion of the failed step; one-command start/status/stop. 14/14 real-Chromium tests against the real UI, including that the panel shows real stages rather than "not started" at 0s, that the answer renders as Markdown, and that a failed attempt stays visible after a later one succeeds. |
| **G7** comparative live evidence | **BLOCKED** | requires an authorized paid run |

**Verdict: READY_FOR_CONTROLLED_UAT, not production-ready.** G7 is
outstanding, and G1 is proven only through a mock.

## The three working checkpoints

### 1. "Who are you?" — MODEL MOCK, REAL SOCKET for the delivery path

| Measure | Result |
|---|---|
| Generation calls | **1** |
| Catalog calls | 0 |
| Execution submissions | 0 |
| Sonnet / preprocessing calls | 0 |
| Summary calls on the answer path | 0 |
| Whole catalogue in the prompt | **no** — asserted field-by-field |
| Terminal state | `COMPLETED` |

`test_who_are_you_finishes_in_one_generation`,
`test_help_does_not_receive_the_whole_catalogue`

### 2. Latest-quarter EAD by sector — MODEL MOCK, REAL DATABASE, ORACLE

| Measure | Result |
|---|---|
| Generation calls | 2 |
| Execution submissions | 1 of 5 |
| Sectors returned | 11 |
| Oracle agreement | **exact for all 11 sectors** (tolerance 1e-5 on float money) |
| Largest sector | Information Technology, 5,231.577413502459 SAR million |
| Rendered in the answer | `5,231.58 SAR million` — exact value bound, displayed at declared precision |
| Terminal state | `COMPLETED` |

`test_ead_by_sector_matches_an_independent_oracle`

### 3. Stage-2 exposure change over the latest year — MODEL MOCK, REAL DATABASE, ORACLE

| Measure | Result |
|---|---|
| Quarters compared | 2025Q2 → 2026Q2 |
| Total change | +135.363111 SAR million, matching the oracle |
| Sectors that **entered** stage 2 | Manufacturing — present in the result and marked `entered` |
| Sectors that **exited** | none in this release |
| Per-sector agreement | **every sector matches the oracle** |
| Column total vs oracle total | matches |

This is the case an inner join fails silently: it would drop Manufacturing and
report a smaller change with nothing saying so. The test asserts the entering
sector is present, marked, and carries a zero prior.

`test_stage2_year_change_handles_entering_and_exiting_sectors`

## The real-socket delivery run — REAL SOCKET, MODEL STUB

`scripts/cockpit_v4/live_path_evidence.py`, recorded in
`docs/cockpit_v4/evidence/live_path.json`.

| Measure | Result |
|---|---|
| Accepted → **first visible event** | **144 ms** (target: under 1 s) |
| Accepted → visible final answer | 2,982 ms |
| Progress arrived **before** the answer | **yes** |
| Distinct stages streamed | accepted, understanding, preparing, validating, executing, publishing |
| Reconnect from cursor 2 | replayed seqs 3…18, in order, **no new run** |
| Cancellation of a working run | settled `CANCELLED` |
| Terminal state of run 1 | `COMPLETED` |
| Settled cost (fixture price schedule) | USD 0.081 |
| `cost_enforced` | true |

Roughly 2.4 s of the 2,982 ms is a deliberate delay inside the stub, standing
in for model latency. **No model latency is reported.**

## Live commissioning set (10 cases)

| # | Case | Status |
|---|---|---|
| 1 | Who are you? — one call, no analytical tool | mock PASS · **live NOT RUN** |
| 2 | PIT/TTC explanation — no portfolio claim | mock PASS · **live NOT RUN** |
| 3 | Latest-quarter EAD by sector | mock+oracle PASS · **live NOT RUN** |
| 4 | Stage-2 change vs four quarters earlier | mock+oracle PASS · **live NOT RUN** |
| 5 | Rating + DSCR/covenant/collateral join integrity | grain rules PASS · **live NOT RUN** |
| 6 | Controlled failure then **real Opus** repair | mock repair PASS · **live NOT RUN — this case is not satisfiable without a provider** |
| 7 | EWS and What-if referral pair | PASS, zero excluded-domain execution |
| 8 | Non-English query with number, entity, exclusion | PASS — Hindi, Bengali, Arabic, Hinglish fixtures |
| 9 | Contextual follow-up theory → data → What-if | PASS, new run id, same thread |
| 10 | Browser reconnect/cancel + Python capability | reconnect and cancel PASS over a real socket; **Python UNAVAILABLE and reported as such** |

Case 6 deserves emphasis: the *mechanism* is proven — a truthful diagnostic
goes back, the application changes nothing, and a corrected batch runs. That
the corrected code came from a scripted turn rather than from Opus is the
whole gap, and it is not closed by anything in this build.

Case 10's Python half is **not** marked passed. The analyst wrote SQL; the
Python runner is unavailable; reporting that as a Python pass is exactly the
false claim the case exists to prevent.

## Capabilities in this environment

| Capability | Status | Why |
|---|---|---|
| `ready_for_product_help` | needs a credential and a verified price card | neither is present here |
| `ready_for_sql_analysis` | **working** | real DuckDB against `v4-saudi-20q-v1` |
| `ready_for_python_analysis` | **unavailable** | the jail's escape self-test found network access was not blocked, so the runner refuses to certify itself |

## Acceptance inventory

100 of 100 cases carry automated evidence
(`docs/cockpit_v4/ACCEPTANCE_CASES.json`), each with the test that asserts it
and its label. **Every case's `real_provider` field reads `NOT RUN`.**

At handoff:

| Suite | Result |
|---|---:|
| V4 backend (`tests/cockpit_v4`) | 492 passed, 0 failed |
| Frontend unit (`npm test`, 38 suites) | 470 passed, 0 failed |
| Real Chromium (`browser_evidence.py`) | 33 / 33 passed |
| V3 regression (`tests/cockpit_agentic`) | 564 passed, 26 skipped, 0 failed |
| Acceptance coverage | 100 / 100 |

Zero failures and zero errors throughout. The 26 V3 skips are V3's own,
pre-existing and unchanged.

## Product Help — MOCK ANALYST, GROUNDED PACK

Product Help is now answered from the Product Knowledge Pack
(`2026-09-11.1`, ingested from the 14-page functionality deck, source SHA256
`bdb3ce5d…`), not from the model's recollection. The ~924-token synopsis sits
in the starting context, so **"Who are you?" costs exactly one model call and
no tool call**.

`test_product_help_benchmark.py` runs 30 product questions with 81 assertions,
covering: the seven functionalities; the senior-credit-officer framing; that
no answer describes the current runtime as multi-agent (slide 14 is recorded
as `HISTORICAL_ARCHITECTURE` and is not retrievable); that deck figures are
never presented as live portfolio values; and that a broad product answer
carries no schema inventories, route names or release identifiers.

This is grounding and framing evidence against a **stubbed** analyst. It is not
evidence about what Opus will write. That still needs G7.

## What a paid live commissioning run would need

1. `COCKPIT_ANTHROPIC_API_KEY` in the authorized runtime — via the environment
   or the macOS Keychain, never pasted into a chat window.
2. `config/cockpit_v4/price_card.json` filled in with the current published
   schedule for the exact model id, all four billing classes and a real
   `verified_at`. Until then V4 refuses to make a paid request.
3. An agreed total test budget. Per-run ceilings are USD 1 Standard / USD 2
   Deep and are enforced by reservation, but the *total* for a commissioning
   session is the owner's decision, not a setting.
4. Explicit approval to run the ten commissioning cases — and only those. The
   84-question bank is not run automatically by anything here.

## The next live UAT, and what would confirm it

Seven paid tests, to be run by a human on the Mac with an authorized
credential. **Nothing here runs them.**

| # | Question | What would confirm the fix |
|---|---|---|
| 1 | "Who are you?" | ONE generation, no `inspect_product_knowledge`, a polished answer, a truthful trace, and both latency and cost below the recorded 28.048 s / USD 0.19004 |
| 2 | "What is Cockpit of CreditProbe?" | a Cockpit-scoped answer, grounded in the pack, materially different from the answer to test 1 |
| 3 | "wat is cockpit of creditporbe" | the same understanding as test 2, with no clarification asked about the spelling |
| 4 | "CreditProbe ka cockpit kya karta hai?" | understood, and answered in the language it was asked in |
| 5 | A Segment Requires Attention card → drawer → Investigate Further → "show me the customers behind this" | the seeded segment and quarter are used without restating them |
| 6 | "What is total exposure at default by sector in the latest quarter?" | the real SQL path, matching `oracles.ead_by_sector` |
| 7 | "Which sectors saw the largest increase in Stage 2 exposure over the latest year?" | the correct year comparison and ranking, with no silent inner-join loss |

Tests 1–4 are the ones the mock cannot settle: they are about what Opus
writes, and every automated result in this build is explicitly about what
CreditProbe puts in front of it and what it does with the answer.

## The live UAT after this round

Still not run here. Four paid tests, on the Mac, with an authorized credential.

**A. The landing page**, by eye: greeting, the wide Ask box, the prompt chips,
the Trace line, Requires attention with its reporting period, ECL highlights,
the right-side drawer, and Continue where you left off. The reference for the
layout is `docs/cockpit_v4/evidence/cockpit_v4_landing.png`.

**B.** "What is total exposure at default by sector in the latest quarter?" —
no clarification asked, the binder proven before the query was announced
as checked,
the query executed, the result matching `oracles.ead_by_sector`, and the whole
run inside the 120-second / $1.50 analytical allowance.

**C.** "Show me ECL by sector in the latest quarter and rank the top five
sectors." — booked ECL, latest populated quarter, ranked, matching
`oracles.top_sectors_by_ecl`.

**D.** "Which sectors saw the largest increase in Stage 2 exposure over the
latest year?" — 2026Q2 against 2025Q2, outer-preserving, with no sector lost
to an inner join, matching `oracles.stage2_year_change`.

Only after these pass does testing widen.

## What the next paid run should show

"What is total exposure at default by sector in the latest quarter?"

| | |
|---|---|
| Semantics | resolved automatically, as they already are |
| Catalogue calls | zero, or one targeted call naming field ids |
| Never | a 60-field dump, a `sample_rows` rejection, a repeated identical call |
| Path | generation 1 → `execute_analysis` → bind proof → SQL → generation 2 → answer |
| Inside | 120 s and $1.50 |

Then ECL top-five by sector, then Stage 2 over the latest year.
