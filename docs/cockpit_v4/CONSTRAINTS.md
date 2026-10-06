# Cockpit V4: constraints that can stop a run

Reference for engineers and operators debugging "query not built" and "answer timed out".
Describes CURRENT behaviour. It does not describe planned behaviour (see section 10).

**Line numbers** are verified against the working tree of branch `feat/v4-omlx-reliability` (HEAD `72a5163` plus the uncommitted edits to `orchestration.py`, `execute_tool.py`, `intent_check.py`, `context.py` and the untracked `examples.py`). Line numbers drift on any edit: re-grep the symbol if a number is off. All paths are under `IPM_V2-CockpitLiveUAT/backend/` unless stated (`cv4/` = `cockpit_v4/`).

---

## 1. Run limits (`cv4/config.py`)

`Limits` dataclass: `config.py:80-180`. Constants: `STANDARD_LIMITS` :183, `DEEP_LIMITS` :199, `ANALYTICAL_STANDARD_LIMITS` :247, `ANALYTICAL_DEEP_LIMITS` :250. The two analytical constants are `dataclasses.replace` of Standard/Deep and change ONLY `deadline_seconds` and `spend_ceiling_usd`; every other field below is inherited.

**Resolver.** Nothing reads the four constants directly any more. `config.effective_limits(family_key)` (`config.py:334-354`) returns the constant for `standard | deep | analytical_standard | analytical_deep` with the selected profile's overrides applied (`FAMILY_KEYS` :265). It reads the module attribute at call time (`globals()`, :345), so a script that replaces `STANDARD_LIMITS` still takes effect. Callers:

| Caller | Where |
|---|---|
| `limits_for(mode)` / `analytical_limits_for(mode)` | `config.py:357,362` (both delegate) |
| Envelope (`limits_for_family`, `Verdict.limits`, `policy()`) | `envelope.py:69-80,83-106,118-120` |
| Route: stamps `deadline_at` from `verdict.limits` | `routes.py:382-384,409` |
| Worker: `verdict.limits` becomes the `Ledger` | `worker.py:163-169` |
| Mid-run widening (`Ledger.adopt`) | `orchestration.py:1997-1998` via `analytical_limits_for` |
| Supervisor result-only finalizer | `supervisor.py:169-174` via `analytical_limits_for` |
| Diagnostics: all four families plus `limits_profile` | `service.py:461-472`; family ladder `service.py:477-480` |

**Profile `COCKPIT_V4_LIMITS_PROFILE`** (`PROFILE_VAR` :257; `active_profile()` :293-300): `default` (unset or empty; no overrides) or `local`. Read at call time, never at import. An unknown name raises `ConfigurationInvalid` at the first resolution (a run accept or diagnostics call), not at process start. `local` (`_LOCAL_ANALYTICAL` :284-286, `PROFILES` :287-290) overrides the two ANALYTICAL families only:

| Field | default An-Std / An-Deep | local An-Std / An-Deep | Product help (Std / Deep) |
|---|---|---|---|
| deadline_seconds | 180 / 240 | **300 / 300** | 60 / 120, unchanged |
| action_call_seconds | 30 / 45 | **45 / 45** | 30 / 45, unchanged |
| answer_call_seconds | 55 / 90 | **100 / 100** | 55 / 90, unchanged |
| finalization_reserve_seconds | 20 / 25 | **90 / 90** | 20 / 25, unchanged |

Invariants (`_check_invariants` :303-331, applied only where a profile changed something): for analytical families `2 x action_call_seconds + finalization_reserve_seconds + SETTLEMENT_MARGIN_SECONDS (2.0, :262) <= deadline_seconds`, and `answer_call_seconds <= deadline_seconds - min_call_seconds`; else `ConfigurationInvalid`. Local: 2x45+90+2 = 182 <= 300 and 100 <= 295. Product help is never overridden by a profile, so it is identical under `default` and `local`. A Product-Help run that later submits SQL is widened by `Ledger.adopt` (section 2) to the analytical family's values, local ones included.

No other `COCKPIT_V4_*` variable feeds a `Limits` field. The table below is the `default` profile.

| Field | Std | Deep | An-Std | An-Deep | What trips it / code raised |
|---|---|---|---|---|---|
| deadline_seconds | 60 | 120 | 180 | 240 | `Ledger.check_deadline` `budgets.py:268-272` and `check_call_window` :242 raise `DEADLINE_EXPIRED`; supervisor also settles at `deadline_at`+15s grace (`supervisor.py:71`) |
| execution_submissions | 5 | 5 | 5 | 5 | `spend_submission` `budgets.py:319` (raise :329): `EXECUTION_LIMIT`. Counted for every received `execute_analysis`, even one that later fails parse or validation |
| analysis_rounds | 3 | 3 | 3 | 3 | `open_round` :352 (raise :362): `ROUND_LIMIT`. A round closes only on a fully OK batch (`orchestration.py:1730-1733`) |
| generation_attempts | 12 | 16 | 12 | 16 | `spend_generation` :277 (raise :280): `CALL_LIMIT`. One per `Analyst.ask` |
| provider_attempts | 24 | 32 | 24 | 32 | `spend_provider_attempt` :287 (raise :294): `CALL_LIMIT`. Counted 2-3 times per generation (section 5) |
| catalog_calls | 4 | 6 | 4 | 6 | `spend_catalog_call` :301 (raise :304): `CALL_LIMIT`, ends the run (BudgetExceeded is not caught in `_handle_call`, `orchestration.py:1126` catches only `Rejection`, so it reaches `_run_to_completion` :305) |
| artifact_reads | 6 | 10 | 6 | 10 | `spend_artifact_read` :310 (raise :313): `CALL_LIMIT`, same propagation |
| steps_per_batch | 6 | 8 | 6 | 8 | `parse_steps` `contracts.py:496-504`: `Rejection(INVALID_MODEL_OUTPUT)` back to the model. It fires AFTER `spend_submission` (`orchestration.py:1434-1435`), so an oversize batch still burns a submission |
| total_steps | 12 | 24 | 12 | 24 | `spend_steps` `budgets.py:374` (raise :376): `EXECUTION_LIMIT`, ends the run. Checked after validation (`orchestration.py:1551`) |
| step_seconds | 15 | 30 | 15 | 30 | Per-step budget `min(step_seconds, remaining)` `execute_tool.py:613`; batch budget `step_seconds x n_steps` capped by remaining (`orchestration.py:1709-1713`). Step watchdog interrupts DuckDB: `SqlRejected(RESOURCE_LIMIT)` `sql.py:628`. Step failure, not a run stop |
| python_memory_mib | 512 | 1024 | 512 | 1024 | Passed to Python sandbox `execute_tool.py:833` |
| sql_memory_mib | 512 | 1024 | 512 | 1024 | **UNUSED.** Only `config.py:95,187,203` reference it (grep over `backend/` and `tests/`). DuckDB's real limit is hard-coded in `catalog.py:467` |
| output_bytes_per_step | 25 MiB | 50 MiB | 25 MiB | 50 MiB | Passed to Python sandbox `execute_tool.py:834` |
| preview_rows | 100 | 100 | 100 | 100 | Clips the preview, not a stop (`execute_tool.py:736,765,800-801`) |
| preview_columns | 32 | 32 | 32 | 32 | Clips the preview (`execute_tool.py:728-729`) |
| plan_retries | 3 | 3 | 3 | 3 | `spend_plan_retry` `budgets.py:336` (raise :345): `PLAN_FORMAT_EXHAUSTED` on the 4th compiler OR intent-check refusal. Called at `orchestration.py:1384` |
| format_regenerations | 2 | 2 | 2 | 2 | ACTION-phase recoveries: `spend_format_recovery(phase="action")` :384 (raise :410): `ACTION_FORMAT_EXHAUSTED`. NB the error text says "the one re-ask" (:406) but the limit is 2 |
| answer_format_regenerations | 2 | 2 | 2 | 2 | ANSWER-phase: `ANSWER_FORMAT_EXHAUSTED`. Also drives `answer_reask_remains` :208 |
| answer_corrections | 1 | 1 | 1 | 1 | `spend_answer_correction` :416 (raise :418) raises `CALL_LIMIT`, which `orchestration.py:1828-1852` converts to `ANSWER_VALIDATION` |
| spend_ceiling_usd | 1.00 | 2.00 | 1.50 | 3.00 | `Ledger.reserve` :474 (raise :488): `COST_LIMIT`; `provider.py:446` `COST_LIMIT` if affordable output < `MIN_RESPONSE_TOKENS`. Local oMLX price card is all zeros, so this never trips there |
| charts | 6 | 8 | 6 | 8 | Extra charts dropped with a warning (`finalization.py:453-456,1774`) |
| soft_input_tokens | 6,000 | 10,000 | 6,000 | 10,000 | **UNUSED in product code.** Only a test reads it (`tests/cockpit_v4/test_convergence.py:165`). The real input guard is context capacity (`provider.py:419-430`, `INPUT_CONTEXT_LIMIT`) |
| reserved_output_tokens | 8,192 | 12,288 | 8,192 | 12,288 | First answer-turn `max_tokens` (`orchestration.py:721-722`). Clamped to the model's `max_output_tokens` at `provider.py:417-418` (8,192 on local oMLX) |
| action_output_tokens | 3,072 | 4,096 | 3,072 | 4,096 | First action-turn `max_tokens` (`orchestration.py:728-729`). Overrun is `OutputTruncated` (`provider.py:573`, `OUTPUT_LIMIT`), recovered via `format_regenerations` |
| action_output_ceiling | 4,096 | 6,144 | 4,096 | 6,144 | Action `max_tokens` after one truncation (`action_truncated` latch, `orchestration.py:724-727,833`) |
| answer_output_ceiling | 12,288 | 16,384 | 12,288 | 16,384 | Answer `max_tokens` after one truncation (`orchestration.py:718-719`). See defect D12: clamped to 8,192 on local oMLX |
| action_call_seconds | 30 | 45 | 30 | 45 | Cap on ONE action call: `call_timeout_seconds` `budgets.py:199-200`, further bounded after the first generation (section 2). (The comment at `config.py:130` says "2 x 40s"; the value is 30.) |
| answer_call_seconds | 55 | 90 | 55 | 90 | Cap on ONE answer call while a re-ask remains `budgets.py:204-205` |
| action_effort / answer_effort / answer_recovery_effort | low / medium / low | medium / high / low | same as Std | same as Deep | Sent only if capability supports effort control (`provider.py:487-488`) |
| finalization_reserve_seconds | 20 | 25 | 20 | 25 | Action calls refused inside this window once >=1 generation done (`budgets.py:239-240`); also caps an action call's length (`budgets.py:199-203`) |
| min_call_seconds | 5 | 5 | 5 | 5 | No provider call below this: `DEADLINE_EXPIRED` (`budgets.py:241-242`) |

Other hard caps outside `Limits`: `MAX_PRODUCT_CALLS = 4` (`orchestration.py:2117`, tool error `CALL_LIMIT` at :1227-1229, not a run stop); `BARREN_CATALOG_TERMINAL = 3` (:2125, raises `NO_PROGRESS` and ends the run at :1168-1170).

---

## 2. Ledger rules (`cv4/budgets.py`)

| Rule | Where | Current behaviour |
|---|---|---|
| `call_timeout_seconds(phase)` | :158-206 | `usable = remaining - SETTLEMENT_MARGIN_SECONDS (2.0)` :197; `bound = min(usable, deadline_seconds)`. ACTION phase: `min(bound, action_call_seconds)`, and **once `generation_attempts >= 1` also `min(bound, usable - finalization_reserve_seconds)`** (:198-203), i.e. an action may not run past `remaining - reserve - 2s`, so it cannot eat the reserve it was admitted to protect. ANSWER phase while `answer_reask_remains()`: `min(bound, answer_call_seconds)` :204-205. LAST answer attempt (no re-ask left): the whole remainder. Floor `max(1.0, bound)` :206 |
| `answer_reask_remains` | :208-217 | `answer_format_recoveries < answer_format_regenerations` (2). Not affected by transport retries or corrections |
| `check_call_window(phase)` | :219-248 | Refuse if `remaining < min_call_seconds`; for non-answer phases ONLY once `generation_attempts >= 1`, floor also includes `finalization_reserve_seconds` (:239-240). So an action needs `>= 25s` (Std) / `30s` (Deep) left, `>= 95s` on a local analytical run; an answer needs `>= 5s`. Raises `DEADLINE_EXPIRED` |
| `action_window_closed` / `answer_window_open` | :250 / :264 | Same arithmetic as a question; used by `_demand_an_answer_if_time_is_short` |
| `check_deadline` | :268 | `DEADLINE_EXPIRED` only at remaining <= 0 |
| `adopt(limits)` | :98-122 | Widens **every time field**: `deadline_seconds`, `action_call_seconds`, `answer_call_seconds`, `finalization_reserve_seconds`, plus `spend_ceiling_usd`; each takes `max(old, new)` (never narrows). Nothing else is widened. Caller: `_widen_to_analytical` `orchestration.py:1993-2008`, then `store.extend_deadline` (:2002) sets `deadline_at = now + new deadline_seconds`. Triggered by `_do_execute` (:1420) before anything is parsed, and by the analyst's declared intent (`orchestration.py:1981,2031,2067`) |
| `spend_generation` | :277 | `check_deadline`, then `CALL_LIMIT` |
| `spend_provider_attempt` | :287 | `CALL_LIMIT`; no deadline check |
| `spend_catalog_call` / `spend_artifact_read` | :301 / :310 | `CALL_LIMIT` |
| `spend_submission` | :319 | `EXECUTION_LIMIT` |
| `spend_plan_retry` | :336 | `PLAN_FORMAT_EXHAUSTED` |
| `open_round` / `close_round` | :352 / :370 | `ROUND_LIMIT`; repair inside a failed batch stays in the same round |
| `spend_steps` | :374 | `EXECUTION_LIMIT` ("nothing was executed, no step dropped") |
| `spend_format_recovery(phase)` | :384 | Per-phase counters; `ACTION_FORMAT_EXHAUSTED` / `ANSWER_FORMAT_EXHAUSTED` |
| `spend_answer_correction` | :416 | `CALL_LIMIT` (converted to `ANSWER_VALIDATION` by caller) |
| `spend_transport_retry` | :424-462 | ONE per run (`transport_retries >= 1`, :453): second raises `PROVIDER_UNAVAILABLE` (:454-455, not `CALL_LIMIT`). Shared by every `retry_class="transport"` failure (timeouts, connection, 502/503, 429) |
| `MIN_RESPONSE_TOKENS` | :96 (= 1,024) | If the budget affords fewer response tokens than this, `COST_LIMIT` before sending (`provider.py:446`); otherwise `max_tokens` is reduced to the affordable amount (`affordable_output_tokens` :124) |
| `reserve` / `settle` | :474 / :499 | Worst-case reservation at verified price; `COST_LIMIT` if over ceiling; failed or cancelled calls settle as UNCERTAIN (held pending) |
| `no_progress_check(key)` | :528-542 | Same code+params+release+failure class seen before: `NO_PROGRESS`. In `_do_execute` it is converted to a `Rejection` (`orchestration.py:1552-1561`), so it refuses ONE submission (which already consumed a submission slot and steps) and does not end the run |
| `Ledger` clock | `worker.py:134,167` | `started_monotonic` = when the worker begins `execute`, not when the run was accepted |

---

## 3. Error codes that can end a run

"Rows published?" means `finalization.RESULT_ONLY_REASON` (`finalization.py:62-105`). It applies only if the run had executed a query and stored artifacts and had not already published an answer (`orchestration.py:453-454`, `_result_only_response` :474-528); the run then settles `PARTIAL` instead of `FAILED`. Codes absent from that dict never publish rows. The supervisor passes a `result_reason` only for `DEADLINE_EXPIRED` (`supervisor.py:98`).

| Code | Raised where | Retried? | Rows published? |
|---|---|---|---|
| DEADLINE_EXPIRED | `budgets.py:242,270`; `supervisor.py:93-98` (worker not settling within grace) | No | Yes (worker path via dict; supervisor path via `_rows_already_computed` `supervisor.py:117`) |
| CALL_LIMIT | `budgets.py:280,294,304,313`; `provider.py` via `spend_provider_attempt`; `orchestration.py:1229` (product reads, tool error only) | No | Yes |
| EXECUTION_LIMIT | `budgets.py:329,376` | No | Yes |
| ROUND_LIMIT | `budgets.py:362` | No | Yes |
| COST_LIMIT | `budgets.py:488`; `provider.py:446` | No (the allowance is first reduced to what is affordable) | Yes |
| PLAN_FORMAT_EXHAUSTED | `budgets.py:345` from `orchestration.py:1384` (compiler or intent-check refusal) | Three corrections first | **No** (not in dict). Matters only if an earlier round had already stored rows |
| ACTION_FORMAT_EXHAUSTED | `budgets.py:410` | `format_regenerations` = 2 re-asks first | Yes |
| ANSWER_FORMAT_EXHAUSTED | `budgets.py:410` | `answer_format_regenerations` = 2 first | Yes |
| OUTPUT_LIMIT | `provider.py:573` (`OutputTruncated`); `orchestration.py:309-310` if it escapes | Yes: as a format recovery, with the larger ceiling | Yes |
| ANSWER_VALIDATION | `orchestration.py:1827-1852` (second failed evidence check; includes the entity-scope and unweighted-average problems, section 4c) | One correction (`answer_corrections`=1) | Yes (`_result_only_response(..., rejected=final)` :1848-1849) |
| PROVIDER_UNAVAILABLE | `provider.py:579-580` (refusal stop), :732-746 (`_classify`); `budgets.py:454` (second transport fault) | Once per run, only if `retry_class="transport"` (timeouts including oMLX "timed out after Ns", connection, 502/503, "overloaded"). Unclassified failures (e.g. oMLX "request failed (TypeName)") are NOT retried | Yes |
| PROVIDER_RATE_LIMIT | `provider.py:718-724` | Shares the one transport retry | **No** |
| PROVIDER_AUTH | `provider.py:706,713` | No | No (operator code) |
| PROVIDER_REQUEST_INVALID / TOOL_SCHEMA_INVALID | `provider.py:675-704` (any "400" in text) -> `orchestration.py:311-314` `_provider_rejected` (:366) | No (a 400 naming `tool_choice`/`output_config`/`effort` is re-sent once without them, `provider.py:346-365`) | No (operator code) |
| INPUT_CONTEXT_LIMIT | `provider.py:424` (`InputTooLarge`) | No | **No** |
| INVALID_MODEL_OUTPUT | Run-ending: `provider.py:158,185,190,587,599`. Per-turn: `contracts.py:497-498`, `orchestration.py:1088-1100` (`_reject_batch`) which spends a format recovery | Per-turn form: via format recovery | No |
| NO_PROGRESS | `orchestration.py:1168-1170` (3 barren catalog calls, ends run). Repeat-submission form is a rejection, not a stop (section 2) | No | No |
| WORKER_LOST | `supervisor.py:105-108` (lease heartbeat stale > 10s); `orchestration.py:316-325` (`LeaseLost`/`TerminalAlready`) | No: "Nothing was replayed" | **No** (supervisor passes no `result_reason`; artifacts stay in the store but are not published) |
| STORAGE_UNAVAILABLE | `orchestration.py:316-325` | No | No |
| CANCELLED_BY_USER | `orchestration.py:298-304` | n/a | n/a |

Not run-ending: `SQL_VALIDATION`, `SQL_RUNTIME`, `SECURITY_DENIED`, step-level `RESOURCE_LIMIT`, `CLARIFICATION_UNGROUNDED` (the clarification gate, section 4d2: a plain `Rejection` on `finalize_response`, once per run, spends no budget and no answer correction, and only under `COCKPIT_V4_CLARIFY_GATE=enforce`), and the plan/SQL intent-check refusals (they return as a `PLAN_INVALID` Rejection and spend a plan retry). They return as tool errors to the analyst and each submission that causes one has already cost a submission slot (plan refusals cost a plan retry, not a submission).

---

## 4. The failure flows

### 4a. "Query not built"

| # | Step | Where |
|---|---|---|
| 1 | Loop: `_guard` (cancel + `check_deadline`), `_demand_an_answer_if_time_is_short`, `_generate` | `orchestration.py:555-561` |
| 2 | `action_state.decide(...)` picks the legal tools and whether one is forced (`require=execute_analysis` in READY_FOR_EXECUTION) | `action_state.py:142`; called `orchestration.py:667` |
| 3 | Provider call (`analyst.ask` :799); a truncated action = one `ACTION_FORMAT_RECOVERY` (ceiling raised) | `orchestration.py:810-886` |
| 4 | `_do_execute`: widen to analytical limits (:1420), then `_compiled` (:1357-1405): intent check (:1377), then compile plan -> SQL. A compiler or intent `Rejection` spends a plan retry (4th = `PLAN_FORMAT_EXHAUSTED`); no submission is spent | `orchestration.py:1377-1397` |
| 5 | `spend_submission` (6th = `EXECUTION_LIMIT`) | `orchestration.py:1434` |
| 6 | `parse_execution` (oversize batch -> `Rejection INVALID_MODEL_OUTPUT`, submission already burned) | :1435, `contracts.py:496-504` |
| 7 | `validate_batch` (structure, table access, join grain, DuckDB EXPLAIN bind; raw-SQL intent checks in shadow, section 4d). `Rejection` is recorded as a rejected submission and returned to the model | :1459-1550, `execute_tool.py:365` |
| 8 | `spend_steps(n)` (`total_steps` exceeded = `EXECUTION_LIMIT`, run ends) | :1551 |
| 9 | `no_progress_check` (duplicate failure = rejection, not stop) | :1553 |
| 10 | `open_round` (4th round = `ROUND_LIMIT`) | :1562 |
| 11 | `run_batch` with deadline `min(remaining, step_seconds x steps)` | :1709-1713 |

Each failed pass through 2-10 costs one generation (about 8.7s p50, 24.8s p90, see section 7) plus at least one submission. With `execution_submissions=5` and an action p90 of ~25s, five attempts need ~125s, which exceeds a 60s or 120s deadline: in practice `DEADLINE_EXPIRED` or the finalization reserve closes the window before `EXECUTION_LIMIT` is reached. An action is refused outright once `remaining < 25s` (Std; 95s local analytical) because of `check_call_window`, and after the first generation each action call is cut at `remaining - reserve - 2s` (section 2).

### 4b. "Answer timed out"

| # | Step | Where |
|---|---|---|
| 1 | Query ran; `self.executed = True`; `decide` returns RESULT_READY with `require=finalize_response` | `orchestration.py:1720` (`executed = True`); `action_state.py:183-190` |
| 2 | `_demand_an_answer_if_time_is_short`: if `action_window_closed()` (remaining < 25s Std, 95s local analytical) and `answer_window_open()` (>=5s), once, forces an answer-only turn | `orchestration.py:568,588-596` |
| 3 | `_generate` -> `analyst.ask(phase="answer")`; `check_call_window("answer")` needs only 5s | `orchestration.py:689,799`; `provider.py:410` |
| 4 | `timeout = call_timeout_seconds("answer")`: `min(remaining-2, answer_call_seconds)` while a re-ask remains, else the whole remainder | `provider.py:463`; `budgets.py:197-206` |
| 5 | oMLX times out: `LLMError("oMLX request timed out after Ns")` (never retried inside oMLX); `_classify` sees "timed out" and returns `PROVIDER_UNAVAILABLE`, `retry_class="transport"`, `detail.timed_out` | `omlx_provider.py:302-324`; `provider.py:531,732-740` |
| 6 | `spend_transport_retry` (once) and the loop re-enters `_generate`. **The retry call gets the same `answer_call_seconds` cap again, not "what is left"** (defect D6; the full-remainder retry, plan step 11, is NOT implemented) | `orchestration.py:887-908`; `budgets.py:204-205` |
| 7 | Second transport fault, or a non-timeout failure not classified transport (e.g. "oMLX request failed (TypeName)"): `PROVIDER_UNAVAILABLE`, no further retry, run stops | `budgets.py:454`; `provider.py:741-746` |
| 8 | Ledger deadline: next `check_deadline` raises `DEADLINE_EXPIRED`; `_stop` publishes rows via `_result_only_response` (state PARTIAL) | `orchestration.py:305-306,413-460` |
| 9 | If the worker is blocked on a socket past `deadline_at + 15s`, the supervisor settles it: `EXPIRED`/`PARTIAL`, rows via `_rows_already_computed` | `supervisor.py:71,85-100,185-200` |

### 4c. Finalization checks that refuse an answer (`finalization.py`)

`Finalizer.validate` (`finalization.py:391`) adds two problems that spend the one `answer_corrections` (they are problems, so the answer is refused and corrected once, then `ANSWER_VALIDATION`/PARTIAL):

| Check | Where | Problem when |
|---|---|---|
| Entity scope `_check_entity_scope` | `finalization.py:659`, called :464; inputs set at `orchestration.py:1811-1815` | The server resolved the question to `field=value` (`resolved_entities`), at least one numeric claim is bound to a row carrying that field, and NONE is on the named value. Skipped for comparison questions (two+ values resolved, or comparison wording, `_is_comparison`). WARNING only (not a problem): no cited artifact carries the field but another run artifact spans several values |
| Unweighted-average disclosure `_check_average_disclosure` | `finalization.py:756`, called :467; set at `orchestration.py:1816-1819` | A claim uses a column from a governed plain average (label starts "Simple average") and neither the narrative, limitations, `resolved_assumptions` nor the clarification says "simple average" / "unweighted" / "not weighted" / "un-weighted" |

### 4d. Plan and SQL intent checks (`cv4/intent_check.py`)

Flag `COCKPIT_V4_INTENT_CHECK` (`ENV` :42, `mode()` :57): `off | shadow | enforce`, **default `enforce`**; any unrecognised value is `enforce`, never silently off. Purpose: a valid governed plan can still answer a different question than the one asked, and the local model does so silently.

| Rule | Severity | Where | Behaviour |
|---|---|---|---|
| `period_named` | REFUSE | `_rule_period` :164 (finding :188) | The question spells a period (2026Q1, "Q4 2023") and the plan's period would not cover it. Message gives the exact `period.kind "explicit"` + `values` to send |
| `period_transition` | REFUSE | `_rule_period` :164 (finding :171) | Two+ named periods with a "from ... to" / "between" style transition. A plan cannot express a period-to-period transition: the analyst is told to use raw-SQL `steps` joining two periods on the entity key |
| `entity_scope` | REFUSE | `_rule_scope` :216 (finding :242) | The question resolved exactly to `field=value` (resolver `exact`) and the plan has no filter on it (a GROUP BY does not select). Skipped for comparisons (two+ values, comparison words) and fields the plan cannot filter |
| `aggregation_word` | WARN | `_rule_aggregation` :267 | "average/mean" asked but a sum metric chosen, or "total/sum" asked but an average. Recorded, never refused |
| `operation_dropped` | SHADOW | `requested_operations` :318, `_rule_operations` :347 | Table-driven. The question asks for an operation from a fixed vocabulary (`change`: change word + two named periods; `pct_change`; `rank_change`: ranking word + change; `top_n`; `intersect`: overlap of two rankings, e.g. "share top 10 ... absolute and relative") and the plan does not produce it (compare `none`; sort not a `change_*` sort; `limit` != N; any plan for `intersect`, which needs `steps`). The detail names the operation; the message gives the repair. Recorded under every mode, **never refused**, until `shadow_precision.py` measures it at >= 0.95. Added after run b8aa21b3 (valid plan, both quarters covered, no change / ranking / overlap computed) |

How refusal works: `Orchestrator._check_intent` (`orchestration.py:1329-1355`, called inside `_compiled`'s `try` at :1377) calls `intent_check.gate` (:309). Under `enforce`, REFUSE findings become `refusal()` (:299), a `Rejection(PLAN_INVALID)` that goes through the plan-retry path, so each refusal costs one of `plan_retries` (3), not a submission. Warnings (and, under `shadow`, every finding) are recorded on `self._intent_findings` and emitted as a `CONTEXT_READY` event (`operation="intent_check"`, :1342-1353).

Raw-SQL checks (`check_sql` :598; rules `sql_period`, `sql_entity`, `sql_ratio_sum`, and `sql_operation_dropped` (`_sql_operations` :569: the same vocabulary read from the parse tree, i.e. no subtraction for `change`, no LIMIT/rank for `top_n`, fewer than two rankings and no INTERSECT for `intersect`), severity `shadow`) are **shadow only, never refused**: `execute_tool.validate_batch` calls `_shadow_intent` per step (`execute_tool.py:400,405-420`), reading `intent_question` / `intent_envelope` set by `_do_execute` (`orchestration.py:1448-1463`); findings join `_intent_findings`. A bug here is swallowed (`check_sql` never raises). `scripts/cockpit_v4/shadow_precision.py` replays stored raw-SQL submissions and stored plans (assistant messages, through `check_plan` against the live book) from the state DB (read-only), lists flags for human labelling and, with `--labels`, computes per-rule precision against `BAR = 0.95` (`shadow_precision.py:35`); a rule is promoted only at >= 0.95 (`artifacts_eval/gates.json`, `phase_2_wrong_answers.shadow_rule_promotion_precision_min`). `sql_entity` is not exercised by the replay (a stored run has no `resolved_entities`).

### 4d2. Clarification gate (`cv4/clarify_gate.py`, hook `Orchestrator._check_clarification`)

Flag `COCKPIT_V4_CLARIFY_GATE` (`mode()`): `off | shadow | enforce`, **default `shadow`**, and an unrecognised value is also `shadow` (unlike 4d, which fails to `enforce`: this guards an unnecessary question, not a wrong answer, so an unknown value must not start refusing). Purpose: "What is driving Stage 2 and ECL growth?" was a clarification on some runs and an answer on others, depending on whether the local model chose to ask.

- Runs in `_do_finalize` right after `_record_intent(final.intent)`, only when ALL hold: `disposition == "clarification"`, nothing executed, not `answer_only`, not `_answer_demanded`, analytical turn, mode not `off`, gate not already used this run, and `ledger.check_call_window("action")` passes (otherwise there is no time to act on a refusal).
- A clarification is allowed when `clarify_gate.grounds()` is non-empty: undecided term, ambiguous or `needs_a_question` value, envelope open question, `semantics.ambiguous_methods_in(question)` (PD/LGD/CCF attribution order; averages), `excluded_parts`, a `referred` coverage entry, or `readiness.sufficient` false (precision guard for follow-ups). Only server-held facts are read; never the model's own clarification text.
- `shadow`: finding appended to `_intent_findings`, `CONTEXT_READY` `operation="clarify_gate"`, nothing refused. `enforce`: sets `_clarify_gate_used`, clears `must_clarify` and the sticky `blocking_ambiguities` on the envelope/intent, emits a visible `INTENT_VALIDATED` (rejected), and raises `Rejection("CLARIFICATION_UNGROUNDED")` through `_handle_call` (a tool error: no budget spent; the retry counts as a generation attempt). It deliberately does NOT use `spend_answer_correction`, which would set `answer_only` and leave only `finalize_response` on offer. A second clarification always passes.
- Three guards keep the gate from refusing a question that was in fact earned or that the run cannot afford to retry. (1) **Non-Latin question** (`semantics.non_latin`): the ambiguous-term and method matchers are English, so `grounds()` returns `non_latin_question` and `clarification_not_needed` is omitted; multilingual term aliases are the longer-term fix. (2) **Budget** (`_can_afford_a_retry`): the gate lets the question through unless at least 2 generation attempts, 6 provider attempts and 1 execution submission remain and cost headroom covers two more turns at the run's average per-generation cost. (3) **Deictic follow-ups**: readiness counts measures carried "from earlier in this thread", so `grounds()` also requires a governed measure with `from == "this question"`, else `no_measure_in_this_question`.
- The refusal's `message` is reader-safe (it is published as the `TOOL_FAILED` public message); the instruction to the analyst travels in `detail.note`. The call report records `temperature` when one was sent.
- `semantics.readiness()` now carries `clarification_basis` and, when nothing is the reader's choice, `clarification_not_needed`.

### 4e. Plan compiler periods and averages (`cv4/plan_compiler.py`)

| Item | Where | Behaviour |
|---|---|---|
| `period.kind` | `PERIOD_KINDS` :44 | `latest, prior, year_ago, last_n, all, explicit`. `explicit` takes `values` (1 to `MAX_PERIOD_VALUES` = 12, :45). `values` with no `kind` implies `explicit` (:373); `values` with another kind is refused (:376-378). **No period at all still defaults to `latest`** (:373) |
| Populated-period validation | `Book.populated` :88; `_populated_periods` :203; check at `_explicit_periods` :429 (refusal :472-475) | A named period the relation does not hold is refused with the periods that are held; nothing is substituted. With no readable session the check degrades to format only |
| Retail quarter -> month | `_explicit_periods` :429-477 | A quarter asked of a monthly (retail) book: STOCK measures take the quarter-END month (:467); FLOW measures (`FLOW_COLUMNS` :51: `write_off_sar_mn`, `recovery_sar_mn`) take the SUM of the three months (:457-465, labelled "sum of ..."), and a quarter of flows cannot be combined with other periods. Mixed flow and stock measures in one plan are refused (:447-451) |
| Compare with explicit | `normalize` :399-410 | `prior` / `year_ago` are allowed with `latest`, or `explicit` with exactly one value and no summed-flow aggregate; otherwise refused |
| `compare: "between"` | `COMPARES` :57; `normalize` :399; `_sql` :744 | Needs `explicit` with exactly two values (either order; no summed-flow quarter). The later value is current, the earlier is `prev`; output columns are the usual `_prev`, `_change`, `_pct_change`. Unlike `prior`/`year_ago` it uses a FULL JOIN and reads a missing ADDITIVE (`SUM`/`COUNT`, `_additive` :803) side as 0, so a group held in only one period still shows its movement; averages stay NULL |
| Change sorts | `CHANGE_SORTS` :60; `_order` :815 | `change_desc/asc`, `pct_change_desc/asc` rank by the first measure's change (pct NULLs last, ties by dimension). Refused without a compare. With `limit` this is "largest increase / decrease" |
| Plain averages | `build_book` :174 | `AVG(col)` metrics are labelled "Simple average of X (unweighted)". Row average, not exposure-weighted. **Interim convention** pending `docs/cockpit_v4/TODO_FORMULAE_IFRS9.md`; the answer must disclose it (section 4c) |

### 4f. Few-shot examples (`cv4/examples.py`, `examples.json`)

Flag `COCKPIT_V4_EXAMPLES=on` (`ENV_FLAG` :39; `enabled()` :57; **default off**). Curated, hand-written plans from TRAIN, ready items only (no holdout/sealed, guarded by `tests/cockpit_v4/test_examples.py`), relative periods except where an explicit period is the point (each such example carries a `note`; `c_ecl_top5_increase` shows `compare: "between"` + `change_desc` + `limit`), up to 2 same-domain neighbours at token-Jaccard >= `MIN_SIMILARITY` 0.2 (:43; `select` :78, `block` :95). Included only when the capability supports the plan compiler (`context.py:352-356`), in the VOLATILE system block only (`context.py:383`, so the cached stable prefix is unaffected), and stripped from the answer turn (`context.py:748-754`, `finalization_system`) because every answer-turn token costs local latency. `scripts/cockpit_v4/verify_examples.py` compiles AND executes every example against the live releases (exit 1 on a compile error, execution error or empty result; refuses to run under the test fixtures). Kept only if live paired holdout runs say so.

---

## 5. Provider layer

### `cv4/provider.py`
| Item | Where | Behaviour |
|---|---|---|
| Token counting | `count_input` :220-249 | Provider `count_tokens` if `capability.supports_token_counting`, else local estimate `len(json)/2.2 + 1`. Local oMLX price card has `supports_token_counting: false`, so oMLX always uses the estimate and spends no attempt on counting |
| Fit check | `fits` :251-255; used :419, :453 | `counted + reserved_output + max(1024, 2%) <= context_tokens`, else `InputTooLarge` (`INPUT_CONTEXT_LIMIT`) |
| Output clamp | :417-418 | `reserved_output = min(requested, capability.max_output_tokens)` |
| Provider attempts per generation | :232 (count_tokens), :332 (`send`), :497 (`ask`) | `ask` spends one at :497 and then `_converse.send` spends another at :332, so **2 per generation minimum** (oMLX) and **3** on Anthropic (count_tokens). A reduced allowance re-runs `fits` (:453) and counts again; a refused-parameter re-send (:365) spends one more. Std: 12 generations x 2 = 24 = the whole `provider_attempts`; on Anthropic the 24 is reached after ~8 generations |
| `allow_retry` | :344 | `send` always passes `allow_retry=False` to the provider, so no provider-internal retry (oMLX or Anthropic) ever applies to V4; the one retry is V4's own `spend_transport_retry` |
| `_classify` | :652-746 (called :352, :531) | Order: pre-coded `ProviderFailure` passes through; **400** -> `PROVIDER_REQUEST_INVALID`/`TOOL_SCHEMA_INVALID`; 401/"authentication" -> `PROVIDER_AUTH`; 403/"permission" -> `PROVIDER_AUTH`; 429/"rate"+"limit" -> `PROVIDER_RATE_LIMIT` (transport); timeout/"timed out"/"deadline"/"connection"/502/503/"overloaded" -> `PROVIDER_UNAVAILABLE` (transport, `detail.timed_out`, :732-740); everything else -> `PROVIDER_UNAVAILABLE`, `retry_class="none"` (:741-746) |
| "400" substring | :675 (and :355) | `"400" in text` matches ANY message containing the digits 400 (a request id, a port, token counts). It runs first, so such an error is reported as a malformed request and the timeout/connection tests never run |
| Transport retry | `orchestration.py:887-908` | Once per run (`budgets.py:453`) |
| Temperature | `_temperature`; `model_capabilities.ModelTraits.sampling_control` | `COCKPIT_V4_TEMPERATURE` (default 0, clamped 0..1) is sent to `converse(temperature=...)` ONLY when the model registry trait `sampling_control` is true (claude-opus-4-6, claude-sonnet-4-6, claude-sonnet-4-5, claude-haiku-4-5, prefix match). Omitted otherwise, and NOT covered by the refused-parameter drop (`_PARAM_REFUSED` is unchanged). `claude-opus-5` does not get a temperature, so its consistency rests on the clarification gate (4d2) |

### `llm/omlx_provider.py`
| Item | Where | Behaviour |
|---|---|---|
| Default timeout | :109 | `timeout: float = 60.0`; V4 always passes its own `timeout` (`provider.py:341`) |
| Concurrency | :96, slots :88-100 | `OMLX_MAX_CONCURRENCY` default **2**, process-wide `BoundedSemaphore` per base URL |
| Slot wait | :133-140 | Acquires a slot with `timeout`; wait is subtracted from the request timeout; `LLMError` if busy or spent |
| Timeout detection | `_is_timeout` :27-49 | Walks the cause/context chain for socket/requests timeouts and "timed out" text |
| Timeout handling | :299-324 | **A timeout is never retried internally.** It raises `LLMError(f"oMLX request timed out after {window:.0f}s")` (:323-324), which V4 `_classify` maps to transport (V4's one ledger-bounded retry) |
| Internal retry | :298-315 | Only a NON-timeout, non-`LLMError` fault (dropped connection, broken chunked stream) with `allow_retry=True` and `remaining >= _MIN_RETRY_WINDOW` (1.0s, :53) gets one more `converse` with `timeout=remaining` (the window LEFT, not a fresh one) and `allow_retry=False`. **V4 passes `allow_retry=False` (`cv4/provider.py:344`), so this never applied to V4 runs.** It does for `structured()` (:169) and other default-`allow_retry` callers |
| Error wrapping | :316-325 | `LLMError` is re-raised as is; a non-timeout fault becomes `LLMError("oMLX request failed (TypeName)")`, which `_classify` does not treat as transport, so it is not retried by V4 |
| Schema re-ask | `_reask_or_keep` :327-358 | `OMLX_REASK_TIMEOUT` default **45s** (:340), but bounded by what is LEFT of the caller's window: `timeout=min(cap, remaining)` (:354); **skipped when remaining < 5s** (`_MIN_REASK_WINDOW` :55, check :347-350), keeping the original turn. Never fatal (any `LLMError` keeps the original turn). Slot wait inside the re-ask is inside that timeout |
| Thinking | :196-199 | `chat_template_kwargs.enable_thinking = False`; `temperature = 0` |

### `llm/anthropic_provider.py`
| Item | Where | Behaviour |
|---|---|---|
| `MAX_ATTEMPTS` | :35 | 3 (applies only if `allow_retry`; V4 passes False, so 1 loop iteration, :244) |
| `TIMEOUT_SECONDS` | :40 | 60.0, client default (:55, :327-328); V4 per-call `timeout` goes through `with_options(timeout=...)` (:262-264) |
| SDK `max_retries` | :327 | **Not set**, so the SDK default applies: `DEFAULT_MAX_RETRIES = 2` in the pinned `anthropic==0.112.0` (`requirements.txt:28`). Hidden SDK retries each get the per-call timeout and are invisible to the ledger, although `budgets.py:289-290` and `provider.py:342-343` state the SDK retries are disabled. Shared with V3, deferred (D17) |

### Price card `config/cockpit_v4/price_card.omlx.local.json`
`max_output_tokens: 8192`, `context_tokens: 262144`, `supports_token_counting: false`, all prices 0.0 (so no `COST_LIMIT` locally). `max_output_tokens` clamps every response allowance (`provider.py:417-418`).

---

## 6. SQL, worker, routes, frontend

| Area | Constraint | Where |
|---|---|---|
| SQL | `MAX_MODEL_ROWS = 200` rows shown to the model; `MAX_RESULT_ROWS = 100,000` produced (more = `RESOURCE_LIMIT`) | `sql.py:57,60,640-644` |
| SQL | Step watchdog thread interrupts `session.connection` after the step budget; `SqlRejected(RESOURCE_LIMIT)` | `sql.py:596-633`; parameterised path `execute_tool.py:772-781` |
| DuckDB | `SET threads TO 2`, `memory_limit = '1536MB'`, external access off | `catalog.py:451,467,484` |
| DuckDB | One cached session per (tenant, domain, release, fingerprint), shared by all runs in the process | `analytical_runtime.py:180-195` |
| Worker | ONE worker thread and ONE supervisor thread per API process | `app.py:176-184` |
| Worker | Claim: oldest unclaimed outbox row; lease with fence | `run_store.py:769-784` |
| Worker | Lease heartbeat every 2.0s; run is `WORKER_LOST` if heartbeat older than 10s (`lease_stale_seconds`); supervisor polls every 2s | `worker.py:481-494`; `config.py:519-521`; `supervisor.py:58,85-108` |
| Worker | Ledger clock starts at worker `execute`; `deadline_at` was stamped at accept (`routes.py:409,493`); worker pushes it out to `now + deadline` at claim (`worker.py:174-185`) | |
| Store | SQLite `timeout=30.0`, `PRAGMA busy_timeout=30000` | `run_store.py:396-402` |
| Routes | Question max 8,000 chars (Pydantic, 422) | `routes.py:111` |
| Routes | `RUN_IN_PROGRESS` 409: one active run per thread; `TOO_MANY_RUNS` 429: two active runs per principal | `routes.py:400-408` |
| Routes | SSE: replay from cursor, heartbeat frame every `COCKPIT_V4_HEARTBEAT_SECONDS` (default 5, floor 1) | `routes.py:2148,2180-2181`; `config.py:518` |
| Frontend | `frontend/src/components/cockpit-v4/client.ts`: **no fetch timeout** (no `AbortController`/`signal` anywhere in the file); SSE reconnect backoff `RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 8000, 8000]` (:446) then "lost" state (:1418-1425); a disconnect does not cancel the run | `client.ts:446,1356-1426` |

---

## 7. Measured behaviour (local oMLX Qwen)

### 7a. Run latency

Source: the V4 state DB, 44 runs, 2026-09-30 to 2026-10-04. Figures were supplied with the plan and are quoted here as given; they were not re-derived while writing this document.

- Latency fit: seconds ≈ −9 + 0.86 × input_ktok + 23.2 × output_ktok (±5s).
- Prefill ~1,160 tok/s; decode ~43 tok/s.

| Phase | p50 | p90 | p95 | max |
|---|---|---|---|---|
| Action | 8.7s | 24.8s | 30.0s | 60s |
| Final answer | 20.5s | 60.7s | 75.7s | 119s |
| Answer correction | 58.5s | 87.7s | n/a | n/a |

| Tokens | Input avg | Input max | Output typical | Output max |
|---|---|---|---|---|
| Action | 15k | 29k | ~440 | n/a |
| Answer | 21k | 59k | ~980 | 4.8k |

- V4 transport retry fired 0 times in that window (measured before the oMLX timeout classification fix, so the figure describes the old behaviour). Output truncation fired once.

### 7b. Context budget (`docs/cockpit_v4/evidence/context_budget.json`, `scripts/cockpit_v4/context_budget.py`)

Evidence class: REAL DATABASE, NO MODEL (generated 2026-10-04). Static half = the real request assembled for one corporate question (the retail release was not published in that runtime, so retail static is an error entry); dynamic half = 181 provider calls from 45 recorded runs (99 action, 72 answer; 4 runs skipped on call-count mismatch). Cost uses 0.86s per 1k input tokens (uncached upper bound; oMLX prefix-cache reuse would lower it).

| Turn | Fixed part (system + tools) | Whole request | Source |
|---|---|---|---|
| Action, first call (static) | 14,444 tokens calibrated, **~12.4s** | same (conversation is one question) | `static.corporate.action_turn.total` |
| Action (recorded) | p50 13,752 tokens, **p50 11.8s / p90 12.4s** (93% of input at p50) | input p50 14.6k, p90 16.5k, max 27.3k; **p50 12.6s, p90 14.2s** | `dynamic.action_turns` |
| Answer, fixed part (static) | 16,426 tokens calibrated, **~14.1s** (excludes the tool_result conversation) | n/a | `static.corporate.answer_turn_fixed_part.total` |
| Answer (recorded) | p50 11,462 / p90 24,007 tokens, p50 9.9s / p90 20.7s | input p50 20.8k, p90 31.5k, max 59.3k; **p50 17.9s, p90 27.1s** | `dynamic.answer_turns` |

So the fixed prompt costs roughly **12s on every action turn and 14s on every answer turn** before the model writes a token. Largest pieces: `analyst.md` instruction 16.1 KB (~5.0s calibrated, carried unchanged on the answer turn), stable semantics 10.0 KB, `finalize_response` schema 13.0 KB (answer turn; 2.7 KB on action), and on the answer turn the `execute_analysis` OK result packet (mean 3,384 tokens, p90 8,840 = 7.6s, mostly preview rows).

Top trims are **recommendations only, not implemented, and gated on paired live runs** over the scored corpus (`recommendations_not_implemented`): (1) answer-turn variant of the instruction (-5.0s/answer turn, MEDIUM risk); (2) de-duplicate `finalize_response` schema prose (up to 4.1s, MEDIUM-HIGH); (3) cap preview rows and send `answer_kit`/citation guide once per result (2.9s mean, 7.6s p90, HIGH risk: claim verifier binds to row ids); (4) withhold `inspect_catalog`/`execute_analysis`/`read_artifact` schemas on the answer turn (1.8s, MEDIUM: the ANSWER_CORRECTION path may rely on them). Not proven by that file: that trimming preserves answer quality (no model ran, nothing re-scored); exact token counts (estimated, calibrated by regression); that old recorded conversations match today's code.

---

## 8. Known defects (current behaviour)

Status: FIXED / PARTIAL / OPEN, with a pointer.

| # | Status | Defect | Where | Effect / resolution |
|---|---|---|---|---|
| D1 | FIXED | oMLX internal retry re-issued the call with the full `timeout`. **It never applied to V4** (`allow_retry=False`, `cv4/provider.py:344`); the historic 60s calls on a 30s window seen on V4 came from the unbounded schema re-ask (D3), not from this retry. Now a timeout is never retried internally and a non-timeout fault retries once with the REMAINING window, for `allow_retry=True` callers only | `omlx_provider.py:298-315`; `cv4/provider.py:344` | Non-V4 callers can no longer double their window |
| D2 | FIXED | An oMLX timeout was wrapped as "oMLX request failed" and `_classify` could not see it, so the V4 transport retry never ran. Now raises "oMLX request timed out after Ns", classified transport (one ledger-bounded retry). Residual: non-timeout faults still read "oMLX request failed (TypeName)" and are not retried by V4 | `omlx_provider.py:323-325`; `cv4/provider.py:732-740` | Answer/action timeout on oMLX gets V4's one retry |
| D3 | FIXED | The schema re-ask had its own fresh timer after the first call used up to `timeout`, so one generation could take `timeout + 45s` (the real source of the 60s-on-30s calls on V4). Now bounded by the remaining window, skipped under 5s | `omlx_provider.py:327-358` | A generation stays within `call_timeout_seconds` |
| D4 | FIXED | `adopt` widened only `deadline_seconds` and `spend_ceiling_usd`, so an upgraded run kept product-help call windows and a 20s reserve. Now widens all time fields | `budgets.py:98-122` | Widened run gets analytical windows |
| D5 | FIXED | Two code paths for "which Limits" (`envelope` read constants directly). Now envelope, route, worker, diagnostics, widening and supervisor all resolve through `config.effective_limits` | `envelope.py:69-80`; `config.py:334-369`; `service.py:461-472` | One resolver; profile applies everywhere |
| D6 | OPEN | A transport retry in the answer phase is capped at `answer_call_seconds` again and is not sized to what is left; `answer_reask_remains()` is unchanged by a transport retry. **Plan step 11 (answer-timeout full-remainder retry) is NOT implemented**, pending the oMLX server-cancellation experiment (does a client timeout cancel the server-side generation or does it keep decoding and block a slot) | `budgets.py:204-205`; `orchestration.py:887-908` | Retry can be cut short by the run deadline, or sized wrong |
| D7 | PARTIAL | Finalization reserve 20s (Std) / 25s (Deep) is far below measured answer latency (p50 20.5s, p90 60.7s, max 119s). Local analytical profile now holds 90s, and an action call is capped at `remaining - reserve - 2s`, so an action can no longer run into the reserve. `default` profile unchanged | `config.py:178,196,213,284-286`; `budgets.py:197-203,239-240` | OPEN under `default`; mitigated under `local` |
| D8 | OPEN | Provider attempts are counted 2x per generation on oMLX and 3x on Anthropic (`ask` :497 and `send` :332, plus `count_tokens` :232) | `cv4/provider.py:232,332,497` | `provider_attempts=24` is exhausted at 12 generations (oMLX) or about 8 (Anthropic); can raise `CALL_LIMIT` while `generation_attempts` has headroom |
| D9 | OPEN | `"400" in text` matches any message containing "400" and runs before the timeout and connection checks | `cv4/provider.py:675,355` | Misreported as `PROVIDER_REQUEST_INVALID`; not retried |
| D10 | OPEN | `deadline_at` is stamped at accept (`routes.py:409`) but the ledger clock starts at claim (`worker.py:134`). The supervisor's `expiring_runs` includes not-yet-claimed runs (`run_store.py:819,844`). Worker pushes the deadline out at claim (`worker.py:180`) and `adopt` sets store deadline to `now + deadline` (`orchestration.py:2002-2006`), looser than the ledger's `started + deadline` | listed | With one worker thread, a queued run can be expired by the supervisor before it is claimed; store and ledger deadlines differ after widening |
| D11 | OPEN | The DuckDB session is shared and cached across runs; the step watchdog calls `session.connection.interrupt()`, which interrupts whatever is running on that connection. Single worker thread limits concurrent use today | `analytical_runtime.py:180-195`; `sql.py:613`; `execute_tool.py:776` | A late or misdirected interrupt could cancel another statement if a second consumer ever shares the connection |
| D12 | OPEN | `answer_output_ceiling` (12,288 Std / 16,384 Deep) and the "larger allowance" re-ask message are defeated on local oMLX: `min(requested, capability.max_output_tokens=8192)` clamps the re-ask to the same 8,192 as the first attempt (Std). Deep's `reserved_output_tokens` 12,288 is clamped to 8,192 on the first attempt too | `cv4/provider.py:417-418`; price card `max_output_tokens: 8192` | A truncated answer is re-asked with no extra room, while the prompt tells the model the allowance is larger (`orchestration.py:843-885`) |
| D13 | OPEN | `ACTION_FORMAT_EXHAUSTED` message says "the one re-ask" but `format_regenerations = 2`; `config.py` comment on `action_call_seconds` says 2x40s but the value is 30 | `budgets.py:406`; `config.py:130,183-197` | Misleading diagnostics |
| D14 | PARTIAL | Plain-average metrics are `AVG(col)`, a row average, not exposure-weighted. Now labelled "Simple average of X (unweighted)" and a finalization check forces disclosure (or a question about weighting). The weighting convention itself is undecided (`TODO_FORMULAE_IFRS9.md`) | `plan_compiler.py:174`; `finalization.py:756` | An average of rows can differ from a portfolio-weighted figure; disclosed, not corrected |
| D15 | PARTIAL | A plan with no `period` defaults to `kind="latest"` (`plan_compiler.py:373`). `explicit` periods now exist (:44) and the intent check refuses a plan whose period misses one the question names (section 4d), but a question naming no period still gets the silent latest default | `plan_compiler.py:44,373`; `intent_check.py:164` | Silent latest-period default when the question names no period and the model omits `period` |
| D16 | OPEN | `PLAN_FORMAT_EXHAUSTED`, `INPUT_CONTEXT_LIMIT`, `WORKER_LOST` and `PROVIDER_RATE_LIMIT` are not in `RESULT_ONLY_REASON`, so rows already stored are never published for them | `finalization.py:62-105`; `supervisor.py:105-108` | A late failure of those kinds shows a red box over a computed result |
| D17 | OPEN | Anthropic SDK `max_retries` not set (SDK default 2, hidden from the ledger). Deferred: the client is shared with V3 | `llm/anthropic_provider.py:327-328`; `requirements.txt:28` | Hidden retries each get the per-call timeout |
| D18 | OPEN | Queued runs can expire before claim (same mechanism as D10). Listed separately because it is the observable symptom under load | `run_store.py:819,844`; `supervisor.py:90-100` | A run that never started can settle as `EXPIRED` |
| D20 | OPEN | `compare: prior/year_ago` use `cur LEFT JOIN prev` and raw `cur - prev`: a group absent from the earlier period gets a NULL change (sorted last), and a group absent from the current period is dropped. `between` does not have this (FULL JOIN, additive zero-fill); `prior`/`year_ago` were left unchanged on purpose so existing compiled SQL stays byte-identical | `plan_compiler.py` `_sql` :744 | "Largest increase" via prior/year_ago can miss a group that started from zero; switch them to the `between` join only with a paired eval |
| D19 | OPEN | `model_capabilities.REGISTRY` matches by longest PREFIX, so the `claude-opus-5` entry also matches `claude-opus-5-5` (and any other `claude-opus-5-*`), giving it opus-5 traits whether or not that is right | `model_capabilities.py` `traits_for` | A later model sharing the prefix silently inherits the family's traits (including no `sampling_control`) until it gets its own entry |

---

## 9. Evaluation pipeline and gates

| Piece | What it does |
|---|---|
| `tests/cockpit_v4/golden/questions.json` | The corpus (99 questions; +3 on 2026-10-05 from run b8aa21b3: `c_s3_overlap` and `c_s3_top5_abs` in holdout, one group; `c_ecl_top5_increase` in train, backing the `between` example). Schema v2: `id, group, domain, mode, question, split (train/holdout/sealed), status (ready/pending_formula), expect_state, contract, gold_sql`. Splits are per `group`, never per item |
| `scripts/cockpit_v4/golden_import.py` | The only writer of the corpus: `--from-csv/--from-xlsx`, `--bootstrap-question-bank`, `--derive-contracts`, `--group-twins`, `--assign-splits [--seed]`, `--promote RUN_ID --id ID --yes`, `--validate` |
| `scripts/cockpit_v4/golden_scoring.py` | Pure contract scoring: gold rows aligned to run rows by entity key, each measure compared in its own column, tolerance per contract; verdict `pass | fail | unscorable` (unscorable is kept out of eligible accuracy, inside total coverage) |
| `scripts/cockpit_v4/golden_eval.py` | Runs each question on the live Cockpit and scores it by contract (`legacy_match` kept for comparison). Options: `--ids --domain --mode --split --status --repeat --out`, `--mine DB` (score runs already in a state DB, starts nothing), `--paired BASELINE.json` (paired comparison). Reports via `golden_report.py` (Wilson 95% intervals, exact McNemar) |
| `scripts/cockpit_v4/golden_triage.py` | Puts every non-passing run in exactly ONE fix class, each naming the file where a general fix belongs; writes `artifacts_eval/triage_<UTC ts>.{md,json}` (options `--db --results --since --out-dir`) |
| `scripts/cockpit_v4/shadow_precision.py`, `verify_examples.py`, `context_budget.py` | Sections 4d, 4f, 7b |
| `artifacts_eval/gates.json` | Frozen acceptance gates (frozen 2026-10-04, before any candidate run; changing one after a candidate was measured needs a new baseline and a `changelog` entry). Rate claims need the full scored corpus x >= 3 paired repeats; smoke set is directional only. Every phase: unit tests green with no `COCKPIT_V4_LIMITS_PROFILE` set, no significant paired regression (McNemar one-sided p < 0.05), wrong-answer rate not up. Phase 1: 0 window-overrun calls (tolerance 2s), 0 action calls entering the reserve, lower failure rate. Phase 2: all 3 known silent-wrong cases refused-and-repaired or correct, shadow-rule promotion precision >= 0.95, 0 false entity-scope refusals on comparison questions. Phase 3: holdout paired, accuracy and coverage not lower and one higher |

---

## 10. Not implemented

- Plan step 11: answer-timeout retry sized to the full remainder (D6), pending the oMLX server-cancellation experiment.
- Context trims in section 7b, pending paired live runs.
- Weighted averages: pending `docs/cockpit_v4/TODO_FORMULAE_IFRS9.md`.
- Promotion of any raw-SQL shadow intent rule, or of the plan rule `operation_dropped`, to enforcement: pending measured precision >= 0.95 (first replay, 2026-10-05: 45 stored plans, 7 `operation_dropped` flags, all from one question family; 82 raw-SQL steps, 0 `sql_operation_dropped` flags; too narrow to promote).
- Deferred: Anthropic SDK `max_retries` (D17); provider attempt accounting (D8); the "400" classification (D9); queued-run expiry (D10/D18); DuckDB shared-session interrupt (D11); `RESULT_ONLY_REASON` coverage (D16).

This document is updated when behaviour changes.
