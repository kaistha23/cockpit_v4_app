# Token and cost policy

## What replaced V3's ceilings

V4 retires V3's mandatory full catalogue, its independent 64k/96k input
cutoffs and its default cumulative 250k/500k token stops. Those were arbitrary
product limits standing in for a capacity check.

They are replaced by an actual capacity check plus real cost, time, call and
execution limits. That is not permission to spend without bound — the limits
below are stricter in the ways that matter.

## Before every generation

```
assembled_input + reserved_output + safety_margin  ≤  verified_model_context
```

- The **whole** request is counted: system blocks, tool schemas and the full
  conversation, because all three are input the provider bills and the context
  window holds.
- Counting uses the selected model's own counting interface where available.
  A local character estimate is **labelled** an estimate
  (`local_conservative_estimate`) and is deliberately conservative; it is never
  presented as calibrated truth.
- Safety margin: `max(1024 tokens, 2% of measured input)`.
- If the essential context still does not fit, the run stops with
  `INPUT_CONTEXT_LIMIT` — not with "try rephrasing your question", which
  cannot make a data dictionary smaller.

A 64,626-token request is not invalid if the model, the spend budget and the
scope permit it. Nor is it efficient. The difference is recorded rather than
legislated.

## Soft input targets

6,000 tokens Standard, 10,000 Deep, **including** schemas. These generate
telemetry; they are not refusal thresholds, and no required instruction is
mutilated to hit one.

## Output reservation

4,096 Standard / 6,144 Deep, applied to a small action contract rather than a
planning essay. It may increase after an explicit output-truncation event,
within the verified model maximum, remaining spend and the deadline. The
increase and its cost are recorded. There is no unlimited continuation, and
4,096 is not a definition of analytical adequacy.

## The price card is a gate, not decoration

`COCKPIT_V4_PRICE_CARD` names a versioned card that must carry, for the exact
configured model: provider, context window, maximum output, and **all four**
billing classes — uncached input, output, cache write, cache read. A missing
entry, a wrong provider, a missing class or a non-numeric value fails closed
with `CAPABILITY_UNVERIFIED`, and **no paid request runs**.

The card shipped at `config/cockpit_v4/price_card.json` is a **placeholder
with zero prices and an empty `verified_at`**. It must be replaced with the
current published schedule before any paid run. V3's Sonnet price settings are
not carried forward as facts.

## Reservation and settlement

1. Before each paid attempt, reserve the **worst applicable** cost: counted
   input, worst cache write, and the **maximum** output — not an expected
   output. A run must be able to afford the response it asked for.
2. If the projection exceeds the ceiling, stop with `COST_LIMIT` before
   sending.
3. After the response, settle at actual usage from the provider's own counters.
4. If the call failed, was cancelled or disconnected, hold the reservation
   **pending** and mark it uncertain. It may still have been billed; booking it
   as zero understates real spend.

`cost_enforced` is true only when the price is verified **and** nothing is held
as an unknown amount. A user is never shown an "enforced" badge while the cost
is unknown — asserted by
`test_a_failed_provider_call_holds_its_reservation_pending`.

## The bounds

| Guardrail | Standard | Deep |
|---|---:|---:|
| Run deadline from acceptance | 60 s | 120 s |
| Execution submissions (global) | 5 | 5 |
| Analysis rounds (global) | 3 | 3 |
| Generation HTTP attempts | 12 | 16 |
| Total provider HTTP attempts incl. counting | 24 | 32 |
| Catalog calls/pages | 4 | 6 |
| Artifact reads/pages | 6 | 10 |
| Steps per batch | 6 | 8 |
| Total steps attempted | 12 | 24 |
| Execution wall time per step | 15 s | 30 s |
| Python memory | 512 MiB | 1 GiB |
| SQL memory | 512 MiB | 1 GiB |
| Executed output per step | 25 MiB | 50 MiB |
| Result preview | 100 rows / 32 cols | same |
| Format regeneration | 1 | 1 |
| Answer-only correction | 1 | 1 |
| Request spend ceiling | USD 1 | USD 2 |
| Charts | 2 | 3 |
| Active runs per thread | 1 | 1 |
| Active runs per user | 2 | 2 |

No earlier limit must be exhausted before a later one can stop a run. Five
submissions is an upper bound, not a promise that a run can afford five
expensive generations.

## Counting rules that matter

- **Any fully received `execute_analysis` request costs a submission**,
  including one that fails validation. It cost a generation and a validation
  pass; free invalid submissions are an unbounded loop with a counter that
  never moves.
- **An incomplete or truncated generation costs model, cost and time budgets
  but not an execution submission** — no complete tool request was obtained
  from it, and nothing from it ran.
- **SDK automatic retries are disabled** (`allow_retry=False`), so every actual
  HTTP attempt passes through the ledger. The scripted provider in the test
  suite asserts this on every call.
- **The deadline is enforced outside the client.** An HTTP read timeout bounds
  one socket read, not an end-to-end run; the supervisor enforces the run
  deadline independently of the worker.

## No-progress

The same exact code, parameters, release and failure class are not executed
twice without a recorded environmental change. The resubmission still costs a
submission slot. The key includes the error **class**, so a transient
environment failure is not permanently cached as an invalid query while an
identical deterministic failure cannot be paid for twice.

`NO_PROGRESS` rejects that submission; it does not end the run. The analyst may
still write different code or finalize a supported partial answer.

## Prompt caching

Optional. Stable instructions, product metadata and tool definitions precede
volatile request content so a cache write stays reusable, and dynamic budgets
are placed last so they cannot invalidate the stable prefix. Cached context is
still context and is counted. Cache writes and reads are priced separately
because they are billed separately.

## Measured in this build

From `docs/cockpit_v4/evidence/live_path.json` — real socket path, stubbed
model, so these are **delivery** measurements, not model latency:

| Metric | Value |
|---|---|
| Accepted → first visible event | 144 ms |
| Accepted → visible final answer | 2,982 ms (of which ~2.4 s is deliberate stub delay) |
| Generation attempts for one SQL analysis | 2 of 12 |
| Execution submissions | 1 of 5 |
| Settled cost at the fixture price schedule | USD 0.081 |
| `cost_enforced` | true |

No live-provider latency, token or cost measurement is reported, because no
paid run was authorized in this environment.
