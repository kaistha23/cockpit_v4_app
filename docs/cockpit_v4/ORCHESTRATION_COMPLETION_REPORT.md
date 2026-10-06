# Cockpit V4 — analytical orchestration completion

Branch `claude/cockpit-single-agent-v4-h8fsbq`. Starting point: Mac checkpoint
`6d54b8e`. No paid provider call was made in this round, and nothing below is
presented as a live measurement.

The objective was one thing: carry a mathematical request all the way from the
user's question to the user's screen — Opus understands, Opus authors the
analysis, CreditProbe validates it, SQL binds, SQL executes, the result is
preserved, Opus interprets, CreditProbe validates the final answer, and the
**user actually receives it** — without catalogue loops, oversized
tool-generation responses, model-response truncation, one recovery phase
consuming another's allowance, successful SQL being discarded, final-answer
retries rerunning the analysis, or provider calls ignoring the time the run
has left.

---

## 1. The two live failures, root-caused

**Failure A — successful SQL, then CALL_LIMIT.** A run was cut off while
authoring its action, recovered, executed correctly, and then stopped at
publication with *"the one structure-regeneration attempt for this run was
already used."*

One counter, `format_recoveries`, limit one, was spent by three different
situations in two different phases: a truncated action, a turn with no tool
call, and a rejected batch. A hiccup while AUTHORING the analysis silently
disarmed the recovery that WRITING THE ANSWER would later need. The analysis
succeeded; the bookkeeping around it threw the result away.

The first regression written for this failure **passed on unmodified code** —
because the assumed cause (the answer failing validation) already had its own
counter. The live message named *structure*-regeneration, which is truncation,
not validation. Rewritten as truncation-then-truncation, it reproduced the
live message verbatim.

**Failure B — the seeded Manufacturing covenant investigation.** The query
executed and returned seven borrower rows; the run then spent its deadline on
provider turns. One catalogue call in it returned **59 field definitions**.

`cockpit_covenant_quarter` carries exactly 59 columns in this release, and no
covenant column is among the sixteen canonical measures the starting context
resolves. So naming the relation was the only way a covenant question could
ask anything at all, and the catalogue answered honestly and completely. The
analyst did not ask for too much; it had no narrower way to ask.

## 2. Recovery budgets, separated by phase

`format_recoveries` is now `action_format_recoveries` and
`answer_format_recoveries`, each still limited to **one**. Four distinct
allowances now exist and none can consume another:

| Allowance | Limit | Spent by |
|---|---|---|
| `action_format_recoveries` | 1 | a truncated, tool-call-less or rejected ACTION turn |
| `answer_format_recoveries` | 1 | the same, once the analysis has executed |
| `answer_corrections` | 1 | an answer that fails its evidence check |
| transport retries | separate | a request that did not complete |

This is not a widening. Before the split, two failures shared one token; after
it, each phase has exactly the one it always nominally had.

## 3. The model-call purpose taxonomy

Every generation now declares what it is for, on the ledger reservation, in
the trace detail and in the panel line a reader sees:

| Purpose | Panel says |
|---|---|
| `ANALYSIS_ACTION` | Preparing the next action |
| `ACTION_FORMAT_RECOVERY` | Asking again for a complete action |
| `FINAL_ANSWER` | Writing the answer from the result |
| `ANSWER_FORMAT_RECOVERY` | Asking again for a complete answer |
| `ANSWER_CORRECTION` | Correcting the written answer against the result |

The retry notice already says what went wrong, so these say what is being
asked for now rather than repeating the same sentence twice in a row.

**A defect this taxonomy found the moment it was readable:** the purpose was
never reset after a recovery succeeded. A run cut off once reported its FINAL
ANSWER as a structure re-ask — on the ledger reservation as well as in the
trace — so the cost of writing the answer was booked against a failure that
had already been repaired. The label now belongs to the call that recovers and
to no call after it.

## 4. Truncation: the root cause

Truncation was not a prompt problem. A truncated turn is INCOMPLETE, not
shorter: nothing in it runs, the partial assistant message is rolled out of
history, and the run pays for one re-ask. What made it terminal was that the
re-ask allowance had usually been spent by a different phase (§2), and that
the answer turn carried an action turn's worth of context into the call most
likely to be long (§5).

The prompt already tells the analyst to keep a tool action compact — one next
action and only the public rationale it needs, with length belonging in the
final answer. That wording is unchanged and is not what this round relied on.

## 5. Payload compaction, measured

The answer turn now carries only the tools an answer can use and only the
system context an answer needs. The catalogue index, the canonical semantics,
the product synopsis and the module registry decided what to run; once the
result is in hand they decide nothing. Narrowing is **per call** — the full
set is restored immediately, so a run that genuinely needs another analytical
round still has every tool.

Simple analysis, measured (`docs/cockpit_v4/evidence/orchestration.json`):

| | action turn | answer turn | change |
|---|---|---|---|
| system context | 30,029 B | 14,006 B | −53% |
| tool schemas | 22,786 B | 13,505 B | −41% |
| tools offered | 4 | 2 | −2 |
| counted input tokens | 24,100 | 15,655 | −35% |

The conversation itself grows between the two calls — it now carries the
executed result — so the saving shows up where it was made.

## 6. The finalization packet

`context.finalization_system()` replaces the authoring blocks with a compact
note for answer turns. `_finalization_tools()` strips `execute_analysis`,
`inspect_catalog` and `inspect_product_knowledge` once the analysis has
executed or an answer has already been refused. Both are restored in a
`finally`.

## 7. Seeded-context design, and the field counts

When an attention card seeds a thread, the case file is built **before the
first model call**, from the columns that card's own server-authored SQL
reads plus the columns identifying a row at its relation's grain. CreditProbe
already knows them: it ran that SQL to build the card.

What it is not is a method. It states type, unit, aggregation, allowed values,
grain and join key. Which field answers the question, how to aggregate it and
which quarter to compare stay with the analyst, and `inspect_catalog` remains
available for anything the card was not built on.

**Field definitions delivered to a seeded thread, before and after:**

| | before | after |
|---|---|---|
| covenant thread | 59 (whole relation) | **14** |
| bytes | 31,196 | 5,825 |
| extra generation to fetch them | yes | no |

Most indicators map to an empty tuple, and that is the finding rather than an
omission: stage, ECL, PD, arrears, collateral and concentration cards are all
computed from facility columns the canonical packet already carries. Only two
indicators have a measure outside the facility relation:

| indicator | field definitions |
|---|---|
| `covenant_breach_share` | 14 |
| `rating_rank` | 4 |
| every other indicator | 0 |

Bound: `MAX_SEED_FIELDS = 16`, chosen against the widest real entry (14), not
picked blind. A test derives the covenant entry from `SQL_COVENANT` itself, so
a typo cannot quietly shrink it — two columns in the first draft of that map
(`test_result`, `headroom_pct`) do not exist under those names and would have
been skipped in silence.

## 8. Deadline behaviour, before and after

**Before:** a provider call was given the client timeout regardless of how
much of the run's deadline remained, so a stalled socket could outlive the
run's own watchdog; and a call could be started with no time left to use what
it produced.

**After:**
- `Ledger.call_timeout_seconds()` bounds every call by `remaining_seconds`
  less a 2-second settlement margin, capped at the run's deadline.
- `Ledger.check_call_window(phase=…)` refuses a call that cannot be used. An
  ACTION must leave the finalization reserve intact (20 s standard, 25 s
  deep); the ANSWER call may spend it, because that is what it was held for.
- The reserve applies from the second generation onwards. The first one is
  exempt: a run starts on the 60-second product-help allowance and widens to
  120 s only once the analyst declares `DATA_ANALYSIS`, so reserving against
  the starting bound would kill an analytical run with a slow first turn.
  **My own latency test caught this**, and the fix was the exemption, not a
  larger reserve.

## 9. Analysis preservation

`_preserve_analysis()` now fires on **any** terminal stop after successful
SQL, not only on an exhausted answer correction. A reader is told which half
stopped and which artifact survived, so "this run failed" is never mistaken
for "the query failed".

## 10. Mandatory regression A — simple EAD

*"What is total exposure at default by sector in the latest quarter?"*

| | measured |
|---|---|
| state | COMPLETED, published, no `{{claim.}}` placeholder left |
| generations | **2** |
| catalogue calls | **0** |
| SQL executions | **1** |

## 11. Mandatory regression B — the Manufacturing seeded thread

Two turns, because a seeded thread that loses its subject on the second
question is not a thread.

| | turn 1 | turn 2 |
|---|---|---|
| state | COMPLETED | COMPLETED |
| generations | 2 | 2 |
| catalogue calls | **0** | 0 |
| SQL executions | 1 | 1 |

Turn 2 asserts the card, the case file and turn 1's question and answer are
all still in front of the analyst.

## 12. Mandatory regression C — "Why is risk building across the book?"

No measure, no sector and no quarter named, and every one of them resolvable
from the canonical semantics and the release's own calendar. The run executes,
publishes, and its coverage entry reads `answered`. A run that comes back
asking which was meant has refused to work.

| | measured |
|---|---|
| state | COMPLETED |
| generations | **2** |
| catalogue calls | **0** |
| SQL executions | **1** |

## 13. Why those three numbers are worth anything

The model is scripted, so *"the analyst did not call inspect_catalog"* would
be true by construction and worth nothing. What a script cannot fake is
**sufficiency**: every field each query names is checked against the bytes the
run was actually handed, so a passing case means the analyst COULD have
written that query without asking. A fifth test proves that check is not
vacuous — `cockpit_collateral_quarter.net_realizable_value`, a real column
deliberately in no packet, fails it.

## 14. The per-call report

`Analyst.turns` already existed and nothing read it. It is now a record of
every generation ATTEMPT — including the ones refused before they were sent,
which are exactly the calls a stopped run needs explained — carrying purpose,
phase, attempt, payload bytes split three ways, output allowance wanted and
granted, counted input tokens, how it ended, and provider duration. It reaches
the reader on the outcome and as a run detail, on every terminal path.

**A second defect this found:** every provider exception was recorded as a
transport error. A truncated response was served, billed and complete as far
as the socket was concerned; filing it as a network fault sends an operator
looking for a problem that is not there. Failures now keep their own code.

## 15. Provider time vs CreditProbe local overhead

Split on every run: `elapsed_ms`, `provider_ms`, `local_ms`.

Measured on a complete two-call analysis with a stub model — so provider time
is zero by construction and **CreditProbe's own overhead is the whole figure**:

| | ms |
|---|---|
| total elapsed | 415 |
| provider | 0 (stub) |
| CreditProbe: context, validation, binding, DuckDB, ledger, store | **415** |

Local overhead for a full analytical run, real release and real execution, is
under half a second. A live run that overruns is overrunning on the provider.

## 16. Recovery injection results

| injected | result |
|---|---|
| truncated action, then truncated answer | COMPLETED |
| truncated action, then invalid answer | COMPLETED |
| second truncated action | fails closed, CALL_LIMIT |
| second invalid answer | fails closed, ANSWER_VALIDATION |
| truncated action | never executes partial arguments |
| action inside the finalization reserve | refused; answer call still affordable |
| answer call with 2 s left | refused before send, reported with its code |

## 17. Test counts

| suite | result |
|---|---|
| V4 backend (`tests/cockpit_v4`) | **873 passed, 0 failed** |
| Frontend (`frontend`, node:test) | **476 passed, 0 failed** |
| Browser (real Chromium, real UI, real API, stub analyst) | **46/46 passed** |
| V3 regression (`tests/cockpit_agentic`) | **564 passed, 26 skipped, 0 failed** |

Added this round: `test_seeded_case_file.py` (28), `test_call_report.py` (10),
`test_mandatory_analytical_cases.py` (5).

## 18. The browser "intermittent" is gone, and was not a product defect

*"The seeded context load appears in the process panel"* failed about one run
in three, passed in isolation, and had been written off as intermittent. It
was not. The test expanded the trace's stages once and read the text once,
while the run was still going; on a slow scheduling round the snapshot caught
the run on its first stage with nothing in it but "Request accepted".

The panel is a live view whose substeps are collapsed until a reader opens
one, so reading it is a poll, not a read. It now expands and re-reads until
the line appears or the wait runs out. **Five consecutive isolated runs and
two consecutive full suites, all green.** No product code changed.

## 19. No limit was raised (§30)

`generation_attempts`, `catalog_calls`, `analysis_rounds`,
`execution_submissions`, `deadline_seconds`, `spend_ceiling_usd` and
`reserved_output_tokens` are **unchanged from `6d54b8e`**. The only additions
are a per-phase recovery counter (still one each, not two for one) and two
time-reservation parameters that make a run stricter, not looser.

## 20. CreditProbe did not become the analytical brain (§1)

No Sonnet preprocessing. No second model. No router, planner or classifier.
CreditProbe authored no analytical prose and no SQL repair. There is no hidden
legacy fallback. What CreditProbe gained is messenger work: it hands over
schema it already computed, it narrows the tool list on a call that cannot use
the missing tools, and it writes down what each call cost. Every analytical
choice — which measure, which period, which aggregation, what the answer says
— is still the one model's.

## 21. V3 untouched

Files changed since `6d54b8e` are confined to `backend/cockpit_v4/`,
`tests/cockpit_v4/` and `docs/cockpit_v4/`. No file under
`backend/cockpit_agentic/` or `tests/cockpit_agentic/` was modified, and the
V3 suite passes unchanged.

## 22. Unresolved blockers

**None for Mac UAT.** Two things a live run will settle that a stub cannot:

1. **Call latency is unmeasured.** Every number in §15 is real except the
   provider's, which is zero because the model is a stub. Whether a real
   analytical turn fits inside 120 seconds minus a 20-second finalization
   reserve is the question this round makes *observable* — the call report
   now records it — but does not answer.
2. **Real truncation frequency is unmeasured.** The recovery paths are proved
   correct under injection. How often a live answer turn actually reaches its
   4,096-token allowance at 15,655 input tokens is not something a scripted
   suite can say.

Neither blocks UAT; both are what UAT is for, and the call report is the
instrument that will report them.

## 23. Verdict

**LIVE ANALYTICAL ORCHESTRATION: READY FOR MAC UAT**

## 24. Mac commands

```bash
cd ~/IPM_V2

# stop whatever is running
python3 scripts/cockpit_v4/stop.py

# pull this branch
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull origin claude/cockpit-single-agent-v4-h8fsbq

# start
python3 scripts/cockpit_v4/start.py

# confirm it came up
python3 scripts/cockpit_v4/status.py
```

The double-clickable equivalents are
`scripts/cockpit_v4/STOP_COCKPIT_V4.command`,
`START_COCKPIT_V4.command` and `STATUS_COCKPIT_V4.command`.

## 25. What to watch during UAT

The trace now names every model call. On a run that stops, read the run
detail's `call_report`: it says how many calls were made, what each was for,
how large each request was, what allowance it got, how it ended, and where the
seconds went. A stop with a `refused_before_send` entry never reached the
provider, and its `refusal` field names the bound that stopped it.

## 26. Reproducing the evidence

```bash
python3 -m pytest tests/cockpit_v4 -q                    # 873
cd frontend && npm test                                  # 476
python3 scripts/cockpit_v4/browser_evidence.py           # 46/46
python3 -m pytest tests/cockpit_agentic -q               # 564, 26 skipped
python3 scripts/cockpit_v4/orchestration_evidence.py     # the tables above
```
