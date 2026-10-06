# Round H — the analyst, not the machinery

**Branch** `claude/cockpit-single-agent-v4-h8fsbq` · **from** `bef72b6` ·
**to** `2b2a5b7` · 2026-09-20

Round G made the answer readable: axes, hover, zoom, downloads, a theme that
survives being dark, sharing, a governance trace. This round was asked a
different question — not *does it work*, but *does it behave like a strong
credit-risk analyst*: governed execution, traceable numbers, appropriate
visuals, reliable multi-turn reasoning, honest failure.

Ten defects, every one found by reading the product against that standard
rather than by a failing test. Nine fixed, one logged open with the
observability to catch it next time. No test was weakened, skipped, xfailed
or budgeted around to reach green.

---

## 1 · Scope

Standalone AdvancedCockpit only. No Retail Demo integration, no Retail
candidate, no cross-branch work. The canonical worktree is
`/home/user/cockpit_v4_wt`, branch `claude/cockpit-single-agent-v4-h8fsbq`.

## 2 · What changed

| Commit | What |
|---|---|
| `ee54046` | Chart restraint: `why_this_chart`, and the rule for when not to chart |
| `df9c610` | `ANALYTICAL_ANSWER`: what a good analytical answer is |
| `edd91fd` | Credit policy found by the name a reader uses |
| `412792a` | A clarification's question reaches the turn that answers it |
| `5307e97` | Restored performance evidence swept in by a bulk add |
| `d940704` | The benchmark stops calling chart plumbing a judgement |
| `4c5e173` | A heading that waits until it knows the name; a timeout that says which half failed |
| `2ad2426` | An unverified price was still a price |
| `2b2a5b7` | A clause id pasted out of a document resolves too |

Ten source files changed, 298 lines; nine test files, 1175 lines.
`analyst.md` is not among them: it is byte-identical to `bef72b6` and still
contains no occurrence of the word "chart".

---

## The defects

## 3 · `why_this_chart` was sent by two fixtures and had never existed

`$defs.Chart` is `additionalProperties: false`. Two test fixtures built
charts carrying `why_this_chart` and asserted on what the product did with
them. `git log -S` over `backend/cockpit_v4/contracts/` returns nothing: the
field had never been in the contract. The fixtures passed because they build
the answer object directly and never meet the wire schema, so what they
exercised was a payload a real provider would have refused outright.

A fixture that proves a shape the provider rejects is worse than no fixture.
It reports coverage of a path that cannot run.

**Fixed** — the property is declared, optional, capped at 200 characters so
it stays one line rather than a second narrative. Proved against the
FLATTENED schema (`contracts._inline`), which is what actually goes on the
wire: accepted with the field, accepted without it, rejected over the cap,
and an unknown field still rejected.

## 4 · Nothing told the analyst when NOT to draw a chart

`PRESENTATION` and the `charts` contract description were both written after
four live answers arrived as bare tables, one of them to a reader who had
asked for a line chart in those words. Everything written then says SEND A
CHART, six ways. Nothing said when not to.

So the only failure the guidance could still produce was the opposite one: a
single-bar chart under a sentence that already gave the number, a pie of two
slices, a line through three points.

The product's own benchmark bank already held the verdict. Twelve of its
twenty questions carry a reasoned "no chart" — *"a chart of three numbers is
decoration"*, *"two movements per sector on different scales; the table is
honest, a dual axis is not"*. None of those twelve judgements was expressible
by an analyst that had never been told the rule.

**Fixed** — two clauses on the answer turn, and the same rule in the schema
description the provider receives.

## 5 · The analytical answer had no shape

`analyst.md` has a section called "Writing the answer". It is careful work,
and it gives four WORKED SHAPES — what a good answer contains and in what
order. All four are product questions: who are you, what does Early Warning
do, what is Cockpit, what is TAC.

The shape it never describes is the one this product exists for: the answer
written with a result set in hand. The "Analysis" section beside it is
entirely about how to RUN the query and stops the moment rows come back.

The single act the system is built around was the one act with no statement
of what good looks like, while four less central ones had theirs spelled out.
The default in that absence is narrating the table, which is the one thing
the reader already has.

**Fixed** — `ANALYTICAL_ANSWER`, on the turn that has the rows: lead with the
finding and the figure; put a number against something the RESULT already
holds and reach for nothing that was not computed; say how much of the total
sits in how few names; name the driver where the result shows direction and
say "movement, not cause" where it does not; say what it means for the book;
state the limitation once rather than hedging every clause; do not walk the
table; do not narrate the process. And `length_follows_the_question`, because
a checklist read as a form to fill produces the padding the rest of it
prevents.

## 6 · A policy clause nobody could find by its working name

`clauses_for` lowercased the question and asked whether each keyword was a
substring. "single-name limit" and "single name limit" were different
questions, and a hyphen decided whether a credit officer reached CP-1.1.

The map had begun paying for this by hand — "write-off" beside "write off",
"cut-off" beside "cutoff", "loan-to-value" beside "loan to value". Three
entries standing in for a missing normalisation, and incomplete even so:
"cut off", the third spelling, matched nothing.

Measured over the product's own vocabulary rather than phrases invented for
the occasion: **75 of the map's keywords failed when their own punctuation
was flipped back at them.**

**Fixed** — `flatten` reduces every run of separators to one space, on both
sides of the comparison, including the en- and em-dash range because a
question pasted out of a policy document carries typographic dashes. 75 → 0.

Separately and honestly separately, two vocabulary gaps no normalisation
fixes: CP-1.1 "Single obligor limit" had no "single name"; CP-1.3 "Sector
concentration" had no "industry".

The clause-id pattern beside the keyword matcher had the same blind spot and
was fixed with it (`2b2a5b7`): it allowed a hyphen, a space or nothing, and
nothing typographic, so `CP-1.2` reached its clause and `CP‑1.2` — the same
three characters after a word processor had been through them — reached
nothing. Quoting a clause id is the likeliest way one arrives, and it
arrives by copy and paste.

## 7 · A clarification lost its own question

A turn can end by putting one question to the reader instead of guessing.
That half works. The reader clicks an option, and what the next turn receives
as its question is the option's text: *"Symmetric allocation."* Three words,
arriving alone.

The history block carried the question asked, the narrative and the
disposition — not `clarification_question`. So the analyst saw that it had
asked SOMETHING, and a phrase to work backwards from, with the thing that
would have made it obvious sitting unread in the stored answer.

The round trip completed only when the analyst had happened to repeat its own
question inside the narrative as well as putting it in the field built for
it. The clarification path exists to stop the run guessing; it had moved the
guess one turn later.

**Fixed** — a projection, not a mechanism. `recent_turns` already returns the
whole stored answer. A clarification turn now carries `you_asked`, the
choices offered, and a note that stops short of a rule: the next question is
*most likely* the answer, and if it plainly asks something else it is a new
question.

## 8 · The benchmark scored chart plumbing as judgement

`visualization_judgement`, weight 10, marked not model-dependent, tested as
*"a chart is published exactly when the question warrants one"*.

But the scripted analyst reads `question["chart"]` out of the bank and emits
that form, and the mark compares the published chart against the same field.
It can only fail if the pipeline drops or mangles a chart the analyst
declared. That is worth marking and it is not judgement.

The rubric's own comment says why this matters: *"a rubric that grades the
harness is worse than no rubric."*

**Fixed** — split in half. `visual_fidelity`, 5, scored. `visualization_judgement`,
5, model-dependent and unscored, beside insight and clarity.
Scorable falls 80 → 75. Three guards hold the shape, one of which reads
`_score` itself so a dimension cannot drift from the mark behind it.

## 9 · A heading that claimed a name before it had one

`transcript?.title ?? ""` collapses "not loaded yet" and "loaded, unnamed"
into the same empty string, both rendering as "New conversation". A thread
the reader had named appeared, briefly, to have been renamed back on every
reload.

**Fixed** — while the server has not said, the heading says it is still
asking, under a different test id so a test waits for a title rather than
racing the fetch. Rename is disabled until then.

## 10 · The price card accepted a price nobody had checked

`load_price_card` is the gate in front of every paid request. Its docstring
says *"Every failure is explicit and fails closed."* The shipped card's note
repeats it: *"A missing or unverified entry fails closed."*

A missing entry did. An unverified one did not.

The shipped card is a template: one entry, `REPLACE-WITH-YOUR-MODEL-ID`,
`source: "PLACEHOLDER"`, `verified_at: ""`, all four billing classes 0.0,
under a line saying in capitals that these are NOT verified pricing. It
loaded. `verified_at` was read into the Capability, surfaced in `to_dict()`,
and read by nothing anywhere else in the product — `grep -rn verified_at
backend/cockpit_v4` outside `capability.py` returns nothing.

Copy the template, change the model id to the one you are about to spend
money on, leave the date blank — the one field with nothing obviously wrong
about leaving blank — and every request is costed at zero. A spend cap can
never be reached. The run reports no cost. Nothing says the numbers were
never real.

That is the failure the card exists to prevent, arriving through the card
itself, in the round immediately before a paid run was to be authorised.

**Fixed** — `verified_at` must be present and must parse as a date, because
"Verified: soon" is worse than blank: it reads, to anyone scanning the card,
as though someone had checked. `source` must name where the numbers came
from and must not still say PLACEHOLDER. A zero price still loads when
someone vouched for it — a free tier or a fixed-fee arrangement is a real
price of zero, and refusing it would be inventing a rule about the
provider's business. What is refused is a zero nobody signed.

`tests/cockpit_v4/test_price_card.py` is the first test this function has
ever had.

---

## 11 · D-002, open: the result-only journey under Midnight

Logged in `DEFECTS.md`, not fixed, not closed, not explained away.

Thirteen full browser runs, one session, one machine. The default-theme
runs counted here are the six after §9's thread-title fix; the run before
it failed a different test.

| Theme | Runs | Result |
|---|---|---|
| default | 6 | 76/76 every time |
| `porcelain` (themed path, light palette) | 1 | 76/76 |
| `midnight` | 6 | 4 × 76/76, 2 × 75/76 |

The same test each time, waiting the full 120 s and taking 151 s against a
usual 5.8 s. Not the test alone (2/2 in isolation under `midnight`), not the
themed code path (`porcelain` seeds `localStorage` through the same helper),
and not run ordering — two default runs back to back both passed, which was
the first hypothesis and was disproved.

**What is not established is the cause**, and specifically whether the run
never produced an answer or produced one the page rendered with no box.
`waitForSelector` waits for VISIBLE, so the old message covered both and
distinguished neither. `expect()` now reports DOM presence, box, `display`,
`visibility`, `opacity` and the active `data-theme`. It has not reproduced in
the five runs since, so that message is not yet exercised against a real
failure.

Nothing was widened, skipped or retried. The wait is 120 s and the test is
required.

---

## What was audited and found sound

## 12 · Multi-turn context (H1) — no defect

I suspected the follow-up history gave an unactionable `read_artifact`
instruction and disproved it myself. `_read_turn` takes a `turn_id`, which IS
in history; it returns the full `answer`, which carries `tables[].artifact_id`,
readable as `kind="result"`. Covered by
`test_catalog_answer_memory.py::393`. Reported as no defect.

## 13 · Failure and repair behaviour (H7) — no defect

Ten injected failure classes, each asserting a terminal state, the right
error code (not a catch-all), that nothing shaped like an answer was
published, and an operator reference where the class warrants one. The
architecture holds the line the brief demands: `execute_tool` is *"validate
exactly, execute exactly, repair never"*, and the analyst authors every
correction. The result-only channel publishes computed rows under a caveat
the server writes — because the model is precisely the thing that failed.

---

## What is proven, and what is not

## 14 · The evidence label on every claim

No paid provider call was made in this round, and none was authorised. Every
suite carries an evidence label in its docstring and none of them claims REAL
PROVIDER; `test_multilingual_and_evidence_labels.py` enforces that.

## 15 · MECHANICS VERIFIED · LIVE MODEL BEHAVIOUR NOT YET VERIFIED

This label applies to every result in this round. The overnight benchmark
states it in its own words: *"CreditProbe executed this analysis correctly and
published a figure the evidence supports" is proven here. "Opus would have
chosen this analysis" is NOT.*

Two of the five changes to the analyst's context — the chart restraint rule
and the analytical-answer standard — are instructions to a live model. Their
mechanics are proven: the block exists, is serialised, reaches the turn that
can act on it, stays off the turn that cannot, and costs what is claimed.
Whether a live analyst writes better answers because of them is exactly the
class of thing a scripted provider cannot establish, and is not claimed.

## 16 · What the test suites establish

| Suite | Result |
|---|---|
| `tests/cockpit_v4` + `tests/frontend` | see §27 |
| `tests/cockpit_agentic` (V3) | see §27 |
| frontend `node --test` | 587 passed |
| `tsc --noEmit` | clean |
| browser, default theme | 76/76 |
| browser, `midnight` | 76/76 (see §11) |
| dark-mode evidence | 19/19 pairs, by measured mean luminance |

## 17 · Mutation checks (§16 of the brief)

Every fix carries one, and each reproduces the OLD behaviour and asserts it
fails:

- the schema property removed → the fixture payload illegal again
- the answer standard read out of the serialised block, not the source, so a
  clause written in a `#` comment cannot satisfy it
- the old substring matcher reproduced → ≥70 of the map's keywords still lose
- the old four-key history projection reproduced → the clarification question
  is not in it
- the rubric's scored dimensions checked against `_score`'s own source
- the old price-card checks walked one by one → the shipped template
  satisfies every one of them

## 18 · The architecture is unchanged

CreditProbe did not become the analytical brain. Nothing added decides an
analysis. The two context blocks say what an answer is held to and never name
a field, a threshold, a portfolio or a figure —
`test_the_standard_is_advice_about_shape_not_a_lookup_of_answers` asserts
exactly that. The policy fix is a keyword map on purpose: a server guessing
at policy relevance would be making the analyst's call for it.

No canned SQL, no prompt-specific answer lookup, no hardcoded expected
answer, no dataset substitution, and CreditProbe still never repairs
model-authored SQL.

## 19 · The cost of what was added

The finalization packet was compacted by dropping the catalogue block —
roughly four thousand tokens, which is why an answer is affordable on a run
that already spent an action retry. The answer standard spends about five
hundred of that. A test bounds the two standing blocks at 5000 bytes
together, so policy cannot grow back into that space one clause at a time
without a single change looking like it had.

The clarification keys cost nothing on a turn that was not a clarification:
absent, not empty. The options are capped, because the block rides on every
remaining turn of the thread.

`analyst.md` is unchanged and still contains no occurrence of "chart" —
`test_the_action_turn_does_not_pay_for_it` keeps it that way.

---

## Live UAT

## 20 · Provider preconditions, checked not assumed

| | |
|---|---|
| `COCKPIT_ANTHROPIC_API_KEY` | unset |
| `ANTHROPIC_API_KEY` | unset |
| `AI_COCKPIT_REASONING_MODEL` | unset |
| `COCKPIT_V4_PRICE_CARD` | unset |
| `config/cockpit_v4/price_card.json` | `verified_at: ""`, one model `REPLACE-WITH-YOUR-MODEL-ID`, all four prices `0.0`, `source: PLACEHOLDER` — and as of §10 it now refuses to load |
| spend approval | none given |

## 21 · Nothing was invented

No model id was chosen. No pricing was entered. No credential was created or
read from elsewhere. No spend was approved on anyone's behalf.

The card fails closed on a missing entry, as it always did, and on an
unverified one, which it did not until §10 of this round.

## 22 · What a live run needs before it can start

1. A model id, and the account's published schedule for it.
2. `config/cockpit_v4/price_card.json` filled for that id, all four billing
   classes, with a real `verified_at` and `source`. Since §10 these are
   enforced rather than requested: the card refuses to load without them,
   so a template left half-filled stops the run instead of costing it at
   zero.
3. `COCKPIT_ANTHROPIC_API_KEY`. There is no fallback: V4 deliberately does not read `ANTHROPIC_API_KEY`, an SDK default, or any other module's key.
4. `AI_COCKPIT_REASONING_MODEL` set to the same id.
5. A stated spend cap.

## 23 · What the first live session should look for

The two changes that a scripted provider cannot judge:

- **Chart restraint.** Run the twelve bank questions whose expected verdict is
  "no chart". A live analyst that charts them anyway has read the rule and
  not acted on it, which is a different problem from not having the rule.
- **Answer shape.** Read ten analytical answers against §5's list. The failure
  to watch for is the opposite of the one it fixes: a checklist walked
  section by section, which `length_follows_the_question` is there to prevent
  and which only a live run can show.

Then the clarification round trip end to end: provoke a method ambiguity,
click an option, and read what the next turn understood.

---

## Housekeeping

## 24 · One commit had to be corrected, and why it will happen again

`edd91fd` swept `dual_domain_performance.json` in via `git add -A`. That file
is rewritten by its own test on every run, so a suite run on a quieter
machine commits a different machine's timings as though they were a change.
`5307e97` puts it back. The test asserts against fixed budgets, which is
where the pass and the fail live.

Four evidence JSONs behave this way. A bulk `git add -A` after a test run
will sweep all four. This is not a product defect — regenerating evidence is
the point — but it is worth knowing before the next round.

## 25 · Nothing outside the standalone product was touched

No file under any Retail path, no branch other than the canonical one, no
merge, no pull request.

## 26 · Every commit is on the designated branch

`claude/cockpit-single-agent-v4-h8fsbq`, nine commits,
`bef72b6..2b2a5b7`.

---

## 27 · The numbers

Filled from the authoritative run at `2b2a5b7`. See §16 for what each suite
does and does not establish.

| Suite | Result | Wall |
|---|---|---|
| `tests/cockpit_v4` + `tests/frontend` | 3110 passed, 4 skipped, 0 failed | 14m 04s |
| `tests/cockpit_agentic` (V3) | 564 passed, 26 skipped, 0 failed | 2m 08s |
| frontend `node --test` | 587 passed, 0 failed, 47 suites | 6.7s |
| `tsc --noEmit` | clean | — |
| browser, default theme | 76/76 | — |
| browser, `midnight` | 76/76 | — |

`tests/cockpit_v4` collects **3062** against **2863** at `bef72b6` — 199
tests added, none removed — and `tests/frontend` 52 against 51. The four
skips are the same four as at `bef72b6`; none is new, and no test was
converted into one. V3 is untouched at 564 passed, 26 skipped.

Both suites exited 0.

## 28 · Verdict

**READY FOR LIVE INTELLIGENCE UAT — PROVIDER APPROVAL REQUIRED**

The mechanics are verified and the standards a strong credit-risk analyst is
held to are now stated where the analyst reads them. Whether a live model
meets them is the one question this round could not ask, and it is the
question the next one exists for.

One defect is open and logged: D-002, with the observability to answer it
added and not yet exercised.

The one finding worth carrying into the next round out of order: §10. The
card that stands between this product and somebody's money accepted a price
nobody had checked, and had never been tested. It is tested now.
