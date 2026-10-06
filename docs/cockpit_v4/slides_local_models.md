# CreditProbe Cockpit: choice of AI model, local or cloud. Source for 2 slides

> **Instructions for Claude (paste this whole file into Claude chat):**
> Make **two slides** from this file: Slide 1 *Technical solution*, Slide 2 *Commercial case*.
> Use only the facts below. Do not invent numbers. Any figure marked **[FILL IN]** must stay
> as a visible placeholder. Keep the status honest: the local model has **not yet passed**
> its qualification bar. The re-measurement is pending. Suggested layout and speaker notes
> are given for each slide.

---

## Context (one line, for both slides)

CreditProbe Cockpit answers credit-risk questions on a governed loan book. Until now it ran on
one cloud model (Claude). We have made the model **selectable**: Claude, or an open-weight
model running **locally** on the bank's own hardware. Every governance control stays the same.

---

## SLIDE 1: Technical solution

**Title:** One governed analysis engine, any qualified model

**Subtitle:** Only the model changes. Validation, authorisation and evidence rules do not.

### Diagram (left half of slide): flow, top to bottom

```
User picks model on landing page  ──►  Run is PINNED to provider + model (audited)
                                             │
                            ┌────────────────┴────────────────┐
                     Claude (cloud)                 Local model (oMLX, on-prem)
                     writes SQL directly            fills a small PLAN of governed names
                                                    → CreditProbe compiles the SQL
                            └────────────────┬────────────────┘
                                             ▼
          SAME checks for both: structure · table access · join grain · bind (EXPLAIN)
                                             ▼
          Execute → stored result → answer figures bound to result cells (server-checked)
```

### Key points (right half, 5 bullets)

1. **Provider layer.** The new oMLX adapter speaks the same tool contract as Claude. It also:
   - repairs malformed tool calls, or re-asks the model;
   - enforces structured output;
   - limits how many requests run at once;
   - can record traffic for offline replay.
2. **Per-run model pinning.** Each run stores its provider and model. That pair is part of the
   run's audit trail, its cost record and its de-duplication key. Users keep a saved preference.
   Unqualified models are marked in the dropdown.
3. **Plan → SQL compiler, for local models.** The model chooses only from lists built from the book:
   - measures, dimensions and filter values;
   - the period (latest, prior, year-ago, last N);
   - a comparison with an earlier period.

   Code then writes the SQL. It is one self-contained query per table with no joins, so nothing
   can fan out and the model cannot use a column that doesn't exist.
4. **Server-drafted answer figures.** For planned results the server pre-binds every figure to its
   result cell, including totals and shares. The model writes prose around placeholders, and every
   number is re-checked against the stored result before it is published.
5. **Fixes that help every model.** We fixed:
   - schema, prompt and parser that disagreed with each other (field names, allowed values,
     step limits);
   - SQL error messages that hid the database's own hint, which is now passed back;
   - repair being blocked after a partly failed batch;
   - catalogue search that couldn't find columns, only tables.

### Footer strip: how we know (measurement, not trial and error)

- **Golden test set:** 16 credit questions (8 corporate, 8 retail), each with hand-written correct
  SQL. Every model is scored on:
  - completion rate and time;
  - **result match**: does the answer contain the correct figures?
  - **answer grounding**: are the narrative's numbers traceable to the evidence?
- **Pass bar to qualify a local model:** ≥ 80% completed · median ≤ 90 s · result match ≥ 0.8 ·
  grounding ≥ 0.95.
- **Baseline before these fixes:** Qwen3.6-35B (local) completed **4 of 10**, averaging
  **131 s**. Re-measurement with all fixes is **pending**.
- The full automated regression suite passes. Approach adapted from our slm_v1 research, where a
  small local model reached 0.89 result accuracy using the plan-then-compile pattern.

### Speaker notes (Slide 1)

The design rule was that a weaker model may reach fewer answers, but never a less governed one.
The local model doesn't write free SQL. It picks governed names from lists, and our code writes
the query. Every query, from either model, goes through the same checks, and every published
number is recomputed from the stored result. About half of the earlier "model mistakes" turned
out to be contradictions in our own contract. Fixing them helps Claude too. We judge success on
a fixed question set with known answers, not on demos.

---

## SLIDE 2: Commercial case

**Title:** Deployment choice without a governance trade-off

**Subtitle:** Cloud, on-prem or hybrid, decided per question, with the same audit trail

### Three deployment options (three columns or cards)

| | **Cloud (Claude)** | **Local (open-weight, on-prem)** | **Hybrid** |
|---|---|---|---|
| Where data is processed | Cloud model provider | Bank's own hardware; nothing sent out | Per question / per user |
| Running cost | Per-token API fees, **[FILL IN: cost per question]** | No per-token fees; hardware cost **[FILL IN]** | Mix |
| Best for | Complex, multi-step analysis | Routine questions, data-residency-sensitive books | Most institutions |
| Status | In production use | Built; **qualification pending** | Available once local model qualifies |

### Value points (4 bullets)

1. **Data residency and sovereignty.** With the local option, questions and loan-book data never
   leave the bank's infrastructure. This answers a common procurement and regulatory objection.
2. **Cost control.** Routine questions can run with no per-token fees. Cost becomes a fixed
   hardware cost instead of usage-based spend. **[FILL IN: volume × cost comparison]**
3. **No vendor lock-in.** The model is a setting, not a rebuild. New open-weight models can be
   qualified against the same golden test and switched on without changing governance.
4. **Same assurance whichever model runs.**
   - Every run records which model answered.
   - Unqualified models are flagged.
   - Every published figure is bound to evidence and checked by the server, not by the model.

### Risks and next steps (small box)

- **Qualification:** the local model must pass the bar (≥ 80% completed, ≤ 90 s median). The
  baseline was 4/10 at 131 s. The re-run with all fixes is next.
- **Speed:** local answers are currently slower than cloud. Hardware sizing **[FILL IN]**.
- **Coverage:** the main Cockpit analysis path follows the selected model today. Older assistant
  paths still use Claude, and migrating them is planned.
- **Next:** golden re-run → qualification decision → pilot with one book.

### Speaker notes (Slide 2)

The commercial point is choice without compromise. Banks that can't send loan-book data to a
cloud model can still use Cockpit, and banks that can may keep Claude for hard questions while
moving routine ones on-prem. Because governance sits in our engine and not in the model, we can
add or swap models as the open-weight market improves without re-certifying the controls. We're
being explicit that the local model isn't yet qualified. The next milestone is a measured pass
on the golden question set.

---

## Facts sheet (do not exceed these claims)

- Local model: Qwen3.6-35B-A3B (open-weight), served by oMLX, an OpenAI-compatible local server.
- Cloud model: Claude (Anthropic).
- Model choice: a landing-page dropdown listing both. The choice is pinned per run and saved per
  user.
- Baseline (before fixes): 4/10 golden questions completed, 131 s average. Post-fix result:
  pending.
- Earlier tool-calling checks on the local model passed 4/4 scenarios and 5/5 repeat trials.
- Pass bar: ≥ 0.8 completed, median ≤ 90 s, result match ≥ 0.8, answer grounding ≥ 0.95.
- Not yet true: local-model qualification, legacy-path migration, any cost-saving figure.
