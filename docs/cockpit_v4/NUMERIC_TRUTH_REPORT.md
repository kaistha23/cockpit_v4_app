# Cockpit V4 — numeric truth, answer rendering, Saudi-native data

No paid provider call was made in this round. Every figure below is measured
from the real release, the real catalogue, real DuckDB execution, the real
derivation arithmetic and the real validator; only the model's prose is
scripted, and each evidence artefact says so in its own text.

---

## 1. Verified starting HEAD

`716ddc4` — confirmed equal to `origin/claude/cockpit-single-agent-v4-h8fsbq`
before anything was changed, with a clean working tree. Nothing newer was on
the branch, so nothing had to be preserved.

## 2. Final HEAD

`docs/cockpit_v4/NUMERIC_TRUTH_REPORT.md` is the last commit of the round;
the pushed SHA is printed by the push and named in the handover below.

## 3. Numeric-rendering architecture

The failure this round removes:

> SQL executes correctly → the mathematics is correct → Opus writes a rounded
> number → CreditProbe rejects the answer → another expensive call is spent
> reformatting → or the user receives nothing.

The cause was a division of labour that made **Opus responsible for the exact
decimal string on the screen**. It bought nothing: the server had already
computed the value, already knew its unit, and already knew how that kind of
figure is written in this domain.

    USER QUESTION
      → OPUS            understands, authors execute_analysis
      → CREDITPROBE     validates, binds, executes, stores the artifact,
                        stamps the release header on it
      → OPUS            interprets; references CLAIM IDS, types no numbers
      → CREDITPROBE     recomputes each claim, checks it, formats it,
                        substitutes it into the narrative, renders the
                        tables and charts from the stored artifact
      → USER

What stayed with Opus: what the question means, the method, the SQL, which
results matter, whether a table or chart helps, the interpretation, the
caveats, the next questions. What moved to CreditProbe: canonical truth,
recomputation, evidence binding, release provenance, unit, currency, display
formatting and rendering.

## 4. Direct claim contract

A direct claim names one executed result cell:

```json
{"claim_id": "it_ead", "unit": "SAR million",
 "evidence": {"artifact_id": "art-…", "row_key": "r0",
              "column_id": "ead_reported_sar_mn"}}
```

No value. The server reads the cell, keeps it at full precision, and writes
it. A cell that does not exist, is null, or belongs to another release still
refuses the answer.

## 5. Derived claim contract

A derived claim names arithmetic over cells that exist — twelve closed
operations, one level of nesting, no expression language, no `eval`:

```json
{"claim_id": "total_ead", "unit": "SAR million",
 "derivation": {"operation": "sum", "operands": [
   {"artifact_id": "art-…", "column_id": "ead_reported_sar_mn",
    "row_ids": ["r0", "…", "r11"]}]}}
```

The server recomputes it from the stored artifact. Wrong rows, wrong
denominator, wrong operation or a unit the operation cannot produce all
refuse the answer.

## 6. Claim-reference syntax

`{{claim.<claim_id>}}` in the narrative. A reference with no matching claim
refuses the answer; a claim declared and never referenced is a warning, not a
failure. Rendering reads the server's canonical registry, never the model's
string.

```
written:   "Total portfolio EAD in 2026Q2 is {{claim.total_ead}}."
published: "Total portfolio EAD in 2026Q2 is SAR 40,599 million."
```

**`decimal_value` and `display_precision` are now optional and normally
absent.** An analyst that sends a value is offering a cross-check and is still
held to it exactly as before — what it may no longer do is have a correct
analysis refused because a figure it reproduced by hand differed in the
fifteenth decimal place.

## 7. Server formatter design

`backend/cockpit_v4/display.py` is the single authority and answers three
questions: what KIND of quantity this is, how many decimals that kind shows,
and how it reads once published. `precision.py` now delegates to it, so there
is one policy rather than a validator's opinion and a renderer's.

| class | decimals | reads as |
|---|---|---|
| MONETARY_AMOUNT | **0** | `SAR 40,599 million` |
| PERCENTAGE | 2 | `61.24%` |
| PROBABILITY | 2 | `4.33%` (canonical 0.043276) |
| PERCENTAGE_POINT | 2 | `2.10 pp` |
| RATIO | 2 | `1.25x` |
| COUNT / INTEGER | 0 | `47 borrowers` |
| RATING / IFRS_STAGE / PERIOD | categorical | `2` |

The class is read from the **unit** and never from the claim's name —
`total_ead` and `ead_share` differ by unit, not by spelling. The
authoritative unit is the **catalogue's**, because it describes the data
rather than whoever is describing it.

**§17 answered by the release rather than by fiat:** the catalogue declares
`pd_pit_12m` as `probability_0_1` and `lgd_pit` as `fraction_0_1`, so
canonical PD and LGD are **fractions** and the hundredfold belongs to the
display class. It is never inferred from the magnitude: 0.04 is an ordinary
figure in percent as well as in fractions, and guessing between them publishes
a PD two orders of magnitude wrong on exactly the small numbers that matter.

**§18:** PERCENTAGE and PERCENTAGE_POINT are different classes. 5.20% → 7.30%
is `+2.10 pp` and `+40.38%`; both are true and they are not interchangeable.

**§16:** rounding happens once, at the end. A regression proves the arithmetic
this prevents — three cells summed at full precision round to 7,733 and the
same cells rounded first sum to 7,734.

`crore` and `lakh` are gone from V4 source, and not by deletion: the scale
word after a currency code is now **open** rather than enumerated, because
listing the scales a policy accepts means listing the ones it silently
downgrades. The required **space** keeps it honest — `notional` is one word
and does not become a currency.

## 8. Table rendering design

The analyst chooses the table: a title, an artifact, and which columns. Every
value comes from the stored artifact, formatted once by the display policy,
with the **canonical value kept beside it** — ordering, scale and any further
arithmetic run on that and never on a formatted string, because
`SAR 9,000 million` sorts above `SAR 40,599 million` as text.

A column's unit is asked for, never assumed: first a numeric claim already
bound to that artifact and column (a unit the answer has already been held
to), then the catalogue. A column neither can name is written for a person
with **no unit asserted**.

That the model cannot smuggle a value into a table is a property of the
schema, not of its behaviour: `Table` and `Chart` are closed objects whose
every field is a name or a label, and a test reads the schema and says so.

## 9. Chart rendering design

The same contract. The analyst decides a chart is useful, its kind, its axes
and its unit; the server supplies every point from the artifact at full
precision, with the reader's form beside each value for axes and tooltips.
Ordering is checked against full precision.

## 10. Saudi release id

`v4-saudi-20q-v1` — now the **default**, not a required setting. An explicitly
configured `COCKPIT_V4_RELEASE_ID` is always honoured, and a configured
release that is missing from the runtime is an error rather than an excuse to
load this one quietly.

## 11. Release fingerprint

```
24df0d375e72d1cd23a8a92f1410745426d35cd756858b266a0e801e64acca88
```

SHA-256 over every published file of the release by name, size and contents,
folded in sorted order — a fact about what is on disk, not about what a
manifest says is on disk, which is the distinction a silent rebuild turns on.

## 12. Saudi currency and scale provenance

Country `Saudi Arabia`, currency `SAR`, amount scale `million`, reporting
frequency quarterly, latest populated quarter `2026Q2`, `not_client_data`
true — all read from the release's own manifest. **No FX conversion was
applied**: multiplying synthetic figures by a rate would manufacture economic
meaning that was never in them.

A release that declares none of this is handed no nationality, Saudi
included. `Header.unverified` names what is missing, `denominated` is false,
and startup refuses. That half matters as much as the other — a default that
overrode what a release says about itself would be the same defect pointing
the other way.

## 13. Synthetic Saudi borrower and sector design

Borrower names are a pure function of the borrower id, so reseeding is
byte-identical. Each is a place or descriptor paired with a line of business —
`Al Ahsa Contracting`, `Al Buraida Food Industries`, `Jubail Steel Works` —
deliberately generic so no combination reads as a particular firm. A test
asserts every name in the book is built from that vocabulary rather than
eyeballing a sample.

Sectors, as the book actually holds them: Construction, Real Estate,
Manufacturing, Chemicals, Metals and Mining, Power and Utilities, Transport
and Logistics, Wholesale Trade, Retail Trade, Information Technology,
Hospitality, Agriculture and Agri-processing. Currency `SAR` only; country
`SA` only.

**One item of §3 was not delivered**: Healthcare and Professional/Business
Services are not in the taxonomy. The taxonomy belongs to the shared V3
generator, and adding to it would change V3's releases — which this round was
told not to do. The test therefore asserts that what the generator produces
*is* a Saudi corporate book, not that V4 rewrote it.

## 14. Proof of zero INR/crore in the V4 demo surface

Scanned as a test, over the frontend Cockpit components, the analyst prompt,
the product knowledge and every evidence artefact: no `INR`, `crore`, `lakh`,
`₹` or `Rs`. Separately: the book's own data audited relation by relation
(`saudi.audit` leaks empty, currencies `["SAR"]`), a published answer
serialised whole and scanned, and the attention dashboard scanned.

The only surviving occurrences in the repository are the localizer's own
source→target map and audit vocabulary, and the browser suite's
forbidden-word list — none of which is reader-facing, all of which would have
to name the word to forbid it.

## 15. M01 canonical vs rendered

Question: *"What is total exposure at default by sector in the latest
quarter?"* Published narrative, verbatim:

> **Total portfolio EAD in 2026Q2 is SAR 40,599 million. The four largest
> sectors carry 56.70% of it.**

| claim | oracle canonical | published | analyst sent a value |
|---|---|---|---|
| `total_ead` | `40599.173664` | `SAR 40,599 million` | **no** |
| `top4_share` | `56.70434499856228403851091741` | `56.70%` | **no** |

Table (rendered by CreditProbe, 12 rows, `ead_reported_sar_mn` → `SAR
million`):

| sector | canonical | display |
|---|---|---|
| Information Technology | 7013.1167117986615 | SAR 7,013 million |
| Manufacturing | 6936.155746775151 | SAR 6,936 million |
| Power and Utilities | 4563.685615211775 | SAR 4,564 million |

Full precision survives into every row; the reader's form sits beside it.

## 16. M01 model-call count

**2 generations** — `ANALYSIS_ACTION`, then `FINAL_ANSWER`. Zero action
format recoveries, zero answer format recoveries, **zero answer
corrections**, zero catalogue calls, one execution submission.

## 17. M01–M15

**15/15 publish**, twice over, through two different contracts:

- `test_math_pipeline` — the analyst SENDS a rounded decimal string (the
  credit officer's form the old contract refused). Still passes: an offered
  cross-check is still checked.
- `test_math_bank_rendering` — the analyst types **no numbers at all**. All
  fifteen publish in two generations each, and every claim is asserted to
  carry no `decimal_value`, so the module cannot quietly drift back to
  testing the old path.

The oracle checks **both layers separately**, because a display layer that
dropped a factor of a hundred would agree with itself perfectly: the
canonical figure the derivation produced, and the string the reader is shown,
recomputed independently through the policy.

**A defect this found:** a table column whose unit nothing could name was
published raw, so a reader was served `1.5690646127781567`. Declining to name
a unit is honest; printing sixteen digits at a credit officer is not, whether
or not we know what the number measures. Such a cell is now written for a
person with no unit asserted — whole if the value is whole, two places
otherwise.

## 18. Wrong-mathematics rejection

Nothing was weakened to buy presentation.

| injected | result |
|---|---|
| derivation over 3 of 12 rows | publishes the wrong total it actually names, not the right one |
| claim naming an invented row (`all sectors`) | ANSWER_VALIDATION |
| analyst cross-check of `1.00` against the real total | ANSWER_VALIDATION |
| a count declaring decimal places | refused by the policy |
| a hundredfold scale error on a share | refused |
| a thousandfold scale error (million restated as billion) | refused |
| evidence from another release | ANSWER_VALIDATION |
| evidence from another build of the same release | ANSWER_VALIDATION |

## 19. Release-isolation tests

Every analytical object carried a release id, and an id is a **name**. Two
builds of one id share the name and differ in every number, so "same
release_id" was satisfied perfectly by an artifact stored before a rebuild.
The fingerprint is what two builds cannot share.

- The run pins one execution header and stamps it on every artifact.
- A claim citing an artifact from another release is refused by name.
- A claim citing a different **build** of the same release is refused by
  fingerprint — the case an id could never catch.
- The published answer carries the header, so a saved analysis, a shared link
  or a thread reopened months later says which release, which bytes, which
  country, which currency, which scale.
- The attention cache key was release + tenant, which was not enough: a
  rebuilt release produced the same key and would be served a dashboard
  computed from bytes that no longer exist. The fingerprint is now part of
  the key.
- A seeded investigation answers from, and reports, the release it was seeded
  from.

## 20. D-001 — release stability

**Closed, by measurement rather than argument.** The fingerprint is taken at
module import before any test runs and compared at the end of the session; a
publish attempt over a live release is proved to raise `UnsafeTarget`
("immutable") rather than replace. Measured before this round's work and
after the whole matrix:

```
v4-saudi-20q-v1  24df0d375e72d1cd23a8a92f1410745426d35cd756858b266a0e801e64acca88
v4-uat-20q-v1    dd799a1d9328229642d1a9ef1b2e49918e803e1df79aedb658c5569789efa983
```

Identical. Nothing rebuilt the release.

## 21. Performance

Numeric formatting is deterministic and free: ten thousand formats of the
same value are asserted to complete well inside a second, and it runs once
per published figure.

Orchestration was not regressed. Measured on the same simple analysis:

| | action turn | answer turn |
|---|---|---|
| system context | 30,836 B | 14,813 B |
| tool schemas | 23,287 B | 14,006 B |
| tools offered | 4 | 2 |
| counted input tokens | 24,693 | 16,961 |

Wall clock for a complete two-call analysis with a stub model: 438 ms total,
0 ms provider (stub), **438 ms CreditProbe** — so local overhead for a full
analytical run, real release and real execution, is under half a second.

The seeded case file is unchanged: 14 field definitions / 5,825 bytes in
place of a 59-field / 31,196-byte relation dump and the generation that
fetched it.

## 22. Full test counts

| suite | result |
|---|---|
| V4 backend (`tests/cockpit_v4`) | **1037 passed, 2 skipped, 0 failed** |
| Frontend (`frontend`, node:test) | **476 passed, 0 failed** |
| Browser (real Chromium, real UI, real API, stub analyst) | **46/46 passed** |
| V3 regression (`tests/cockpit_agentic`) | **564 passed, 26 skipped, 0 failed** |

New this round: `test_release_header.py` (10), `test_display_policy.py` (38),
`test_claim_rendering.py` (13), `test_server_rendered_output.py` (8),
`test_math_bank_rendering.py` (75), `test_release_binding.py` (8),
`test_saudi_demo_surface.py` (14).

The two skips are `test_math_bank_rendering` questions whose result is empty
on this book — an empty result is a finding, and the display assertions have
nothing to assert on.

## 23. V3 comparison

564 passed, 26 skipped, 0 failed — unchanged. No file under
`backend/cockpit_agentic/` or `tests/cockpit_agentic/` was modified this
round; the diff is confined to `backend/cockpit_v4/`, `tests/cockpit_v4/`,
`scripts/cockpit_v4/` and `docs/cockpit_v4/`.

## 24. Unresolved blockers

**None for live Mac UAT.** Three things named rather than claimed:

1. **Healthcare and Professional Services are not in the sector taxonomy**
   (§13). Adding them means changing the shared V3 generator, which this
   round was told not to touch. The existing twelve sectors are a credible
   Saudi corporate book.
2. **Call latency is still unmeasured.** Every figure in §21 is real except
   the provider's, which is zero because the model is a stub. Whether a live
   analytical turn fits inside 120 seconds less a 20-second finalization
   reserve is now *observable* — the call report records it — but not
   answered.
3. **`v4-uat-20q-v1` is still the old Indian synthetic book** and is kept, as
   §29 asks, for regression and migration comparison. It is not the default
   and is not reachable in the normal experience, but its name says "uat"
   and will read as the UAT release to anyone who does not know the history.
   Worth renaming in a later round; renaming it now would change a published
   release id, which is exactly what §41 forbids.

## 25. Verdict

**SAUDI-NATIVE NUMERIC CLAIM RENDERING: READY FOR LIVE MAC UAT**

Against the hard acceptance criteria:

| | criterion | |
|---|---|---|
| 1 | default V4 release is Saudi-native | ✅ |
| 2 | normal V4 UI shows no INR/crore | ✅ |
| 3 | release metadata says Saudi / SAR / million | ✅ |
| 4 | canonical values keep full precision | ✅ |
| 5 | monetary display = 0dp | ✅ |
| 6 | PD/LGD/share display = 2dp | ✅ |
| 7 | counts = integer | ✅ |
| 8 | claim rendering replaces model-entered strings | ✅ |
| 9 | tables use validated data | ✅ |
| 10 | charts use validated data | ✅ |
| 11 | wrong maths still fails | ✅ |
| 12 | M01–M15 publish | ✅ 15/15 |
| 13 | M01 needs no formatting correction | ✅ 2 calls, 0 corrections |
| 14 | no cross-release contamination | ✅ |
| 15 | published Saudi release is byte-stable | ✅ |

## 26. Mac instructions

```bash
cd ~/IPM_V2

# stop whatever is running
python3 scripts/cockpit_v4/stop.py

# pull this branch
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull origin claude/cockpit-single-agent-v4-h8fsbq

# start — the release block it prints should read
#   release v4-saudi-20q-v1 · Saudi Arabia · SAR · million
python3 scripts/cockpit_v4/start.py

# confirm
python3 scripts/cockpit_v4/status.py
```

The double-clickable equivalents are `scripts/cockpit_v4/STOP_COCKPIT_V4.command`,
`START_COCKPIT_V4.command` and `STATUS_COCKPIT_V4.command`.

Reproducing the evidence in this report:

```bash
python3 -m pytest tests/cockpit_v4 -q                     # 1037
cd frontend && npm test                                   # 476
python3 scripts/cockpit_v4/browser_evidence.py            # 46/46
python3 -m pytest tests/cockpit_agentic -q                # 564, 26 skipped
python3 scripts/cockpit_v4/numeric_evidence.py            # §15, §16
python3 scripts/cockpit_v4/orchestration_evidence.py      # §21
```
