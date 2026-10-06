# The action turn: why it took eighty seconds, and what it is now

Two ordinary Corporate questions were asked on the Mac against a backend
that was already correct — `v4-saudi-corporate-20q-v3`, quarterly, twenty
periods, latest `2026Q2` — and both produced the same screen:

```
Request accepted
Understanding the request                    ~80s
(the response was cut off before the stage completed; asking again)
one structure-regeneration attempt used
CALL_LIMIT
```

No query ran. No answer was published. The questions were
`What is driving Stage 2 and ECL growth?` and, in a thread seeded from the
Construction ECL card, `What drove the change in Recognised ECL for
Construction in 2026Q2?`

This round did not change the data model, the calendars, the releases, V3,
or the analytical orchestration. It changed what an **action turn is
allowed to be**.

---

## 1. The root cause

Not one thing. Four, and they compounded — each one is a reason the turn
could go wrong, and together they made the failure certain and unrecoverable.

**1. `tool_choice` was accepted and thrown away.** `Analyst.ask()` took a
`tool_choice` parameter, defaulted it to `"any"`, and never passed it to the
provider — and `converse()` had no such parameter to pass it to. Every turn
in every V4 run went out unconstrained. An action turn, whose only legal
outcome is choosing the next action, was free to answer in prose, and on an
open analytical question it did.

**2. The action turn carried the answer contract.** `finalize_response`
inlines to 9,683 bytes of schema describing narrative, coverage, numeric
claims, evidence refs, tables, charts, limitations and follow-ups. It was on
every action turn — 58% of the tool payload — which is an answer's shape
handed to a turn that has executed nothing and therefore cannot legally
fill any of it in.

**3. The action turn was given the answer's output allowance.** One number,
`reserved_output_tokens = 4,096`, served both phases. On a model that thinks
before it answers, thinking is spent from that same allowance. With ~17,000
tokens of input describing an answer it could not write, an action turn had
both the room and the invitation to spend four thousand tokens before
deciding anything.

**4. One call could block for the whole run.** `call_timeout_seconds()`
returned `min(remaining − 2s, deadline)` — about 118 seconds at Standard.
So the first action was permitted to consume the budget the second one
needed.

The observed stop reason was `max_tokens`. That is what "cut off" meant:
the turn was served and billed, the tool call was incomplete, and nothing
from a truncated turn runs. With `format_regenerations = 1`, the second
attempt hit the same four conditions, spent the recovery, and the run
stopped at `CALL_LIMIT` — which named the guardrail correctly and explained
nothing.

**The prompt was not the problem.** `analyst.md` already said "Do not write
a planning essay… Choose your next action and take it." The request
permitted one anyway. The fix is in the contract, not in more words.

### A fifth defect, found while reproducing the fourth

A client timeout was classified as a **non-retryable** provider failure. The
classifier matched the phrase `"timed out"` against the exception's *class
name* — where there is no space, so `TimeoutError` never matched — and never
looked at the message at all. A stalled action therefore failed the whole
run instead of being asked again. Bounding the action window would have
turned every slow turn into a dead run without this.

## 2. Measured, per stage of one action turn

`Analyst` now records `serialize_ms`, `count_ms` and `provider_ms`
separately on every generation attempt, plus the stop reason, the request
id, the output allowance, the timeout it was given, the tool_choice it was
sent with and the effort it asked for. "The model is slow" and "we spent the
deadline assembling payloads" are no longer the same observation.

Serialization and token counting are sub-millisecond on both failing
questions. The eighty seconds was generation.

## 3. What changed

| | before | after |
|---|---|---|
| Action `tool_choice` | accepted, never sent | `{"type": "any"}`, sent |
| Answer `tool_choice` | never sent | not sent — prose is what an answer is |
| Action tool schemas | 15,539 B | **8,588 B** |
| `finalize_response` on an action | 9,683 B, full contract | 2,732 B, stop-early subset |
| Action output allowance | 4,096 (the answer's) | **3,072** Standard / 4,096 Deep |
| Answer output allowance | 4,096 | 4,096 Standard / 6,144 Deep |
| One action's wall clock | whole remaining run (~118s) | **≤ 40s** Standard / 60s Deep |
| Answer wall clock | whole remaining run | unchanged — it is the last call |
| Effort | never sent | `low` on actions, `medium`/`high` on answers, capability-gated |
| Client timeout | non-retryable failure | transport, one retry, named in the panel |
| Seeded thread's first action | whatever it decided | `execute_analysis`, with a deterministic reason |

### The action payload, for the two questions that failed

| | before | after | change |
|---|---|---|---|
| `What is driving Stage 2 and ECL growth?` | 59,571 B ≈ 17,020 tok | **47,906 B ≈ 13,687 tok** | −19.6% |
| Seeded Construction investigation | 67,335 B ≈ 19,238 tok | **55,905 B ≈ 15,972 tok** | −17.0% |

Token figures are the local conservative estimate, labelled as such — the
same estimator the ledger falls back to when the provider cannot be asked.

Where the new bytes sit, for the seeded question: instruction 12,723 ·
governed facts 22,460 · pinned scope and readiness 4,206 · tools 8,588 ·
conversation 7,684 (of which the analysis packet is the bulk, and is the
reason that thread needs no catalogue call at all).

What came off: the product pack (`creditprobe`, `product_functionalities`,
`product_knowledge_coverage` — about 5.3 KB describing what CreditProbe *is*,
on a question about what a book *did*) and the answer half of
`finalize_response`. `inspect_product_knowledge` was already withheld from
analytical first actions; the pack it describes now goes with it.

What stayed: `cockpit_semantics.canonical_measures` — thirty governed terms
with relation, column, dtype, unit, grain, period column and join key. That
is what §8 asks for, and it is why a first action can author SQL.

## 4. Forced tool use

`tool_choice: {"type": "any"}` requires the turn to be a tool call. Claude
Opus 5 accepts it. Claude Fable 5.1, Claude Mythos 5.1 and Mythos Preview
answer `400` — it is a model-specific restriction, so it is declared on the
price card as `supports_forced_tool_use` and defaults to true.

It also degrades safely. If the provider rejects a request naming
`tool_choice`, `output_config` or `effort`, that parameter is dropped for
the rest of the run and the attempt is re-sent once — recorded on the call
report as `request_parameters_refused`, never silently. A rejection naming a
**tool schema** is a different fault and is *not* retried that way: retrying
it without `tool_choice` would hide a defect in what CreditProbe published.

No tool is ever forced by name. Which action to take stays the analyst's;
what is no longer optional is that the turn takes one.

## 5. The stop-early finalize contract

An analytical action turn is offered `finalize_response` with a strict
subset of its fields: the terminals it can legitimately reach with nothing
executed — `clarification`, `referral`, `unsupported` — and the fields those
need. `answer` and `partial_answer` are not on it, because every number in a
V4 answer is bound to a result that ran.

It is a narrowing, never a widening: a test asserts the action properties
are a subset of the full contract's, so nothing here can accept a shape the
real parser would reject. The full contract comes back on the turn that has
a result to write up.

## 6. Sufficiency, decided by the server

`semantics.readiness()` is a deterministic check, run before any model call,
that answers one question: are the facts needed to author a query for *this*
question already in this packet?

It matches the question's words against the release's own canonical measures
(with plural and British/American spelling tolerance — "covenants" is the
term "covenant"), against the governed values the value resolver recognised,
and — for a seeded thread — against the four things the analysis packet must
carry: relation, period column, reporting period and subject.

It reaches the analyst in the volatile block as `analysis_readiness`, with
`normal_first_action: "execute_analysis"` when sufficient. It names no
aggregation, no filter, no comparison and no method: which analysis to run
stays entirely the analyst's, and a test asserts the block contains no such
word.

It is capable of saying no. `What is the gizmo ratio of the frobnicator?`
resolves nothing and gets `inspect_catalog`.

## 7. What was run

| Suite | Result |
|---|---|
| V4 Python (`tests/cockpit_v4`) | **2330 passed, 0 failed, 4 skipped** (7m46s) |
| V3 regression (`tests/cockpit_agentic` + `tests/agentic`) | **771 passed, 0 failed, 105 skipped** (2m01s) |
| Frontend unit (`npm test`) | **538 passed, 0 failed** |
| TypeScript (`npx tsc --noEmit`) | clean |
| Browser, real Chromium | **75/75, five consecutive runs** |

The V4 suite was 2,256 at the start of this round. The new files:

* `test_action_contract.py` (19) — forced tool use, the degradation path, the
  stop-early contract, the two output allowances, the action window, the
  recorded stop reason, prose beside a valid tool call;
* `test_mac_action_replay.py` (7) — both Mac failures, driven to publication,
  plus the bounded-failure case;
* `test_action_payload_snapshot.py` (10) — the measured payload and what the
  request does and does not say;
* `test_action_latency.py` (15) — simulated slow turns, early cancellation,
  and five repetitions of each Mac flow;
* `test_action_matrices.py` (23) — thirteen questions, two five-step chains,
  every attention family.

No paid provider call was made. Every latency in the suite is simulated.

## 8. The acceptance gate, item by item

| | Requirement | Evidence |
|---|---|---|
| 1 | Stage2/ECL-growth replay publishes | `test_stage2_and_ecl_growth_publishes_after_a_truncated_action` |
| 2 | Construction seeded replay publishes | `test_the_seeded_construction_investigation_publishes` |
| 3 | Action turn bounded and compact | 3,072 tokens, ≤40s, 8,588 B of schema |
| 4 | Action output is tool-oriented | `tool_choice: {"type": "any"}`, no answer contract |
| 5 | No silent 80s first action | `test_the_first_action_cannot_spend_the_whole_deadline` |
| 6 | Recovery leaves time to finalize | `2 × 40s + 20s ≤ 120s`, asserted |
| 7 | No repeated nested intent | `test_no_tool_schema_asks_the_analyst_to_restate_the_run` |
| 8 | Seeded investigations skip the catalogue | 0 catalogue calls, asserted per run |
| 9 | Common governed questions skip it too | 13/13 across both books |
| 10 | No CALL_LIMIT in the Mac failures | asserted on both replays |
| 11 | No DEADLINE_EXPIRED in them | asserted on both replays |
| 12 | Five slow-stub repetitions | `test_the_*_flow_repeats_five_times_under_a_slow_provider` |
| 13 | Five full browser runs | 75/75 × 5, zero failures |
| 14 | V3 has zero new failures | 771 / 0 / 105, unchanged |

## 8b. Convergence and latency, measured

**Provider calls for a clean analysis: two.** One action, one answer. With
one bad action turn: three. Never more on either Mac flow — asserted, not
observed.

**Catalogue calls: zero.** Across all thirteen matrix questions, both
five-step chains and every attention family exercised, no run made an
`inspect_catalog` call. The tool remains available; nothing needed it.

**Simulated latency, all inside the 120-second Standard allowance:**

| Scenario | Outcome |
|---|---|
| 10s action → 30s finalization | publishes |
| 35s truncated action → 20s action → 30s finalization | publishes |
| 55s stalled action (cancelled at 40s) → 15s action → 25s answer | publishes, ×5 |
| 90s stalled action → 12s action → 25s answer | publishes |
| two 200s stalled actions | fails bounded, ≤ 2 × 40s spent |
| 5s action → 50s answer | publishes — the answer keeps the remainder |

The browser suite is 75 tests, one more than last round: a cut-off first
action driven through the real UI, asserting the panel shows the failed
attempt and then Preparing → Validating → Executing → Publishing, and that
a single cut-off action is never reported as a call limit.

## 8c. Unresolved

None blocking. Two things a reader should know:

* **`supports_effort_control` is off by default.** Until it is turned on for
  your model, action turns do not ask for low effort — every other bound in
  this round still applies, and the action window still cancels a turn that
  runs long, but the turn itself will think as hard as the model's default.
  See §9.
* **No live provider call was made anywhere in this round.** Every latency
  here is simulated and every provider response is scripted. What is proven
  is the contract, the bounds and the recovery paths; what a live Opus does
  inside them is what the Mac retest is for.

## 9. Retesting on the Mac

No release changed. Nothing to rebuild, nothing to re-seed.

```
python3 scripts/cockpit_v4/stop.py

git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull --ff-only origin claude/cockpit-single-agent-v4-h8fsbq

python3 scripts/cockpit_v4/start.py
python3 scripts/cockpit_v4/status.py
```

Hard-refresh the browser tab (⌘⇧R) after the restart.

**One optional step, and what it buys.** Open your price card — the file
`COCKPIT_V4_PRICE_CARD` points at — and, on your model's entry, add:

```json
"supports_forced_tool_use": true,
"supports_effort_control": true
```

The first already defaults to true. The second is off unless you say
otherwise, because a model that does not know `output_config.effort` rejects
the request rather than ignoring it, and a run that fails closed over a
latency optimisation is worse than a slow one. Turning it on is what makes
an action turn ask for `low` effort, which is the difference between a
decision that takes a few seconds and one that takes most of a minute. If
your model does not accept it, the run drops it after one rejection and
carries on — the call report will say `request_parameters_refused`.

### What to look at

1. **Ask "What is driving Stage 2 and ECL growth?" on Corporate.** The panel
   should move off "Understanding the request" in seconds, not eighty, and
   go through Preparing → Validating → Executing → Publishing.
2. **Open the Construction ECL card, then Investigate Further.** Ask what
   drove the change. There should be no "reading data definitions" step at
   all: the thread already holds the relation, the segment value and both
   quarters.
3. **If an action does get cut off**, the panel says so — "the response was
   cut off before it was complete; asking again once" — and then carries on
   through the real stages. A single cut-off action is not a call limit and
   no longer reads like one.
4. **Check the trace on any answer.** Each generation now records its stop
   reason, its output allowance, the timeout it was given and how long the
   provider actually took.
