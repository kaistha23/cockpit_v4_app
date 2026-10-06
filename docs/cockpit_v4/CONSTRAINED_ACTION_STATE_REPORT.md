# One legal transition at a time

`run-62282e6be0ca48c5a4afdd3c468e3de8` — "What is driving Stage 2 and ECL
growth?", Corporate, `v4-saudi-corporate-20q-v3`, 120-second allowance:

```
  0.137s   first action generation starts
 28.303s   rejected · operation=output_truncated
           "The response was cut off before it was complete; asking again once."
 28.3xx    the recovery generation starts — and the tool surface BROADENS:
           "Product knowledge lookup available."
 55.918s   failed: "the one structure-regeneration attempt for choosing an
            action was already used."
```

At the moment it failed: **2 of 12** generations, **9 of 24** provider
attempts, **0 of 5** executions, **0 of 3** analysis rounds, **0 of 4**
catalogue calls, **$0.33538 of $1.50**, **64.079 seconds** left.

Nothing was exhausted. The run was reported as `CALL_LIMIT`, and that was
untrue.

---

## 1. The immediate failure

**The action generation did not complete a valid tool action, twice.**

Attempt 1 is proven: `output_truncated`, at 28.303s, with no complete tool
call in it.

**Attempt 2** — reconstructed, and the reconstruction is itself a finding.
The terminal error came from `spend_format_recovery(phase="action")`, which
is reached only when a turn needs *another* structure recovery. So attempt 2
either truncated again or returned no tool call. Elapsed 27.6s against
attempt 1's 28.2s makes a second truncation the likely one — but the ledger
could not tell the two apart, because one counter is charged for both.

That ambiguity is fixed rather than guessed at. Every generation attempt now
records `truncated`, `parse_status`, `tool_names`, `usable` and
`rejected_because` alongside the stop reason, allowance, timeout, tool
choice and latency it already carried. **Both** shapes are replayed in
`test_live_run_62282e6b.py`, and both must publish.

## 2. Why `tool_choice: {"type": "any"}` was not enough

`any` requires *some* tool call. It does not narrow *what the turn has to
consider*. With five tools on the request — including a 9.7 KB answer
contract the turn could not legally fill in — "any" still means *read all of
this, then decide*, and the deciding is what ran out of output.

The live first attempt carried **15,539 bytes of tool schema** and about
**17,000 input tokens**, for a turn whose only legal transition was to run
one query.

## 3. The action-state gate

`backend/cockpit_v4/action_state.py`. CreditProbe knows the orchestration
**state**; Opus owns the analytical **content**.

| State | When | Tools on the request | Required |
|---|---|---|---|
| `NEEDS_METADATA` | readiness says something is unresolved | `inspect_catalog` | `inspect_catalog` |
| `READY_FOR_EXECUTION` | readiness says the packet holds what a query needs | `execute_analysis` | `execute_analysis` |
| `RESULT_READY` | a validated result exists, or an answer is being corrected | `finalize_response`, `read_artifact` | `finalize_response` |
| `PRODUCT_HELP` | not an analytical turn | `finalize_response` (+ `inspect_product_knowledge` where the question needs it) | — |

`NEEDS_METADATA` is a state a run passes **through**: once the catalogue has
answered, the next turn is `READY_FOR_EXECUTION`. A run cannot sit in it
reading the catalogue twice.

**What the gate does not do.** It names no relation, no measure, no
aggregation, no filter, no period arithmetic. A test parses the module's AST,
strips the docstrings and asserts the remaining *code* contains no `select`,
`group by`, `sum(`, `reporting_quarter`, `ead_`, `ecl_` or `order by`.
Choosing the analysis is the analyst's; choosing the transition is the
server's.

## 4. Forcing the specific legal tool

The provider is sent `tool_choice: {"type": "tool", "name": "<the one legal
tool>", "disable_parallel_tool_use": true}` whenever the state leaves exactly
one transition. Where more than one is legal, `{"type": "any"}`.

Claude Opus 5 accepts both forms. Claude Fable 5.1, Claude Mythos 5.1 and
Mythos Preview return 400 for either — a model-specific restriction, so it is
declared per family in the capability registry, and a run that meets the 400
anyway drops the parameter, re-sends once, and records
`request_parameters_refused`.

- **execute_analysis** is required in `READY_FOR_EXECUTION`.
- **inspect_catalog** is required in `NEEDS_METADATA`.
- **finalize_response** is required in `RESULT_READY`.

## 5. Recovery narrows. Always.

The live run's recovery was a *broader* question than the attempt that had
just failed. Now a re-ask may only ever narrow:

* the tool surface is rebuilt from the state, and a recovery never restores a
  withheld tool — `product_tool_withheld` is true on the first action *and*
  on every recovery;
* the prompt is targeted: the exact reason the last attempt was unusable, the
  question unchanged, the governed field ids already resolved, this book's
  period column and latest period, and *"Reply with ONE `<tool>` call and
  nothing else."*
* what is **not** resent: the malformed output, the product synopsis, the
  catalogue, any tool the state does not allow, any transcript this turn does
  not need.

A permanent assertion checks it on the replay: `surfaces[1] ⊆ surfaces[0]`.
And the words the live trace printed — "Product knowledge lookup available" —
are asserted absent from the whole event stream.

## 6. The re-ask allowance, and why it is now two

A re-ask is granted **while the request can still change**:

1. attempt 1 → the surface narrows to the state's one legal tool;
2. attempt 2 → after a *truncation*, the output allowance is raised once,
   from 3,072 to the 4,096 ceiling, because a truncation is direct evidence
   that the allowance was the binding constraint on that turn;
3. attempt 3 → nothing left to change. The run stops, as
   `ACTION_FORMAT_EXHAUSTED`.

This is not "increase max output and hope". The schemas, the product pack,
the tool ambiguity and the effort were all removed first; the escalation is
the residual the evidence points at, it is bounded by the answer's own
allowance, and it only ever happens after a truncation.

It still fits: **3 × 30s + 20s reserve = 110s** inside the 120-second
Standard allowance (Deep: 3 × 45s + 25s = 160s inside 240s). Standard stays
120 seconds and Deep stays 240.

## 7. Capability is not pricing

`supports_forced_tool_use` and `supports_effort_control` were read from
`price_card.json`. Whether a model accepts `tool_choice` is a fact about the
provider's API, not about its price, and an operator updating a price is not
making a claim about a request parameter. Two consequences, both bad: a
deployment that had not touched its price card silently ran without effort
control, and a reader had to open a pricing document to find out what the API
accepts.

`backend/cockpit_v4/model_capabilities.py` is now the source of truth, keyed
by model id with longest-prefix matching so a dated snapshot inherits its
family. Unknown models get a conservative default (forcing yes, effort no)
and the runtime degradation still catches a wrong guess. The price card no
longer carries either key.

## 8. Truthful failure

`CALL_LIMIT` → **`ACTION_FORMAT_EXHAUSTED`** when the re-ask for a complete
action is what ran out, and `ANSWER_FORMAT_EXHAUSTED` for the same thing on
the answer turn (where the analysis is preserved and the run settles
`PARTIAL`). A test asserts the run does not claim to be out of generations
when it has ten left.

## 9. Measured

One action turn on the failing question, before and after:

| | live failure | now (attempt 1) | now (recovery) |
|---|---|---|---|
| tools on the request | 5 | **1** (`execute_analysis`) | 1 |
| tool schema bytes | 15,539 | **2,679** | 2,679 |
| total request bytes | 59,571 | **42,069** | 42,703 |
| counted input tokens | ~17,020 | **12,047** | 12,230 |
| output allowance | 4,096 | 3,072 | **4,096** (raised, once, after truncation) |
| per-call timeout | ~118s | **30s** | 30s |
| effort | not sent | `low` | `low` |
| tool choice | not sent | `tool:execute_analysis` | `tool:execute_analysis` |

The recovery costs **+634 bytes** over the first attempt — the targeted
prompt, and nothing else.

Output and thinking token counts are provider-reported and therefore only
observable on a live call; the fields are recorded per attempt
(`output_tokens`, `stop_reason`, `provider_ms`) and the Mac retest will fill
them in. Serialization and token counting are sub-millisecond.

## 10. What was run

| Suite | Result |
|---|---|
| V4 Python | **2375 passed, 0 failed, 4 skipped** |
| V3 regression | **771 passed, 0 failed, 105 skipped** — untouched |
| Frontend unit | **538 passed** |
| TypeScript | clean |
| Browser, real Chromium | **75/75, five consecutive runs** |

New this round: `test_live_run_62282e6b.py` (6) and
`test_action_state_machine.py` (38). No paid provider call was made.

### The matrix

Thirteen questions, both books, each driven through the real orchestrator:

| Book | Question | First legal action | Provider calls | Catalogue calls |
|---|---|---|---|---|
| Corporate | What is driving Stage 2 and ECL growth? | `execute_analysis` | 2 | 0 |
| Corporate | Why is risk building in Construction? | `execute_analysis` | 2 | 0 |
| Corporate | What drove Construction ECL growth? | `execute_analysis` | 2 | 0 |
| Corporate | Which borrowers were downgraded? | `execute_analysis` | 2 | 0 |
| Corporate | Show Project Finance ECL by sector. | `execute_analysis` | 2 | 0 |
| Corporate | Which covenants are in breach? | `execute_analysis` | 2 | 0 |
| Corporate | ECL decomposition. | `execute_analysis` | 2 | 0 |
| Retail | Where is delinquency building? | `execute_analysis` | 2 | 0 |
| Retail | Which behavioural score bands deteriorated? | `execute_analysis` | 2 | 0 |
| Retail | What is driving retail ECL growth? | `execute_analysis` | 2 | 0 |
| Retail | Which products saw the largest Stage 2 increase? | `execute_analysis` | 2 | 0 |
| Retail | Which accounts deteriorated most? | `execute_analysis` | 2 | 0 |
| Retail | ECL decomposition. | `execute_analysis` | 2 | 0 |

Every one publishes. Every one is one action, one execution, one answer.

### The control case

`What is the gizmo ratio of the frobnicator, by widget class?` resolves
nothing, is classified `NEEDS_METADATA`, and is offered `inspect_catalog`
alone. Having read it, the next turn is offered `execute_analysis` alone.
The catalogue still works when it is genuinely needed — and only then.

## 11. Retesting on the Mac

No release changed. Nothing to rebuild, nothing to re-seed.

```
python3 scripts/cockpit_v4/stop.py

git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull --ff-only origin claude/cockpit-single-agent-v4-h8fsbq

python3 scripts/cockpit_v4/start.py
python3 scripts/cockpit_v4/status.py
```

Hard-refresh the browser tab (⌘⇧R).

**Nothing to configure.** The two capability flags that used to live in the
price card are gone from it; Claude Opus 5 is in the registry as accepting
forced tool use, named-tool forcing and effort control, so an action turn now
goes out with one tool, `effort: low`, a 3,072-token allowance and a 30-second
bound without anyone editing a file. If your price card still carries
`supports_forced_tool_use` or `supports_effort_control`, they are ignored and
can be deleted.

### What to look at

1. **Ask the exact failing question.** "What is driving Stage 2 and ECL
   growth?" on Corporate. The first action should be `execute_analysis` and
   the panel should reach Preparing → Validating → Executing → Publishing.
2. **Open the Construction ECL card → Investigate Further.** Two generations,
   no catalogue step.
3. **If an action is cut off anyway**, the panel says it was cut off and asks
   again *once, more narrowly* — it must never say "Product knowledge lookup
   available" on that turn — and the trace shows the second attempt with a
   larger allowance and the same single tool.
4. **If a run does fail on the action path**, the code is
   `ACTION_FORMAT_EXHAUSTED`, not `CALL_LIMIT`, and the trace shows what each
   attempt returned: truncated, no tool call, or a tool call that would not
   validate.
