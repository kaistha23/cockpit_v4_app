# Cockpit V4 — the mathematical query engine

Branch `claude/cockpit-single-agent-v4-h8fsbq`. No pull request, no merge,
no V3 module modified, no paid provider call.

**Verdict, stated first: MATHEMATICAL QUERY ENGINE — READY FOR LIVE UAT.**
M01–M15 all complete *through final answer publication*, with every
calculated figure recomputed by the server and cross-checked against an
independent pandas oracle. §15 states precisely what that does and does not
cover.

---

## 1. HEAD

| | |
| --- | --- |
| Started from | `b4ac6aa` |
| Merged in mid-round | `9d1845d` (another session's defect log, D-001) |
| Now at | `1a64bdd` |

`9d1845d` arrived on the branch while this round was in progress. It was
rebased under this work rather than forced over — see §14.

## 2. The live mathematical failure, root cause

The question was *"What is total exposure at default by sector in the latest
quarter?"* and the analysis was **correct**: understood, EAD resolved to
`ead_reported`, latest quarter resolved from the calendar, SQL bound,
executed, twelve rows returned. Publication was then refused twice:

```
claim 'total_ead' names row 'all sectors', which is not in artifact ...
claim 'total_facilities' names row 'all sectors', which is not in artifact ...
claim 'top4_pct' names row 'top 4 sectors', which is not in artifact ...
```

**Root cause: `finalization._check_claim` required every numeric claim to
resolve to one physical result cell**, via `_locate(rows, row_key, column)`.
A total across twelve sectors and the share carried by the largest four are
both real numbers a credit officer needs, and neither is a cell, because the
query grouped by sector and produced no total row. The analyst had exactly
one move left — invent a row name — and inventing it is what got the answer
thrown away after the work was already done correctly.

This was never an SQL failure. The SQL was right and its result is still
right.

## 3. The new evidence contract

Two claim kinds, and **exactly one** applies to any claim. Sending both, or
neither, is refused by name.

**DIRECT** — the value appears in one executed result cell:

```json
{"claim_id": "it_ead", "decimal_value": "5231.58", "unit": "SAR million",
 "evidence": {"artifact_id": "art-…", "row_key": "r0",
              "column_id": "ead_reported_sar_mn"}}
```

**DERIVED** — the value is calculated from cells that exist:

```json
{"claim_id": "top4_pct", "decimal_value": "83.61…", "unit": "percent",
 "derivation": {"operation": "percentage", "operands": [
   {"artifact_id": "art-…", "column_id": "ead_reported_sar_mn",
    "row_ids": ["r0", "r1", "r2", "r3"]},
   {"artifact_id": "art-…", "column_id": "ead_reported_sar_mn",
    "row_ids": ["r0", …, "r10"]}]}}
```

A derived claim is **stricter**, not looser: a direct claim is compared
against a cell; a derived claim has its entire arithmetic redone by the
server before the answer may publish.

There is **no expression language**. No parser, no `eval`, no Python in the
payload, no way to add an operation. Nesting stops at exactly one level — an
operation over cell sets — which covers every derived figure a Cockpit
answer has needed and keeps the schema finite, since the provider's tool
dialect rejects the recursive constructs a deeper tree would need. The
compatibility suite still passes over all seventeen tool-set variants.

## 4. Operations supported

Twelve, closed set, each knowing its own arity.

| Operation | Operands | Meaning |
| --- | --- | --- |
| `identity` | 1 | the single referenced cell, unchanged |
| `sum` | 1 | the sum of the referenced cells |
| `min` / `max` | 1 | smallest / largest of them |
| `count` | 1 | how many hold a value |
| `difference` | 2 | sum(first) − sum(second) |
| `ratio` | 2 | sum(first) / sum(second) |
| `percentage` | 2 | 100 × sum(first) / sum(second) |
| `percentage_change` | 2 | 100 × (later − base) / base |
| `share_of_total` | 2 | part / whole, part's rows must be inside the whole's |
| `weighted_average` | 2 | Σ(value × weight) / Σ(weight), same rows in order |
| `rank` | 2 | 1-based position of one cell within a population |

Everything is `Decimal`, parsed from the artifact's stored form. No floats
anywhere in the engine.

## 5. Validator

`backend/cockpit_v4/derivation.py`, called from `finalization.Finalizer`.

- Every operand artifact must be one **this run produced** and readable by
  **this tenant**.
- Every row id must resolve; a missing one names the real range (`r0 to r10`).
- A row referenced twice is refused — "a cell counted twice is not a sum".
- A NULL operand is refused explicitly: *"a null is not zero"*.
- Division by zero is named, never published.
- Units are checked against the operation: percent / ratio /
  percentage-point confusions are caught.
- The value is recomputed and compared at a relative 1e-9 — enough to absorb
  a rounded display string, nothing wider. A dropped row moves these figures
  by whole millions.

**Direct and derived claims now share one row-id vocabulary.** The packet
publishes `r0`; a direct claim citing `r0` being refused while a derivation
citing `r0` was accepted would have been a trap of our own making.

## 6. Result packet and the claim-building guide

Every successful result now carries `row_ids` aligned with the preview, plus
`how_to_cite_these_numbers`: the artifact id, its columns, its row ids, the
direct rule, the derived rule, the operation table, and two worked examples.
It states plainly:

> Do NOT invent a row to point at. There is no 'total', 'all sectors' or
> 'top 5' row unless one is listed in row_ids above.

This is a contract, stated once before the answer is written — not discovery
by trial and error, which is what cost the live run two rejected answers.

## 7. Answer-only repair

Answer-correction mode already blocked re-running SQL or the catalogue. What
was missing was anything useful to repair *with*: the rejection sent back
prose, and the analyst had never been shown the row ids.

A rejection now carries a correction packet — the artifact it may cite, its
columns, its real row ids **with the rows themselves**, the rejected claims,
the derivation contract, the operation table, and the remaining budget — and
says explicitly not to rerun the query, reread the catalogue, or ask the
user anything. One answer-only rewrite, unchanged from existing policy.

## 8. M01–M15 oracle results

Computed with pandas straight from the pinned Parquet; nothing imports the
query path. Release `v4-saudi-20q-v1`, latest quarter 2026Q2.

| | Oracle |
| --- | --- |
| M01 | 11 sectors, total EAD 20,720.34, 59 facilities, top sector IT at 25.2% |
| M02 | book ECL 119.65, top five hold 82.74 (69.2%) |
| M03 | 2 sectors with Stage 2, total 272.08, **shares sum to exactly 1.0** |
| M04 | top ten ECL 71.75 of 119.65 = 59.97% |
| M05 | five largest sectors hold 14,761.24 of 20,720.34 = 71.2% |
| M06 | 11 sectors, total Stage 2 change +135.36 over the year |
| M07 | 2 sectors where ECL outgrew EAD; no undefined (zero-base) growth |
| M08 | book delta −69.70; **contributions sum to exactly −69.70** |
| M09 | 10 borrowers with higher ECL, total increase +7.63 |
| M10 | 1 borrower both downgraded and into Stage 2 |
| M11 | portfolio EAD 20,720.34, ECL 119.65, coverage 0.577%, Stage 2 1.31% |
| M12 | Construction, latest vs a year ago |
| M13 | 1 sector with rising ECL and weakening coverage |
| M14 | top ten carry **59.9658%** of book ECL |
| M15 | 11 sectors ranked by top-three concentration |

## 9. M01–M15 pipeline results

The real loop, nothing short-circuited: generation → `execute_analysis` →
SQL validation and bind proof → DuckDB → result packet → generation →
`finalize_response` → evidence validation → published answer.

The scripted analyst builds its numbers **from the result packet** — it reads
the published row ids and cites those, and never reads the oracle. So a
published figure matching the oracle afterwards is the pipeline agreeing with
an independent calculation, not a test agreeing with itself.

| | State | Gens | Claims | Derived | Table | Chart | ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M01 | COMPLETED | 2 | 2 | 2 | yes | bar | 564 |
| M02 | COMPLETED | 2 | 2 | 2 | yes | bar | 46 |
| M03 | COMPLETED | 2 | 2 | 2 | yes | bar | 49 |
| M04 | COMPLETED | 2 | 2 | 2 | yes | bar | 44 |
| M05 | COMPLETED | 2 | 2 | 2 | yes | — | 54 |
| M06 | COMPLETED | 2 | 2 | 2 | yes | bar | 51 |
| M07 | COMPLETED | 2 | 1 | 1 | yes | — | 48 |
| M08 | COMPLETED | 2 | 2 | 2 | yes | bar | 49 |
| M09 | COMPLETED | 2 | 2 | 2 | yes | bar | 50 |
| M10 | COMPLETED | 2 | 1 | 1 | yes | — | 61 |
| M11 | COMPLETED | 2 | 4 | 4 | yes | — | 55 |
| M12 | COMPLETED | 2 | 4 | 4 | yes | — | 47 |
| M13 | COMPLETED | 2 | 1 | 1 | yes | — | 53 |
| M14 | COMPLETED | 2 | 2 | 2 | yes | — | 44 |
| M15 | COMPLETED | 2 | 1 | 1 | yes | bar | 52 |

**15/15 published. Two generations each — no answer-repair round needed.**

M01 is checked in full against the question as asked: the quarter is stated,
the sectors and their order match the oracle row for row, the total is a
`sum` derivation over the eleven real rows, the top-four share is a
`percentage` derivation — which is exactly the answer the live run was trying
to give.

## 10. Table and chart validation

A table names an artifact and columns and is served the stored rows, so it
**cannot** misreport a number — that is a property of the design, not a check.
What it could do is name a column that does not exist, which renders as an
empty column and reads as missing data rather than as a mistake. Table
columns are now checked against the artifact.

Charts already had column and artifact checks. Added: where an answer both
**claims a ranking** ("top", "largest", "smallest", "rank") and publishes a
chart, the charted measure must actually be ordered. That catches a query
that forgot its `ORDER BY` under an answer that says "the largest", which
nothing else would see.

The UI collected evidence links only from `evidence.artifact_id`, so a
derived claim would have shown a calculated figure with nothing to open. It
now collects the artifacts a derivation consumed.

## 11. Catalogue-call counts

**Zero catalogue calls across all fifteen**, two generations each, asserted
per question. Round 8's convergence fix survives the new machinery, and the
starting context is checked for not looking like a catalogue dump.

`finalize_response`'s schema grew 7,575 → 10,228 bytes (whole tool set
23,558 → 26,211, about +11%) to carry the derivation contract. One
consequence is recorded honestly: a failure-injection case that used to
exhaust the call ceiling now exhausts the cost ceiling first. Both are
correct bounded stops and the case still publishes nothing.

## 12. Failure injection

Fourteen ways a derivation can be wrong, each failing **by name** — the
message is the entire repair mechanism the analyst has:

bad row ref (names the real range) · bad column ref (lists the real columns)
· artifact this run did not produce · another tenant's artifact · duplicated
row reference · divide by zero · null operand · share whose part is outside
its whole · percentage declared as a ratio · percentage called a percentage
point · ratio declared as percent · unsupported operation · wrong operand
count · `identity` over many cells · weighted average over mismatched rows.

And the two that matter most: **the live payload is still refused** — an
invented row is not evidence, and loosening that would have been the wrong
fix — while **a deliberately wrong share is refused** even though every row
it cites is real, because the arithmetic is redone.

## 13. Performance

| | |
| --- | --- |
| Recompute a 250-cell derivation | **0.243 ms** |
| M01 end to end, CreditProbe's own time | **50.6 ms** median |

Correctness here is bought with arithmetic, not with another model call.

## 14. Regression and V3

| Suite | Result |
| --- | --- |
| V4 backend | **739 passed**, 0 failed |
| Frontend (`node --test`) | **476 passed**, 0 failed |
| Real Chromium | **42 passed**, 0 failed |
| TypeScript | clean |

New this round: 24 derived-claim tests, 52 mathematical-pipeline tests.

**V3 untouched.** The backend diff for this round is confined to
`backend/cockpit_v4/`; the only frontend edits are the V4 client, the V4
response panel and the V4 event list.

`9d1845d` (another session's D-001 defect log) landed on the branch
mid-round. It was **rebased under** this work, not forced over. Its concern
is load-bearing for this report — if running the suite rebuilds the pinned
release, the oracle and the pipeline could agree perfectly while both measure
data that is no longer on the branch. So the consequence is now **asserted**:
the release is byte-identical across a full suite run, fingerprint
`724fe14954f7ea61` over eleven relations, recorded with the evidence. The
launcher-side cause remains open and is not mine to close.

## 15. Verdict

**MATHEMATICAL QUERY ENGINE: READY FOR LIVE UAT.**

What is established:

- The live failure is root-caused to the evidence contract, not to SQL, and
  fixed at its cause.
- M01–M15 all complete **through final answer publication** — the bar this
  round set. A query that executes but whose answer is rejected would have
  been a failure; none is.
- Every calculated figure is recomputed by the server from the stored
  artifact, and the ones the oracle independently computes match it.
- A wrong figure is refused even when every row it cites is real.
- The numbers are measured against a release proven stable across the run.

What this does **not** establish, and cannot without a credential:

- That Opus, unprompted, writes derivations in this shape. The contract is
  now stated in the result packet and in the tool schema, and the correction
  packet catches a first attempt that gets it wrong — but the analyst turn is
  scripted here, so what is proven is that the machinery accepts a correct
  answer and refuses a wrong one, not that the model produces the former
  first time.
- Anything about prose quality. Unchanged from the overnight round: insight
  and clarity remain unscored.

**The rest of the Cockpit is not certified by this.** This verdict covers the
mathematical answer path only. The overnight report's NOT READY stands for
live commissioning as a whole, and the thread/landing/sharing defects the
round deliberately left alone are still open.

## 16. Mac pull and start

```bash
cd ~/path/to/IPM_V2
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull --ff-only origin claude/cockpit-single-agent-v4-h8fsbq
# expect HEAD = 1a64bdd (or later)

# one-time, if the release is not yet built locally
python3 scripts/cockpit_v4/seed_release.py

# start the stack (API + UI on their own ports)
./scripts/cockpit_v4/START_COCKPIT_V4.command
# or:  python3 scripts/cockpit_v4/start.py
#      python3 scripts/cockpit_v4/status.py
#      python3 scripts/cockpit_v4/stop.py
```

Live UAT needs `ANTHROPIC_API_KEY` exported in the shell that starts the API.
**Do not paste it into a file in the repository.** Nothing in this session
asked for it, used one, or made a paid call.

First question to ask, because it is the one that failed:

> What is total exposure at default by sector in the latest quarter?

Expect: the quarter stated, eleven sectors with a table, a ranked bar chart,
a total of **20,720.34 SAR million**, and a top-four share — with the total and
the share arriving as `sum` and `percentage` derivations rather than as
pointers to a row called "all sectors". Check the published figures against
`tests/cockpit_v4/math_bank.py::oracle("M01")`, which is already in the repo.
