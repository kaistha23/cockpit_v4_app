# Cockpit V4 runbook (macOS)

Three commands. No pid hunting, no `lsof`, no `kill -9`.

## Once, before the first start

**1. Get the code**

```bash
cd /path/to/IPM_V2
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git worktree add ../cockpit_v4 claude/cockpit-single-agent-v4-h8fsbq
cd ../cockpit_v4
```

A separate worktree, so nothing you have running from the V3 checkout is
affected. Do **not** switch branches in a directory that is serving a live
frontend — file changes reach a running dev server even without a restart.

**2. Install dependencies** (in whatever environment you already use)

```bash
pip install -r requirements.txt
pip install uvicorn duckdb pandas pyarrow httpx
cd frontend && npm install && cd ..
```

**3. Store the key once** (optional, and the only way to avoid a prompt)

```bash
security add-generic-password -s creditprobe-cockpit-v4 -a "$USER" -w
```

Paste the key at the hidden prompt. It goes into the Keychain, not into the
repository, not into your shell history, and never into a chat window.

**4. Replace the price-card placeholder** — required before any paid run

Edit `config/cockpit_v4/price_card.json`. Replace
`REPLACE-WITH-YOUR-MODEL-ID` with your exact model id and fill in all four
billing classes from the current published schedule, plus `verified_at`.

Until you do, V4 starts, reports `CAPABILITY_UNVERIFIED`, and refuses to make
a paid request. That is deliberate: an "enforced" cost badge over an unknown
price is a false claim.

**5. Seed the isolated V4 release** (once)

```bash
python3 scripts/cockpit_v4/seed_release.py --release v4-uat-20q-v1
```

Writes into the `cockpit_v4` namespace only. It refuses the V3 namespace, and
refuses to overwrite an existing release unless you pass `--overwrite`
deliberately.

## Every day

| Do this | Double-click | Or run |
|---|---|---|
| Start | `scripts/cockpit_v4/START_COCKPIT_V4.command` | `python3 scripts/cockpit_v4/start.py` |
| Check | `scripts/cockpit_v4/STATUS_COCKPIT_V4.command` | `python3 scripts/cockpit_v4/status.py` |
| Stop | `scripts/cockpit_v4/STOP_COCKPIT_V4.command` | `python3 scripts/cockpit_v4/stop.py` |

`start` prints the URL and opens it. Default ports **8414** (API) and **5414**
(UI).

### What start does, in order

1. Confirms the worktree and records the **startup** SHA — read once, so the
   trace names the code the process is running.
2. Confirms no V4 instance of ours is already up.
3. Finds free ports. **A busy port is reported with who holds it and an
   alternative is chosen. Nothing is ever stopped to free a port** — the
   occupant may be your V3 demo.
4. Obtains the key from the environment, then the Keychain, then a hidden
   prompt.
5. Verifies the model, its capabilities and its price **without a paid call**.
6. Opens the release read-only.
7. Starts the API, waits for real health, starts the UI, waits for it, prints
   a diagnostics table and the URL.

Any step failing stops there and says which setting to fix.

### What the UI is told, and why it is two variables

The V4 frontend process is given **both** API addresses, at the port the
launcher actually selected:

| Variable | Read by | Why it matters |
|---|---|---|
| `NEXT_PUBLIC_COCKPIT_V4_API` | the Cockpit V4 client | the runs, the event stream, cancellation |
| `NEXT_PUBLIC_API_URL` | the application shell and `lib/api.ts` | the header, the backend-status badge, the landing-page widgets |

Setting only the first was a real defect: the Cockpit talked to 8414 while the
header and every landing widget kept talking to `http://127.0.0.1:8000`, a
backend the V4 instance never started. Half the page then reported a system
that was not there.

Both are set **for the V4 UI process only**. The default inside `lib/api.ts`
is untouched, so every other CreditProbe instance behaves exactly as before.

The V4 Cockpit client has no default address at all. If
`NEXT_PUBLIC_COCKPIT_V4_API` is missing it says so plainly rather than
borrowing the shell's variable — there is nothing for it to silently fall back
*to*.

### What the Cockpit page is, in V4 mode

When `NEXT_PUBLIC_COCKPIT_V4_API` is set, `/` renders the **Cockpit V4** Ask
surface: a question box, a Standard/Deep control, Ask, Stop, a hideable
**Show process / Hide process** panel driven by the backend's own persisted
events, the final answer, links to the artifacts a figure was read from, and
an explicit terminal failure with a support reference.

The legacy Cockpit page is not rendered at all in this mode — which is the
point. It opens with four `useAsync` calls to `/ask/suggestions`, `/ask/mode`,
`/ask/briefing` and `/investigations`, and React fires a component's hooks the
moment it mounts. Guarding the JSX would not have stopped those requests;
declining to instantiate the component does.

Asking runs the V4 lifecycle end to end:
`POST /runs` → `run_id` → the process panel appears → the browser subscribes to
`/runs/{run_id}/events` → `GET /runs/{run_id}` for authoritative state →
the answer renders. Stop calls `/runs/{run_id}/cancel`. A browser refresh
reconnects to the **same** run from the last sequence it rendered — it does not
ask again.

Widgets whose backend is not part of this runtime (risk cases, investigations,
early warning, the briefing, workspace notifications) render a neutral "not
available in this isolated V4 runtime" state and **make no request at all**.

### What the status badge will say

The header polls `/api/v1/health`, which the V4 API now serves about itself.
You should see:

> **CreditProbe Cockpit V4 reachable · dashboard service not in this runtime**

That is the honest reading. This instance runs the Cockpit API only; the
landing-page widgets, briefing, threads and dashboard routes are served by the
main CreditProbe backend, which this instance does not start. The system-status
panel lists them as **Not configured** with that explanation.

"Backend offline" now means what it says: the V4 API did not answer. A genuine
V4 fault — an unreadable release, an unwritable state store — still turns the
headline red, because the distinction has to cut both ways or it is just a
green light.

### What stop does

It stops a process only when **all four** still match what V4 recorded when it
started it: the pid exists, its start time is within seconds of the record,
its command line is the one V4 launched, and its working directory is the V4
worktree. Anything failing a check is **reported and left alone** — on a busy
Mac that pid now belongs to something else.

SIGTERM to the process group V4 created, then SIGKILL only to a group that
ignored it. There is no `pkill`, no `kill $(lsof -ti:8414)` and no pid chosen
with `tail -1`.

## Reading the diagnostics table

Three **separate** readiness flags, deliberately not one badge:

| Flag | Needs |
|---|---|
| `ready_for_product_help` | credential + model + verified price + state database |
| `ready_for_sql_analysis` | the above, plus the release opening |
| `ready_for_python_analysis` | the above, plus an isolated Python runner that passes its own escape self-test |

`ready_for_python_analysis: false` is normal on a stock Mac without a
container. SQL analysis is unaffected. A Python step will fail with
`PYTHON_UNAVAILABLE` and will **not** be quietly rewritten as SQL.

Live at `http://127.0.0.1:8414/api/v1/cockpit-v4/diagnostics`.

## When something goes wrong

| Symptom | What it means | What to do |
|---|---|---|
| `COCKPIT_ANTHROPIC_API_KEY is not available` | The Cockpit's own key is not set. V4 will not use another module's key. | Set it, or store it in the Keychain (step 3). |
| `CAPABILITY_UNVERIFIED` | The price card has no verified entry for your model. | Step 4. No paid request runs until then. |
| `DATA_UNAVAILABLE` | The pinned release is not readable. Another release is a different book, so nothing is substituted. | Step 5, or set `COCKPIT_V4_RELEASE_ID`. |
| `port 8414 is in use … NOT stopped` | Something else has the port. | Let it pick the alternative it names, or pass `--api-port`. |
| The panel shows `CONNECTION_LOST` | **This browser** stopped receiving updates. It says nothing about the run. | Click "Check its status". The answer is persisted; it is not lost and does not need re-asking. |
| A run ends `INTERRUPTED` | The worker stopped and the supervisor settled it. Nothing was replayed, because a paid call may already have happened. | Ask again; it is a new run. Any results already produced are preserved. |
| A run ends `EXPIRED` | The deadline passed. | Use Deep mode, or narrow the question. |
| An answer shows a support reference | An application defect, recorded with its operation. | Quote the reference. Rephrasing will not help. |

Logs: `$COCKPIT_V4_RUNTIME_DIR/logs/{api,ui}.log`, default
`~/.creditprobe/cockpit_v4/logs`.

## Running the browser suite

A real Chromium against the real UI and a stubbed analyst. It starts and stops
everything it needs on free ports:

```bash
python3 scripts/cockpit_v4/browser_evidence.py
```

Evidence lands in `docs/cockpit_v4/evidence/browser.json`. Add `--keep-up` to
leave the stack running afterwards for manual poking — and remember to stop it.
Set `V4_BROWSER_SCREENSHOT=<path>` to also capture a rendered answer; the one
committed at handoff is `docs/cockpit_v4/evidence/cockpit_v4_answer.png`.

Fourteen tests. Every request the page makes is recorded, so "the page never
calls the legacy backend" is checked rather than claimed. Four of them exist
because the corresponding defect shipped past weaker tests once already: the
process panel must show real stages rather than "not started" at 0s; the answer
must render as Markdown with no raw `**` left over; the suggested-question
chips must stay interactive UI rather than Markdown; and a failed attempt must
remain visible after a later attempt succeeds.

The analyst is a **stub**. The suite proves the wiring, the run lifecycle and
the rendering. It is not a live Opus validation.

## Product Help and the Product Knowledge Pack

Product questions are answered from a versioned pack, not from the model's
recollection.

| | |
|---|---|
| Pack | `backend/cockpit_v4/product_knowledge.json`, version `2026-09-11.1` |
| Source | `CreditProbe_AI_Functionality_Deck_1.pdf`, 14 pages, SHA256 `bdb3ce5d3b84317fe891512334a2470972d4182062e2438912920bbe56119e3e` |
| Human-readable | `docs/product_knowledge/creditprobe_product_knowledge.md`, generated from the JSON |
| Ingestion | `python3 scripts/cockpit_v4/ingest_product_deck.py` — one-time, re-run only when the deck changes |

How it reaches the analyst:

- A ~924-token **synopsis** is in the starting context of every run, so
  "Who are you?" is answered in one generation with no tool call.
- `inspect_product_knowledge` retrieves named sections for a narrower question.
  The deck itself is never attached to a prompt.
- Slide 14 describes an older **multi-agent** design. It is recorded as
  `HISTORICAL_ARCHITECTURE` / `NOT_CURRENT_V4_ARCHITECTURE` and is not
  retrievable, so the analyst cannot present it as how V4 works today.
- Numbers in the deck are illustrations. They are labelled as such and must
  never be quoted as a current portfolio value.

To re-point Product Help at a newer deck: replace the PDF, re-run the
ingestion script, review the regenerated Markdown, and run
`python3 -m pytest tests/cockpit_v4/test_product_help_benchmark.py` — 30
questions with 81 assertions over grounding, framing and what must never
appear.

## The Cockpit landing page

The layout is the earlier Cockpit's, restored deliberately; the backend is
entirely V4.

1. **Greeting** — "Good morning / afternoon / evening", with the session's
   display name when there is one. The time of day comes from the reader's own
   browser clock, not the server's. No name means no name; nothing invents one.
2. **"What's on your mind?"**
3. **One wide Ask box** across the workspace, with Standard / Deep and Ask.
4. **Prompt chips** beneath it — business questions, dismissible with the ×.
   Rendering them costs no model call.
5. **"Every answer carries a Trace."** — the link explains what a trace is.
6. **Segments requiring attention**, with `REPORTING PERIOD Q<n> <year>`
   beside the heading — up to five SEGMENT-level deterioration items and
   nothing else. No book-wide observation and no individual borrower appears
   here.
7. **Latest-quarter ECL highlights**, a separate feed in the same visual
   language: sector, borrower and book-level ECL developments. Nothing in it
   is repeated above.
8. **Continue where you left off** — real V4 threads with at least one
   completed turn, or a quiet empty state. Clicking one reopens the real
   persisted thread, with its attention seed when it had one.

The process panel appears when a run exists and not before: an idle landing
page reserves no space for it.

## The Cockpit home feed

Below the question box the home page carries two analytical sections computed
from the pinned release: **Segments requiring attention** and **Latest-quarter
ECL highlights**. Full method in `ATTENTION_METHOD.md`.

What an operator needs to know:

- **No model call.** Rendering the page costs nothing at the provider, and a
  test asserts `model_calls == 0`.
- **Cached per release and tenant.** `GET /api/v1/cockpit-v4/attention` serves
  a cached feed after the first request; `?refresh=true` recomputes. A new
  release is a new cache key, so there is nothing to clear by hand.
- **Measured here:** 446 ms for the first request on a cold DuckDB session,
  64 ms of computation, 2.4 ms median cached, 31 ms on a forced refresh.
- **Click a card** for the right-side drawer: why it appeared, what changed,
  the numbers, what moved alongside it, what to review next, the trace, and
  **Investigate Further**.
- **Investigate Further** opens a V4 thread seeded with the item. The segment,
  quarter, comparison period, metric and evidence live on the thread, so the
  next question can be "show me the customers behind this". The process panel
  shows *Investigation context loaded* because that step really happens.
- **Drill-down** goes sector → borrower → facility. This release has no
  subsegment column and the drawer says so rather than offering a level that
  does not exist.
- **If it fails**, the section alone says *Segment attention feed unavailable*
  with a reference, and Ask keeps working. A dashboard that cannot compute is
  not a backend that is down, and the header will not say the backend is
  offline because of it.

Fewer than five cards is a real answer: it means the remaining movements did
not clear the materiality floor.

## What is done to a question before the analyst reads it

Unicode NFC, whitespace, and invisible or bidirectional control characters.
Nothing else — no spelling correction, no translation, no case folding, no
digit conversion. Misspellings, telegraphic phrasing, long paragraphs and
mixed-language questions reach the analyst as typed, and the analyst is the
component that infers what was meant.

When anything is changed, the original and a report naming each transformation
are persisted as run detail and the trace says "Question text normalized
(formatting only)". When nothing is changed, nothing is recorded.

## Analytical runs: what they are allowed

The mode is not knowable at intake, so a run starts on the product-help
allowance and widens once the analyst declares a DATA_ANALYSIS.

| Query mode | Standard | Deep |
|---|---|---|
| Product help, theory, referral | 60 s · $1.00 | 120 s · $2.00 |
| Data analysis | 120 s · $1.50 | 240 s · $3.00 |

The effective values appear in the run budget, the process trace and
`/diagnostics`. Counters — submissions, rounds, generations, catalog calls,
steps — are the same in both cases.

Reading a catalogue step in the trace:

- **"Requested ead_reported, sector_name · returned 2 definition(s) · coverage
  complete"** — the request was satisfied.
- **"Catalogue request needs a scope; nothing was read"** — the request would
  have expanded a whole relation, or named nothing at all. Neither returns a
  partial dump.
- **"No new catalogue information added; it was already in context"** — a
  repeat. Two in a row warns the analyst; a third ends the run as
  `NO_PROGRESS` rather than spending the deadline on a loop.

`catalog_calls` is still 4: distinct, useful metadata reads remain available.
The bound is on repeating.

Two things worth knowing when reading a trace:

- **"Query checked … also bound against the live tables, executing
  nothing"** means DuckDB was asked, with the step's own parameters, and
  answered. It is emitted after the proof, never before. The same line
  states the declared grain and units as DECLARED: they are the analysis's
  own words, echoed, and nothing has checked them against a result.
- There are three ways a step can stop, and they are different facts.
  **"failed while running"** is the only one where the engine executed
  anything. **"did not bind and was not run"** is the binder's refusal.
  **"was refused before it ran"** is a check's — a fan-out join, an
  unavailable sandbox — and it happens before the query reaches the engine
  at all. The step's `phase` says which (`runtime`, `bind`, `check`, or
  `not_started` for a step the batch never reached) and `executed` is that
  same fact, not a second opinion about it.
- Full detail — the DuckDB exception type, the binder message, the join and
  the measures a fan-out would double-count, the submission number, the SQL
  and its parameters — is in the operator record, which every event names
  as `detail_ref` and which
  `GET /api/v1/cockpit-v4/runs/{run_id}/details/{detail_ref}` serves.

## Settings

| Variable | Meaning |
|---|---|
| `COCKPIT_AGENTIC_V4` | The V4 switch. Does **not** enable or alter V3. |
| `AI_PROVIDER` | Supported provider; `anthropic`. |
| `AI_COCKPIT_REASONING_MODEL` | The analyst model id. No default, no `latest`, no inheriting `AI_MODEL`. |
| `COCKPIT_ANTHROPIC_API_KEY` | The only credential. Reported as PRESENT/MISSING only. |
| `COCKPIT_V4_RUNTIME_DIR` | Private state, artifacts, logs, pid records. |
| `COCKPIT_V4_STATE_DATABASE` | State database; secret-bearing URLs are redacted in diagnostics. |
| `COCKPIT_V4_RELEASE_ID` | The pinned authorized release. |
| `COCKPIT_V4_API_PORT` / `COCKPIT_V4_UI_PORT` | 8414 / 5414. Verified, never forcibly freed. |
| `COCKPIT_V4_LOCAL_DEMO_AUTH` | Loopback synthetic-only profile. Off outside it. |
| `COCKPIT_V4_PRICE_CARD` | Versioned non-secret price/capability metadata. |
| `COCKPIT_V4_MEMORY_ENABLED` | False for UAT. |
| `COCKPIT_V4_MEMORY_MODEL` | Required only when memory summarization is on. |
| `COCKPIT_V4_PYTHON_RUNNER` | Enables the Python capability *probe*. The self-test still has to pass. |

`.env.example` carries these names with harmless placeholders.

## What V4 never touches

Docker, EWS, What-if, Playbook, Planner, Lenses, any presentation instance,
the V3 branch, the V3 namespace, published releases, `data/curated`,
`data/raw`, `metadata`, or `data/analytics`. `config.check_write_target`
refuses a write outside the V4 runtime directory before the first byte.
