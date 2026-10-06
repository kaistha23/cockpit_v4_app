# Cockpit V4 — overnight product-completion round

Branch `claude/cockpit-single-agent-v4-h8fsbq`. Starting HEAD `870c5fd`,
verified against the remote before anything was changed. Not merged. V3
untouched. No paid provider call was made and no key was asked for.

**The attached screenshots did not arrive.** The message carried no image
content — the third round in a row. Per §0 the prompt itself was treated as
the complete specification.

---

## 1. What this round is, and what it is not

The brief asks for two things of very different sizes.

The first is a **blocking analytical defect**: live questions ending with
`intent must be an object`, and analytical runs dying on a sixty-second
Product Help clock. That is what stops UAT today. It is root-caused, fixed,
and covered by regressions that fail without the fix.

The second is a **dual-domain product**: a Saudi retail Cockpit alongside the
corporate one, both on twenty monthly snapshots, with a domain switch, domain
pinning, domain-aware dashboards and attention engines, a V4-native Data
Builder, export in three formats, and twenty-four oracle-backed question-bank
cases across the two books.

**The second is not done, and nothing in this round pretends otherwise.**
§11 of this report says exactly why, what it would take, and what the design
is. §55 forbids fake domains, fake exports and dead buttons; a thin version
of this work would be all three. The verdict in §13 is NOT READY, and the
dual-domain work is the blocker.

---

## 2. Root cause of `intent must be an object`

Two faults, chained.

**Fault one — a field restated four times a run.** `intent` was `required` on
all five tools. It is a nine-field object that does not change within a run,
and the analyst had to send it verbatim on every `inspect_catalog`, every
`read_artifact`, every `execute_analysis` and the final answer. A field
restated for no reason is a field that will eventually come back wrong, and
the answer to that is not a sterner instruction — it is to stop asking.

What was checked and ruled out before settling on that:

* `$ref` is inlined before the schema goes on the wire, so the model does see
  the object's shape. Not this.
* `oneOf`/`allOf`/`anyOf`/`$defs` are absent from every wire schema; the
  existing compatibility suite enforces it after a live 400 over an `allOf`.
  Not this.
* Union types (`"type": ["array", "null"]`) are present throughout. I
  narrowed them, then **reverted it**: unions are not on the provider's
  unsupported list, Product Help runs fine with them today, and nothing here
  can show they contributed. Changing the model-facing contract on a hunch is
  how the next round gets an unexplained regression. This is recorded in
  `test_intent_contract.py` as a documented non-change rather than silently
  dropped.

**Fault two — the clock waited on the field.** The analytical allowance was
adopted inside `_record_intent`, which needs a well-formed `intent` parsed
first. So a run whose analyst declared DATA_ANALYSIS **by submitting SQL**
executed that SQL on the Product Help allowance, and a malformed restatement
meant the allowance never widened at all. Sixty seconds, spent on a query
that needed more, and a public failure naming the parser.

The regression measured it before the fix:

```
the allowance was adopted at step 12, after the query ran at step 5
```

## 3. The fix

**`intent` is authored once and carried.** Absent or empty means "as already
declared" and the server supplies what it is already holding. A restatement
is still honoured — the analyst may refine its reading mid-run — so Opus
keeps analytical ownership of the intent and loses only the obligation to
retype it. Only a first call with nothing to carry is asked, and the message
names the fields to send rather than the type that was wrong.

An `intent` arriving as a JSON **string** of the object is read rather than
refused: a real provider behaviour, mechanical to undo.

**The allowance follows the tool.** `execute_analysis` widens it, before
anything expensive and before any field of the request is parsed. Asking to
run an analysis IS the declaration. `inspect_catalog` deliberately does not:
a Product Help answer may look a field up, and widening for that would widen
for everything. Product Help keeps its tight bound, asserted.

Twelve regressions in `tests/cockpit_v4/test_intent_contract.py`, all failing
on `870c5fd`.

## 4. A race that a rerun would have hidden

The repeat matrix (§53) failed once in five on "Continue where you left off
uses real V4 threads". §53 says treat that as a defect until root-caused, and
it was one.

The transcript row was written **after** `update_state(terminal=True)`. The
ordering is observable from the browser: the answer appears the moment the
run goes terminal, so a reader who clicks back at once reaches the landing
page before the turn exists — and that list shows threads which HAVE a turn.
The conversation they just held was missing, then appeared a moment later,
which is worse than never because nobody is looking by then.

The turn now goes in first, and `append_turn` is idempotent per run so a
settle that loses its lease cannot write a second copy. The regression
watches the ordering rather than timing it: at the moment the run is marked
terminal, its turn is already there. It fails with the old ordering.

## 5. Visualization intelligence

`_check_chart` asked one question: do these columns exist in the artifact?
They did, so a "show me the customers behind this" answer published one bar
per borrower — a wall of bars above a table that said it better (§29).

Existence is not usefulness. The server now validates the chart against the
**result shape**, with two counts that are facts about the result rather than
guesses about the question:

| Points | Verdict |
| --- | --- |
| < 2 | not a comparison; the sentence already said it |
| 2 – 25 | a chart |
| > 25 | not a comparison either; this is a table |

Every §28/§29 example lands correctly: twelve sectors charted, a ten-row
top-N charted, a full facility list dropped, a single scalar dropped. It
deliberately does **not** infer intent from column names or grain — a
ten-borrower ranking is ten points and passes, whatever the rows are called.

Dropping an unhelpful chart is a warning, never a correction round, and the
table it sat above is untouched. Five regressions in `test_visual_choice.py`.

## 6. Thread UX

* **§22 — back button.** `← Cockpit` at the top left, above the title, where
  readers look. The old far-right "Cockpit home" is gone rather than
  duplicated. Browser back works too; both are asserted, including that the
  thread is still listed and reopens with its full transcript.
* **§23 — title.** Written when the question is ASKED, not when the answer
  lands. A reader no longer watches "New conversation" work for a minute, and
  a thread whose run failed no longer keeps that name. A second question does
  not rename the thread. No model call.
* **§24 — follow-ups.** Moved out of the answer panel into the sticky
  composer, immediately above the input. A strip in normal flow above a
  sticky composer just scrolls away from it, so it sits inside the composer
  block and travels with it. Asserted by DOM containment and viewport
  position, and that clicking one appends to the SAME thread.

## 7. Preserved

Nothing in §3 of the brief regressed: Saudi-native V4, SAR million, release
fingerprinting, provider schema compatibility, catalogue convergence, compact
action and finalization context, separate recovery budgets, bind-before-
validation, direct and derived claims, server-owned numeric rendering,
monetary 0dp and percentage 2dp, threads and their persistence, the composer,
the process panel, live elapsed clocks, the attention feed, ECL highlights,
Product Help, multilingual handling and V3 isolation. One Opus analyst; no
router, no preprocessing model, no CreditProbe-authored reasoning or SQL
repair.

## 8. Tests that encoded superseded behaviour

Two, both rewritten rather than deleted:

* `test_catalog_answer_memory.py` built a **one-row** artifact and expected
  its valid chart to survive. One row is now correctly not a chart, so the
  fixture is four sectors — the test is about the column rule and still tests
  it.
* `test_intent_contract.py` carries the union-type narrowing as a documented
  non-change, so the next round does not rediscover the idea and ship it.

## 9. Test matrix

| Suite | Result |
| --- | --- |
| Cockpit V4 backend (`tests/cockpit_v4`) | **1,138 passed, 2 skipped, 0 failed** |
| — intent contract (new) | 12 passed |
| — visual choice (new) | 5 passed |
| — thread transcript, incl. ordering (new) | 20 passed |
| — M01–M15 mathematical bank | all passed |
| — provider schema compatibility, tool-parser agreement | all passed |
| Frontend unit (`npm test`) | **500 passed, 0 failed** |
| Browser (real Chromium, real UI, real V4 API, real DuckDB) | **63 passed, 0 failed** |
| V3 regression (`tests/cockpit_agentic`) | **564 passed, 26 skipped, 0 failed** |

V3 is identical to the previous three rounds.

UI latencies, unchanged within noise: thread opens 237 ms, first process
event 240 ms, chart and table 61 ms, transcript restored 324 ms, follow-up
visible 119 ms.

## 10. Repeat testing

The critical conversation flows were run five times before the ordering fix:
one failure in five, root-caused in §4 above rather than rerun away. After
the fix, the isolated case passed five times in five, and the full
sixty-three-test matrix was then run five times end to end: **63/63 on every
run, no intermittent failure**.

The §53 matrix is **not** complete: domain-switch and export cycles cannot be
repeated because neither exists yet.

## 11. Not done, and why

None of the following is started, half-built or stubbed. There are no dead
buttons and no placeholder domains.

**The blocker is architectural, and it is one line of code repeated
everywhere.** `backend/cockpit_v4/__init__.py` defines
`DOMAIN = "corporate_cockpit"` as a module constant, and the runtime pins one
`COCKPIT_V4_RELEASE_ID` with a process-wide catalogue cache. There is exactly
one domain and one release per process. Everything the brief asks for in
§9–§20 sits downstream of making that per-request state.

On top of that, neither dataset exists:

* The corporate release is built by **V3's** `generate.build_release`, which
  is quarterly. §43 forbids keeping quarterly semantics, so twenty monthly
  corporate snapshots need a V4-native generator — V3 must not be modified.
* There is no retail generator at all. §14 wants a genuine retail grain
  (customer, account, month) with IFRS 9, products, behavioural scores and
  their drivers, delinquency, vintage and secured-retail fields — a new
  dataset, not a relabelled corporate table.

Outstanding, in dependency order:

1. `DOMAIN` becomes per-request state; catalogue, coverage and attention
   caches keyed by domain; `domain_id` on every run, thread and artifact.
2. A V4-native monthly Saudi **corporate** generator (§13) and release
   `v4-saudi-corporate-20m-v1`.
3. A V4-native monthly Saudi **retail** generator (§14) and release
   `v4-saudi-retail-20m-v1`. Option A of §15 — separate immutable
   fingerprinted domain releases — fits the existing store with no change to
   its shape.
4. Monthly period semantics (§43) replacing quarterly throughout.
5. Domain-aware attention and ECL highlights with independent oracles for
   both books (§19, §20).
6. Cockpit Home domain switch, domain-specific prompt chips, domain badge and
   thread pinning (§9, §10, §11, §18, §21).
7. V4-native Data Builder domain list and drilldown (§16, §17).
8. Export: analysis, table and chart (§31–§34).
9. Question banks C01–C12 and R01–R12 against independent oracles (§41, §42,
   §44).
10. Domain-switch, Data Builder, export and repeat browser matrices (§45,
    §46, §50, §53) and the fourteen screenshots of §56.

Also not done, and smaller: a structured `presentation` block in the final
response (§27) — the chart/table decision is currently expressed by which of
`tables` and `charts` the analyst sends, which the shape rule then validates;
and `Save Analysis` in the thread header (§35), where save exists per answer.

## 12. Screenshots

Four refreshed from this HEAD, in `docs/cockpit_v4/evidence/`:
`cockpit_v4_landing.png`, `thread_analytical_chart.png`,
`thread_analytical_table.png`, `thread_investigation.png`,
`thread_multi_turn.png`. The other ten of §56 need the dual-domain work
first; there is nothing truthful to photograph yet.

## 13. Verdict

**NOT READY** — blockers, in the order they must be cleared:

1. **No Retail domain exists.** §59.1–§59.8 (switch, two Data Builder
   domains, twenty monthly snapshots each, isolation both ways, dashboards,
   attention and ECL highlights by domain) cannot be met.
2. **No twenty-month monthly corporate release.** The book is quarterly;
   §43's monthly semantics are not in force.
3. **Data Builder still says the domain overview is not part of this
   runtime** (§16). True today, and unacceptable for UAT.
4. **No export** (§31–§34, §59.17).
5. **Question banks C01–C12 and R01–R12 do not exist** (§59.24, §59.25).
6. **Repeat matrix incomplete** (§59.26): domain-switch and export cycles
   have nothing to exercise.

What this round DID clear, and what a human can now test on the corporate
book: `intent must be an object` is gone (§59.20); DATA_ANALYSIS gets the
correct allowance before the expensive loop (§59.23); every question opens a
thread (§59.9); the top-left back button exists (§59.10); a thread is never
"New conversation" once a question exists (§59.11); follow-ups sit directly
above the composer and stay in the thread (§59.12, §59.13); chart choice is
validated against result shape and entity-detail lists no longer get
pointless graphs (§59.14, §59.15, §59.16); Trace works (§59.18); timers tick
(§59.19); the existing numeric-truth and orchestration suites are green
(§59.27); and V3 has zero new failures (§59.28).

§59.21 and §59.22 — ECL decomposition and the Stage 2 diagnostic — are
**unverified rather than fixed**. Their reported failure mode was the intent
and allowance defect, which is fixed; whether each then produces a good
answer is a live-model question this round could not answer without a paid
call.

## 14. Running it on the Mac

```bash
# stop what is running
python3 scripts/cockpit_v4/stop.py

# take this round
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull origin claude/cockpit-single-agent-v4-h8fsbq

# frontend dependencies, in case the lockfile moved
cd frontend && npm install && cd ..

# the corporate release, if this machine has not published it
python3 scripts/cockpit_v4/seed_release.py --release v4-saudi-20q-v1

# start, and confirm
python3 scripts/cockpit_v4/start.py
python3 scripts/cockpit_v4/status.py
```

Open `http://127.0.0.1:5414` and ask *"What is driving Stage 2 and ECL
growth?"* — the question that used to end with `intent must be an object`.
It should open a conversation named after itself, widen to the analytical
allowance before the query runs, and answer. There is no Retail switch and
no Data Builder domain to click; both are in §11 above.
