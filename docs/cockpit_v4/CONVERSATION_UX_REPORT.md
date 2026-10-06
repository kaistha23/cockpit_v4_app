# Cockpit V4 — conversation, thread and visual analytics round

Branch `claude/cockpit-single-agent-v4-h8fsbq`. Not merged. V3 untouched.

---

## 1. Verified starting HEAD

`a88a3c36386f2f2b70daf93a19f36f9edfc88cfc` — *"Cockpit V4: the runtime-readiness
report"*, the last commit of the previous round, confirmed as the remote tip of
this branch before any file was changed. Nothing newer existed on the remote, so
nothing was rebased over or discarded.

## 2. Final HEAD

`bd25826cc187c86f75cbaf16c5cb45d42e74a447`.
Commits added, oldest first:

| Commit | What it does |
| --- | --- |
| `4d884ef` | A conversation a reader can scroll, reopen and rename |
| `52f4a30` | Seconds that move while something is running |
| `0ba42bc` | A conversation, not a page that answers once |
| `57fb4f0` | The finished run keeps its trace, and a refresh picks it up |
| `0bd9316` | A visual is bound to the release that produced it |
| `8e6bf31` | A past turn can be traced, and a reload does not re-ask |
| `226560e` | The question never reaches the URL |
| `0727358` | The remembered run keeps the question it is answering |
| `eb03ad5` | The figure in the list is the figure in the sentence |
| `76f6735` | Share the conversation, and say what cannot be shared |
| `bd25826` | This report, its evidence and the performance measurements |

## 3. Files changed

Twenty-four files, +3,131 / −464 excluding evidence artifacts.

**Added** — `frontend/src/app/cockpit/thread/[threadId]/page.tsx`,
`components/cockpit-v4/thread-view.tsx`, `visuals.tsx`, `visual-choice.ts`,
`clock.ts`, `claim-display.ts`, and the unit tests `visuals.test.ts`,
`clock.test.ts`, `claim-display.test.ts`, plus
`tests/cockpit_v4/test_thread_transcript.py`.

**Modified** — `backend/cockpit_v4/run_store.py`, `routes.py`,
`orchestration.py`; `components/cockpit-v4/client.ts`, `cockpit-v4-home.tsx`,
`process-panel.tsx`, `reducer.ts`, `response-panel.tsx`, `client.test.ts`;
`scripts/cockpit_v4/stub_server.py`, `browser_evidence.py`;
`tests/cockpit_v4/browser/cockpit_v4.browser.mjs`, `test_claim_rendering.py`.

**Deleted** — `components/cockpit-v4/cockpit-v4.tsx`. This was the in-page
answer panel the landing page used to host. The thread replaced it; leaving a
349-line component nothing mounts would be dead code pretending to be a
surface.

## 4. Thread architecture

V4-native, on the run lifecycle that already existed. No V3 investigation
model was restored.

```
thread  ─┬─ turn 1 ── run ── events ── artifacts
         ├─ turn 2 ── run ── events ── artifacts
         └─ turn n ── run ── events ── artifacts
```

* `threads` gained a `title` column, migrated idempotently by
  `_ADDED_COLUMNS` in `run_store.py` — an existing database is altered in
  place, never rebuilt.
* A turn is `(turn_id, run_id, ordinal, question, answer, created_at)`.
  `append_turn()` writes it when a run settles, and names the thread from the
  first question if it has no title. **No model call is spent on naming.**
* `recent_turns()` is the capped slice the ANALYST sees. `thread_turns()` is
  everything, and only the READER sees that. Opus context does not grow with
  thread length (§50).
* The transcript is server state. `sessionStorage` holds one pointer only —
  `{runId, threadId, cursor, question}` under `cockpit-v4:active-run` — so a
  refresh can re-attach to a run still in flight. Nothing a reader sees is
  reconstructed from the browser.

## 5. Route and view

`/cockpit/thread/[threadId]` — a real App Router page, a real URL, back and
forward work, and the link is shareable. `CockpitV4Thread` renders it.

There is no `?q=` parameter. Asking from home creates the thread **and starts
the run** before navigating, so the question never enters the URL and a reload
can never re-ask it (§4).

## 6. Home → thread

The landing page keeps its greeting, its Ask box, its attention feed and its
ECL highlights. Submitting no longer answers in place: it creates a thread,
starts the run, remembers the run pointer, and navigates. The thread is already
following the live run when it mounts.

## 7. Investigate Further

An attention card's *Investigate Further* calls the V4
`/attention/{id}/investigate` endpoint, which seeds a thread with the case
context, and then opens that thread. It no longer drops a context block onto
the landing page. The seed renders as a `SeedCard` at the top of the transcript
carrying `data-thread-id` and `data-segment`, and follow-ups use that context
without the reader restating it.

## 8. Transcript persistence

`GET /threads/{id}` returns every turn, the title, the turn count, the seed
context and the release header. A reload rebuilds the conversation from the
server. The live turn stays on screen after it settles and is de-duplicated
against the reloaded transcript by `run_id`, so the process panel and its trace
do not vanish at the moment the answer lands (§56).

## 9. Follow-up context

A follow-up is a new run in the same thread. The server attaches the capped
recent turns; the client sends only the new question. Nothing re-sends the
transcript, and the catalogue payload, the model-call count and the analysis
rounds are unchanged from the previous round (§50).

## 10. Composer

A sticky composer under the transcript: `Ask a follow-up…`, Enter asks,
Shift+Enter is a newline, and a Depth selector sits beside it. It is disabled
while a run is in flight and `dir="auto"`, so an Arabic follow-up reads
right-to-left in the box.

## 11. Header

Title (the question, renamable), turn count, Rename, Cockpit home, and the
conversation action strip below them.

## 12. Save / Share / Add to Project / Trace

| Action | Where | Status |
| --- | --- | --- |
| Save | per-answer | works — `POST /saved-analyses` |
| Comment | per-answer | works — `POST /comments` |
| Share | per-answer **and thread header** | works — saves, then `POST /shares` |
| Add to investigation | per-answer **and thread header** | works — `POST /investigations` |
| Trace | thread header, per-turn | works — `/trace/{runId}`, and an in-place replay |
| **Add to Project** | — | **not available, and not faked** |

**The Add to Project limitation, stated plainly (§26).** There is no project
endpoint in the V4 API — `grep -c project backend/cockpit_v4/routes.py` returns
`0`. Projects are a main-CreditProbe surface and this isolated V4 runtime serves
the Cockpit API and nothing else. So there is no button, not even a disabled
one: the investigation panel says in a sentence that Projects are not part of
this runtime and offers the V4-native equivalent, which does work. A button that
looks like it works and does not is worse than the sentence.

The share delivery line still never says "Sent" unless a transport accepted the
message. This build configures none, so it says "Recorded, not sent" and why.

## 13. Chart selection

`visual-choice.ts` decides, and it is pure and unit-tested. A chart is offered
only when it has at least two points, at least one y-column and at least one
value that is genuinely numeric. `numeric()` rejects `null`, `undefined`, `""`
and booleans — `Number(null)` is `0`, and plotting a null cell as a zero-height
bar is a lie about the data (§40). A no-rows result renders the empty-state, not
an empty graph.

## 14. Table selection

The analyst chooses the table — its title, its result, its columns. Every cell
carries both `canonical` and `display`. Sorting is on `canonical`, so a column
of `SAR 7,013 million` strings sorts numerically. Ten rows show, with "View all"
for the rest.

## 15. Visualization artifact design

Server-rendered. The analyst names what to show; `render_tables` and
`render_charts` read the stored artifact and format every value through the one
display policy. **No number in a table or a chart has passed through the model,
and the frontend does no arithmetic and no rounding of its own.** Bars scale
from the canonical value, labels come from the display value, and the visual
carries the release it was computed from.

## 16. Screenshots

`docs/cockpit_v4/evidence/`:

* `thread_analytical_chart.png` — a thread with a ranked bar chart
* `thread_analytical_table.png` — the same answer, Table selected
* `thread_investigation.png` — a seeded investigation thread
* `thread_multi_turn.png` — a multi-turn conversation

**The reference screenshots named in the brief never arrived in this
conversation** — no images were attached to the message. Everything here was
built from the written specification, which enumerates the behaviours the
screenshots were said to show. **Visual fidelity to the old CreditProbe Cockpit
is therefore unverified.** If the images are re-attached I will do a visual pass
against them.

## 17. Live timer design

The timer is anchored on the backend, never on an independent clock.
`reducer.ts` stamps `anchorLocalMs` on every event; `clock.ts` computes
`elapsed_ms + (now − anchor)` for a running step and freezes at the
server's figure once the run is terminal. A browser whose clock is wrong, or
which was backgrounded, cannot invent elapsed time.

## 18. Refresh and replay timers

A refresh mid-run re-attaches to the run, replays its events from the stored
cursor, and resumes counting from the server's elapsed figure — not from the
moment the page reloaded. A settled run shows `Answered in 31s`, fixed.

## 19. Browser evidence

`docs/cockpit_v4/evidence/browser.json` — real Chromium, the real Next UI, the
real V4 API, real DuckDB against the published Saudi release, deterministic
fixtures, and a scripted analyst. **No provider key was used and none was
asked for** (§66).

## 20. No legacy endpoint

Every browser test asserts the request log contains none of
`/api/v1/investigations`, `/api/v1/agentic/officer`, `/api/v1/ask/mode`,
`/api/v1/ask/briefing`, `/api/v1/ask/suggestions`,
`/api/v1/ask/cockpit-v2/diagnostics`, `/api/v1/cockpit/diagnostics`, and that
nothing at all goes to port 8000. All 59 pass. The V4 Cockpit's own
`/api/v1/cockpit/investigations` is a different endpoint on the V4 API and is
the one the investigation action uses.

## 21. Performance

Measured in the browser suite and written into `browser.json`:

| Measurement | Observed | Budget |
| --- | --- | --- |
| Thread opens after asking | 234 ms | 5,000 ms |
| First process event on screen | 236 ms | 10,000 ms |
| Chart and table rendered | 56 ms | 5,000 ms |
| Transcript restored after reload | 305 ms | 10,000 ms |
| Follow-up appears in the transcript | 77 ms | 5,000 ms |

These are UI latencies against a scripted analyst. They do not measure model
time.

## 22. Test counts

| Suite | Result |
| --- | --- |
| Cockpit V4 backend (`tests/cockpit_v4`) | **1,085 passed, 2 skipped, 0 failed** |
| Frontend unit (`npm test`) | **500 passed, 0 failed** |
| Browser (real Chromium) | **59 passed, 0 failed** |
| V3 regression (`tests/cockpit_agentic`) | **564 passed, 26 skipped, 0 failed** |

V3's numbers are identical to the previous round. No file outside
`backend/cockpit_v4`, `frontend/src/components/cockpit-v4`,
`frontend/src/app/cockpit`, `scripts/cockpit_v4`, `tests/cockpit_v4` and
`docs/cockpit_v4` was touched.

## 23. V3 comparison

V3 was neither modified nor consulted. The conversation pattern was rebuilt on
V4's own run lifecycle, evidence model and release integrity. No V3 endpoint,
agent, investigation implementation or mathematical engine was restored.

## 24. Defect fixed this round that was not in the brief

The figures list under an answer rendered `claim.decimal_value` raw, so an
answer whose narrative read `SAR 7,013 million` carried, two inches below it,
`7013.1167117986615 SAR million`. `decimal_value` is the analyst's optional
lossless cross-check, not a display field. Every published claim now carries
`display_value` — the same string the narrative used — and the panel renders
that, saying the unit once rather than appending it beside a string that already
contains it. Two backend regressions and seven frontend unit tests cover it, and
a browser test asserts no answer on screen contains four or more consecutive
decimals. This corrects §37; it does not redesign the claim architecture that
§15 puts out of scope.

## 25. Unresolved observations

1. **A claim may declare a finer precision than the table beside it.**
   `PERMITTED[MONETARY_AMOUNT]` is `(0, 1, 2, 3)`, default `0`. An analyst that
   declares `display_precision: 2` produces an answer whose prose reads
   `SAR 7,013.12 million` above a table and chart reading `SAR 7,013 million`.
   Both are policy-legal and both are CreditProbe's own rounding, but they
   disagree on screen inside one answer. Resolving it means deciding whether a
   claim may override the class default — a numeric-rendering decision §15 and
   §51 put out of scope for this round. Flagged, not changed.

   **Resolved in the pre-UAT round that followed.** Every governed class now
   permits exactly its own default and a declared precision is ignored rather
   than honoured or refused. See `DISPLAY_PRECISION.md` and
   `tests/cockpit_v4/test_governed_precision.py`.
2. **Visual fidelity is unverified** — see §16 above.

No other blockers.

## 26. Verdict

**COCKPIT V4 CONVERSATION EXPERIENCE: READY FOR LIVE MAC UAT.**

The interaction pattern is a conversation: a thread at its own URL, a scrollable
transcript, a follow-up composer, a renamable title, restored state after a
reload, per-turn process and trace, server-rendered analytical visuals, and
conversation-level actions. The V4 architecture, run lifecycle, evidence model,
Saudi release and one-Opus analytical path are unchanged.

Two qualifications, both stated above and neither a functional blocker: the
reference screenshots never arrived, so visual fidelity to the old Cockpit is
unverified; and Add to Project is withheld rather than faked, because this
runtime has no project endpoint.

## 27. Running it on the Mac

```bash
# stop what is running
python3 scripts/cockpit_v4/stop.py

# take this round
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull origin claude/cockpit-single-agent-v4-h8fsbq

# frontend dependencies, in case the lockfile moved
cd frontend && npm install && cd ..

# start
python3 scripts/cockpit_v4/start.py

# confirm
python3 scripts/cockpit_v4/status.py
```

Then open `http://127.0.0.1:5414`, ask something analytical, and the landing
page should hand you a conversation at its own URL rather than answering in
place.
