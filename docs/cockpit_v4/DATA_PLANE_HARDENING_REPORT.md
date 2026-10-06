# Cockpit V4 — data-plane, compartmentalization, investigation-reliability
# and live-provider contract hardening

This round did not patch the questions the Mac UAT reported. It changed the
contracts underneath them and then checked those contracts across a matrix
large enough that a wrong one is wrong here rather than on a Mac.

What follows is what changed, what it was changed FROM, and what now holds it
in place. Every figure in it was read out of the build.

---

## 1. The two books, and the two calendars

| | Corporate Credit | Retail Credit |
|---|---|---|
| Release | `v4-saudi-corporate-20q-v3` | `v4-saudi-retail-20m-v3` |
| Fingerprint | `46962675d801ed4e…` | `95fa5d3e2c6b979b…` |
| Reports | **20 quarters** | **20 months** |
| Window | 2021Q3 … 2026Q2 | 2025-01 … 2026-08 |
| Period column | `reporting_quarter` | `reporting_month` |
| Relations | 4 | 4 |
| Rows | 839,960 | 815,551 |
| Entities | 3,652 borrowers · 12,782 facilities · 1,725 groups · 14 sectors · 75 sub-sectors · 10 regions · 7 product types | 12,000 customers · 16,000 accounts · 4 products · 8 regions |

The previous Corporate release reported months. It was NOT rewritten: both
`v4-saudi-corporate-20m-v1` and `-v2` are still published and still verify
against the fingerprints they were published under, because an analysis saved
against one of them names it, and a calendar change under a release id nobody
changed would silently restate every saved answer.

### What made two calendars possible

`schema.period_column()` and `schema.frequency()` are the source, and
`schema.period_keys()` is the single place that decides what a payload spells
its period under. It fills `reporting_period`, `comparison_period` and
`period_noun`, and of the two legacy keys it fills ONLY the one that book
reports in — a Corporate card leaves `reporting_month` empty rather than
putting a quarter under a month's name. An empty string is a visible mistake;
a quarter called a month is an invisible one.

`Calendar.frequency` no longer has a default. It defaulted to `"quarterly"`,
which is how a monthly book came to describe itself as quarterly in a manifest
nobody read closely.

### What the two calendars found

- `values.dimensions` excluded calendars by the literal suffix `_month`. The
  moment the Corporate book reported quarters, `reporting_quarter`,
  `origination_quarter` and `waiver_quarter` became "governed dimensions a
  reader could name", and sixty dates entered the bounded enumeration. The
  rule is now derived from the set of calendars there are.
- The attention panel's cover line read `feed.prior_quarter` and
  `feed.prior_year_quarter`, which the per-domain engine never emitted. The
  first sentence a reader saw was "CreditProbe reviewed Q2 2026 against  and
  and identified 5 segment issues" — two blanks, in the opening line.
- The trend chip offered "how has this moved over the last twelve months?" to
  a book that reports quarters: a question about three years, offered to a
  reader who clicked something else.

---

## 2. The intent is authored once

`intent` was a required nested object on all five tools — nine fields retyped
on every catalogue read, every artifact read, every execution and the answer,
none of which changed between them. A live run sent it as a string and
`intent must be an object` reached a reader's screen twice, in two rounds,
each round making the parser more forgiving instead of removing the
restatement.

The field is gone from every tool schema. The run opens an `IntentEnvelope`
holding what the server already knew before the first provider call: which
book, which release and its exact bytes, what kind of turn this is, what a
period means here, and what the question's own words resolved to against the
values the release holds. `finalize_response` may restate the reader-facing
half — understood, assumed, mapped, excluded — flat and optional, because that
half is answer content.

The server's half is not overwritable. The one movement allowed is **widening**:
submitting SQL declares the turn analytical, so a question the budget
classifier read as product help escalates rather than being refused for a
classification the reader was never shown. It never narrows — a run that has
already read the book does not move onto the cheaper clock by saying so.

`tests/cockpit_v4/test_intent_envelope.py` replays ten shapes a live model has
sent — a bare string, a sentence, a list, a number, an empty object, nothing —
through the real parser. None of them can reject a run that has its envelope.

---

## 3. The seeded investigation

A reader clicked the Construction card. The thread read the catalogue, read it
again, read product knowledge, and died at CALL_LIMIT without answering
anything. Not one of those calls could have returned something the server had
not already computed: it wrote that card.

A seeded thread now opens with an **analysis packet** — the relation the
finding came from, the column the segment lives in, the governed value of that
segment and the label beside it, the measure's own columns, the two periods,
the grain and the keys. Bounded by construction: under 6 KB and at most
eighteen columns, because the covenant relation has fifty-nine and carrying
all of them would be the relation dump this exists to avoid.

Checking every offered question against the release, rather than against
prose, found two defects:

- A covenant-breach card offered "Split Leverage by region", and
  `corp_covenant_quarter` has no region column. The cross-cut list was the
  book's second axes IN GENERAL rather than the columns of the relation the
  finding actually came from. Four such chips existed.
- The trend chip's calendar, above.

All **86** offered questions across both books now bind against the release
before they are shown.

---

## 4. Compartmentalization

Every previous round fixed the leak it was shown — the dashboard, then
Investigate Further, then the export — and none of those fixes prevented the
next one, because a leak is not a property of a feature. It is a property of
any path that takes a domain from somewhere other than the thread.

`tests/cockpit_v4/test_compartmentalization.py` enumerates the surfaces and
checks the rule on each, and its last test fails when a route serves a book
and is not listed, so the audit stays an audit. Two surfaces failed it:

- `/ecl` served its whole answer without naming the book at its top level. A
  caller had to reach into `profile.domain_id`, and a caller who has to do
  that eventually does not.
- The run-accept 202 echoed the release but not the domain, so a client could
  not see that the thread's own book had won over the one it asked for.

A third failure was in the vocabulary rather than in a route. Asked of the
Retail book, "and within project finance?" resolved the single word `finance`
and asked "did you mean Auto Finance or Personal Finance?" — a question built
by discarding the word that made the reader's phrase unambiguous. The Retail
book has no project finance. Saying nothing is the honest answer; asking
invites the reader to accept an answer to a different question.

In the Corporate book the same phrase resolves exactly — from `prject finance`
too, without a clarifying question — and the trace quotes what the reader
wrote rather than the fragment that matched.

---

## 5. The process panel

Two defects, both of them the panel deciding things the server had not said.

The panel laid out "Request accepted" and "Understanding the request" as empty
circles before anything happened, so a reader watching a live run at 0s saw
two rows reading "not started". `StepState` no longer has a `prospective`
member and `INITIAL_STAGES` is empty: a step appears when the server says its
stage started.

The panel closed a stage by inference — "any earlier stage still marked
running has been left behind" — which holds only while stages run in array
order and never re-enter. A run that re-enters `preparing` after a failed
submission breaks it in both directions.

The emitter now runs the state machine. Every event carries its stage
instance (`preparing#1`, `preparing#2` — two passes are two things that
happened), when that instance started, its state, and the instances this event
CLOSED. A terminal event closes two: the one it displaced and its own, so a
browser reconnecting after the last frame is not left with a stage spinning.

`tests/cockpit_v4/test_process_events.py` folds the persisted stream in Python
— a second implementation, deliberately — because if it and `reducer.ts` can
disagree then the stream is not enough and something on screen is coming from
somewhere else.

---

## 6. The Data Builder

`/data-builder` from the sidebar showed the onboarding estate and nothing
about the two published analytical books. They are now the first thing on that
page, rendered from `/schema` — the same governed catalogue the analytical
path reads.

Every assertion in `test_data_builder.py` pairs a Data Builder answer with the
analytical path's own answer and requires the same object, not a similar one.
A Data Builder backed by its own store would agree on the day it was written
and drift afterwards, and when it drifted nothing on either screen would say
which of the two was wrong.

Each card carries country, denomination, calendar with its noun, the period
span, the entity counts a credit officer recognises, the release and its
fingerprint, and the status. Columns are grouped by the catalogue's own
subject areas. Each column shows the label a person reads AND the identifier
SQL filters on, and a category column enumerates the values it may hold —
labelled `governed_values` where the set is closed and `sample_values` where
it is not, because a reader told twelve borrower names were the governed list
would build a filter that silently excluded 3,640 others.

---

## 7. Convergence

The response to runs dying at their deadline had been more time — 60s, then
120s — and more time is not convergence: a run that needs four catalogue calls
to answer "what is EAD by sector" will spend whatever it is given.

`test_convergence.py` bounds the WORK. A simple question costs one action turn
and one answer turn, makes no catalogue call at all, reads back a tool result
inside half the soft input budget, and finishes inside half its allowance. The
two structure-recovery budgets are checked to be separate, so a malformed
action cannot consume the one allowance the answer has.

---

## 8. The matrix

| Bank | Size | What it checks |
|---|---|---|
| Corporate questions | 40 | Every figure against an independent pandas oracle over the Parquet |
| Retail questions | 40 | The same |
| Follow-up chains | 20 (10 + 10) | 4–6 turns each, ≥80 turns, book and calendar held across every turn |
| Attention chains | 20 (10 + 10) | Card → investigate → suggested question → execute → publish |
| Mac replays | 7 | One per recorded live failure, asserting on the cause |
| Negative cases | 22 | What must be refused, and the shape of each refusal |
| Performance gates | 15 | DuckDB doing the filtering, checked by plan as well as by clock |

The oracles import nothing from the analytical path. One that called the code
under test would prove the code is self-consistent, which is exactly what a
wrong answer also is.

Writing the bank found three things the data says and the questions did not:
no facility cured in the latest quarter (cures are real and authored, 348 of
them in 2024Q4–2025Q2); `valuation_age_quarters` cycles one to four, so "older
than two years" is unreachable; and `months_on_book / 12` divides as a float
in DuckDB, so a seasoning curve grouped by 0.083 rather than by whole years.

And one bug in the oracles themselves, which is the kind a bank catches and a
single example does not: flags are `int64` in the Parquet, so
`frame[frame["breach_flag"]]` selects COLUMNS rather than rows — silently,
until the integers happen not to be column names.

---

## 9. The flake that was a defect

§57 says an intermittent failure is a defect and must be root-caused rather
than rerun until green. One appeared: a full-suite run stopped a follow-up
chain at `sqlite3.OperationalError: database is locked` after waiting out its
whole sixty-second allowance, and the same test passed on its own every time.

The cause was not contention. `RunStore._tx` reached its ROLLBACK only via
`except sqlite3.Error`, so any OTHER exception raised inside the block — a
validation failure, an HTTPException, a KeyError in the caller's own code —
propagated straight through with `BEGIN IMMEDIATE` still open on that
thread's connection, holding the database's write lock.

Nothing released it. Every later writer waited out its thirty-second
`busy_timeout` and failed with "database is locked": a message that reads as
a storage problem and is not one. It is one earlier caller's exception, still
holding the door — and which caller it was depends on test ordering, which is
why it looked like a flake.

The rollback is now in `finally`, and a connection too broken to roll back is
dropped rather than kept, because the next caller on that thread would inherit
its open transaction. `test_store_transactions.py` has twelve tests; nine of
them fail on the old code.

---

## 10. What is NOT in this round

Stated so the next reader does not have to infer it from silence.

- **No paid provider call was made.** No suite here declares REAL PROVIDER,
  and a test in the suite fails if one starts to. Everything analytical runs
  against a scripted analyst driving the real worker, the real contracts and
  the real database.
- **V3 was not modified.** The only files this round touched outside
  `backend/cockpit_v4`, `tests/cockpit_v4` and `scripts/cockpit_v4` are the
  V4 components under `frontend/src/components/cockpit-v4` and one line of
  `frontend/src/app/data-builder/page.tsx` that mounts the V4 books.
- **The published Corporate releases were not rewritten.** The book moved to
  quarters by publishing `v4-saudi-corporate-20q-v3`; `-20m-v1` and `-20m-v2`
  are still published and still verify.
- **Seven product types, not eight.** §8 asks for seven with deep
  populations, and the release has seven with 500+ facilities each. An eighth
  thin one would have met a word and not the requirement, and adding one now
  would mean a new release and a new fingerprint for every stored answer.
- **`valuation_age_quarters` cycles one to four.** It cannot express a stale
  valuation, and the governance question asks for the oldest band instead.
  Changing the column means rebuilding and republishing the book.

---

## 11. Release gate

| # | Gate | State |
|---|---|---|
| 1 | Corporate publishes 20 quarters, 2021Q3 … 2026Q2 | ✅ |
| 2 | Retail publishes 20 months, 2025-01 … 2026-08 | ✅ |
| 3 | Neither book's calendar appears in the other's payload, export, feed or vocabulary | ✅ |
| 4 | The published monthly Corporate releases were not rewritten and still verify | ✅ |
| 5 | Corporate ≥3,000 borrowers / ≥8,000 facilities | ✅ 3,652 / 12,782 |
| 6 | Retail ≥3,000 customers | ✅ 12,000 customers, 16,000 accounts |
| 7 | 14 sectors, 75 sub-sectors, 7 product types, all with real populations | ✅ |
| 8 | Both books carry the full credit-risk model by subject area | ✅ checked against the catalogue's own groups |
| 9 | `/data-builder` from the sidebar opens the dual-domain books | ✅ |
| 10 | The Data Builder reads the SAME catalogue the Cockpit answers from | ✅ asserted object by object |
| 11 | Field inspection shows labels, identifiers, governed values and bounded samples | ✅ |
| 12 | Every path that serves a book serves the one it was asked for | ✅ audited, and the audit fails on an unlisted route |
| 13 | A thread's book is immutable, refused by name with an offer | ✅ 409 `DOMAIN_PINNED` |
| 14 | A seeded investigation gets its analysis packet on turn one | ✅ under 6 KB |
| 15 | Every attention family in both books: investigate → execute → publish | ✅ no CALL_LIMIT, no DEADLINE_EXPIRED |
| 16 | Every suggested question binds against the release before it is offered | ✅ 86 of 86 |
| 17 | `intent` is not a property of any tool schema | ✅ |
| 18 | `intent must be an object` is structurally impossible | ✅ ten live shapes replayed through the real parser |
| 19 | The run owns its intent before the first provider call | ✅ |
| 20 | The mode widens into analysis and never narrows | ✅ |
| 21 | Action and answer structure-recovery budgets are separate | ✅ |
| 22 | A simple question costs one action turn and one answer turn | ✅ with zero catalogue calls |
| 23 | 120s standard / 240s deep, with product help still at 60s | ✅ |
| 24 | Only started steps appear in the process panel | ✅ `prospective` removed from the type |
| 25 | The previous foreground step closes when the next begins | ✅ server-stated, not inferred |
| 26 | Sequence, stage instance, start, end and state are persisted | ✅ |
| 27 | Replay and reconnect neither duplicate nor reorder | ✅ folded twice, in two implementations |
| 28 | 40 Corporate + 40 Retail questions against independent oracles | ✅ |
| 29 | 20 follow-up chains (4–6 turns) + 20 attention chains | ✅ |
| 30 | Every recorded Mac failure has a replay fixture | ✅ 7 of 7 |


---

## 12. Retesting on the Mac

The Corporate book changed release. A Mac that ran the previous round has
`v4-saudi-corporate-20m-v2` on disk and nothing that points at the new one,
so the seed step below is not optional: without it the Cockpit opens, reports
the Corporate book unavailable by name, and refuses rather than substituting.

Run these in order, from the repository root.

**1. Stop whatever is running.**

```
python3 scripts/cockpit_v4/stop.py
```

Or double-click `scripts/cockpit_v4/STOP_COCKPIT_V4.command`. It stops only
Cockpit V4; nothing else on the Mac is touched.

**2. Pull this branch.**

```
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull --ff-only origin claude/cockpit-single-agent-v4-h8fsbq
```

**3. Build and publish the two releases.**

```
COCKPIT_AGENTIC_V3_NAMESPACE=cockpit_v4 \
  python3 scripts/cockpit_v4/seed_domains.py --domain all
```

About a minute on a Mac that has neither release. It prints each book, the
number of periods in that book's own noun — "20 quarters" for Corporate,
"20 months" for Retail — and the fingerprint it published under.

It REFUSES to overwrite a release that already exists, so running it twice is
safe. A Mac that already has both prints this and writes nothing:

```
  corporate  v4-saudi-corporate-20q-v3  already exists -- immutable, nothing written
  retail     v4-saudi-retail-20m-v3  already exists -- immutable, nothing written
```

**4. Check what was published, without building anything.**

```
COCKPIT_AGENTIC_V3_NAMESPACE=cockpit_v4 \
  python3 scripts/cockpit_v4/seed_domains.py --verify
```

Expect exactly:

```
  corporate  v4-saudi-corporate-20q-v3  verified  46962675d801ed4e
  retail     v4-saudi-retail-20m-v3  verified  95fa5d3e2c6b979b
```

"verified" means the bytes on disk still hash to the fingerprint the release
was published under. The two fingerprints above are the ones this branch
built; a Mac that rebuilds from the same source gets the same two, because
the generators are deterministic.

If a fingerprint does not match, stop there and say so: a release whose bytes
moved under its own name is the one thing nothing downstream can detect.

**5. Start.**

```
python3 scripts/cockpit_v4/start.py
```

Or double-click `scripts/cockpit_v4/START_COCKPIT_V4.command`. It prints the
UI and API ports it chose and the release each book opened.

**6. Check it is up.**

```
python3 scripts/cockpit_v4/status.py
```

### What to look at first

1. **The home page.** The heading over the Corporate dashboard reads
   "Reporting quarter Q2 2026", and over the Retail one "Reporting month
   Aug 2026". Neither says the other's word.
2. **Switch the book.** The cards change entirely — no card appears on both
   pages — and the ECL panel's cover line changes with it.
3. **Click a card, then Investigate Further.** The thread opens on three to
   five questions written in that book's calendar. Click one: it should
   answer without a "reading data definitions" step.
4. **Ask "and within project finance?" in a Corporate thread.** It filters on
   `product_type`, declares the mapping, and does not ask a clarifying
   question. Misspell it — "prject finance" — and it still does.
5. **Ask the same phrase in a Retail thread.** The book has no project
   finance, and the answer should say so rather than offering Personal
   Finance.
6. **Open Data Builder from the sidebar.** Both books are at the top of the
   page with their calendars, entity counts, subject areas and fingerprints.
   Open a relation: every column has a label, an identifier, a meaning, and —
   where it is a category — the values it may hold.
7. **Watch the process panel on a live question.** At 0s it is empty. Rows
   appear as stages start, and a stage that is finished says how long it took
   rather than "not started".

---

## 13. The round, item by item

Thirty-nine claims. Each one is a thing that is now true and was not before,
with where to look.

**The data plane**

1. The Corporate book publishes 20 quarters, 2021Q3 … 2026Q2, as
   `v4-saudi-corporate-20q-v3`. `scripts/cockpit_v4/seed_domains.py --verify`
2. The Retail book publishes 20 months, 2025-01 … 2026-08, as
   `v4-saudi-retail-20m-v3`. Same command.
3. Both previous Corporate releases are still published and still verify
   against their original fingerprints. `test_release_scale.py`
4. `schema.period_column` / `frequency` / `period_noun` / `period_keys` are
   the one place a calendar is decided.
5. `Calendar.frequency` has no default. A book that does not state its
   frequency cannot be opened.
6. Only a book's OWN legacy period key is ever filled. A Corporate payload
   leaves `reporting_month` empty. `schema.period_keys`
7. `values.dimensions` derives its calendar exclusion from the set of
   calendars there are, not from the literal `_month`.
8. The Corporate book holds 3,652 borrowers, 12,782 facilities, 1,725 groups,
   14 sectors, 75 sub-sectors, 10 regions and 7 product types, each with 500+
   facilities. `test_release_scale.py`, `test_data_builder.py`
9. The Retail book holds 12,000 customers and 16,000 accounts across 4
   products and 8 regions.
10. Every relation in both books is read by some question in the bank.
    `test_question_banks.py`

**The contract**

11. `intent` is not a property of any tool schema, in either book.
12. The run owns an `IntentEnvelope` before its first provider call.
13. Ten shapes a live model has sent — including the bare string that
    produced `intent must be an object` — all parse. `test_intent_envelope.py`
14. The mode widens into analysis and never narrows, at the parser and in
    the orchestrator.
15. `finalize_response` carries the reader-facing half flat and optional;
    no other tool carries any of it.
16. A run's book, release and fingerprint are not overwritable by an answer.

**The investigation**

17. A seeded thread opens with an analysis packet under 6 KB.
    `investigation.analysis_packet`
18. Every attention family in both books runs investigate → execute →
    publish with no CALL_LIMIT and no DEADLINE_EXPIRED.
    `test_investigation_packet.py`
19. All 86 offered questions in both books bind against the release before
    they are shown. `investigation.executable`
20. A cross-cut is a column of the finding's OWN relation. Four chips that
    were not have gone.
21. A trend question is written in the book's own periods — eight quarters,
    or twelve months.

**Compartmentalization**

22. Every route that serves a book is audited, and the audit fails on one
    that is added without a row. `test_compartmentalization.py`
23. `/ecl` and the run-accept response now name the book at their top level.
24. A thread's book is immutable and refused by name with an offer.
25. Reopening a conversation reopens its book.
26. A phrase only the other book has resolves to nothing rather than to a
    question about something the reader did not say.
27. An obvious typo is resolved, declared, and not escalated.

**The panel**

28. No step is drawn before it starts; `prospective` is gone from the type.
29. A stage closes because the server said so, with its instance named.
30. Two passes through one stage are two rows, in the order they ran.
31. A terminal event leaves nothing running.
32. Replaying the stream twice lands where it landed once, in two
    independent implementations. `test_process_events.py`, `reducer.test.ts`

**The evidence**

33. 40 Corporate + 40 Retail questions against pandas oracles that import
    nothing from the analytical path.
34. 20 follow-up chains of 4–6 turns, and 20 attention chains.
35. 7 Mac failure replays, and a test that fails when one has no fixture.
36. 22 negative cases, including six the executability checker must refuse.
37. 15 performance gates, two of which check the plan rather than the clock.
38. 12 store-transaction tests; 9 of them fail on the code they replaced.
39. The Data Builder's every claim is checked against the analytical path's
    own catalogue object.

---

## 14. Test evidence

### Browser, five consecutive runs

The suite was run five times in a row against a freshly started stack each
time. §57 asks for repeated runs precisely because one green run says
nothing about a race.

```
=== browser run 1 ===  exit=0 ok=69 fail=0
=== browser run 2 ===  exit=0 ok=69 fail=0
=== browser run 3 ===  exit=0 ok=69 fail=0
=== browser run 4 ===  exit=0 ok=69 fail=0
=== browser run 5 ===  exit=0 ok=69 fail=0
```

An earlier attempt at this matrix produced three failures on its first run.
All three were assertions naming one calendar — "a monthly book offered a
quarter", about a book that reports quarters — and one was the stub server
filtering the Corporate relation on `reporting_month`, which is the only
reason the single analytical browser test failed.

### Screenshots

Twenty, under `docs/cockpit_v4/evidence/`. `data_builder_sidebar.png` is the
one this round added: `/data-builder`, the route the Mac reader opened, as
distinct from `/cockpit/data`, which is a different page. A screenshot of the
right page proving the wrong one works is not evidence.

### The V4 suite

```
tests/cockpit_v4
2246 passed, 4 skipped, 1 warning in 619.76s
```

Zero failures. The previous sweep had two, and neither was rerun until it
went green: one was the leaked write lock in §9, which now has twelve tests
behind it, and the other was a new module that declared no evidence label —
caught by the suite's own labelling test, which exists so that a reader can
tell what a passing result establishes.

### V3 regression

§58: do not change V3, and run its regression at the end with zero new
failures.

```
tests/cockpit_agentic + tests/agentic
771 passed, 105 skipped, 2 warnings in 186.34s
```

Zero failures. The 105 skips are the suite's own gating: tests conditioned on
a live provider or on a release that is not provisioned in this environment.
Nothing this round changed can reach them — no file under
`backend/cockpit_agentic` or `tests/cockpit_agentic` was touched.

Nothing under `backend/cockpit_agentic` or `tests/cockpit_agentic` was
touched. The complete list of files this round changed outside
`backend/cockpit_v4`, `tests/cockpit_v4`, `scripts/cockpit_v4` and
`frontend/src/components/cockpit-v4` is one line of
`frontend/src/app/data-builder/page.tsx`, which mounts the V4 books on the
sidebar route.

### Frontend unit tests

```
node --experimental-strip-types --test frontend/src/components/cockpit-v4/*.test.ts
119 pass, 0 fail
```

`npx tsc --noEmit` is clean.
