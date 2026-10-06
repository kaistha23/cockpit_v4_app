#!/usr/bin/env python3
"""What a published release actually holds, and a digest that travels.

    python3 scripts/cockpit_v4/release_report.py
    python3 scripts/cockpit_v4/release_report.py --release v4-saudi-retail-20m-v3
    python3 scripts/cockpit_v4/release_report.py --patterns

READ ONLY. This opens parquet files and manifests and writes nothing. It
cannot publish, overwrite or delete a release, which is the point: it is
what you run to check a release rather than to change one.

Why this exists, and why the fingerprint was not enough
------------------------------------------------------
`lake.fingerprint` is a SHA-256 over the PARQUET BYTES. Parquet encoding --
compression, row-group layout, the writer version stamped into the file --
depends on the pyarrow and pandas versions and on the platform. So the same
release id, built from the same generator at the same commit, fingerprints
differently on a Mac and in a Linux container. Both are correct. The
fingerprint answers "have these bytes changed since they were published
HERE", which is the question `lake.verify` asks, and it is a good answer to
that question.

It is not an answer to "is your copy of this release the same data as
mine", and that question was asked. `content_digest` below is: it is
computed from the VALUES, in schema column order, with the rows sorted by
the relation's own key, so nothing about how the bytes were laid down can
reach it. Two machines that agree on the content digest hold the same book
whatever their fingerprints say.

Floats are hashed as their little-endian IEEE-754 bytes rather than as text,
because formatting a float is where platforms disagree and the bit pattern
is where they do not. Missing values are hashed as a separate mask so a null
and a zero cannot collide -- a book with a column of nulls and a book with a
column of zeros are different books, and an ECL of nothing is not an ECL of
nought.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

#: Rows hashed at a time. The books are millions of rows and there is no
#: reason to hold a second copy of one in memory to digest it.
CHUNK = 100_000

#: Past this many distinct values a column is a list of names, not a
#: category, and printing it is printing the book. Mirrors
#: `values.MAX_CARDINALITY`, which is what the Data Builder itself uses to
#: decide between governed values and a sample.
MAX_PRINTED_VALUES = 80


def _digest_frame(digest, name, frame, columns, key_columns) -> None:
    """Fold one relation into the running content digest.

    `columns` and `key_columns` come from the RELEASE'S OWN MANIFEST, never
    from the schema this build happens to carry. An older release is a
    different book with a different shape -- retail v3 has no
    `sub_product` -- and reading it through today's schema raises a
    `KeyError` on a file that is perfectly intact. A report that cannot
    describe a superseded release is no use for the one question a
    superseded release is kept to answer: is the new one different.
    """
    # Published column ORDER, and the relation's own key ORDER.
    # `lake.publish` reorders columns before writing; sorting the rows as
    # well means a release written in a different row order still digests
    # the same, which is the only sort of difference that is not one.
    frame = frame[list(columns)]
    keys = [c for c in key_columns if c in frame.columns]
    if keys:
        frame = frame.sort_values(keys, kind="mergesort").reset_index(drop=True)

    digest.update(name.encode("utf-8"))
    digest.update("\x1e".join(columns).encode("utf-8"))
    digest.update(str(len(frame)).encode("utf-8"))

    for start in range(0, max(len(frame), 1), CHUNK):
        block = frame.iloc[start:start + CHUNK]
        for column in columns:
            series = block[column]
            digest.update(column.encode("utf-8"))
            # The missing-value mask, always, and separately from the
            # values: null and zero are different facts.
            digest.update(series.isna().to_numpy(dtype="<u1").tobytes())
            if series.dtype.kind in "fc":
                digest.update(b"f")
                digest.update(
                    series.fillna(0.0).to_numpy(dtype="<f8").tobytes())
            elif series.dtype.kind in "iub":
                digest.update(b"i")
                digest.update(
                    series.fillna(0).to_numpy(dtype="<i8").tobytes())
            else:
                digest.update(b"s")
                digest.update("\x1f".join(
                    series.fillna("").astype(str)).encode("utf-8"))


def _published_shape(manifest) -> dict[str, dict[str, object]]:
    """Each relation as the release itself declares it.

    The manifest records the full field list at publication, so a release
    describes its own shape and nothing here has to assume the code that
    reads it is the code that wrote it.
    """
    out: dict[str, dict[str, object]] = {}
    for spec in manifest.get("relations") or []:
        fields = spec.get("fields") or []
        out[str(spec["relation"])] = {
            "columns": [str(f["name"]) for f in fields],
            "key_columns": [str(c) for c in (spec.get("key_columns") or [])],
            "period_column": str(spec.get("period_column") or ""),
            "fields": fields,
        }
    return out


def content_digest(release_id: str) -> str:
    """A digest of what the release SAYS, not of how it was written down."""
    import pandas as pd

    from backend.cockpit_v4 import lake

    manifest = lake.read_manifest(release_id)
    shape = _published_shape(manifest)
    digest = hashlib.sha256()
    digest.update(str(manifest["domain_id"]).encode("utf-8"))
    for name in sorted(manifest["row_counts"]):
        frame = pd.read_parquet(lake.relation_path(release_id, name))
        here = shape.get(name) or {
            "columns": sorted(frame.columns), "key_columns": []}
        _digest_frame(digest, name, frame,
                      list(here["columns"]), list(here["key_columns"]))
    return digest.hexdigest()


def categorical_values(release_id: str) -> dict[str, dict[str, object]]:
    """Every categorical column, and what it actually holds.

    The same rule the Data Builder applies: a closed set small enough to
    show is shown, and anything wider is reported by its size and a sample.
    This is what makes the report a check on the FIELD SURFACE and not only
    on the row counts -- a column that exists in the schema and is empty in
    the release is a column the reader will find blank.
    """
    import pandas as pd

    from backend.cockpit_v4 import lake

    manifest = lake.read_manifest(release_id)
    shape = _published_shape(manifest)
    out: dict[str, dict[str, object]] = {}
    for name in sorted(manifest["row_counts"]):
        here = shape.get(name)
        if not here:
            continue
        frame = pd.read_parquet(lake.relation_path(release_id, name))
        for field in here["fields"]:
            column = str(field["name"])
            # The manifest writes `type`; the /schema endpoint writes
            # `dtype` for the same thing. Read both rather than pick one.
            dtype = str(field.get("type") or field.get("dtype") or "")
            if dtype != "string" or column.endswith("_id"):
                continue
            if column == here["period_column"] or column not in frame.columns:
                continue
            values = sorted(str(v) for v in frame[column].dropna().unique())
            if len(values) > MAX_PRINTED_VALUES:
                out[f"{name}.{column}"] = {
                    "distinct": len(values), "governed": False,
                    "sample": values[:6]}
            else:
                out[f"{name}.{column}"] = {
                    "distinct": len(values), "governed": True,
                    "values": values}
    return out


def report(release_id: str, *, values: bool) -> dict[str, object]:
    from backend.cockpit_v4 import lake

    manifest = lake.read_manifest(release_id)
    periods = list(manifest.get("reporting_periods") or [])
    rows = dict(manifest.get("row_counts") or {})
    relations = list(manifest.get("relations") or [])
    body: dict[str, object] = {
        "release_id": manifest["release_id"],
        "domain_id": manifest["domain_id"],
        "byte_fingerprint": str(manifest.get("release_fingerprint") or ""),
        "bytes_verify": lake.verify(release_id),
        "content_digest": content_digest(release_id),
        "row_counts": rows,
        "total_rows": sum(rows.values()),
        "entity_counts": dict(manifest.get("entity_counts") or {}),
        "relation_count": len(relations),
        "field_count": sum(len(r.get("fields") or []) for r in relations),
        "period_count": len(periods),
        "periods": f"{periods[0]}..{periods[-1]}" if periods else "",
        "reporting_frequency": manifest.get("reporting_frequency", ""),
        "reporting_currency": manifest.get("reporting_currency", ""),
        "amount_scale": manifest.get("amount_scale", ""),
        "built_with": dict(manifest.get("built_with") or {}),
    }
    if values:
        body["categoricals"] = categorical_values(release_id)
    return body


def _print(body: dict[str, object]) -> None:
    print(f"\n  {body['release_id']}  ({body['domain_id']})")
    print(f"    byte fingerprint   {body['byte_fingerprint'][:16]}  "
          f"{'verified' if body['bytes_verify'] else 'MISMATCH'}"
          f"   (machine-specific -- see the module docstring)")
    print(f"    content digest     {str(body['content_digest'])[:16]}"
          f"   (portable -- this is the one to compare)")
    print(f"    rows               {body['total_rows']:,}")
    for name, count in sorted(dict(body["row_counts"]).items()):
        print(f"      {name:28s} {count:>9,}")
    entities = dict(body["entity_counts"])
    if entities:
        print("    entities           " + "  ".join(
            f"{k}={v:,}" for k, v in sorted(entities.items())))
    print(f"    relations          {body['relation_count']}")
    print(f"    fields             {body['field_count']}")
    print(f"    periods            {body['period_count']}  "
          f"{body['periods']}  ({body['reporting_frequency']})")
    print(f"    amounts            {body['reporting_currency']} "
          f"{body['amount_scale']}")
    built = dict(body.get("built_with") or {})
    if built:
        # Recorded so that a disagreement between two machines is a lookup
        # rather than a day's forensics. It is NOT a licence to treat two
        # releases as interchangeable because their versions look close.
        print("    built with         " + "  ".join(
            f"{k}={v}" for k, v in sorted(built.items())))
    categoricals = body.get("categoricals")
    if categoricals:
        print("    categorical columns, as the Data Builder serves them:")
        for key, info in sorted(dict(categoricals).items()):
            if info.get("governed"):
                shown = ", ".join(str(v) for v in info["values"][:12])
                more = ("" if len(info["values"]) <= 12
                        else f", +{len(info['values']) - 12} more")
                print(f"      {key:52s} {info['distinct']:>4} governed"
                      f"  [{shown}{more}]")
            else:
                shown = ", ".join(str(v) for v in info["sample"])
                print(f"      {key:52s} {info['distinct']:>4} sampled "
                      f"  [{shown}, ...]")


def _patterns() -> int:
    """The planted patterns, read straight out of the published parquet.

    The same facts `tests/cockpit_v4/test_planted_patterns.py` asserts, in a
    form somebody can run without pytest and read without knowing what an
    oracle is.
    """
    import pandas as pd

    from backend.cockpit_v4 import domains as dom
    from backend.cockpit_v4 import lake
    from backend.cockpit_v4.generate import corporate as corp_gen
    from backend.cockpit_v4.generate import retail as retail_gen

    def frame(domain_id, relation):
        return pd.read_parquet(lake.relation_path(
            dom.DEFAULT_RELEASES[domain_id], relation))

    accounts = frame(dom.RETAIL, "retail_account_month")
    last = accounts[accounts.reporting_month == accounts.reporting_month.max()]
    print("\n  Retail patterns")

    new = accounts[accounts["product"] == retail_gen.NEW_PRODUCT]
    new_last = last[last["product"] == retail_gen.NEW_PRODUCT]
    rest_last = last[last["product"] != retail_gen.NEW_PRODUCT]
    print(f"    R1  {retail_gen.NEW_PRODUCT}: first month "
          f"{new['reporting_month'].min()}, "
          f"{len(new_last)/len(last)*100:.1f}% of accounts, "
          f"30+ {(new_last.dpd_days >= 30).mean()*100:.2f}% against "
          f"{(rest_last.dpd_days >= 30).mean()*100:.2f}% for the rest")

    product, sub, employment = retail_gen.GOLD_COHORT
    cohort = accounts[(accounts["product"] == product)
                      & (accounts.sub_product == sub)
                      & (accounts.employment_type == employment)]
    early = cohort[(cohort.reporting_month >= retail_gen.GOLD_FROM)
                   & (cohort.dpd_days.between(1, 29))]
    print(f"    R2  {product} - {sub} x {employment} from "
          f"{retail_gen.GOLD_FROM}: "
          f"{(early.delinquency_bucket_fine == '20-29').mean()*100:.0f}% of "
          f"its 1-29 population is in 20-29")

    def weighted(month):
        here = accounts[accounts.reporting_month == month]
        bad = here[here.dpd_days >= 30].ead_sar_mn.sum()
        return bad / here.ead_sar_mn.sum() * 100, (here.dpd_days >= 30).mean() * 100

    first_month = accounts.reporting_month.min()
    last_month = accounts.reporting_month.max()
    (bal0, cnt0), (bal1, cnt1) = weighted(first_month), weighted(last_month)
    print(f"    R3  {first_month}..{last_month}: 30+ by balance "
          f"{bal0:.3f}% -> {bal1:.3f}%, by account {cnt0:.2f}% -> {cnt1:.2f}%"
          f"   (the two disagree in sign)")

    aged = accounts[accounts.months_on_book.between(6, 9)]
    bad_v = aged[aged.origination_month.isin(retail_gen.BAD_VINTAGE_MONTHS)]
    ok_v = aged[~aged.origination_month.isin(retail_gen.BAD_VINTAGE_MONTHS)]
    print(f"    R4  vintage {retail_gen.BAD_VINTAGE_MONTHS[0]}.."
          f"{retail_gen.BAD_VINTAGE_MONTHS[-1]} at 6-9 months on book: "
          f"{(bad_v.dpd_days >= 30).mean()*100:.2f}% against "
          f"{(ok_v.dpd_days >= 30).mean()*100:.2f}%")

    entries = accounts[accounts.dpd_days.between(1, 29)]
    rate = (entries.groupby("reporting_month").size()
            / accounts.groupby("reporting_month").size() * 100)
    print("    R5  seasonal entries: " + "  ".join(
        f"{m} {rate.get(m, float('nan')):.2f}%"
        for m in retail_gen.SEASON_MONTHS))

    product, sub, region = retail_gen.REGION_POCKET
    pocket = last[(last["product"] == product) & (last.sub_product == sub)]
    inside = pocket[pocket.region == region]
    outside = pocket[pocket.region != region]
    print(f"    R6  {product} - {sub} in {region}: 30+ "
          f"{(inside.dpd_days >= 30).mean()*100:.2f}% against "
          f"{(outside.dpd_days >= 30).mean()*100:.2f}% elsewhere")

    print("\n  Corporate patterns")
    borrowers = frame(dom.CORPORATE, "corp_borrower_quarter")
    facilities = frame(dom.CORPORATE, "corp_facility_quarter")
    construction = borrowers[borrowers.sector == "Construction"]
    cohort = construction[
        construction.sub_sector == corp_gen.EARLY_SUB_SECTOR]
    rest = construction[construction.sub_sector != corp_gen.EARLY_SUB_SECTOR]

    def dscr(f, q):
        return f[f.reporting_quarter == q].dscr_x.mean()

    latest = borrowers.reporting_quarter.max()
    print(f"    C7  {corp_gen.EARLY_SUB_SECTOR} from "
          f"{corp_gen.EARLY_FROM_QUARTER}: DSCR 2025Q3 "
          f"{dscr(cohort, '2025Q3'):.2f}/{dscr(rest, '2025Q3'):.2f} -> "
          f"{latest} {dscr(cohort, latest):.2f}/{dscr(rest, latest):.2f}")

    groups = borrowers[["borrower_id", "reporting_quarter",
                        "group_name"]].drop_duplicates()
    joined = facilities.merge(
        groups, on=["borrower_id", "reporting_quarter"], how="left")
    grouped = joined[joined.group_name == corp_gen.CONCENTRATION_GROUP]

    def total(q):
        return grouped[grouped.reporting_quarter == q].ead_sar_mn.sum()

    biggest = grouped[grouped.reporting_quarter == latest].groupby(
        "borrower_name").ead_sar_mn.sum().max()
    print(f"    C8  {corp_gen.CONCENTRATION_GROUP}: aggregate EAD 2025Q4 "
          f"{total('2025Q4'):,.0f} -> {latest} {total(latest):,.0f}, "
          f"largest single name {biggest:,.0f}")

    last_q = facilities[facilities.reporting_quarter == latest]
    scf = last_q[last_q.product_type == corp_gen.NEW_PRODUCT_TYPE]
    everything = facilities[
        facilities.product_type == corp_gen.NEW_PRODUCT_TYPE]
    print(f"    C10 {corp_gen.NEW_PRODUCT_TYPE}: first quarter "
          f"{everything.reporting_quarter.min()}, at {latest} "
          f"{scf.ead_sar_mn.sum()/last_q.ead_sar_mn.sum()*100:.2f}% of EAD "
          f"and {scf.ecl_sar_mn.sum()/last_q.ecl_sar_mn.sum()*100:.2f}% of "
          f"ECL, stage 3 {(scf.stage == 3).mean()*100:.2f}%")
    return 0


def main() -> int:
    from backend.cockpit_v4 import domains as dom
    from backend.cockpit_v4 import lake

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="append", default=[],
                        help="A release id. Repeatable. Defaults to the "
                             "release each domain currently points at.")
    parser.add_argument("--no-values", action="store_true",
                        help="Skip the categorical column listing.")
    parser.add_argument("--patterns", action="store_true",
                        help="Also print the planted patterns, read from "
                             "the parquet of the CURRENT default releases.")
    args = parser.parse_args()

    wanted = args.release or [dom.DEFAULT_RELEASES[d] for d in dom.DOMAIN_IDS]
    missing = [r for r in wanted if not lake.exists(r)]
    if missing:
        for release_id in missing:
            print(f"  {release_id}  NOT PUBLISHED -- run "
                  f"scripts/cockpit_v4/seed_domains.py")
        return 1

    print("READ ONLY. Nothing below writes to the lake.")
    for release_id in wanted:
        _print(report(release_id, values=not args.no_values))
    if args.patterns:
        _patterns()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
