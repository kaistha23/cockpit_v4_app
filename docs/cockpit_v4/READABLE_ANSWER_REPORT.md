# A readable answer, a chart you can interrogate, and a trace that shows its working

Six things reported from live use of the Cockpit thread view, and what was
found underneath each of them.

---

## What was reported, and what was actually wrong

| Reported | Cause |
|---|---|
| The charts have no axes, no hover values and no zoom | A **rule**, not an oversight: `visuals.tsx` may not format, round or re-scale a number, so the browser could not invent "0, 5, 10, 15 SAR mn" — and nobody had built the place those numbers could come from. |
| The colours are flat | The Cockpit V4 slice was written in `slate`/`sky`/`amber` literals and never touched the eight-theme token system, including a per-theme categorical ramp that was already tuned and validated and which nothing in the slice used. |
| A dark theme makes the answer almost unreadable | 445 literal colour values, ~90% of them in this slice. On Midnight the canvas repainted to `#0a0f16` and the answer paragraph stayed `#334155`. |
| Charts cannot be downloaded from where you are looking at them | Export was a panel of generic links at the foot of the answer. |
| An analysis cannot be sent to a colleague | With no transport configured — the default — every notification was RECORDED and never delivered. There was also no inbox: `readOutbox()` existed and nothing called it, and what it returns is the tenant's whole outbox without the bodies. |
| There is no trace that shows how a question became a number | All of it was stored and none of it had a route. The `submissions` table had **no reader at all**. The "Trace" button linked to the legacy page, which does `Number(runId)` on a `run-<32 hex>` id and gets `NaN`. |

### One correction to the brief

**A question is never spell-corrected.** `intake.py` has stated that policy
since it was written, and the governance record now repeats it rather than
leaving a reader to assume otherwise. Normalisation is mechanical only:
Unicode NFC, zero-width and bidi removal, whitespace folding. There is no
sentence rewriting and no language detector.

What *is* corrected is an **entity value** — `"prject finance"` →
`facility_type = 'Project Finance'` — and the trace shows that as what it
is, in a table of "you typed" beside "governed value".

---

## Stage 1 — one design system

445 literal palette classes became role tokens across the Cockpit V4,
analytics, collaboration and exports components.

**Three independent bugs surfaced on the way.**

1. `theme-provider.tsx` applies the stored theme before first paint, and its
   allowlist was still the original four names after the set grew to eight.
   Anyone on Alpine, Porcelain, Oxblood or Forest got the default painted
   for one frame and the real theme a frame later — a white flash on every
   navigation, from the one piece of code whose whole job is to prevent it.
   It is derived from `THEMES` now.
2. Fifteen sites use Tailwind's `dark:` variant, but `darkMode` was never
   configured, so in Tailwind v4 it means `prefers-color-scheme` — the
   reader's **operating system**, not the theme they chose. A
   `@custom-variant dark` now binds it to the four dark `[data-theme]`
   values.
3. `borrower-360/page.tsx` styled two figures `text-[var(--danger,#b91c1c)]`.
   There is no `--danger`; every theme resolved to the fallback.

**The guard.** `tests/frontend/test_theme_contrast.py` validates token
*values*, which is the other end of the pipe: it proves the palettes are
legible, not that anybody uses them. `test_no_colour_literals.py` scans
components and fails on a Tailwind palette class, a literal black or white,
a hex or an `rgb()`. It also runs in the matrix now — `pytest tests/cockpit_v4`
excludes `tests/frontend`, which is why nothing there had been running.

---

## Stage 2 — the server owns the axis

`backend/cockpit_v4/axis.py`. Tick **values** by a 1/2/5 rule; tick
**strings** by `display.py`, the one policy that governs every published
figure; axis **labels** from the catalogue's business name, so a reader sees
"Exposure at default" rather than `ead_sar_mn`.

The decisions that are not cosmetic:

| | |
|---|---|
| a bar axis includes zero | a bar encodes magnitude by length, so a scale starting at 92 draws a 3% difference as a doubled bar |
| a line axis need not | a line encodes by position and asserts no magnitude; forcing it through zero flattens the movement it exists to show |
| a stack is read by its TOTAL | an axis built from the tallest segment stops below the tallest bar and clips it |
| a 100% stack is in SHARE | its segments are re-scaled to the whole, so a denomination on the height is a denomination on a proportion |
| a box plot is transposed | categories down the page, measure across |
| a pie has no axis | magnitude is area; a scale over it is a second, wrong story about the slices |
| a category axis has no ticks | the categories ARE the points, and a second copy of those strings could drift |

`combo` is the one form drawn on two scales, because a rate over the volumes
it is a rate of is what it is for. Both are published and both are named —
an unnamed second axis is the chart mistake it is otherwise
indistinguishable from.

**`export.py` reads the same axis**, which closes a gap that was already
open: the browser SVG and the downloaded SVG were two independent renderers
of one payload, each computing its own scale. The parity test measures it —
the same bar is 40% shorter under the published axis than under the data's
own range.

### On screen

`chart-geometry.ts` holds the positioning arithmetic, in `.ts` so the test
runner reaches it. `chart-frame.tsx` holds everything a reader uses to
interpret the marks: gridlines, tick labels, axis titles, a crosshair, a
tooltip carrying the server's own string and the point's row id, and four
zoom controls. Buttons rather than a drag-brush — a brush is nicer with a
mouse and unusable with a finger or a keyboard, and this chart sits in a
thread a credit officer reads on whatever they have open.

Series colour is by fixed **slot** in the theme's categorical ramp, never by
rank, so a filter that changes how many series are on screen cannot repaint
the survivors.

**No charting library, and that is a deviation from the plan.** The plan
reached for Recharts to get axes, tooltips and zoom. Once the server owns
the axis and every label, what a library adds is its own number formatting,
its own locale rules and its own idea of a sensible scale — three more
places for a published figure to change on its way to the screen.
`tests/frontend/test_chart_formats_nothing.py` now fails the build on
`toFixed`, `toLocaleString`, `Intl.NumberFormat` or hand-rolled rounding
anywhere in the chart layer; coordinates go through one `px()` helper, which
is the only place a number becomes a string.

---

## Stage 3 — download the figure you are looking at

SVG or PNG per chart, CSV per table, on the figure rather than in a panel at
the foot of the answer.

What is downloaded is the **server's** chart. PNG is rasterised from that
same SVG rather than scraped from the DOM — a picture of the page would
carry the reader's theme, their zoom and whatever they were hovering, and
none of that belongs in a credit pack.

`choose()` now returns each kept chart's index in the **answer**. The
rendered list is filtered, so the second chart on screen can be the fourth
in the payload, and addressing the export by on-screen position would have
handed the reader a different chart from the one the button sits under.

---

## Stage 4 — a record of how the question became a number

`GET /runs/{run_id}/governance` reads; it opens no model, runs no query and
recomputes no figure. Guarded by `_authorize` rather than `_export_context`,
because a run that failed or was refused before it ever ran is exactly the
run a reviewer opens this for.

**A refused submission is a first-class entry**, with its exact query and the
reason it was stopped. It is the part that shows the controls working, and a
record that left it out would read as a system that has never stopped
anything.

**A step names what it read.** The artifact carried `relations`, which is the
whole authorized set — so a record built from it would say a question about
corporate exposure read the retail book. `sql.referenced_relations` could
already tell the difference and its answer was discarded at the
authorization check; it is stored on the artifact scope now.

**The SQL is gated to Administrator**, with the step, its purpose, its
checks, the relations it read and the rows it produced all still shown. A
withheld query *says* it is withheld: going quiet reads as a control that did
not run. The principal carried only `{id, tenant}` and now carries `roles`;
an absent `roles` grants nothing.

`GET /runs/{run_id}/governance/export` is a zip — the readable document, the
record as JSON, and every stored result as CSV. The gate holds inside the
pack, and there is a test that downloads it as a non-administrator and greps
for the query.

**What it will not claim.** Provenance stops at the result set. A published
total is traced to a stored row; that row's own source rows are not
retained. The panel and the document both say so at that boundary rather
than laying out a waterfall that implies a link nobody can follow.

---

## Stage 5 — a message that arrives

An in-app message needs no relay: it is addressed to someone who already has
an account on this tenant and can already read the analysis being sent. So
`InAppTransport` delivers it, and the recipient allow-list — which exists to
stop a build mailing the open internet — is answering a question that was
not asked.

**Mail is unchanged.** A build with no mail transport still sends no mail,
an address outside the allow-list is still REFUSED, and both say so in the
same words as before.

The two are **separate fields** on the notifier, because one boolean cannot
answer "can this build deliver?" once the answer is "inside, yes; outside,
no". A single `can_deliver` that flipped true when in-app delivery arrived
would have quietly reported a build as able to mail people it cannot mail.
The two existing tests that pin the no-mail posture pass unchanged.

`GET /inbox` is addressed to the reader and carries the **body**. A refused
message is not in it — an inbox showing one would be showing a message that
was never sent. `GET /recipients` is the directory the share panel should
have been offering instead of a free-text field, and it says who is missing:
somebody who has never signed in is not listed.

`deliveryWording` is unchanged and is still the only thing allowed to claim
delivery.

---

## Verification

| | |
|---|---|
| V4 backend | `COCKPIT_AGENTIC_V3_NAMESPACE=cockpit_v4 pytest tests/cockpit_v4 -o addopts=""` — **2859 passed, 4 skipped** (2706 at the start of the round) |
| V3 backend | `pytest tests/cockpit_agentic -o addopts=""` — **564 passed, 26 skipped**, unchanged |
| Frontend unit | `npm test` — **587 passed** (549 at the start) |
| Frontend source rules | `pytest tests/frontend -o addopts=""` — **51 passed** |
| Typecheck | `npx tsc --noEmit` — clean |
| Browser | `python3 scripts/cockpit_v4/browser_evidence.py`, run twice: default theme and `V4_THEME=midnight` — **76/76 in each** |

`V4_THEME` is new. Dark-mode evidence used to mean a person switching the
theme by hand and taking a picture, which is not evidence anybody can
re-take; the theme is now seeded into `localStorage` before first paint,
where `ThemeScript` reads it, so the screenshot is of the theme a reader
would actually have seen rather than a flash of the default.

A themed run also writes to its own directory — `evidence/midnight/`. The
dark pass produces the same twenty filenames as the default one, so
running it second replaced the light screenshots and the committed set
silently became all-Midnight. That proves the dark theme renders and loses
the baseline it is supposed to be compared against; the two are only side
by side if they are in two places.

**And the theme did not reach every page.** Four blocks built their own
browser context — three to set a viewport, one for the landing screenshot
— so a dark run produced a LIGHT landing page: the picture a reader is
most likely to open, proving the least. Nothing in the test output showed
it, because every assertion still passed. It took measuring the mean
luminance of the two files and finding them identical to 246.0, where
every other pair was 242 against 23. Every context goes through the helper
now, and `test_every_browser_context_carries_the_run_theme` allows exactly
one direct call — the one inside it.

### Two browser failures, and what each turned out to be

The first browser run came back 74/76.

**"the conversation links to the trace of its latest answer" was asserting
the broken route.** It matched `/trace/<id>`, which is the legacy
reasoning map — the page that does `Number(runId)` on a `run-<32 hex>` id
and resolves nothing. The assertion passed for as long as the button was
dead, because a link is well-formed whether or not anything is behind it.
It now matches the V4 route **and clicks it**, which is the part the old
form could not have caught.

**"the thread header names the conversation and renames it" was a cold
bundler.** It reproduced on a second run and then passed on a third, and
reverting `thread-view.tsx` to the previous commit "fixed" it — which
looked like a regression and was not. `next dev` compiles a route on its
first request; the thread page's bundle grew by four modules this round,
and a reload that raced that compile lost the title it had just set.

`_common.warm_routes` exists for exactly this and its docstring says so:
*"a browser assertion waiting sixty seconds for an answer that had not
started rendering reported a product defect that was a cold bundler."*
The new `/cockpit/trace` and `/cockpit/saved` routes were not in the warm
list. They are now, in both `browser_evidence.py` and `flake_matrix.py`.

### One backend failure, and a guard that was passing by accident

`test_every_v4_fetch_goes_through_the_v4_prefix` — *"a fetch that skips
`API_PREFIX` is how a shim gets back in"* — caught the governance pack
download. Two things came out of it.

The pack is now a plain `<a download href>`, which is what `exportLinks`
documents as the pattern: *"the browser downloads the document straight
from the API with the session cookie it already has, so nothing about the
file passes through this code and nothing here can reformat a figure on
its way out."* Every other export in the product works that way and this
one is not special.

The interesting part is why `figure-download.tsx` — which fetches for
real, because a PNG needs the bytes — was **not** caught. The guard skips
targets beginning `url`, `input` or `request`, and that file's helper
happened to take a parameter called `url`. It was passing by accident, not
by being checked. The `url` escape is gone; `exportLinks(` is named as the
one sanctioned source; and the fetch now reads
`fetch(pick(exportLinks(runId)), …)`, so a reader can see the V4 prefix
without following a variable.

### New regression files

```
tests/cockpit_v4/test_chart_axis.py                the tick rule and the label
tests/cockpit_v4/test_chart_axis_rendering.py      every form's axis, decided
tests/cockpit_v4/test_chart_axis_export_parity.py  screen and file agree
tests/cockpit_v4/test_governance_record.py         the record, and its gate
tests/cockpit_v4/test_in_app_messages.py           delivery, and its two channels
tests/frontend/test_no_colour_literals.py          no component names a colour
tests/frontend/test_chart_formats_nothing.py       the browser writes no number
frontend .../chart-geometry.test.ts                the positioning arithmetic
frontend .../chart-download.test.ts                Arabic labels, raster size
```

---

## Out of scope, and stated as such

- Row-level provenance beneath a result set. The record says where it stops.
- A sentence-level spelling corrector. Policy forbids it and the trace says
  so rather than implying one ran.
- The legacy `/workspace` collaboration stack and the legacy `/trace` engine
  page. Both untouched; both still answer for what they know about.
- Any change to a published release.
