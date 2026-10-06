"""Grow and maintain the golden question set (tests/cockpit_v4/golden/questions.json).

The golden set is only useful for qualifying a model if three things hold:
  1. it is big enough that one flaky question does not move the score;
  2. the questions a model is tuned on (train) are never the ones it is judged
     on (holdout / sealed) -- and a paraphrase of a train question counts as
     the same question, so splits are assigned per `group`, never per item;
  3. every entry says what a correct answer *is* (a contract: keys, measures,
     units, scale, tolerance), not just a SQL string, so scoring does not have
     to guess.

This tool is the one place that writes the file. Subcommands:

  --from-csv / --from-xlsx PATH   merge hand-written questions by id
  --bootstrap-question-bank       export tests/cockpit_v4/question_bank.py cases
  --derive-contracts              fill contract (and source "golden") on legacy entries
                                  whose gold_sql GROUP BY can be read
  --group-twins                   merge near-duplicate questions (token Jaccard >= 0.6)
                                  into one group, so twins always share a split
  --assign-splits [--seed N]      group-stratified, deterministic train/holdout/sealed
  --promote RUN_ID --id ID        take a run's SQL as the gold SQL (needs --yes)
  --validate                      schema check; non-zero exit on any problem

Schema (version 2; old fields keep their meaning, so golden_eval.py's reading of
id / group / domain / question / gold_sql is unaffected):

  id, group, domain (corporate|retail), mode (standard|deep), question,
  split (train|holdout|sealed), status (ready|pending_formula),
  expect_state (COMPLETED|WAITING_FOR_USER), release_id, source
  (golden|question_bank|user), contract|null, gold_sql|null,
  expected {values, text_contains}|null, tags.

Writes only questions.json (and, for --promote, reads the state DB read-only).
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import random
import re
import sqlite3
import sys
from typing import Any, Iterable

ROOT = pathlib.Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "tests" / "cockpit_v4" / "golden" / "questions.json"
DEFAULT_DB = "~/.creditprobe/cockpit_v4_round_h_live_pass/state/cockpit_v4.sqlite3"

SCHEMA_VERSION = 2
ABOUT = (
    "Golden questions for measuring a model on Cockpit V4 (slm_v1 pattern), schema 2. "
    "Each entry carries a split (train|holdout|sealed), a status (ready|pending_formula), "
    "the state a correct run must end in (expect_state), and a contract (keys, measures, "
    "units, scale, cardinality, tolerance) describing what a correct answer is. gold_sql "
    "computes the figures a correct answer must contain; the evaluator checks that those "
    "figures appear in the run's stored results (result_match) and that the narrative's "
    "numbers come from those results (answer_grounded). 'group' ties paraphrases together: "
    "splits are assigned per group so a paraphrase of a train question can never sit in "
    "holdout or sealed."
)

DOMAINS = ("corporate", "retail")
MODES = ("standard", "deep")
SPLITS = ("train", "holdout", "sealed")
STATUSES = ("ready", "pending_formula")
STATES = ("COMPLETED", "WAITING_FOR_USER")
SOURCES = ("golden", "question_bank", "user")
SCALES = ("percent", "fraction")
CARDINALITIES = ("exact", "subset")

#: Split shares. Sealed is the set nobody looks at while tuning, so it gets a
#: fixed small slice (15-20 items on the current ~96) rather than a free share.
SPLIT_SHARE = {"train": 0.63, "holdout": 0.20, "sealed": 0.17}

DEFAULT_TOLERANCE = {"rel": 0.005, "abs": 1e-6}


class ImportError_(ValueError):
    """Bad input to the importer (distinct from the builtin ImportError)."""


# ------------------------------------------------------------- file io

def load(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return list(json.loads(path.read_text()).get("questions", []))


def save(path: pathlib.Path, entries: list[dict]) -> None:
    """One entry per line, like the hand-written file, so diffs stay readable."""
    lines = [json.dumps(normalize(e), ensure_ascii=False) for e in entries]
    body = ",\n    ".join(lines)
    text = ('{\n  "about": %s,\n  "schema": %d,\n  "questions": [\n    %s\n  ]\n}\n'
            % (json.dumps(ABOUT, ensure_ascii=False), SCHEMA_VERSION, body))
    path.write_text(text)


def normalize(entry: dict) -> dict:
    """Fill schema-2 defaults, leaving every value already present alone.

    Old (schema-1) entries have only id/group/domain/question/gold_sql; this is
    what makes them readable as schema 2 without anyone editing them by hand.
    """
    e = dict(entry)
    e.setdefault("group", e.get("id"))
    e.setdefault("mode", "standard")
    e.setdefault("split", None)
    e.setdefault("status", "ready")
    e.setdefault("expect_state", "COMPLETED")
    e.setdefault("release_id", None)
    e.setdefault("source", "user")
    e.setdefault("contract", None)
    e.setdefault("gold_sql", None)
    e.setdefault("expected", None)
    e.setdefault("tags", [])
    order = ["id", "group", "domain", "mode", "question", "split", "status",
             "expect_state", "release_id", "source", "contract", "gold_sql",
             "expected", "tags"]
    out = {k: e[k] for k in order if k in e}
    out.update({k: v for k, v in e.items() if k not in out})
    return out


# ------------------------------------------------------------- validation

def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_contract(contract: Any) -> list[str]:
    if contract is None:
        return []
    if not isinstance(contract, dict):
        return ["contract must be an object or null"]
    p: list[str] = []
    keys = contract.get("keys", [])
    if not isinstance(keys, list) or not all(isinstance(k, str) and k for k in keys):
        p.append("contract.keys must be a list of column names")
    measures = contract.get("measures", {})
    if not isinstance(measures, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in measures.items()):
        p.append("contract.measures must map column name to gold column name")
    units = contract.get("units", {})
    if not isinstance(units, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v for k, v in units.items()):
        p.append("contract.units must map column name to a unit string")
    scale = contract.get("scale", {})
    if not isinstance(scale, dict) or any(v not in SCALES for v in scale.values()):
        p.append(f"contract.scale values must be one of {SCALES}")
    if contract.get("cardinality", "exact") not in CARDINALITIES:
        p.append(f"contract.cardinality must be one of {CARDINALITIES}")
    tol = contract.get("tolerance", DEFAULT_TOLERANCE)
    if (not isinstance(tol, dict) or not tol
            or set(tol) - {"rel", "abs"}
            or not all(_is_num(v) and v >= 0 for v in tol.values())):
        p.append("contract.tolerance must be {rel, abs} with non-negative numbers")
    return p


def validate_entry(e: dict) -> list[str]:
    p: list[str] = []
    for k in ("id", "question"):
        if not isinstance(e.get(k), str) or not e.get(k).strip():
            p.append(f"{k} is required")
    if e.get("domain") not in DOMAINS:
        p.append(f"domain must be one of {DOMAINS}, got {e.get('domain')!r}")
    if e.get("mode") not in MODES:
        p.append(f"mode must be one of {MODES}, got {e.get('mode')!r}")
    if e.get("split") not in SPLITS:
        p.append(f"split must be one of {SPLITS}, got {e.get('split')!r}"
                 + (" (run --assign-splits)" if e.get("split") is None else ""))
    if e.get("status") not in STATUSES:
        p.append(f"status must be one of {STATUSES}, got {e.get('status')!r}")
    if e.get("expect_state") not in STATES:
        p.append(f"expect_state must be one of {STATES}, got {e.get('expect_state')!r}")
    if e.get("source") not in SOURCES:
        p.append(f"source must be one of {SOURCES}, got {e.get('source')!r}")
    if e.get("release_id") is not None and not isinstance(e.get("release_id"), str):
        p.append("release_id must be a string or null")
    if not isinstance(e.get("group"), str) or not e.get("group"):
        p.append("group must be a non-empty string")
    tags = e.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        p.append("tags must be a list of strings")
    gold = e.get("gold_sql")
    if gold is not None and (not isinstance(gold, str) or not gold.strip()):
        p.append("gold_sql must be a non-empty string or null")
    exp = e.get("expected")
    if exp is not None:
        if not isinstance(exp, dict):
            p.append("expected must be an object or null")
        else:
            if not all(_is_num(v) for v in exp.get("values", [])):
                p.append("expected.values must be numbers")
            if not all(isinstance(v, str) for v in exp.get("text_contains", [])):
                p.append("expected.text_contains must be strings")
    if (e.get("expect_state") == "COMPLETED" and e.get("status") == "ready"
            and not gold and not exp):
        p.append("a ready COMPLETED question needs gold_sql or expected")
    p.extend(validate_contract(e.get("contract")))
    return [f"{e.get('id', '?')}: {m}" for m in p]


def validate_all(entries: list[dict]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for e in entries:
        if e.get("id") in seen:
            problems.append(f"{e.get('id')}: duplicate id")
        seen.add(e.get("id"))
        problems.extend(validate_entry(e))
    spans: dict[str, set] = {}
    for e in entries:
        if e.get("split") in SPLITS:
            spans.setdefault(e.get("group") or e.get("id"), set()).add(e["split"])
    for g, s in sorted(spans.items()):
        if len(s) > 1:
            problems.append(f"group {g!r}: paraphrases span more than one split {sorted(s)}")
    for a, b, sim in cross_split_twins(entries):
        problems.append(f"{a} and {b}: near-duplicate questions (Jaccard {sim:.2f}) "
                        "sit in different splits")
    return problems


# ------------------------------------------------------------- merging

def merge_entries(existing: list[dict], incoming: Iterable[dict], *,
                  keep_existing: tuple[str, ...] = ()) -> tuple[list[dict], int, int]:
    """Merge by id: update in place, append new. Returns (entries, added, updated).

    `keep_existing` names fields an update must not overwrite (a re-bootstrap
    must not undo a split that was already assigned).
    """
    out = [dict(e) for e in existing]
    index = {e["id"]: i for i, e in enumerate(out)}
    added = updated = 0
    for new in incoming:
        i = index.get(new["id"])
        if i is None:
            out.append(dict(new))
            index[new["id"]] = len(out) - 1
            added += 1
        else:
            for k, v in new.items():
                if k in keep_existing and out[i].get(k) is not None:
                    continue
                out[i][k] = v
            updated += 1
    return out, added, updated


# ------------------------------------------------------------- csv / xlsx

def _split_list(text: str) -> list[str]:
    return [t.strip() for t in str(text or "").split(";") if t.strip()]


def row_to_entry(row: dict[str, Any], lineno: int) -> dict:
    """One CSV/XLSX row -> a partial entry. Only non-empty cells are carried, so
    merging a sheet that lists a few columns does not blank the rest."""
    r = {str(k).strip(): ("" if v is None else str(v).strip())
         for k, v in row.items() if k is not None}
    where = f"row {lineno}"
    if not r.get("id"):
        raise ImportError_(f"{where}: id is required")
    e: dict[str, Any] = {"id": r["id"]}
    for k in ("domain", "question", "gold_sql", "split", "status",
              "expect_state", "group", "mode", "release_id", "source"):
        if r.get(k):
            e[k] = r[k]
    if r.get("expect_state"):
        e["expect_state"] = r["expect_state"].upper()
    if r.get("tags"):
        e["tags"] = _split_list(r["tags"])
    expected: dict[str, Any] = {}
    if r.get("expected_values"):
        try:
            expected["values"] = [float(v) for v in _split_list(r["expected_values"])]
        except ValueError:
            raise ImportError_(f"{where} ({r['id']}): expected_values must be "
                               "semicolon-separated numbers") from None
    if r.get("expected_text"):
        expected["text_contains"] = _split_list(r["expected_text"])
    if expected:
        e["expected"] = expected
    contract: dict[str, Any] = {}
    if r.get("keys"):
        contract["keys"] = _split_list(r["keys"])
    if r.get("measures"):
        contract["measures"] = {m.split(":")[0].strip(): m.split(":")[-1].strip()
                                for m in _split_list(r["measures"])}
    if r.get("cardinality"):
        contract["cardinality"] = r["cardinality"]
    if r.get("tol_rel") or r.get("tol_abs"):
        try:
            contract["tolerance"] = {
                "rel": float(r.get("tol_rel") or DEFAULT_TOLERANCE["rel"]),
                "abs": float(r.get("tol_abs") or DEFAULT_TOLERANCE["abs"])}
        except ValueError:
            raise ImportError_(f"{where} ({r['id']}): tol_rel/tol_abs must be numbers") from None
    if contract:
        e["contract"] = contract
    return e


def entries_from_rows(rows: Iterable[dict[str, Any]], existing: list[dict]) -> list[dict]:
    """Rows -> validated entries; refuses duplicate ids and bad enums up front,
    before anything is written."""
    incoming: list[dict] = []
    seen: set[str] = set()
    known = {e["id"]: e for e in existing}
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        e = row_to_entry(row, n)
        if e["id"] in seen:
            raise ImportError_(f"row {n}: duplicate id {e['id']!r} in input")
        seen.add(e["id"])
        incoming.append(e)
    problems: list[str] = []
    for e in incoming:
        base = normalize(known.get(e["id"], {"source": "user"}) | {"id": e["id"]})
        merged = {**base, **{k: v for k, v in e.items() if k != "contract"}}
        if "contract" in e:
            merged["contract"] = {**(base.get("contract") or {}), **e["contract"]}
        if merged.get("split") is None:
            merged["split"] = SPLITS[0]  # unassigned is fine here; --assign-splits fills it
        problems.extend(validate_entry(merged))
    if problems:
        raise ImportError_("; ".join(problems))
    return incoming


def read_csv(path: pathlib.Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def read_xlsx(path: pathlib.Path) -> list[dict]:
    try:
        import openpyxl  # type: ignore
    except ImportError:
        raise ImportError_("openpyxl is not installed; use --from-csv or "
                           "`pip install openpyxl`") from None
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows, [])]
    out = []
    for r in rows:
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        out.append({h: c for h, c in zip(header, r) if h})
    return out


# ------------------------------------------------------------- bootstrap

_RATIO_AVG = re.compile(r"AVG\s*\([^)]*(_pct\b|\bpd_|\blgd)", re.I)
_ECL_RECOMPUTE = re.compile(r"recomput|recalc|sensitiv|migrat|scenario|stress", re.I)
_PD_LGD_EAD = re.compile(r"\bpd_\w*\s*\*\s*\(?\s*\w*lgd", re.I)


def needs_formula(question: str, sql: str, tags: Iterable[str] = ()) -> bool:
    """True where the SQL cannot be trusted as a gold without a stated formula.

    AVG over a ratio column weights every row equally (an average of PDs is not
    the portfolio PD), and an ECL that is recomputed, stressed or migrated
    depends on a methodology the book does not store. Both need a human to
    write the formula down before the question can be `ready`.
    """
    if _RATIO_AVG.search(sql):
        return True
    ecl = re.search(r"\becl\b|expected credit loss|ecl_", question + " " + sql, re.I)
    if ecl and (_ECL_RECOMPUTE.search(question) or _PD_LGD_EAD.search(sql)):
        return True
    return False


def _unit_scale(unit: str, column: str) -> tuple[str, str | None]:
    """The case's unit for display, plus a scale for percent-valued columns
    (a `_pct` column is 0-100; the bank's share/coverage columns are fractions)."""
    if unit == "percent":
        return "percent", ("percent" if column.endswith("_pct") else "fraction")
    return unit, None


def case_to_entry(case: Any) -> dict:
    keys = [case.key] if case.key else []
    measures = {case.value: case.value} if case.value else {}
    units: dict[str, str] = {}
    scale: dict[str, str] = {}
    if case.value:
        u, s = _unit_scale(case.units, case.value)
        units[case.value] = u
        if s:
            scale[case.value] = s
    contract = {"keys": keys, "measures": measures, "units": units, "scale": scale,
                "cardinality": "exact",
                "tolerance": {"rel": float(case.tolerance), "abs": DEFAULT_TOLERANCE["abs"]}}
    pending = needs_formula(case.question, case.sql, case.tags)
    return normalize({
        "id": "qb_" + case.case_id.lower(), "group": "qb_" + case.case_id.lower(),
        "domain": case.domain_id, "mode": "standard", "question": case.question,
        "split": None, "status": "pending_formula" if pending else "ready",
        "expect_state": "COMPLETED", "release_id": None, "source": "question_bank",
        "contract": contract, "gold_sql": case.sql, "expected": None,
        "tags": list(case.tags)})


def bootstrap_entries(cases: Iterable[Any]) -> list[dict]:
    return [case_to_entry(c) for c in cases]


# ------------------------------------------------------------- derive contract

def _top_level_select_list(sql: str) -> list[str] | None:
    """Items of the last depth-0 SELECT ... FROM, split on depth-0 commas."""
    depth, i, start, last = 0, 0, None, None
    up = sql.upper()
    while i < len(sql):
        c = sql[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and re.match(r"\bSELECT\b", up[i:]) and (i == 0 or not up[i - 1].isalnum()):
            last = i + 6
        i += 1
    if last is None:
        return None
    depth, items, cur, i = 0, [], "", last
    while i < len(sql):
        c = sql[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if depth == 0 and re.match(r"\s+FROM\b", sql[i:], re.I):
            break
        if depth == 0 and c == ",":
            items.append(cur.strip())
            cur = ""
        else:
            cur += c
        i += 1
    items.append(cur.strip())
    return items


def _item_name(item: str) -> str | None:
    m = re.search(r"\bAS\s+([A-Za-z_]\w*)\s*$", item, re.I)
    if m:
        return m.group(1)
    return item if re.fullmatch(r"[A-Za-z_]\w*", item) else None


def derive_contract(sql: str | None) -> dict | None:
    """Keys from the final GROUP BY, measures from the other select columns.

    Returns None when the SQL is not a simple grouped query we can read; a
    wrong contract is worse than no contract, since the scorer would trust it.
    """
    if not sql:
        return None
    items = _top_level_select_list(sql)
    if not items:
        return None
    names = [_item_name(i) for i in items]
    if any(n is None for n in names):
        return None
    groups = re.findall(r"GROUP\s+BY\s+([\w\s,]+?)(?=\s+(?:ORDER|LIMIT|HAVING)\b|\)|$)", sql, re.I)
    if groups:
        keys: list[str] = []
        for tok in (t.strip() for t in groups[-1].split(",")):
            if tok.isdigit():
                if not 1 <= int(tok) <= len(names):
                    return None
                keys.append(names[int(tok) - 1])
            else:
                keys.append(tok)
        if any(k not in names for k in keys):
            return None
    else:
        keys = []
    measures = [n for n in names if n not in keys]
    units: dict[str, str] = {}
    scale: dict[str, str] = {}
    for m in measures:
        if m.endswith("_pct"):
            units[m], scale[m] = "percent", "percent"
        elif re.search(r"sar_mn|^ecl|^ead|^balance|^exposure", m):
            units[m] = "SAR million"
        else:
            units[m] = "count"
    return {"keys": keys, "measures": {m: m for m in measures}, "units": units,
            "scale": scale, "cardinality": "exact",
            "tolerance": dict(DEFAULT_TOLERANCE)}


# ------------------------------------------------------------- twins

#: Questions that differ only by period word or by sector/product are the same
#: template: a train twin shown as a few-shot example leaks the answer shape of
#: its holdout/sealed twin. These are dropped or folded before comparing.
TWIN_THRESHOLD = 0.6
_TWIN_STOP = frozenset("the what is in of for by and a which latest quarter month "
                       "this quarters months".split())


def twin_tokens(question: str) -> frozenset[str]:
    t = question.lower()
    t = re.sub(r"product[ _]type", "product", t)
    t = re.sub(r"\bsectors?\b", "product", t)
    t = re.sub(r"\bproducts\b", "product", t)
    return frozenset(w for w in re.findall(r"[a-z0-9_]+", t) if w not in _TWIN_STOP)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def group_twins(entries: list[dict], threshold: float = TWIN_THRESHOLD) -> list[dict]:
    """Union-find over entries: existing groups are united first, then every
    pair at or above the threshold. A component that grew gets the stable id
    `twin_<smallest member id>`; one that did not keep its group name, so the
    originals' hand-written paraphrase groups are only renamed when they really
    matched something else."""
    out = [dict(e) for e in entries]
    parent = list(range(len(out)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    first: dict[str, int] = {}
    for i, e in enumerate(out):
        g = e.get("group") or e["id"]
        if g in first:
            union(i, first[g])
        else:
            first[g] = i
    base = {find(i) for i in range(len(out))}
    base_roots = [find(i) for i in range(len(out))]
    toks = [twin_tokens(e.get("question", "")) for e in out]
    for i in range(len(out)):
        for j in range(i + 1, len(out)):
            if jaccard(toks[i], toks[j]) >= threshold:
                union(i, j)
    comps: dict[int, list[int]] = {}
    for i in range(len(out)):
        comps.setdefault(find(i), []).append(i)
    for members in comps.values():
        old = {base_roots[i] for i in members}
        if len(old) > 1:
            gid = "twin_" + min(out[i]["id"] for i in members)
            for i in members:
                out[i]["group"] = gid
    return out


def cross_split_twins(entries: list[dict], threshold: float = TWIN_THRESHOLD) -> list[tuple[str, str, float]]:
    toks = [twin_tokens(e.get("question", "")) for e in entries]
    hits = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i], entries[j]
            if a.get("split") in SPLITS and b.get("split") in SPLITS and a["split"] != b["split"]:
                sim = jaccard(toks[i], toks[j])
                if sim >= threshold:
                    hits.append((a["id"], b["id"], sim))
    return hits


# ------------------------------------------------------------- splits

def _stratum(entry: dict) -> tuple[str, str]:
    tags = entry.get("tags") or []
    return (entry.get("domain") or "", tags[0] if tags else "")


def assign_splits(entries: list[dict], seed: int = 7, reassign: bool = False) -> list[dict]:
    """Assign train/holdout/sealed per group, stratified by domain + first tag.

    Splitting per item would put a paraphrase in train and its twin in holdout,
    and the holdout score would then measure memorisation. So the unit is the
    group. Within each stratum groups are shuffled with a seeded RNG and handed
    to whichever split is furthest below its target share, which keeps every
    stratum near 63/20/17 and makes the result a pure function of (entries, seed).
    Items that already have a split are left alone unless `reassign`; a group
    with one assigned member pulls its unassigned siblings into that split.
    """
    out = [dict(e) for e in entries]
    if reassign:
        for e in out:
            e["split"] = None
    groups: dict[str, list[dict]] = {}
    for e in out:
        groups.setdefault(e.get("group") or e["id"], []).append(e)

    fixed: dict[str, str] = {}
    pending: dict[str, list[dict]] = {}
    for g, members in groups.items():
        have = sorted({m["split"] for m in members if m.get("split") in SPLITS})
        if have:
            fixed[g] = have[0]  # an inconsistent group stays flagged by --validate
        else:
            pending[g] = members
    for g, split in fixed.items():
        for m in groups[g]:
            if not m.get("split"):
                m["split"] = split

    rng = random.Random(seed)
    strata: dict[tuple[str, str], list[str]] = {}
    for g in sorted(pending):
        strata.setdefault(_stratum(pending[g][0]), []).append(g)
    counts = {s: 0 for s in SPLITS}
    for e in out:
        if e.get("split") in SPLITS:
            counts[e["split"]] += 1
    for stratum in sorted(strata):
        gs = strata[stratum]
        rng.shuffle(gs)
        # Largest groups first so small ones can even things out afterwards.
        gs.sort(key=lambda g: -len(pending[g]))
        local = {s: 0 for s in SPLITS}
        for g in gs:
            n = len(pending[g])
            total_local = sum(local.values()) + n
            total = sum(counts.values()) + n

            def deficit(s: str) -> tuple[float, float, int]:
                return (SPLIT_SHARE[s] * total_local - local[s],
                        SPLIT_SHARE[s] * total - counts[s],
                        -SPLITS.index(s))
            pick = max(SPLITS, key=deficit)
            for m in pending[g]:
                m["split"] = pick
            local[pick] += n
            counts[pick] += n

    # Small strata round toward train, so top sealed and holdout up to their
    # global targets by moving whole not-yet-fixed groups out of train. Moving
    # from the stratum with the most train items keeps every stratum represented.
    total = sum(counts.values())
    target = {"sealed": min(max(round(SPLIT_SHARE["sealed"] * total), 15), 20)
              if total >= 60 else round(SPLIT_SHARE["sealed"] * total),
              "holdout": round(SPLIT_SHARE["holdout"] * total)}
    for dest in ("sealed", "holdout"):
        while counts[dest] < target[dest]:
            room = target[dest] - counts[dest]
            movable = [g for g in sorted(pending)
                       if pending[g][0]["split"] == "train" and len(pending[g]) <= room]
            if not movable:
                break
            load = {}
            for g in movable:
                load[_stratum(pending[g][0])] = load.get(_stratum(pending[g][0]), 0) + len(pending[g])
            top = max(sorted(load), key=lambda k: load[k])
            g = rng.choice([g for g in movable if _stratum(pending[g][0]) == top])
            for m in pending[g]:
                m["split"] = dest
            counts["train"] -= len(pending[g])
            counts[dest] += len(pending[g])
    return out


# ------------------------------------------------------------- promote

def latest_ok_sql(db: str, run_id: str, step: int | None = None) -> str:
    """SQL of the latest `ok` submission of a run, read-only.

    Refuses a multi-step submission unless `step` is given: one gold_sql is one
    query, and silently gluing several steps together would produce a gold that
    no single run step ever computed.
    """
    path = pathlib.Path(db).expanduser()
    if not path.exists():
        raise ImportError_(f"state DB not found: {path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT payload FROM submissions WHERE run_id = ? AND status = 'ok' "
            "ORDER BY ordinal DESC LIMIT 1", (run_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise ImportError_(f"no ok submission for run {run_id}")
    steps = json.loads(row[0]).get("steps") or []
    if not steps:
        raise ImportError_(f"run {run_id} submission has no steps")
    if step is None:
        if len(steps) > 1:
            raise ImportError_(f"run {run_id} has {len(steps)} steps; choose one with --step N (1-based)")
        chosen = steps[0]
    else:
        if not 1 <= step <= len(steps):
            raise ImportError_(f"--step {step} out of range (1..{len(steps)})")
        chosen = steps[step - 1]
    code = chosen.get("code")
    if isinstance(code, list):
        code = "\n".join(str(c) for c in code)
    if not isinstance(code, str) or not code.strip():
        raise ImportError_("selected step has no SQL code")
    return code.strip()


# ------------------------------------------------------------- cli

def summarize(entries: list[dict]) -> str:
    def tally(key: str) -> str:
        c: dict[str, int] = {}
        for e in entries:
            c[str(e.get(key))] = c.get(str(e.get(key)), 0) + 1
        return ", ".join(f"{k}={v}" for k, v in sorted(c.items()))
    return (f"{len(entries)} entries | split: {tally('split')} | status: {tally('status')} "
            f"| domain: {tally('domain')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--questions", default=str(QUESTIONS), help="questions.json to edit")
    ap.add_argument("--from-csv")
    ap.add_argument("--from-xlsx")
    ap.add_argument("--bootstrap-question-bank", action="store_true")
    ap.add_argument("--derive-contracts", action="store_true")
    ap.add_argument("--group-twins", action="store_true")
    ap.add_argument("--assign-splits", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--reassign", action="store_true")
    ap.add_argument("--promote", metavar="RUN_ID")
    ap.add_argument("--id")
    ap.add_argument("--step", type=int)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args(argv)
    path = pathlib.Path(a.questions)
    entries = load(path)

    try:
        if a.from_csv or a.from_xlsx:
            rows = read_csv(pathlib.Path(a.from_csv)) if a.from_csv \
                else read_xlsx(pathlib.Path(a.from_xlsx))
            incoming = entries_from_rows(rows, entries)
            entries, added, updated = merge_entries(entries, incoming)
            save(path, entries)
            print(f"merged: {added} added, {updated} updated")
        if a.bootstrap_question_bank:
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from tests.cockpit_v4 import question_bank
            new = bootstrap_entries(question_bank.all_cases())
            entries, added, updated = merge_entries(entries, new, keep_existing=("split",))
            entries = group_twins(entries)
            save(path, entries)
            pend = sum(1 for e in new if e["status"] == "pending_formula")
            print(f"bootstrapped {len(new)} question_bank cases "
                  f"({added} added, {updated} updated, {pend} pending_formula)")
        if a.group_twins:
            entries = group_twins(entries)
            save(path, entries)
            print(f"twin groups: {len({e['group'] for e in entries if str(e['group']).startswith('twin_')})}")
        if a.derive_contracts:
            n = 0
            for e in entries:
                if "source" not in e:
                    e["source"] = "golden"
                if not e.get("contract"):
                    c = derive_contract(e.get("gold_sql"))
                    if c:
                        e["contract"] = c
                        n += 1
            save(path, entries)
            print(f"derived {n} contracts")
        if a.assign_splits:
            entries = assign_splits(entries, seed=a.seed, reassign=a.reassign)
            save(path, entries)
            print("splits assigned:", summarize(entries))
        if a.promote:
            if not a.id:
                raise ImportError_("--promote needs --id")
            target = next((e for e in entries if e["id"] == a.id), None)
            if target is None:
                raise ImportError_(f"no question with id {a.id!r}")
            sql = latest_ok_sql(a.db, a.promote, a.step)
            print(sql)
            if not a.yes:
                print("\nnot written: re-run with --yes to set gold_sql on", a.id)
                return 0
            target["gold_sql"] = sql
            save(path, entries)
            print(f"gold_sql of {a.id} set from {a.promote}")
        if a.validate:
            problems = validate_all([normalize(e) for e in entries])
            for p in problems:
                print("PROBLEM:", p)
            print(("FAILED: %d problem(s)" % len(problems)) if problems
                  else "OK: " + summarize(entries))
            return 1 if problems else 0
    except ImportError_ as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
