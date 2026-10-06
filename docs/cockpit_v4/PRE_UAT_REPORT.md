# Cockpit V4 — final pre-UAT cleanup

Branch `claude/cockpit-single-agent-v4-h8fsbq`. Starting HEAD `870c5fd`,
verified against the remote before anything was changed. Not merged. V3
untouched. No paid provider call was made and no key was asked for.

---

## 1. The defect, and what it is now

A display class anyone may override is not a policy, it is a default.

`PERMITTED[MONETARY_AMOUNT]` was `(0, 1, 2, 3)`. An analyst declaring
`display_precision: 2` on an amount produced an answer that read, on one
screen:

| Surface | Before | Now |
| --- | --- | --- |
| Prose | `SAR 7,013.12 million` | `SAR 7,013 million` |
| Figures list | `SAR 7,013.12 million` | `SAR 7,013 million` |
| Table cell | `SAR 7,013 million` | `SAR 7,013 million` |
| Chart label | `SAR 7,013 million` | `SAR 7,013 million` |
| Chart tooltip | `SAR 7,013 million` | `SAR 7,013 million` |

All three renderings were policy-legal, which is exactly why it had to be
fixed in the policy rather than at any one rendering site.

## 2. The governed rule

| Class | Decimals | Governed | Example |
| --- | --- | --- | --- |
| Monetary amount | 0 | yes | `SAR 40,599 million` |
| Percentage | 2 | yes | `56.70%` |
| Probability (PD, LGD) | 2 | yes | `4.33%` |
| Percentage point | 2 | yes | `1.23 pp` |
| Ratio | 2 | yes | `1.57x` |
| Count / integer | 0 | yes | `412` |
| Rating, IFRS stage, period | — | yes | `BBB+`, `Stage 2` |
| Unknown unit | 2 | **no** | `1.57` |

**Governed means the class decides and nobody else does.** `PERMITTED` now
lists exactly one precision for each of these, and `GOVERNED` is derived
from that fact rather than maintained beside it, so the two cannot drift.

`UNKNOWN` is the one class left open, on purpose: nothing named the unit, so
there is no business rule to apply and a declared precision is the only
signal available. It is still bounded, and machine precision still never
reaches a reader.

A metric that genuinely needs different places gets them by being classified
differently — a coverage ratio is not an amount — or by a governed rule added
to `DECIMALS` and `PERMITTED`, in one place, deliberately. It does not get
them by a model asking mid-answer.

## 3. Opus cannot override it, and is not made to argue about it

An incompatible `display_precision` is **ignored mechanically, never
refused**. `display.resolve_decimals(unit, declared)` returns the governed
precision, `NumericClaim.precision()` calls it, and `precision.check()` calls
it before comparing anything. The refusal branch that used to say
"CreditProbe allows 0, 1, 2, 3 for a monetary amount" is gone.

Refusing would have cost a model turn to change two characters of
presentation in an analysis that was already right — the same mistake as the
rounding refusal this contract exists to undo. `test_one_metric_one_format_
across_every_surface[2]` drives the whole run with `display_precision: 2` and
asserts `len(provider.sent) == 2`: one action, one answer, no correction.

The analyst's guidance now says so plainly: *"How many decimal places a
figure shows is not yours to set … a display_precision sent anyway is ignored
rather than argued with."*

## 4. The cross-check is about truth, not presentation

These were one question and are now two.

* **Is this the value?** A cross-check is accepted as the canonical value, or
  as that value correctly rounded to *any* number of places. The analyst may
  round its own working however it likes.
* **How is it written?** The display class answers, always.

Nothing about arithmetic was loosened. A number that merely *rounds* to the
right answer — `40599.1699`, which rounds to `40599.17` and is not this value
at any precision — is still refused, and so are `40599.18`, `40.60`
(billions without dividing) and `0.8361` (a percentage sent as a proportion).
All four are covered.

## 5. One formatter

`display.format_value` is the only implementation. `precision.format_value`
delegates to it; `finalization._render` (prose), `_cell` (table cells and
chart points) and the correction packet all reach it. The frontend does no
business formatting: chart geometry is SVG arithmetic, the elapsed clock is
seconds, and `claim-display.ts` renders the server's `display_value` and only
falls back — to the precision the server published — when there is none.

## 6. `decimal_value` remains non-display

Unchanged from `870c5fd`. It is the analyst's optional lossless cross-check,
published for audit and never rendered. Reader-facing components use
`display_value`.

## 7. Regressions added

`tests/cockpit_v4/test_governed_precision.py`, 32 cases:

| Ask | Covered by |
| --- | --- |
| A. canonical `7013.1167117986615` reads `SAR 7,013 million` in prose, table and chart tooltip | `test_one_metric_one_format_across_every_surface` |
| B. a model requesting 2dp on money still gets 0dp, with no retry | same, `[2]` and `[3]`, asserting two provider calls |
| C. PD formats at 2dp | `test_the_policy_worked_examples`, `probability_0_1` |
| D. LGD formats at 2dp | same, `fraction_0_1` |
| E. stage share formats at 2dp | same, both the fraction and the percent spelling |
| F. counts remain integers | `test_a_count_cannot_be_talked_into_decimals` |
| G. no four-or-more-decimal debris on a rendered analytical screen | `test_one_metric_one_format_across_every_surface` server-side, and `a published figure is written once, the way a credit paper writes it` in the browser, over the transcript, table, chart and every tooltip |

Every one of them fails on `870c5fd` and passes here. Additionally, the
browser test asserts no amount carries decimals anywhere on either the chart
or the table screen, and that the amount the prose quotes is an amount the
table shows.

## 8. Tests that encoded the old policy

Eight assertions asserted the behaviour being overturned and were rewritten
to the governed rule, not deleted:

* `test_display_precision.py` — the live `40,599.17` cross-check is still
  accepted and now publishes as `SAR 40,599 million`; a ratio and an amount
  are governed to different places and neither is negotiable; a count asking
  for decimals gets none *and is not refused over it*.
* `test_derived_claims.py` — a total asking for 2dp renders at 0; a share
  asking for 1dp renders at 2.
* `test_release_isolation.py` — an amount is written the same way in every
  currency, at zero places.
* `test_math_pipeline.py` — the M01–M15 harness may still ASK for a
  one-place share, and the published answer must come back at two without
  spending a turn. Its own `assert precision in allowed_precisions` was the
  harness encoding the old policy about itself.

`SAUDI_ROUND_REPORT.md` keeps its historical table with a superseded note
rather than being rewritten; `DISPLAY_PRECISION.md` carries the policy in
force; `CONVERSATION_UX_REPORT.md`'s open observation is marked resolved.

## 9. Visual pass

**The attached screenshots did not arrive.** As in the previous round, the
message carried no image content — nothing to compare against. The pass below
is therefore against the written checklist in §8 of the brief, and **fidelity
to the old Cockpit's visual design remains unverified.** Re-attach them and I
will do the comparison.

| Checked | State |
| --- | --- |
| Dedicated thread page | `/cockpit/thread/[threadId]`, a real URL, back and forward work |
| Question/title at top | the question names the thread; a seeded thread is named after its case |
| Conversation transcript | user turns right, answers left, scrollable, restored on reload |
| Broad workspace | `max-w-[90rem]`, verified at 1440, 1280, 834 and 390 |
| Rename | in the header, persists to the server |
| Share | header and per-answer; delivery line never claims "Sent" without a transport |
| Add to Project | **withheld** — no project endpoint in this runtime; see §10 |
| Trace | header link and a per-turn in-place replay |
| Useful chart/table | server-rendered, ranked, sorted on canonical values |
| Chart/Table toggle | shown only when both are genuinely useful |
| Suggested follow-ups | chips under the answer, they ask when clicked |
| Follow-up composer | sticky at the bottom, Enter asks, Shift+Enter newlines, Depth selector |
| Process trace | "Show process" per turn, with substeps |
| Live ticking seconds | anchored on the server's elapsed figure, never an independent clock |

One change came out of this pass: a seeded investigation thread was titled
"New conversation" until somebody typed in it. It is now named after the
attention item's headline — no model call, and the first question does not
overwrite it.

Screenshots, all four refreshed from this HEAD, in `docs/cockpit_v4/evidence/`:

1. `cockpit_v4_landing.png` — Cockpit Home
2. `thread_analytical_chart.png` / `thread_analytical_table.png` — the
   EAD-by-sector thread, chart and table
3. `thread_multi_turn.png` — a multi-turn conversation
4. `thread_investigation.png` — the seeded Manufacturing covenant-breach
   thread, now showing the case, a question asked from it, and the answer

## 10. Add to Project

Still withheld, still not faked. `grep -c project backend/cockpit_v4/routes.py`
returns `0`: Projects are a main-CreditProbe surface and this isolated V4
runtime serves the Cockpit API and nothing else. The investigation panel says
so in a sentence and offers the V4-native equivalent, which works.

## 11. Analytical orchestration

Unchanged. Compact action context, compact finalization, one-Opus
architecture, claim-driven numeric rendering, the Saudi release, SAR million,
release fingerprinting, separate recovery budgets, catalogue convergence and
the mathematical engine are all as they were at `870c5fd`. The diff touches
`display.py`, `precision.py`, two field docstrings in `contracts.py`, one
guidance string in `execute_tool.py`, and two lines in `routes.py`.

## 12. Final gate

| Suite | Result |
| --- | --- |
| Cockpit V4 backend (`tests/cockpit_v4`) | **1,117 passed, 2 skipped, 0 failed** |
| — of which numeric-format regressions | 32 passed |
| — of which M01–M15 (`test_math_bank_rendering`, `test_math_pipeline`) | all passed |
| — of which thread UX (`test_thread_transcript`) | 16 passed |
| Frontend unit (`npm test`) | **500 passed, 0 failed** |
| Browser (real Chromium, real UI, real V4 API, real DuckDB) | **59 passed, 0 failed** |
| V3 regression (`tests/cockpit_agentic`) | **564 passed, 26 skipped, 0 failed** |

V3 is identical to the previous two rounds. UI latencies, unchanged within
noise: thread opens 240 ms, first process event 244 ms, chart and table 49 ms,
transcript restored 324 ms, follow-up visible 83 ms.

## 13. Verdict

**PRE-UAT COCKPIT V4: READY.**

* Monetary values show 0dp consistently, and no model can change that.
* Percentages, probabilities, point movements and ratios show 2dp; counts are
  integers.
* Prose, figures list, table, chart labels and chart tooltips agree, asserted
  server-side and in a real browser.
* No existing V4 regression fails, and V3 is untouched.

One qualification, stated plainly rather than buried: **the interaction
references were not attached to the message, so the thread UX was checked
against the written checklist and not against the screenshots.** Every item on
that checklist is present and working. Visual fidelity to the old Cockpit's
design is the one thing this round could not verify.

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

# start
python3 scripts/cockpit_v4/start.py

# confirm
python3 scripts/cockpit_v4/status.py
```

Open `http://127.0.0.1:5414` and ask *"What is the EAD by sector for the
latest quarter?"* Every amount on the screen should read `SAR 7,013 million`,
with no decimals anywhere and the same string in the sentence, the figures
list, the table and the chart.
