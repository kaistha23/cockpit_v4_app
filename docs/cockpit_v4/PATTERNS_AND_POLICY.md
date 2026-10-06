# What is in the books, and how to test CreditProbe against it

Written for somebody who wants to sit down in front of the Cockpit and find
out whether it can actually analyse. It states what was planted, which cut
reaches it, and — for each one — the **wrong answer sitting next to the
right one**, because a book where every question has one obvious answer
tests nothing.

Releases: `v4-saudi-retail-20m-v5` (2025-01 … 2026-08) and
`v4-saudi-corporate-20q-v4` (2021Q3 … 2026Q2). Both are synthetic. No real
institution is described, and the credit policy in `credit_policy.json` is
authored for this product rather than taken from anyone's rule book.

Every pattern below is pinned by an oracle in
`tests/cockpit_v4/test_planted_patterns.py`, which reads the published
Parquet with pandas — a different engine and a different code path from
anything the product runs. A tuning change that buries a finding fails a
test rather than being discovered in a demonstration.

Every figure quoted here is read off the **published** release, not off an
intermediate build, and you can reproduce all of them without pytest:

```
python3 scripts/cockpit_v4/release_report.py --patterns
```

---

## Retail

The book gained four dimensions before it gained rows:

| column | values |
|---|---|
| `sub_product` | Fixed Rate / Variable Rate; Salary Advance / Consumer Durable / Debt Consolidation; New Vehicle / Used Vehicle; Classic / Gold / Signature; Instalment 3M / Instalment 6M |
| `employment_type` | Salaried-Government, Salaried-Private, Self-employed, Non-salaried |
| `origination_channel` | Branch, Digital, Partner |
| `delinquency_bucket_fine` | Current, 1-9, 10-19, 20-29, 30-59, 60-89, 90-179, 180+ |

`delinquency_bucket` keeps its five values, and the fine bands nest exactly
inside them: summing the fine bands of a coarse one reproduces it.

### R1 · A product that did not exist a year ago

**Buy Now Pay Later.** No row before **2026-03**, then written fast through
the Partner channel at about three times the card book's entry hazard. By
2026-08 it is 9.8% of accounts and **10.43% 30+** against 4.46% for the
rest of the book.

*Ask:* "What is driving the rise in delinquency?"
*Wrong answer:* Credit Card — large, genuinely deteriorating, and not the
thing that changed.

### R2 · A corner of the card book, not the card book

**Credit Card – Gold held by Non-salaried customers**, from **2026-01**.
Classic and Signature stay flat, so the product line barely moves. **60% of
the cohort's 1-29 population sits in 20-29** from 2026-01 — concentrated,
but not uniformly so, because a cohort with one band and nothing else is a
label rather than a distribution.

*Ask:* "Credit card 1-29 is up this quarter — which sub-bucket is it in, and
who holds those accounts?"
*Wrong answer:* "Non-salaried customers are deteriorating" — one level too
shallow; the other sub-products held by the same employment type are flat.

### R3 · The portfolio and its parts disagree in sign

By **balance** the book improves: 30+ falls from 2.015% to 1.762%. By
**account** it worsens: 4.16% to 5.05%. The improvement is mix — mortgage
goes from 76.6% to 84.3% of balances on a campaign booked from 2025-11.

*Ask:* "Is the book improving?"
*Wrong answer:* the balance-weighted headline, quoted without the mix. Both
numbers are correct; only one is about credit quality.

### R4 · A vintage, not a calendar month

Accounts originated **2025-10 … 2025-12** run at **7.87% 30+** at six to
nine months on book, against **4.18%** for every other vintage at the same
age.

*Ask:* "Is there an origination problem?"
*Wrong answer:* a calendar-time cut, in which the cohort is young and
therefore current.

### R5 · A season is not a trend

Entries into arrears roughly double in **2025-03** and **2026-02** and cure
within two months.

*Ask:* "Did the book deteriorate in February?"
*Wrong answer:* calling a reverting spike deterioration.

### R6 · One region, one sub-product

**Auto Finance – Used Vehicle in Qassim**, from 2025-11: **9.58% 30+**
against **2.27%** for the same sub-product everywhere else. Auto Finance is
the book's *improving* product.

*Ask:* "Is auto finance fine?"
*Wrong answer:* "yes" — true at product level, false in one region.

---

## Corporate

### C7 · Cash flow turns before the rating does

**Building Contracting**: DSCR falls from **2025Q4** — 1.54 at 2025Q3 to
**0.74** at 2026Q2, against 1.56 → 1.39 for the rest of Construction — and
covenant breaches rise with it. The **internal grades lag two quarters** — the cohort is
*less* downgraded than its neighbours until 2026Q2. Construction as a whole
barely moves.

*Ask:* "Was this deterioration visible before it was recognised?"
*Wrong answer:* reading the sector, or reading the rating.

### C8 · A concentration that only exists in the aggregate

**Al Faisaliah Industrial Group** — seven connected names across four
sectors. Aggregate EAD goes **18,365 at 2025Q4 → 63,744 at 2026Q2**; the largest
single name stays at **14,432**, comfortably inside CP-1.1's SAR 25,000
million single obligor limit, while the group crosses CP-1.2's SAR 50,000
million.

*Ask:* "Are we within our single obligor limits?"
*Wrong answer:* an exposure report ordered by borrower, which shows nothing
at all.

### C10 · The product the bank started writing last year

**`supply_chain_finance`** — no facility before **2025Q4**, underwritten
against a programme rather than an obligor. At 2026Q2 it is **4.95% of
exposure**, **11.53% of ECL** and **18.25% stage-3**, the worst product in
the book by a wide margin.

*Ask:* "Which product is driving impairment?"
*Wrong answer:* the borrower cut, which returns the usual names.

---

## Scale

| | before | now |
|---|---|---|
| Retail rows | 815,551 | **1,888,134** |
| Retail customers / accounts | 12,000 / 16,000 | **26,000 / 44,027** |
| Corporate rows | 839,960 | **1,260,267** |
| Corporate obligors / facilities | 3,652 / 12,782 | **5,412 / 21,918** |
| Periods | 20 | 20 (unchanged) |

The DuckDB session limit moved 512MB → 1.5GB on a measurement:
`duckdb_memory()` reported 486 MiB of `IN_MEMORY_TABLE` against a 488 MiB
effective limit, so a group-by had nothing left to build a hash table in.

---

## The credit policy

`credit_policy.json` holds two clause-numbered packs — Retail (`RP-`) and
Corporate (`CP-`) — with thresholds, owners, review dates and, on each
clause, the **levers** it can carry. Thirty clauses across nine sections.

**How it reaches the analyst.** Not as a tool. The turn that holds a result
*requires* `finalize_response`, so a tool offered beside it is a schema the
run pays for and cannot call. Instead the answer turn's context carries the
policy **synopsis** for that book, plus the **clauses the question itself
names**, retrieved server-side at clause grain. Against the standing
question bank that attaches something to about one question in ten, and to
the right ten.

**The books never cross.** A Retail thread cannot receive a Corporate
clause. `CP-1.2` quoted in a Retail answer is a citation that looks checked
and is not, which is worse than no citation.

**A recommendation has to cite.** A narrative that proposes a **policy**
change and names no clause of its own book is refused, the same way a
portfolio number with no evidence is. The trigger is deliberately narrow —
the word *policy* in the same sentence as a change verb, never "recommend"
or "tighten" on their own, because a check that fires on ordinary English
refuses correct analysis. That failure mode cost this product two live
answers already.

*Ask, after any finding above:* "What policy action should we take?"
*What should come back:* the clause id, the rule as written, and the
proposed change. Try it on the Gold cohort (RP-2.2 score cut-offs, RP-1.1
DBR caps, RP-4.1 collections actions by band), on BNPL (RP-3.1 pilot
limits, RP-3.2 partner channel), and on the group concentration (CP-1.1 and
CP-1.2, whose limits the book actually crosses).
