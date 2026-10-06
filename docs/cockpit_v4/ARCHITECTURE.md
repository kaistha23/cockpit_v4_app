# Cockpit V4 architecture

One Opus analyst. Four governed tools. A trace made of facts that happened.

## The shape

```
user question
  → POST /runs           persist run + outbox in ONE transaction, return 202
  → worker claims a lease
  → assemble small authorized context
  → ┌ generation ─────────────────────────────────────────┐
    │ the analyst chooses ONE action:                     │
    │   inspect_catalog   selected metadata               │
    │   inspect_product_knowledge  selected product facts │
    │   execute_analysis  its own exact SQL/Python        │
    │   read_artifact     exact stored evidence           │
    │   finalize_response the terminal answer             │
    └─────────────────────────────────────────────────────┘
  → validate · authorize · execute · persist artifacts
  → tool_result back to the analyst, in order
  → (repeat until finalize, or until a bound stops it)
  → validate the answer against the evidence
  → PERSIST the answer, THEN publish answer.ready
  → optional memory maintenance, separately bounded
```

A BROAD Product Help question — "Who are you?", "What is CreditProbe?", "What
are the seven functionalities?" — finishes in **one** generation with no tool
call: the ~924-token synopsis is already in the starting context, and
`product_knowledge.coverage` withholds `inspect_product_knowledge` from that
first action so the model is not invited to fetch what is in front of it. A
question that NAMES product detail the synopsis does not carry — Cockpit, TAC,
the four layers, Playbook, Graph Data — is offered the tool from the start and
costs a second generation for the retrieval. The withholding lasts one action:
the full tool set is restored for every action after the first, so a misjudged
question can never be stranded. The
deck is never attached wholesale, and a figure quoted in it is an illustration,
never a live portfolio value.

Ordinary help finishes in **one** generation. A simple analysis finishes in
**two** when the starting context already carries enough schema, or three when
one metadata lookup is needed. Those are architectural minimums, not promises
about a particular model's behaviour or elapsed time.

## Who is allowed to do what

| Component | May | Must not |
|---|---|---|
| The analyst (Opus) | understand the original wording, choose mode and owner, choose fields and method, write and repair code, assess evidence, write the answer | grant itself permissions or budget; treat dataset text as instructions |
| `context.py` | return exact authorized catalog/product/thread facts, paginate, redact | infer a business answer, invent a field, choose a method |
| `product_knowledge.py` | return exact recorded sections of the versioned pack, with slide provenance | answer a product question itself; surface the historical multi-agent slide as current |
| `intake.py` | normalise Unicode, whitespace and invisible control characters | correct spelling, translate, transliterate, fold case, expand an abbreviation, or change any number, date or name |
| `attention.py` | compute a published ranking over the pinned release and carry its evidence | interpret what a movement means, assert a cause, or call a model |
| `semantics.py` | state what the catalogue already defines — relation, column, type, unit, grain, period column, join key — and resolve a period phrase against the calendar | invent a meaning the catalogue does not carry, resolve a term with two defensible readings, or say which measure answers a question |
| `catalog_tool.py` | return exactly the metadata asked for, say what was returned and what is still missing, and refuse a request with no scope | expand an unnamed relation into hundreds of definitions, or let a repeat look like a first read |
| `sqlbind.py` | prove the submitted SQL binds, and return the engine's own diagnostic when it does not | rewrite the query, substitute a column, or inline a parameter |
| `contracts.py` + `execute_tool.py` | validate schema, permissions, safety, grain, limits | rewrite a query, trim a step, substitute a field, compute a substitute answer |
| `execute_tool.py` runner | execute exactly the approved code in isolation | reach another domain, a credential, the shell, the network or host files |
| `orchestration.py` | carry messages, match tool ids, persist state, enforce counters and deadlines | repair the plan, the code or the answer; fall back to V3 |
| the UI | render persisted answers and events, collect clarification, cancel, reconnect | guess progress; claim a call succeeded without an event |
| `memory.py` | summarize completed history with turn-id citations | block delivery, invent facts, overwrite newer memory |

The one thing that is *not* authorship: deterministic formatting of a value
already bound to executed evidence. Rendering `5231.577413502459` as
`5,231.58 SAR million` at the declared precision is presentation. Changing which
column it came from is substance, and no code here can do it.

## Modules

```
backend/cockpit_v4/
  __init__.py        domain constant and schema versions
  config.py          the V4 settings contract; fails closed on what matters
  contracts.py       typed intent/tool/answer contracts; rejects, never repairs
  contracts/*.json   the application JSON Schemas from the master spec
  states.py          states, terminal outcomes, error codes, transition registry
  events.py          the persisted event sequence and its SSE frames
  run_store.py       SQLite/WAL: runs, events, messages, artifacts, leases,
                     idempotency, reservations, threads, turns, summaries
  budgets.py         counters, cost reservations, the run deadline
  capability.py      the verified model capability record and price card
  provider.py        one explicit model, native tool loop, real deadlines
  context.py         the small starting context
  catalog_tool.py    inspect_catalog: selective, complete, receipted
  execute_tool.py    execute_analysis: validate whole batch, run exactly
  artifacts.py       read_artifact: exact stored values, tenant-checked
  attention.py       the home feed: deterministic sector movement ranking
                     and ECL highlights, server-authored SQL, no model call
  intake.py          the only thing done to a question before the analyst
                     reads it: Unicode, whitespace, invisible controls
  semantics.py       what a term means when it has one meaning, how a period
                     phrase resolves against this release's calendar, and the
                     mechanical schema facts for every mapped field
  sqlbind.py         proving a query bindable BEFORE saying it was validated,
                     and carrying the step's parameters to the engine
  product_knowledge.py       inspect_product_knowledge: the versioned pack,
                     its always-on synopsis, and keyword retrieval over it
  product_knowledge.json     the Product Knowledge Pack (generated, reviewed)
  finalization.py    evidence binding and numeric-claim rendering
  orchestration.py   the single analyst loop
  worker.py          lease claimant and dispatch
  supervisor.py      independent deadlines and abandoned-run recovery
  memory.py          optional post-answer compaction
  routes.py          the additive V4 API
  compat_health.py   /api/v1/health, so the shell does not call V4 offline
  app.py             the standalone ASGI app
  pyrunner.py        the Python capability, or an honest UNAVAILABLE
  prompts/analyst.md the runtime instruction (short, versioned)

frontend/src/components/system/
  runtime-surfaces.ts  reads a partial runtime out of a health payload

frontend/src/lib/
  runtime.ts           which backend this build talks to, and what it serves

frontend/src/components/cockpit-v4/
  cockpit-v4-home.tsx  the Cockpit page in a V4 runtime
  cockpit-v4.tsx       the Ask surface: question, mode, Ask, Stop, replay
  not-in-this-runtime.tsx  the neutral state for an absent optional widget
  client.ts          start/status/events/cancel/artifacts, bounded reconnect;
                     subscribes to every *named* SSE frame, not just onmessage
  reducer.ts         run-id and sequence fenced UI state, substeps, failures,
                     and server-authoritative elapsed time
  process-panel.tsx  the hideable live trace
  response-panel.tsx answer/referral/clarification/stop
  markdown-parse.ts  Markdown -> a data tree (never an HTML string)
  markdown.tsx       that tree -> React elements; hrefs allow-listed
  attention-panel.tsx  the two home sections; renders, never computes
  attention-drawer.tsx the right-side detail card and Investigate Further
  ask-box.tsx        the wide landing-page question box and its prompts
  greeting.ts        a time-aware greeting from the READER's clock
  continue-where-you-left-off.tsx  real persisted V4 threads, or a quiet
                     empty state
  live-run-fixture.ts  the 15 recorded events of a real 29.4s run, so the
                     panel is tested against a trace that actually happened

scripts/cockpit_v4/
  start.py status.py stop.py  + three .command wrappers
  seed_release.py             isolated synthetic release
  live_path_evidence.py       real socket/SSE evidence capture
  ingest_product_deck.py      one-time deck -> Product Knowledge Pack
  browser_evidence.py         brings the stack up and runs real Chromium
  stub_server.py              a scripted analyst, for tests only

docs/product_knowledge/
  creditprobe_product_knowledge.md  the human-reviewable rendering of the pack,
                     generated from the JSON so the two cannot drift
```

## Three properties that were built, not asserted

**Boundedness.** Every path back to the model consumes a generation attempt,
an execution submission, a round, a format allowance, a correction allowance
or elapsed time — none of which is replenished. `states.TRANSITIONS` records
what each cycle-closing edge spends, and a test fails the build if an edge
closes a cycle without naming one. The same bounds are also exercised against
the live loop, because a graph labelled with counters is not evidence that the
implementation increments them.

**Evidence.** A portfolio number reaches the user through a `numeric_claim`
bound to an artifact *this run* produced, carrying the exact stored value. The
narrative references it as `{{claim.id}}` and the renderer substitutes it at
the declared unit and precision. A claim that restates a figure rounded is
refused. A full answer that reports nothing bound to the evidence is refused.
A *partial* answer that states plainly what it could not establish is accepted
— that is the honest outcome, not an invalid one.

**Durability.** The run, its events, its submitted code, its artifacts and its
cost reservations are committed before the next side effect. `202` means
durable and claimable, not answered. The answer is persisted before
`answer.ready` is published. A worker that stops heartbeating is settled by an
independent supervisor as `INTERRUPTED`, and nothing paid is replayed on a
guess.

## The analysis-round rule

The first `execute_analysis` submission opens round 1. A failed or partly
failed batch may be repaired **within** that round. Only after a batch
completes successfully and its results reach the analyst does the next batch
open a new round. Metadata reads, artifact reads and finalization open no
round.

This avoids asking the orchestrator to judge whether two methods are
"substantively different" — a judgement it has no business making. A failed
batch may change method inside its repair; the five-submission, call, cost and
time limits still bound it.

## What V4 deliberately does not have

- No mandatory Sonnet preprocessing, and no `AI_COCKPIT_PREPROCESS_MODEL` on
  the answer path.
- No separate ownership round trip: intent is declared with the first action.
- No full catalogue in any prompt.
- No planning essay, no functionality-scoring essay, no summary-before-answer.
- No fallback to V3, to another model, or to a deterministic calculator.
- No multi-agent framework, vector database or hosted workflow platform.
