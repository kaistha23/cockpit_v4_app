# A release describes itself

A Cockpit V4 release is immutable. Publishing one writes the Parquet, a
fingerprint over those bytes, and a manifest that records the calendar, the
currency, the amount scale and — this is the part that was being ignored —
**every relation and every field**, with grain, period column, key columns,
type, unit, group, label and aggregation.

Until this round the code read almost none of that. `Catalog` answered every
shape question out of `schema.py` keyed on the **domain**, so the schema this
build happens to carry was imposed on whatever release was opened, and
`_build_session` materialised each Parquet with `SELECT "<every column today
declares>"`.

Adding four columns to Retail therefore stopped `v4-saudi-retail-20m-v3`
opening at all:

```
BinderException: Referenced column "employment_type" not found
```

Corporate v3 kept working, which was the dangerous half: its column set
happened to match, so the coupling was invisible until the day a column was
added, and then it took historical reproducibility with it and said nothing.

## What is true now

* `catalog.build()` reads `manifest["relations"]` and the `Catalog` answers
  `relations`, `columns`, `spec`, `resolve` and `outline` from **that**.
  A release opened by id is described by its own manifest.
* The join graph is the one shape a manifest does not record. It stays a
  per-domain constant, filtered to the relations and `on` columns the
  release actually holds — an opinion about an older book, kept from
  becoming a false statement.
* `for_domain(domain_id, release_id=...)` **selects** that release instead
  of asserting it is the current one. `release_fingerprint` stays a strict
  assertion: bytes that moved under an id are refused.
* `POST /runs` on a thread pinned to a superseded release returns **409
  `RELEASE_SUPERSEDED`**. Its existing turns still open and still render; a
  new question would be answered from different numbers, so it belongs in a
  new conversation. The old behaviour silently rebased it.
* Nothing substitutes. v3 never serves v4's columns, and asking for one says
  it is not in that release.

## Reading a release back

`Field.label` and `Field.additive` are properties over raw slots, and a
manifest records only the resolved answer. The round trip treats them
differently on purpose:

| slot | taken back | why |
|---|---|---|
| `label` | verbatim | nothing reads the raw slot except `label` itself, so the release renders the words it rendered |
| `aggregation` | only where it **overrules** the unit | the raw slot is a claim that an author overruled the unit, and `semantics.py` publishes it to the analyst on that basis |

Filling `aggregation` unconditionally put a redundant verdict on all thirty
canonical measures and 994 bytes on every action request — over the payload
bound, which is how it was caught.

The three oldest manifests predate the `label` key entirely. A release that
recorded none gets the derived label, which is what it was showing at the
time.

## Checking a release on your own machine

The **byte fingerprint** hashes the Parquet files, and Parquet encodes
row-group layout, compression and writer version. Two machines that generate
identical data produce different fingerprints. It is the right check for
"have these bytes moved **here**", and the wrong one for "is your book the
same as mine".

The **content digest** is the portable one: computed per relation from the
release's own manifest, with the rows sorted by that relation's own key and
the columns taken in the schema order the release published. Floats hash as
their little-endian IEEE-754 bytes rather than as text, because formatting a
float is where platforms disagree; missing values hash as a separate mask, so
a null and a zero cannot collide. Nothing about how the bytes were laid down
can reach it, and a single changed value does.

Two things it does **not** mean, both learned the hard way:

* **It is not invariant to the release id.** `dataset_release_id` is a
  stored column on every row, so republishing identical data under a new id
  gives a different digest. That is correct — a row says which release it
  belongs to — but it means comparing digests across two ids answers nothing
  about whether the data matches.
* **A matching digest needs the same generator behaviour, not just the same
  commit.** `v4-saudi-retail-20m-v4` digested `65524d5dd04d2d2e` built on
  Python 3.10/3.11 and `9d444d6969e5e860` on 3.12/3.13 — same commit, same
  seed. Its customer-level mean went through the builtin `sum`, whose float
  behaviour CPython changed in 3.12, over inputs already rounded to two
  places, so the mean sat on a rounding tie and fell either way. Row counts,
  entity counts and field counts were identical, so both manifests looked
  right and only a digest of the values caught it.

`-v5` is that book with the aggregation made exact (`generate/totals.py`),
and it is the same book on every interpreter. `-v4` is frozen rather than
corrected: an id that names two books cannot be rebuilt into one.

```
python3 scripts/cockpit_v4/release_report.py \
  --release v4-saudi-corporate-20q-v4 \
  --release v4-saudi-retail-20m-v5 \
  --release v4-saudi-corporate-20q-v3 \
  --release v4-saudi-retail-20m-v3 \
  --no-values
```

Read only. Nothing it does writes to the lake. Releases published from here
on also record a `built_with` block — Python, pandas, numpy, pyarrow — so the
next disagreement of that kind is a lookup rather than an investigation. It
is there to diagnose, never to justify treating two releases as
interchangeable because their versions look close.

## Where this is pinned

`tests/cockpit_v4/test_release_history.py`, including a case parametrised
over `lake.releases()` — so a release published tomorrow is covered without
anybody remembering to add it.

`tests/cockpit_v4/test_generator_determinism.py` pins the other half: no
generator may add floats with the builtin `sum`, and the Retail book is
rebuilt under every `python3.x` on the machine and compared. That second
test is calibrated — at 300 customers the pre-fix code still diverges
between 3.11 and 3.12 — so the reduced book it builds is small enough to be
quick and large enough to catch what it is for.
