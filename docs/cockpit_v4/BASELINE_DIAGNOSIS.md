# Baseline diagnosis — incident `err-2569c1be3faa`

**Status: root cause UNCONFIRMED.** The evidence needed to identify it is not
reachable from this environment, and nothing below fills that gap with a
guess. What *is* established is where the reference came from, what it can and
cannot tell you, and which causes are ruled out by the structure of the V3
code itself.

Every claim in the OBSERVED and REPRODUCED columns is asserted by a test in
`tests/cockpit_v4/test_incident_err_2569c1be3faa.py`, which reads the V3
source. If V3 changes so that a claim stops holding, that test fails.

---

## The columns

### OBSERVED — established from the V3 source in this repository

| # | Fact | Where |
|---|---|---|
| O1 | The reference shape `err-` + 12 hex characters has exactly **one** generator in V3: `f"err-{uuid.uuid4().hex[:12]}"`. | `backend/cockpit_agentic/runtime.py:351` |
| O2 | That generator sits inside the **generic `except Exception`** handler of `Runtime.run`, and the outcome it produces is `INTERNAL_ERROR`. | `runtime.py:346–367` |
| O3 | The screenshot's stage wording is produced by exactly one label: `FUNCTIONALITY_ASSESSMENT: "Checking this is a Cockpit question"`, lower-cased into the narrative by `st.progress(state).lower()`. | `states.py:446`, `runtime.py:361` |
| O4 | The user-facing text is templated: *"Something in this application failed while {stage}. The failure is recorded under {error_id}."* The wording carries **no** information about the exception. | `runtime.py:358–363` |
| O5 | The error id is **never persisted**. It is created, interpolated into the response text, and passed to `logger.exception`. It reaches no database, no artifact and no event row. | `runtime.py:351–353`; grep for `error_id` finds occurrences only in that block |
| O6 | The stage label does **not** localise the failure to the model call. `Runtime._not_ours` never calls `_advance`, and `_explain` advances only after several statements — so the ownership test, the whole referral path, and the first part of the explain path all run while the state is still `FUNCTIONALITY_ASSESSMENT`. | `runtime.py:449–470`, `runtime.py:811`, `runtime.py:849–860` |

### REPRODUCED — demonstrated by executing code

| # | Fact | How |
|---|---|---|
| R1 | The exact incident envelope shape is reproducible: an arbitrary exception raised while the machine is in `FUNCTIONALITY_ASSESSMENT` yields precisely the observed message template and a fresh `err-` reference. | asserted from source structure in the incident test |
| R2 | In V4, the equivalent failure produces a **persisted** error id, a stored operator detail and a durable failed event — so the same class of incident is diagnosable next time without asking anyone to paste a log. | `test_v4_records_an_internal_defect_with_a_retrievable_detail` |

### ELIMINATED — ruled out by code structure, not by opinion

These exception classes are handled by name **before** the catch-all in
`Runtime.run` and map to their own terminal states, so none of them can have
produced an `err-` reference:

| Class | Terminal state it produces instead |
|---|---|
| `BudgetExceeded` | the matching `STOPPED_*` state |
| `opus.PlanTruncated` | `STOPPED_OUTPUT_LIMIT` |
| `opus.OpusUnavailable` (and its subclass `OutputTruncated`) | `PROVIDER_ERROR` |
| `credential.ProviderCredentialMissing` | `PROVIDER_CREDENTIAL_MISSING` |
| `models.CockpitModelError` | `MODEL_UNAVAILABLE` / configuration state |
| `tokens.TooLargeToSend` | `CONTEXT_TOO_LARGE` |
| `context.ContextTooLarge` | `CONTEXT_TOO_LARGE` |
| `sql.SqlRejected` (session open) | `EXECUTION_FAILED` |

One more is eliminated by a different mechanism, which matters for anyone
reading only the `except` clauses: **`LLMError`** *is* caught by the generic
handler, but an `isinstance` check routes it to `PROVIDER_ERROR` and returns
**before** any error id is minted.

**Therefore:** the incident was **not** a provider transport failure, **not**
an authentication failure, **not** a missing credential, **not** an
unconfigured or unserveable model, **not** an output truncation, and **not** a
context-size stop. It was an unhandled exception inside CreditProbe's own
code.

### HYPOTHESIS — candidates, ranked, none confirmed

Each is a real uncaught-exception site reachable while the state is
`FUNCTIONALITY_ASSESSMENT`. They are listed so an operator with the log knows
what to look for. **None of these is a diagnosis.**

| # | Candidate | Why it is plausible | What would confirm it |
|---|---|---|---|
| H1 | Type coercion of the gate response: `int(s.get("score") or 0)` raises `ValueError` if a score arrives as a non-numeric string. | The mapping runs immediately after the model returns, inside the stage, and is unguarded. | `ValueError: invalid literal for int()` in the log traceback |
| H2 | Construction of `K.FunctionalityDecision` from the mapped fields with an unexpected type or missing key. | Same window; dataclass construction validates and raises. | `TypeError` / `KeyError` naming a decision field |
| H3 | A failure in the **referral** path (`_not_ours`) — which O6 shows runs under the same stage label even though the gate call itself succeeded. | Attributing this to the gate is exactly the mistake the label invites. | a traceback whose deepest frame is in `_not_ours` |
| H4 | A failure in the first statements of `_explain`, before it advances the state. | Same window as H3. | a traceback whose deepest frame is in `_explain` above line 860 |
| H5 | A rejected state transition — `_advance` to a state not in `TRANSITIONS`. | `Machine.advance` raises rather than returning. | a transition error naming the source/target pair |

Ranking is by how much unguarded coercion each site performs, not by evidence.
H1 and H2 are the most exposed; H3 and H4 are the ones a reader is most likely
to overlook.

### NOT AVAILABLE — and why

| Missing evidence | Why it cannot be recovered here |
|---|---|
| The exception type and traceback | Written only by `logger.exception` in the process that served the request, on the operator's machine. |
| The provider request id and stop reason | Not recorded on this path. |
| The run's ledger, counters and elapsed time at failure | V3 does not persist a run record for a request that failed this way. |
| Any correlation for the string `err-2569c1be3faa` itself | O5: the id is generated from `uuid4` and stored nowhere. There is no index to look it up in. |
| The process startup SHA | V3 does not record one; a later `git rev-parse` describes the checkout, not the running process. |

**What would close this:** the serving process's log around the incident. One
line — the `logger.exception` entry containing `err-2569c1be3faa` — carries the
exception type and the failing frame, which selects among H1–H5 immediately.
The corresponding V4 record needs no log at all.

---

## What V4 changes about this class of incident

Not "V4 fixes err-2569c1be3faa" — V4 cannot fix an unidentified defect. What
it changes is diagnosability:

1. **The error id is persisted** with the run, alongside the operation that
   was in flight, and appears in the durable event sequence.
2. **An operator detail record** is written for the failure, redacted on the
   way in (`orchestration._redact`), so a downloadable trace can carry the
   exception type and failing operation without carrying a secret.
3. **Stages are narrower.** V4 emits sub-span events for the outbound request,
   the response, the parse, the typed validation, the policy check and the
   state commit — so "checking this is a Cockpit question" can no longer cover
   five distinct operations, which is the defect O6 identifies.
4. **The startup SHA is recorded once** at process start and stored on every
   run, so the trace names the code that actually ran.

## Also audited from the V3 risk list

| Risk | Finding in V3 | V4 |
|---|---|---|
| Delivery coupled to summary generation | Real: `service.ask` runs the Sonnet summary on the answer path. | Memory is off by default and always post-publish; the answer is persisted before `answer.ready`. |
| Character expansion of scalar strings into list fields | Real: list fields accepted a scalar and expanded it. | The contract **rejects** a scalar for an array field and names it. Recovery of the known defect requires the stored schema version, never a length heuristic. |
| Oversized schema context | Real: the full field dictionary is attached to planning calls. | A compact index; definitions come from `inspect_catalog` on request. Asserted by `test_help_does_not_receive_the_whole_catalogue`. |
| Model output truncation / incomplete tool history | Handled in V3 (`OutputTruncated` rolls the partial turn out of history). | Same behaviour, kept. |
| Runtime trimming of subquestions, fields or steps | Real risk in V3's plan compaction. | No code path in V4 can trim; an overlong batch is rejected whole. |
| Apparent success based on mocked model answers | Real risk in any suite. | Every test is labelled MODEL MOCK / REAL RUNNER / REAL HTTP, and the two analytical checkpoints are verified against independent pandas oracles. |
| Generic frontend timeout / silent catch | Present in V3's request-response flow. | Replaced by durable runs, SSE with replay, and an explicit `CONNECTION_LOST` client state that does not claim the server failed. |

## On the reported ~227-token planning output

Not used as a V4 baseline. It was not a fresh live-provider measurement, and
this build makes no before/after performance claim that rests on it. The
measurements V4 does report are in `UAT_RESULTS.md`, each labelled with how it
was obtained.
