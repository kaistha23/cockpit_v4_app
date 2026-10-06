"""Contract-based scoring of a Cockpit run against golden gold results.

WHY this exists: the first golden scorer (`legacy_result_match`) asked whether
each gold number appeared ANYWHERE in the run's stored results. That passes
wrong answers: a superset of rows, a swapped label, or two measures exchanged
all still "contain" the gold numbers. Here a gold row is aligned to a run row
by its entity key columns, and each measure is compared in its own column
only, so a result passes only if it states the right figure for the right
entity. Everything in this module is pure (no DB, no network) so the rules are
unit-testable with synthetic rows.

Verdicts: "pass" | "fail" | "unscorable". "unscorable" means there was nothing
to compare (no stored result table, no contract); callers must NOT treat it as
a pass, and the report keeps it out of eligible accuracy but inside total
coverage.
"""
from __future__ import annotations

import re
from typing import Any

DEFAULT_TOLERANCE = {"rel": 0.005, "abs": 1e-6}


# --------------------------------------------------------------- primitives

def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def norm_label(value: Any) -> str | None:
    """Label form used for key alignment; None stays None so NULL matches NULL."""
    if value is None:
        return None
    if _is_num(value) and float(value) == int(value):
        return str(int(value))
    return str(value).strip().casefold()


def close(a: float, b: float, tol: dict | None = None) -> bool:
    t = {**DEFAULT_TOLERANCE, **(tol or {})}
    return abs(a - b) <= max(t["abs"], t["rel"] * max(abs(a), abs(b)))


def _as_dict_rows(rows: list, cols: list[str] | None) -> list[dict]:
    out = []
    for row in rows or []:
        if isinstance(row, dict):
            out.append(row)
        else:
            out.append(dict(zip(cols or [], row)))
    return out


def _find_col(columns: list[str], *names: str | None) -> str | None:
    lowered = {c.casefold(): c for c in columns}
    for name in names:
        if name and name.casefold() in lowered:
            return lowered[name.casefold()]
    return None


_PREFIXES = ("total_", "sum_", "avg_", "average_", "mean_")
#: Unit suffixes are decoration too: a run that writes `SUM(ecl_sar_mn) AS
#: total_ecl` answered with the same figure. Stripping them cannot pass a
#: wrong answer -- values are still compared per column after the match.
_SUFFIXES = ("_total", "_sum", "_sar_mn", "_mn")


def norm_column(name: str) -> str:
    """Lowercase and strip aggregate decorations: total_ecl_sar_mn -> ecl_sar_mn."""
    n = name.casefold().strip()
    changed = True
    while changed:
        changed = False
        for p in _PREFIXES:
            if n.startswith(p) and len(n) > len(p):
                n, changed = n[len(p):], True
        for x in _SUFFIXES:
            if n.endswith(x) and len(n) > len(x):
                n, changed = n[:-len(x)], True
    return n


def resolve_measures(measures: dict, cols: list[str], reserved: set[str]) -> tuple[dict, list[str], list[str]]:
    """Map each gold measure to one run column: exact, contract alias, then normalised name.

    WHY not by value: matching on numbers would let a swapped-measure answer
    find its own figures. Names only; ambiguity or absence is a failure that
    lists the candidates. Assignment is one-to-one.
    Returns (mapping gold->run column or None, failure reasons, notes).
    """
    mapping: dict[str, str | None] = {}
    reasons: list[str] = []
    notes: list[str] = []
    claimed: set[str] = set(reserved)
    pending = []
    for m, alias in measures.items():
        found = _find_col(cols, m, alias)
        if found is not None:
            mapping[m] = found
            claimed.add(found)
        else:
            pending.append(m)
    picks: dict[str, str] = {}
    for m in pending:
        pool = [c for c in cols if c not in claimed]
        gn = norm_column(m)
        hit = [c for c in pool if norm_column(c) == gn]
        if not hit:
            gt = set(gn.split("_"))
            hit = [c for c in pool if gt <= set(norm_column(c).split("_"))]
        if len(hit) == 1:
            picks[m] = hit[0]
        elif len(hit) > 1:
            mapping[m] = None
            reasons.append(f"measure '{m}' is ambiguous among run columns {hit}")
        else:
            mapping[m] = None
            reasons.append(f"run table has no column for measure '{m}' (columns: {cols})")
    chosen: dict[str, list[str]] = {}
    for m, c in picks.items():
        chosen.setdefault(c, []).append(m)
    for c, ms in chosen.items():
        if len(ms) > 1:
            for m in ms:
                mapping[m] = None
            reasons.append(f"measures {ms} all matched run column '{c}'")
        else:
            mapping[ms[0]] = c
            notes.append(f"measure {ms[0]} matched run column {c} by normalised name")
    return mapping, reasons, notes


def _table_columns(rows: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def derive_contract(gold_rows: list, gold_cols: list[str]) -> dict:
    """Contract for legacy entries: text columns are keys, numeric are measures.

    Percent columns are recognised by the `_pct` suffix, the same convention the
    legacy scorer used.
    """
    rows = _as_dict_rows(gold_rows, gold_cols)
    keys, measures, scale = [], {}, {}
    for col in gold_cols:
        numeric = any(_is_num(r.get(col)) for r in rows)
        textual = any(isinstance(r.get(col), str) for r in rows)
        if numeric and not textual:
            measures[col] = col
            if col.endswith("_pct"):
                scale[col] = "percent"
        else:
            keys.append(col)
    return {"keys": keys, "measures": measures, "scale": scale,
            "cardinality": "exact", "tolerance": dict(DEFAULT_TOLERANCE)}


def _measure_ok(gold: Any, run: Any, col: str, contract: dict) -> bool:
    tol = contract.get("tolerance")
    if gold is None or run is None:
        return gold is None and run is None
    if _is_num(gold):
        if not _is_num(run):
            return False
        forms = [float(gold)]
        if (contract.get("scale") or {}).get(col) == "percent":
            forms.append(float(gold) / 100)    # run may state the fraction
        return any(close(f, float(run), tol) for f in forms)
    return norm_label(gold) == norm_label(run)


# --------------------------------------------------------------- contract

def _score_table(gold: list[dict], gold_cols: list[str], table: list[dict],
                 contract: dict) -> dict:
    keys = list(contract.get("keys") or [])
    measures = dict(contract.get("measures") or {})
    exact = (contract.get("cardinality") or "exact") == "exact"
    reasons: list[str] = []
    cols = _table_columns(table)

    key_map, measure_map = {}, {}
    for k in keys:
        found = _find_col(cols, k, measures.get(k))
        if found is None:
            reasons.append(f"run table has no column for key '{k}'")
        key_map[k] = found
    key_cols = {c for c in key_map.values() if c}
    data_measures = {m: a for m, a in measures.items() if m not in keys}
    measure_map, m_reasons, notes = resolve_measures(data_measures, cols, key_cols)
    reasons.extend(m_reasons)
    base = {"matched": 0, "missing": [], "extra": [], "mismatched": []}
    if reasons:
        return {"verdict": "fail", "reasons": reasons, "notes": notes, **base}

    run_index: dict[tuple, dict] = {}
    duplicates = []
    for row in table:
        key = tuple(norm_label(row.get(key_map[k])) for k in keys)
        if key in run_index:
            duplicates.append(key)
        run_index[key] = row
    if duplicates:
        return {"verdict": "fail", "matched": 0, "missing": [], "extra": [],
                "mismatched": [],
                "reasons": [f"duplicate keys in run table: {sorted(set(map(str, duplicates)))[:5]}"]}

    gold_key_cols = {k: _find_col(gold_cols, k) or k for k in keys}
    seen = set()
    matched = 0
    missing, mismatched = [], []
    for g in gold:
        key = tuple(norm_label(g.get(gold_key_cols[k])) for k in keys)
        seen.add(key)
        row = run_index.get(key)
        if row is None:
            missing.append(key)
            continue
        bad = [m for m in measure_map
               if not _measure_ok(g.get(_find_col(gold_cols, m) or m),
                                  row.get(measure_map[m]), m, contract)]
        if bad:
            mismatched.append({"key": key, "columns": bad})
        else:
            matched += 1
    extra = [k for k in run_index if k not in seen]
    if missing:
        reasons.append(f"{len(missing)} gold entities missing from run")
    if mismatched:
        reasons.append(f"{len(mismatched)} entities with wrong measure values: "
                       f"{mismatched[:3]}")
    if extra and exact:
        reasons.append(f"{len(extra)} extra entities under exact cardinality")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "notes": notes,
            "matched": matched, "missing": missing, "extra": extra,
            "mismatched": mismatched}


def score_contract(gold_rows: list, gold_cols: list[str],
                   run_tables: list[list[dict]], contract: dict | None) -> dict:
    """Align gold rows to run rows by key and compare each measure in its column.

    Tries every stored run result table and keeps the best (a run often stores
    scratch steps next to the answer table); `table_index` records which.
    """
    if not contract:
        return {"verdict": "unscorable", "reasons": ["no contract"], "matched": 0,
                "missing": [], "extra": []}
    tables = [t for t in (run_tables or []) if t]
    if not tables:
        return {"verdict": "unscorable", "reasons": ["no run result rows"],
                "matched": 0, "missing": [], "extra": []}
    gold = _as_dict_rows(gold_rows, gold_cols)
    best, best_i = None, 0
    for i, table in enumerate(tables):
        res = _score_table(gold, gold_cols, _as_dict_rows(table, None), contract)
        rank = (res["verdict"] == "pass", res["matched"], -len(res["reasons"]))
        if best is None or rank > best[0]:
            best, best_i = (rank, res), i
    result = dict(best[1])
    result["reasons"] = result["reasons"] + result.get("notes", [])
    result["table_index"] = best_i
    result["tables_tried"] = len(tables)
    return result


# --------------------------------------------------------------- weak (no gold SQL)

def _numbers(rows: list[dict]) -> list[float]:
    return [float(v) for r in rows for v in r.values() if _is_num(v)]


def score_values(expected: dict | None, run_rows: list[dict],
                 narrative: str = "") -> dict:
    """Bag-of-numbers check for entries that only have expected values/text.

    Deliberately labelled weak: any expected number found anywhere counts, so a
    pass here is weaker evidence than a contract pass.
    """
    expected = expected or {}
    values = expected.get("values") or []
    texts = expected.get("text_contains") or []
    if not values and not texts:
        return {"verdict": "unscorable", "weak": True, "reasons": ["empty expectation"]}
    reasons = []
    if values:
        if not run_rows:
            return {"verdict": "unscorable", "weak": True, "reasons": ["no run result rows"]}
        pool = _numbers(run_rows)
        found = sum(any(close(float(v), c) for c in pool) for v in values)
        if found / len(values) < 0.95:
            reasons.append(f"{len(values) - found} of {len(values)} expected values not found")
    for t in texts:
        if t.casefold() not in (narrative or "").casefold():
            reasons.append(f"text not in narrative: {t!r}")
    return {"verdict": "fail" if reasons else "pass", "weak": True, "reasons": reasons}


# --------------------------------------------------------------- headline

_DISPLAY_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def parse_display(value: Any) -> float | None:
    """First number in a published display string such as 'SAR 275,512 million'."""
    if _is_num(value):
        return float(value)
    m = _DISPLAY_NUM.search(str(value or ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _claim_target(claim: dict, artifacts: dict[str, list[dict]], keys: list[str]) -> tuple[str | None, str | None]:
    """(column, entity label) a claim is about, when it names one directly."""
    ev = claim.get("evidence") or {}
    col, entity = ev.get("column_id") or None, ev.get("row_key") or None
    if col and entity:
        return col, entity
    d = claim.get("derivation") or {}
    ops = d.get("operands") or []
    if d.get("operation") == "identity" and len(ops) == 1:
        op = ops[0]
        ids = op.get("row_ids") or []
        rows = artifacts.get(op.get("artifact_id") or "")
        if len(ids) == 1 and rows is not None:
            m = re.fullmatch(r"r(\d+)", str(ids[0]))
            if m and int(m.group(1)) < len(rows):
                row = rows[int(m.group(1))]
                label = _find_col(list(row), *keys) if keys else None
                return op.get("column_id"), (row.get(label) if label else None)
    return col, entity


def score_headline(final_response: dict | None, gold_rows: list,
                   contract: dict | None, gold_cols: list[str] | None = None,
                   artifacts: dict[str, list[dict]] | None = None) -> dict:
    """Check the published numeric claims that name a gold entity and measure.

    The stored table can be right while the headline the user reads is wrong,
    so claims are compared to gold per (entity, column) within tolerance (or
    the claim's own display rounding). Claims about other things are ignored;
    with nothing comparable the verdict is "unscorable".
    """
    claims = (final_response or {}).get("numeric_claims") or []
    if not contract or not claims:
        return {"verdict": "unscorable", "reasons": ["no contract or claims"], "checked": 0}
    gold_cols = gold_cols or (list(gold_rows[0]) if gold_rows and isinstance(gold_rows[0], dict) else [])
    gold = _as_dict_rows(gold_rows, gold_cols)
    keys = list(contract.get("keys") or [])
    measures = dict(contract.get("measures") or {})
    artifacts = artifacts or {}
    alias_to_gold = {}
    for m, alias in measures.items():
        alias_to_gold[m.casefold()] = m
        if alias:
            alias_to_gold[alias.casefold()] = m
    by_entity: dict[str, list[dict]] = {}
    for g in gold:
        for k in keys:
            by_entity.setdefault(norm_label(g.get(_find_col(gold_cols, k) or k)) or "", []).append(g)
    if not keys and len(gold) == 1:
        by_entity[""] = gold
    checked, bad = 0, []
    for claim in claims:
        col, entity = _claim_target(claim, artifacts, keys)
        m = alias_to_gold.get((col or "").casefold())
        if m is None:
            continue
        candidates = by_entity.get(norm_label(entity) or "")
        if candidates is None and not keys and len(gold) == 1 and not entity:
            candidates = gold
        if not candidates:
            continue
        claimed = parse_display(claim.get("display_value"))
        gval = candidates[0].get(_find_col(gold_cols, m) or m)
        if claimed is None or not _is_num(gval):
            continue
        prec = claim.get("display_precision")
        tol = dict(contract.get("tolerance") or DEFAULT_TOLERANCE)
        if isinstance(prec, int):
            tol["abs"] = max(tol.get("abs", 0), 0.5 * 10 ** (-prec))
        forms = [float(gval)]
        scale = (contract.get("scale") or {}).get(m)
        if scale == "percent":
            forms.append(float(gval) / 100)
        if scale == "fraction" and claim.get("unit") == "percent":
            forms.append(float(gval) * 100)
        checked += 1
        if not any(close(f, claimed, tol) for f in forms):
            bad.append({"claim_id": claim.get("claim_id"), "claimed": claimed, "gold": gval})
    if not checked:
        return {"verdict": "unscorable", "reasons": ["no claim names a gold entity"], "checked": 0}
    return {"verdict": "fail" if bad else "pass", "checked": checked,
            "reasons": [f"{len(bad)} claims disagree with gold: {bad[:3]}"] if bad else []}


# --------------------------------------------------------------- legacy

def legacy_result_match(gold_rows: list[tuple], gold_cols: list[str], run_rows: list[dict]) -> float:
    """The original bag-of-numbers match, kept only to compare against contracts."""
    candidates = _numbers(run_rows)
    cells = []
    for row in gold_rows:
        for col, value in zip(gold_cols, row):
            if _is_num(value):
                cells.append((col, float(value)))
    if not cells:
        return 1.0
    found = 0
    for col, value in cells:
        forms = [value] + ([value / 100] if col.endswith("_pct") else [])
        if any(close(f, c) for f in forms for c in candidates):
            found += 1
    return found / len(cells)
