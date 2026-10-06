# Cockpit V4 — dual-domain execution and product completion

**Branch** `claude/cockpit-single-agent-v4-h8fsbq`
**Started from** `609e572a765125e4fcf13a4add97edf5bac54304`
**V3** untouched: `git diff` over `backend/cockpit_agentic/` is empty, and its
suite is 564 passed / 26 skipped, the same as at the start of the round.

The previous round refused Retail questions rather than let one run against
corporate relations. That refusal was correct and it is now unnecessary.

---

## 1. Which book a run reads

`backend/cockpit_v4/analytical_runtime.py` resolves one run's book from the
persisted run and its thread, and from nothing else: not the frontend, not the
Home switch as it currently stands, not a startup default, and not whichever
catalogue happened to be cached first.

The RELEASE decides, because the release names the bytes.

| situation | outcome |
| --- | --- |
| run accepted against a domain release | that domain's book, opened per run |
| label and release disagree | refused, `DATA_UNAVAILABLE`, zero model calls |
| release rebuilt since acceptance (fingerprint differs) | refused, naming both fingerprints |
| run accepted against the pre-domain release | THAT release, and only when it is the one this process was configured for |
| anything else | refused; nothing is substituted |

Caching is keyed by tenant, domain, release AND fingerprint. A release id is a
name; two builds share it and hold different numbers.

## 2. A V4 execution surface

`backend/cockpit_v4/sql.py` replaces V3's executor on the V4 path. V3's could
not serve two books, and not for want of a parameter — three of its controls
are corporate constants compiled into the module:

* it filters every table by a module-level `DOMAIN`;
* `multiplication_risk` reads a hard-coded list of corporate join pairs, so
  against `retail_account_month` it finds nothing — not "no risk", but
  "nothing it knows about", which is a diagnostic that silently stopped
  firing;
* every refusal says "its own twenty-quarter corporate domain only".

The V4 layer reads the join keys, the grains and the additive measures off the
session's own catalogue, and a step naming the other book's relation is
refused by name with its owner stated. V3 is not modified and not imported
there.

## 3. Semantics per book

A customer is a borrower in Corporate and a retail customer in Retail. Sector
is the Corporate segment dimension and product the Retail one. Period
vocabulary follows the reporting frequency, so a monthly book is not described
in quarters. `semantics.py` carries a canonical term table per book, and the
packet states the book, its release and its fingerprint.

## 4. The temporary Retail refusal is gone

`analysis_supported` opens the book instead of consulting a hard-coded tuple.
Askable is established per request: a runtime that cannot open a release still
refuses the question at acceptance, before a model call is paid for, and one
book being unopenable does not disable the other.

---

## 5. The books a credit officer would recognise

Reading the generated data as a credit reader would, several figures were not
defensible, and every answer built on them inherited that.

### Retail had no tail

Arrears were a threshold on a smooth stress index, and a smooth index has no
tail: the worst account in twelve thousand reached eighty days past due and
SEVEN ever defaulted. Every question about arrears, Stage 3, write-offs, cures
or non-performing coverage answered "approximately nothing".

Arrears are now a roll-rate process — entry at a hazard set by product,
account fragility and stress; then cure or roll thirty days deeper; charge-off
and exit at two hundred and ten days.

### Corporate was twenty names priced off the wrong curve

Thirteen sectors over twenty borrowers meant "EAD by sector" returned thirteen
rows of which most held one obligor. The book is ninety-seven obligors over
two hundred and ninety facilities. Its average Stage 1 twelve-month PD was
9.4% — a CCC number on a performing book — and a defaulted exposure published
a PD of eight per cent beside its own default flag. PD now comes from a rating
master scale and a defaulted exposure carries a PD of one.

### Covenants tested a constant against a privately recomputed ratio

The leverage ceiling was breached by half the book from month one and the four
floors could not be breached by ANY borrower at any point in the window. A
covenant now tests the figure the book publishes against a threshold set from
where that borrower started.

### What the two books now report (2026-08)

| | Corporate Credit | Retail Credit |
| --- | --- | --- |
| Exposure at default | SAR 208,418 million | SAR 4,084 million |
| Recognised ECL | SAR 6,666 million | SAR 74 million |
| Coverage | 3.20% | 1.82% |
| Stage 1 coverage | 0.39% | 0.64% |
| Stage 2 coverage | 3.31% | 9.14% |
| Stage 3 coverage | 46.97% | 50.19% |
| Stage 2+ share of EAD | 35.8% (from 5.8%) | 6.8% (from 1.9%) |
| Ninety-day arrears | 9 of 290 facilities | 343 of 11,517 accounts (2.98%) |
| Obligors / customers | 97 borrowers, 290 facilities | 8,755 customers, 11,517 accounts |
| Rows | 19,340 | 611,474 |

Both generators are deterministic: two builds in one process produce
byte-identical frames, and `seed_domains.py --verify` re-checks the published
bytes against their fingerprints.

---

## 6. New surfaces

### The ECL panel (`GET /ecl?domain=`)

The stage profile, and a decomposition of the month's ECL movement into new,
closed, stage migration, exposure change and risk change. Every exposure is in
exactly one group, so the components sum to the movement EXACTLY; the residual
is published rather than absorbed. No model call.

### Export (§30–§32)

Three documents, each carrying a lineage block — book, release, fingerprint,
tenant, run, artifacts, code digests, row count:

* `GET /runs/{id}/export` — the analysis as Markdown;
* `GET /runs/{id}/artifacts/{id}/export` — one result as CSV, every row by
  default, with the reader's published string beside each canonical value;
* `GET /runs/{id}/charts/{n}/export` — one chart as SVG, drawn from the points
  the server already supplied.

When the displayed subset IS the whole result the document says so, rather
than attaching a caveat that is not true.

### The schema browser (`GET /schema?domain=&relation=`)

Two cards on `/cockpit/data`: each book's release, fingerprint, period range,
relations, row counts, joins with their repetition warnings, and a drill into
any relation for its columns, types, units and definitions. Asking one book
for the other's relation is refused by name here too.

---

## 7. Defects found and fixed in this round

| # | Defect | Where it would have shown |
| --- | --- | --- |
| 1 | The analytical path read one process-wide catalogue | a Retail thread's answer computed from corporate relations |
| 2 | Join-multiplicity diagnostic silently inert on retail relations | a double-counted total, reported confidently |
| 3 | Refusals named "the corporate domain" in the Retail book | a reader told the wrong thing about their own book |
| 4 | Attention ranked amounts against the segment's own prior value | SAR 55m outranking a sector that had just added more than that |
| 5 | "Collateral cover rose to 138%" | deterioration announced as good news |
| 6 | `feed.ownership` dropped when the per-domain engine landed | the Cockpit home page blank on every render |
| 7 | `possible_drivers` / `what_to_review_next` dropped likewise | the page blank on every card CLICK |
| 8 | `find_item` searched movement cards only | 404 on all four ECL highlight cards |
| 9 | ECL highlights were a short dict where the route expected a card | 500 on Investigate Further, had the 404 been fixed alone |
| 10 | The drawer printed "This segment has  borrowers at 2026-08" | a hole where a count belongs, wrong noun for Retail |
| 11 | `inspect_catalog` published an empty aggregation | "not stated" about a column the catalogue knows is additive |
| 12 | `field_packet` dropped alias terms | the analyst never told that "segment" resolves to `sector` |
| 13 | Retail had no non-performing population | every arrears question answered "approximately nothing" |
| 14 | Corporate Stage 1 PD averaged 9.4% | a coverage ratio no credit reader would accept |
| 15 | A defaulted exposure published a PD of 8% | a contradiction of the flag in the next column |
| 16 | Four of five covenant types could never breach | "which covenants are in breach?" had one answer |
| 17 | Corporate: 13 sectors over 20 borrowers | sector and borrower were the same dimension |
| 18 | Health said "dashboard not in this runtime" over a live dashboard | a header a reader learns to ignore |

Six through ten were found by the browser suite and by nothing else: each sits
exactly where an engine's contract meets a component's expectations, and
neither side's unit tests cover the join. The contract test added in
`test_attention_domains.py` now enumerates every field the panel AND the
drawer dereference.

---

## 8. Evidence

| Suite | Result |
| --- | --- |
| V4 backend (`tests/cockpit_v4`) | **1,408 passed, 2 skipped** |
| V3 regression (`tests/cockpit_agentic`) | **564 passed, 26 skipped** — unchanged |
| Frontend (`npm test`) | **502 passed** |
| Browser (real Chromium, real UI, real API, scripted analyst) | **67 / 67** |
| Flake matrix: 11 flows × 5 runs | **11 / 11 stable over 55 runs** |
| Question banks | C01–C12, R01–R12, D01–D10 — all passing, each checked against an independent pandas oracle |
| Release determinism | both books rebuild byte-identically; `--verify` passes |

Artifacts: `docs/cockpit_v4/evidence/browser.json`,
`flake_matrix.json`, `dual_domain_performance.json`, and nineteen screenshots.

### Performance, per book (CreditProbe's own time; no provider call)

| | Corporate | Retail |
| --- | --- | --- |
| Session open, cold | 128 ms median / 154 ms p90 | 1,114 ms / 1,431 ms |
| Session open, warm | 0.3 ms | 0.4 ms |
| Dashboard, cold | 44 ms / 64 ms | 103 ms / 139 ms |
| Dashboard, cached | 0.0 ms | 0.0 ms |
| ECL panel, cold | 12 ms / 15 ms | 16 ms / 24 ms |
| Schema browse | 0.0 ms | 0.0 ms |
| One analytical run, end to end | 62 ms / 146 ms | 57 ms / 69 ms |

The figure a reader feels is the retail session opening cold: about 1.1
seconds the first time they switch to Retail, because 611,474 rows are
materialized into the session. Every switch after that is immediate, and
switching back to an already-open book is half a millisecond.

---

## 9. §45 — the global-domain audit, stated in full

`tests/cockpit_v4/test_no_global_domain.py` checks, on the AST rather than on
comments, that no module on the analytical path imports a module-level
`DOMAIN`, reads the process runtime's single catalogue, opens the pre-domain
session, or holds a catalogue, session or release of its own.

**What remains, and why.** One data-bound `DOMAIN` constant still exists:

* `backend/cockpit_v4/__init__.py:25` — `DOMAIN = "corporate_cockpit"`.
* Its one remaining reader is `service.py:181`, which stamps it on the
  release summary of the PRE-DOMAIN release. That release's domain id
  genuinely is `corporate_cockpit`, so the constant is a fact about the
  object it describes rather than a default applied to a request.

No module on the analytical path reads it. `routes.py` and `service.py` are
listed as legacy-by-design in the audit, with the reason recorded there, and
the audit fails if either appears on the analytical path list.

Static display labels — `LABELS[CORPORATE] = "Corporate Credit"` — are not
data-bound globals and are not flagged: they decide what a button says, not
what a query reads.

---

## 10. What is NOT proven here

* **No live provider call was made.** Every analytical test in this round
  scripts the analyst. These suites prove the application's half of the
  contract: the right validation, the right execution, the right book, the
  right numbers, the right events, the right terminal state. They prove
  nothing about answer quality, and no test in the suite claims otherwise.
* **The browser suite runs against a stub analyst**, labelled as such in its
  own evidence file.
* **No Mac UAT has been run.** The screenshots are from Chromium on Linux at
  the viewport sizes listed.

---

## 11. Handoff — the thirty-three items

1. **Branch**: `claude/cockpit-single-agent-v4-h8fsbq`. Nothing merged.
2. **V3**: untouched. `backend/cockpit_agentic/` has no diff in this round.
3. **Start the stack**: `scripts/cockpit_v4/START_COCKPIT_V4.command`.
4. **Publish the books**: `python3 scripts/cockpit_v4/seed_domains.py`.
5. **Verify them**: `python3 scripts/cockpit_v4/seed_domains.py --verify`.
6. **Books**: `v4-saudi-corporate-20m-v1`, `v4-saudi-retail-20m-v1`. Twenty
   months, 2025-01 to 2026-08, SAR million, Saudi Arabia, synthetic.
7. **Switching books** is a server round trip, not a filter: proved in the
   browser by the request it issues.
8. **A thread is pinned** to the book it was opened in. A question sent to a
   thread in the other book is refused 409 with an action to start one there.
9. **A run reads the book it was accepted in**, resolved from the persisted
   run. A rebuilt release refuses the run rather than answering it.
10. **Cross-domain SQL is refused by name**, naming the owning book, at zero
    model cost, in both directions.
11. **Question banks**: C01–C12 (Corporate), R01–R12 (Retail), D01–D10
    (metadata). Each figure is checked against an independent pandas oracle.
12. **The ECL decomposition reconciles exactly**; the residual is published.
13. **Export** produces Markdown, CSV and SVG, each with a lineage block.
14. **The schema browser** is at `/cockpit/data`.
15. **Nothing in the Cockpit writes to a book.** A published release is
    immutable and the audit proves no serving code can publish one.
16. **The Data Builder's twenty-seven write endpoints all work** and none of
    them can reach a Cockpit book: different registry, different directory.
17. **Diagnostics** report each book separately, at `GET /diagnostics`.
18. **Startup** prints one line per book: release, fingerprint, periods,
    relations, and whether it is askable.
19. **The dashboard needs no credential.** A runtime with no verified price
    card serves every card on the Cockpit and refuses questions.
20. **Attention ranks amounts by contribution to the book**, and shares by
    movement × weight in the book.
21. **A card's drivers are associations**, never causes, and the drawer says
    so under them.
22. **Every card can be investigated**, including the four ECL highlights.
23. **A seeded thread carries its book**, its release and its fingerprint.
24. **The money string is the server's.** The page renders what it is given.
25. **Monetary amounts are 0 dp, percentages and probabilities 2 dp.**
26. **A monthly book is described in months.** Nothing calls a month a
    quarter in a packet, a card, or a coverage block.
27. **Performance** is in `dual_domain_performance.json`, with budgets.
28. **Flake**: 11 flows × 5 runs, 11/11 stable, recorded in
    `flake_matrix.json` including per-attempt timings.
29. **Screenshots**: nineteen, in `docs/cockpit_v4/evidence/`.
30. **The known slow path** is the first Retail session open, ~1.1 s.
31. **`analysis_supported` opens the book.** A deployment missing a release
    refuses that book's questions and keeps the other.
32. **No live model call has been made** on this branch in this round.
33. **Next**: a live Mac UAT with a real credential, driving the eleven flows
    in the flake matrix across both books.

---

## Verdict

**COCKPIT V4 DUAL-DOMAIN EXPERIENCE: READY FOR LIVE MAC UAT**
