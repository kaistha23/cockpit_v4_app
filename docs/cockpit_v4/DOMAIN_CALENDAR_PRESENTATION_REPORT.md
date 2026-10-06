# One book, one calendar: the presentation contract

The backend was already right. Corporate is `v4-saudi-corporate-20q-v3`,
quarterly, 20 periods, latest `2026Q2`; Retail is `v4-saudi-retail-20m-v3`,
monthly, 20 periods, latest `2026-08`. The attention heading on the live Mac
screen read `REPORTING QUARTER Q2 2026`, from the server, and was correct.

Eight inches above it, on the same screen, the cover line read

```
Saudi Arabia · SAR million · monthly
```

and the chips beside the Ask box offered "Which sectors deteriorated most
this month?", "Show EAD by sector for the latest month.", "Which borrowers
were downgraded this month?" — to a book that has no months in it.

This round changed nothing about how a question is answered. It removed every
place in the presentation layer that states a calendar instead of reading
one.

---

## 1. Where the calendar came from, and where it comes from now

| Surface | Before | Now |
|---|---|---|
| Home cover line | the literal `Saudi Arabia · SAR million · monthly` in `cockpit-v4-home.tsx` | `domainHeadline(domains, domain)` — country, currency, scale and frequency from the selected book's `/domains` entry |
| Ask-box chips | two literal lists, the corporate one written in months | `promptsFor(domains, domain)` — one template list per book, `{period}` filled from that book's `period_noun` |
| Drawer seed note | "…this segment, quarter and movement" | the card's own noun, via `periodNoun(item)` |
| Drawer review steps | "over the same month", in both books | `_review_next(measure, scope.period_noun)` |
| Drawer driver statements | "also rose over this month", in both books | the scope's noun |
| ECL "added the most" card | `measure="ECL added this month"` | `f"ECL added this {scope.period_noun}"` |
| Thread openers | `{period}` from `reporting_month` only, fallback "the latest month", trend window "twelve months" in both books | the seed's own period whichever key carries it, fallback `the latest {noun}`, window eight quarters or twelve months by book |
| Follow-up chips | corporate offered "the last twelve months" | "the last eight quarters" for the quarterly book |

The rule the code now holds: **a V4 surface never writes a period word of its
own.** It either renders a period the server sent, or it renders the noun the
release published. Two unit tests enforce it directly — no prompt template and
no review template may contain the string "month" or "quarter".

## 2. The new module

`frontend/src/components/cockpit-v4/domain-meta.ts` is the single reader of
the `/domains` payload:

- `domainEntry(availability, domain)` — that book's published metadata, or
  `null`;
- `domainNoun(entry)` — `period_noun`, else the declared frequency, else the
  shape of the book's own latest period;
- `domainFrequency(entry)` — `quarterly` / `monthly`, by the same ladder;
- `domainHeadline(availability, domain)` — the cover line, assembled from what
  the release published, omitting what it did not;
- `PROMPT_TEMPLATES` / `promptsFor(availability, domain)` — the chips.

Before `/domains` answers, `domainHeadline` returns an empty string and the
chips say "this period" / "the latest period". A blank line for a moment is
not a claim; `monthly` under a quarterly book is, and that is the one that
shipped.

## 3. Requirement by requirement

**1. Domain header metadata.** Corporate reads `Saudi Arabia · SAR million ·
quarterly`, Retail `Saudi Arabia · SAR million · monthly`. Neither string
exists in the component: the span renders `domainHeadline()` and carries
`data-frequency` for the browser assertions.

**2. Corporate prompt chips.** Quarterly, as specified:

```
What is driving Stage 2 and ECL growth?
Which sectors deteriorated most this quarter?
Show EAD by sector for the latest quarter.
Which borrowers were downgraded this quarter?
Why is risk building across the corporate book?
```

No "month" wording on Corporate Home — asserted in the unit suite and in the
browser.

**3. Retail prompt chips.** Monthly, as specified:

```
Which products saw the largest Stage 2 increase this month?
Where is delinquency building?
Which behavioural score bands deteriorated most?
Show retail EAD by product for the latest month.
Why is risk building across the retail book?
```

**4. Period labels.** Unchanged and already correct: `periodLabel()` renders
`2026Q2` as `Q2 2026` and `2026-08` as `Aug 2026`, and the attention heading
renders `Reporting {noun} {period}` — "Reporting quarter Q2 2026" and
"Reporting month Aug 2026". The cover line above it now agrees with it.

**5. One calendar per surface.** Verified across the attention heading, the
ECL highlights, the detail drawer, the Investigate Further seed note, the
suggested prompts, the thread openers, and the export header. The server-side
sweep is `test_no_card_on_either_dashboard_names_the_other_calendar`, which
walks every card on both real dashboards and fails on any prose field
carrying the other book's period word. Chart and table titles come from the
answer itself and state no calendar of their own; `export.py` already derived
its noun from the release's frequency.

**6. Domain switch, real Chromium.** Five browser tests, listed in §4.

**7. Network and state.** The frequency is read from the `/domains` payload
keyed by book, never from which button is pressed — a unit test swaps the two
books' frequencies in the payload and asserts the rendering follows the
payload. The attention and ECL panels already cleared their state on switch
and dropped any response naming the other book (`domain-guard`), so five
round trips leave nothing behind.

**8. Regression that fails on the shipped code.** Both halves fail on the
build in the screenshot:

- `tests/cockpit_v4/test_domain_calendar_presentation.py` — 9 of its 10 tests
  fail with the previous `attention_v2.py` and `routes.py` restored;
- the five browser tests — 0/5 pass with the previous `cockpit-v4-home.tsx`,
  `ask-box.tsx` and `attention-drawer.tsx` restored, 5/5 after.

## 4. The browser tests

| Test | What it asserts |
|---|---|
| Home states each book's own frequency, from the release | the cover line contains the frequency `/domains` published for that book, and not the other book's |
| prompt chips are asked in the selected book's own period | every chip on Corporate says quarter and none says month; the reverse on Retail |
| Home and the dashboard under it agree on the calendar | the cover line, the chips and `Reporting {noun} {period}` on one screen say one thing |
| five switch cycles leave no calendar behind | Corporate → Retail → Corporate, five times, asserted on every leg |
| the drawer's seed note names the card's own period | the Investigate Further note and the review steps are in the card's calendar |

None of them holds a table of which book is which: each reads the calendar
from `/domains` first. A test carrying its own frequencies would pass a page
that carried one too.

## 5. What was run

| Suite | Result |
|---|---|
| V4 Python (`tests/cockpit_v4`) | **2256 passed, 0 failed, 4 skipped** (7m09s) |
| V3 regression (`tests/cockpit_agentic` + `tests/agentic`) | **771 passed, 0 failed, 105 skipped** (2m01s) |
| Frontend unit (`npm test`, 39 suites) | **538 passed, 0 failed** |
| TypeScript (`npx tsc --noEmit`) | clean |
| Browser, real Chromium | **74/74**, three consecutive runs |

The V4 suite was 2,246 before this round and is 2,256 now: the ten tests in
`test_domain_calendar_presentation.py`. The frontend suite was 527 and is
538: the eleven in `domain-meta.test.ts`. The browser suite was 69 and is 74.

No paid provider call was made. No V3 file was touched. Nothing in the
analytical orchestration changed: the diff is the presentation layer, the
prose templates the dashboard emits, and the tests over both.

### The pre-fix proof

```
git stash push backend/cockpit_v4/attention_v2.py backend/cockpit_v4/routes.py
pytest tests/cockpit_v4/test_domain_calendar_presentation.py
  9 failed, 1 passed

git stash push frontend/src/components/cockpit-v4/{cockpit-v4-home,ask-box,attention-drawer}.tsx
python3 scripts/cockpit_v4/browser_evidence.py   # V4_BROWSER_ONLY on the five
  0/5 browser tests passed
```

After the fix: 10/10 and 5/5.

### Screenshots

`docs/cockpit_v4/evidence/home_corporate.png` — cover line
`Saudi Arabia · SAR million · quarterly`, five quarterly chips, heading
`REPORTING QUARTER Q2 2026`, `Latest-quarter ECL highlights`.

`docs/cockpit_v4/evidence/home_retail.png` — cover line
`Saudi Arabia · SAR million · monthly`, five monthly chips, heading
`REPORTING MONTH AUG 2026`, `Latest-month retail ECL highlights`.

## 6. Retesting on the Mac

No release changed this round, so there is nothing to rebuild and nothing to
re-seed: this is a pull and a restart.

**1. Stop what is running.**

```
python3 scripts/cockpit_v4/stop.py
```

**2. Pull this branch.**

```
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull --ff-only origin claude/cockpit-single-agent-v4-h8fsbq
```

**3. Start.**

```
python3 scripts/cockpit_v4/start.py
```

**4. Check it is up.**

```
python3 scripts/cockpit_v4/status.py
```

If the two books were already published on this Mac, nothing else is needed.
If this Mac has never run the v3 releases, seed them first — it refuses to
overwrite anything that already exists:

```
COCKPIT_AGENTIC_V3_NAMESPACE=cockpit_v4 \
  python3 scripts/cockpit_v4/seed_domains.py --domain all

COCKPIT_AGENTIC_V3_NAMESPACE=cockpit_v4 \
  python3 scripts/cockpit_v4/seed_domains.py --verify
```

Expect:

```
  corporate  v4-saudi-corporate-20q-v3  verified  46962675d801ed4e
  retail     v4-saudi-retail-20m-v3  verified  95fa5d3e2c6b979b
```

**Hard refresh the browser tab** after the restart (⌘⇧R). The old page is
served from the dev bundle otherwise, and the first thing to check is the
line that bundle got wrong.

### What to look at, in order

1. **Corporate Home.** The line beside the switch reads
   `Saudi Arabia · SAR million · quarterly`. The chips say "this quarter" and
   "the latest quarter". The heading below reads `Reporting quarter Q2 2026`.
   Nothing on the page says "month".
2. **Switch to Retail.** The line reads
   `Saudi Arabia · SAR million · monthly`, the chips say "this month" and
   "the latest month", and the heading reads `Reporting month Aug 2026`.
   Nothing says "quarter".
3. **Switch back to Corporate.** The quarterly reading returns immediately,
   with the Corporate cards and the Corporate ECL position — not Retail's.
4. **Open a card, then the drawer.** "What to review next" and the driver
   lines are in that book's period. The note under Investigate Further says
   "this segment, quarter and movement" on Corporate and "…, month and
   movement" on Retail.
5. **Investigate Further.** The thread opens on questions in that book's
   calendar; the corporate trend question asks about the last eight quarters,
   the retail one about the last twelve months.
