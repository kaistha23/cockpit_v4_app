# Cockpit V4 — dual-domain implementation round

Branch `claude/cockpit-single-agent-v4-h8fsbq`. Starting HEAD `ae387cd`,
verified against the remote before anything was changed. Not merged. V3
untouched. No paid provider call was made and no key was asked for.

---

## 1. The verdict, first

**NOT READY — the analytical execution path is still bound to one release.**

Everything the domain model was blocked on is built: two real Saudi monthly
books, a per-request domain architecture, a wall between them that fails
closed, two dashboards computed from two releases, a switch that changes the
data, and threads pinned to the book they were opened in. What is NOT done is
the last hop: the catalogue the ANALYST is shown and the SQL session its
queries run in are still built from the runtime's single pinned release.

That could have been left quiet, and it would have meant a retail thread
running its SQL against the corporate catalogue and coming back fluent,
wrong, and silent about which book it came from. So it **fails closed**: a
retail question is refused at acceptance, before a model call is paid for,
with a message naming what is available and where the other book can be read
instead. §5 says fail closed; §55 says do not let Retail read Corporate.

The remaining gate items are in §12.

## 2. Domain architecture: before and after

**Before.** `backend/cockpit_v4/__init__.py` defined
`DOMAIN = "corporate_cockpit"` as a module constant. The runtime pinned one
`COCKPIT_V4_RELEASE_ID` behind a process-wide `_CATALOG_CACHE`. One book per
process; "Corporate or Retail?" had nowhere to live but a label.

**After.** A domain is a whole analytical world, resolved once per request
and carried:

| Concern | Where it lives now |
| --- | --- |
| The closed set of books, and parsing one | `domains.py` — `UnknownDomain`, never coerced to a default |
| Everything one request knows about its book | `domains.DomainScope` — release, fingerprint, country, currency, scale, frequency, periods, relations |
| What each book contains | `schema.py` — relations and fields as DATA, keyed by domain |
| One book's authorized catalogue | `catalog.Catalog` — built FOR a domain |
| One book's DuckDB session | `catalog.open_session` — materialises only that domain's relations |
| Where releases live | `lake.py` — V4's own namespace, its own manifest, its own fingerprint |
| Which book this request is about | `domain_resolver.resolve` — thread wins, then selection, then default |
| Per-domain readiness | `domain_resolver.availability` — separately, never substituting |

`DOMAIN` is still exported from `__init__.py` because the superseded
quarterly path and its suite still read it. Nothing in the new data plane
does.

## 3. The two releases

| | Corporate | Retail |
| --- | --- | --- |
| Release id | `v4-saudi-corporate-20m-v1` | `v4-saudi-retail-20m-v1` |
| Fingerprint | `471e8aca088ddd9a7df1f37d6e66bc56b45658a078e9b312c921f7d398c6775e` | `0616f87f09fc4c03c8b8000cc059f261eb25ef8262909d209bd7f06ede05861c` |
| Relations | 4 | 4 |
| Fields | 87 | 86 |
| Rows | 3,940 | 616,160 |
| Entities | 20 borrowers, 59 facilities, 14 groups, 13 sectors | 9,000 customers, 12,000 accounts, 4 products, 8 regions |
| Country / currency / scale | Saudi Arabia · SAR · million | Saudi Arabia · SAR · million |
| Frequency | monthly | monthly |

Built by V4-native generators. V3's generator is quarterly and was not
touched; V3's store hard-codes `domain_id: "corporate_cockpit"` and requires
a V3 feature flag, so V4 publishes into its own lake.

## 4. The twenty months

`2025-01 … 2026-08`, exactly as §7 pins them. Latest `2026-08`, previous
`2026-07`, year-ago `2025-08`, latest three `2026-06`/`07`/`08`. No partial
September: a part-month at the end reads as a collapse in every comparison
drawn against it.

## 5. Corporate schema

`corp_borrower_month` (29 fields) — identity, group, sector, sub-sector,
region, relationship tier; a 19-grade rating with its previous grade, notches
moved and outlook; TTC PD; revenue, EBITDA, debt, cash, net debt; leverage,
DSCR, interest cover, current ratio, EBITDA margin; a qualitative score.

`corp_facility_month` (27) — facility type, limit, drawn, undrawn,
utilisation, EAD; IFRS 9 stage, SICR, default, DPD, PIT and lifetime PD, LGD,
12-month / lifetime / recognised ECL, past-due flag, months in stage.

`corp_collateral_month` (15) — type, market value, haircut, allocated value,
coverage, LTV, valuation age.

`corp_covenant_month` (16) — type, threshold, observed value, headroom,
status, breach, waiver and waiver month.

## 6. Retail schema

Not a renamed corporate table: the grain is customer × account × month.

`retail_customer_month` (20) — segment, region, tenure, accounts held;
behaviour score with its previous value, change, band, previous band and
migration; total EAD and ECL; worst stage and worst DPD.

`retail_account_month` (34) — product, secured flag, origination month,
vintage year, months on book; limit, balance, EAD, utilisation; stage, SICR,
default, DPD, delinquency bucket, PIT and lifetime PD, LGD, three ECL
measures, write-off, recovery, cure flag; the customer's score and band.

`retail_behaviour_month` (19) — utilisation and its change, payment ratio,
missed payments, delinquency streak, balance growth, cash-advance ratio
(cards only), overlimit, inflow change, bureau inquiries, repayment score.

`retail_collateral_month` (13) — secured products only. An unsecured account
has no row rather than a row of zeroes.

## 7. Stories in the data

Not noise; a book of random walks answers every question uselessly.

**Corporate** runs from all-Stage-1 in January 2025 to 42/9/8 by August 2026.
Construction and real estate carry authored stress; chemicals, IT and power
quietly improve. Covenant headroom erodes where leverage rises. Real-estate
security softens through the window while industrial security firms up, so
"coverage fell" and "ECL rose" are separable findings rather than one finding
twice.

**Retail** ends at 20% Stage 2+: credit cards at 43%, personal finance at
18%, mortgages at 7%, auto finance at 2% with a cohort that cures and
improves. The 2024 vintage sits at 31% against 2026 at 8%. Score migration
comes out mixed — 488 deteriorated, 324 improved, 88 stable — because a book
where everyone moves one way teaches nothing about telling signal from level.

## 8. Invariants

`invariants.py` runs BEFORE publication and refuses a book that fails:
unique grain, period continuity inside the declared calendar, governance
columns, currency, probability and LGD bounds, non-negative days and amounts,
valid IFRS 9 stage, score range, score band, rating scale, EAD reconciling to
its components, recognised ECL being the 12-month or lifetime figure its
stage requires, referential integrity, retail account continuity against
origination, collateral scope matching the secured flag both ways, and the
customer roll-up equalling the sum of its accounts.

Percentage fields that COMPARE two quantities — headroom, coverage, LTV,
growth, change — are checked for finiteness rather than bounded to 0–100. A
breached leverage covenant really does read below −100%, and a nearly-repaid
mortgage really is covered many times over; capping those would assert
something false about credit rather than catch something false about data.

## 9. Defects found and fixed in this round

1. **The corporate generator was not deterministic.** It used `hash()` on
   strings for group ids, which Python randomises per process, so two builds
   of identical inputs fingerprinted differently. A fingerprint that moves
   when nothing changed checks nothing.
2. **The retail book was too small for its unit.** At 900 customers the
   attention cards read "up SAR 0 million" — arithmetically true and useless.
   It is a population now.
3. **The generator ramp flattened at both ends.** Smoothstep's slope falls to
   zero at the end of the window, so a book that had plainly changed offered
   two month-on-month findings.
4. **A flat cap of two cards per dimension** fixed a retail page filled with
   four customer segments saying one thing and broke the corporate page,
   which has fewer lenses. Dimensions take turns now, strongest first, and
   the page backfills by score once every lens has had its offer.
5. **Ordering those turns alphabetically** decided, by nothing but the letter
   C, that a collateral card outranked the strongest sector finding.
6. **Both dashboards carried an identical headline** — "Stage 2 and 3
   exposure" — so a reader with two tabs open could not tell them apart.
7. **A foreign tenant reached an unhandled `PermissionError`.** It is a typed
   403 now; an empty feed would have read as "your portfolio is fine".
8. **Home created the thread and then started a run in it**, which silently
   discarded the domain: `startRun` reads it only when opening a
   conversation, so a retail question would have opened a corporate thread.

## 10. What works end to end

* `GET /domains` — both books, each with its own readiness, and which can be
  ASKED as distinct from browsed.
* `GET /attention?domain=` — that book's release, catalogue, session and
  dashboard. The two share no card and no highlight.
* Corporate dashboard families: sector Stage 2, sector ECL, ECL coverage,
  past due, facility type, region, collateral cover.
* Retail dashboard families: product Stage 2, product ECL, delinquency,
  score band, vintage, customer segment.
* Ranking: normalised movement × share of book × family weight; one card per
  segment; dimensions take turns.
* Cockpit Home: a Corporate | Retail control beside the Ask box,
  domain-specific prompt chips, server-named section headings, remembered
  per tab.
* Threads: pinned at creation, carrying domain, release and fingerprint; the
  transcript reports them; the header shows the badge; a follow-up naming the
  other book is refused with 409 and an offer that works.
* Investigate Further seeds a thread in the book the finding came from.

## 11. Test counts

| Suite | Result |
| --- | --- |
| Cockpit V4 backend | **1,218 passed, 2 skipped, 0 failed** |
| — domain model, lake, isolation (`test_domains.py`) | 35 |
| — dashboards against pandas oracles (`test_attention_domains.py`) | 28 |
| — switch and pinning through the API (`test_domain_routes.py`) | 20 |
| Frontend unit | **500 passed, 0 failed** |
| V3 regression | **564 passed, 26 skipped, 0 failed** — unchanged |

Every dashboard figure is recomputed in the tests from the parquet with
pandas: a different engine and no shared helper, because a card that agrees
with the SQL that produced it proves only that the SQL ran twice.

The browser matrix was NOT re-run this round and the screenshots were not
refreshed — both belong with the work in §12.

## 12. Remaining gate items

1. **Analytical execution per domain.** `worker._drive` builds its session
   from `runtime.catalog`. It needs the thread's domain, a V4 catalogue and
   session for it, and `context.build` / `CatalogService` / `ExecuteTool`
   taught the V4 catalogue interface. `ANALYSIS_DOMAINS` in
   `domain_resolver.py` is the one tuple to extend, and this refusal is the
   one thing to delete.
2. **Data Builder** (§30–§34) — two domain cards, schema drilldown, metadata
   questions against the selected catalogue.
3. **Export** (§38–§41) — analysis, table and chart, with domain lineage.
4. **Question banks** C01–C12 and R01–R12 (§47, §48) — these depend on 1.
5. **ECL decomposition and the Stage 2 diagnostic** (§45, §46) — depend on 1.
6. **Browser flows, repeat matrices and the twelve screenshots**
   (§58–§62, §66).

## 13. Preserved

Everything §0 lists is intact and covered by the 1,218 passing tests: the
intent contract and its carrying, DATA_ANALYSIS allowance widening before
execution, thread titles on ask, seeded titles, top-left Back to Cockpit,
follow-ups in the sticky composer, result-shape chart validation, no graph
for entity lists, the Continue-where-you-left-off write ordering,
server-owned numeric rendering, claim-driven figures, SAR million, direct and
derived claims, compact orchestration, catalogue convergence, bind
validation, separate recovery budgets, release fingerprinting, fail-closed
readiness, and V3 isolation.

## 14. Running it on the Mac

```bash
python3 scripts/cockpit_v4/stop.py

git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull origin claude/cockpit-single-agent-v4-h8fsbq

cd frontend && npm install && cd ..

# the two domain books (immutable; re-running says so and writes nothing)
python3 scripts/cockpit_v4/seed_domains.py
python3 scripts/cockpit_v4/seed_domains.py --verify

# the runtime's analytical release, if this machine has not published it
python3 scripts/cockpit_v4/seed_release.py --release v4-saudi-20q-v1

python3 scripts/cockpit_v4/start.py
python3 scripts/cockpit_v4/status.py
```

Open `http://127.0.0.1:5414`. The Corporate | Retail control sits beside the
Ask box. Switching to Retail changes the attention cards, the ECL highlights
and the prompt chips — all from the retail release. Corporate questions
answer as before. A Retail question is refused with a message saying the
analytical path is not wired for it yet, which is §12 item 1.
