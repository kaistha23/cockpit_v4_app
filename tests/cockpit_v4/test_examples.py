"""UNIT, NO MODEL; REAL DATABASE for the context cases. Worked plans are opt-in and leak-free.

The worked-example aid is judged later by live paired holdout runs, so these
tests pin the properties that keep that judgement honest: it is OFF by
default (the context is byte-identical to the no-feature build), it lives in
the volatile system block only (the cached prefix is untouched), every shown
plan compiles against the real catalogue, and no example comes from, or
shares a group with, a holdout or sealed question.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import context as ctx_mod
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import examples as ex
from backend.cockpit_v4 import lake
from backend.cockpit_v4 import plan_compiler as pc

QUESTIONS = Path(__file__).parent / "golden" / "questions.json"
WORKED_QUESTION = "What is exposure by region?"


def _corpus() -> dict[str, dict]:
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    rows = data["questions"] if isinstance(data, dict) else data
    return {row["id"]: row for row in rows}


# ---- leakage guard ------------------------------------------------------

def test_examples_come_only_from_ready_train_items():
    corpus = _corpus()
    assert ex.load()
    for example in ex.load():
        source = corpus[example["source_id"]]
        assert source["split"] == "train", example["id"]
        assert source["status"] == "ready", example["id"]
        assert source["split"] not in ("holdout", "sealed")


def test_no_example_shares_a_group_with_a_holdout_or_sealed_item():
    corpus = _corpus()
    held = {row["group"] for row in corpus.values()
            if row["split"] in ("holdout", "sealed")}
    for example in ex.load():
        assert corpus[example["source_id"]]["group"] not in held, example["id"]


def test_example_ids_are_unique_and_the_explicit_one_carries_a_note():
    ids = [e["id"] for e in ex.load()]
    assert len(ids) == len(set(ids))
    explicit = [e for e in ex.load() if e["plan"].get("period", {}).get("kind") == "explicit"]
    assert explicit and all(e.get("note") for e in explicit)


# ---- selection ----------------------------------------------------------

def test_select_returns_same_domain_neighbours_best_first():
    chosen = ex.select("What is exposure by region?", "corporate")
    assert 1 <= len(chosen) <= 2
    assert all(e["domain"] == "corporate" for e in chosen)
    assert chosen[0]["id"] == "qb_c06"


def test_select_never_crosses_domains():
    for domain in ("corporate", "retail"):
        got = ex.select("What is ECL by stage?", domain)
        assert got and all(e["domain"] == domain for e in got)


def test_select_returns_nothing_below_the_similarity_threshold():
    assert ex.select("Tell me a joke about the weather", "corporate") == []
    assert ex.select("", "corporate") == []
    assert ex.block("Tell me a joke about the weather", "corporate") is None


def test_block_shape():
    got = ex.block("What is exposure by region?", "corporate")
    assert set(got) == {"worked_examples", "note"}
    assert all({"question", "plan"} <= set(item) for item in got["worked_examples"])


def test_enabled_reads_the_environment_at_call_time(monkeypatch):
    monkeypatch.delenv(ex.ENV_FLAG, raising=False)
    assert ex.enabled() is False
    monkeypatch.setenv(ex.ENV_FLAG, "on")
    assert ex.enabled() is True
    monkeypatch.setenv(ex.ENV_FLAG, "off")
    assert ex.enabled() is False


# ---- shape (the live compile-and-execute check is scripts/cockpit_v4/verify_examples.py)

def test_every_example_plan_has_the_governed_plan_shape():
    allowed = {"measures", "dimensions", "filters", "period", "compare", "sort", "limit"}
    domains = set()
    for example in ex.load():
        plan = example["plan"]
        domains.add(example["domain"])
        assert plan["measures"] and set(plan) <= allowed, example["id"]
        assert plan["period"]["kind"] in pc.PERIOD_KINDS
        assert plan.get("compare", "none") in pc.COMPARES
        assert plan.get("sort", "auto") in pc.SORTS
        for flt in plan.get("filters", []):
            assert flt["field"] and flt["values"]
        if plan["period"]["kind"] == "explicit":
            assert example.get("note")
            for value in plan["period"]["values"]:
                assert value in example["question"], example["id"]
    assert domains == {"corporate", "retail"}
    kinds = {(e["plan"].get("compare"), bool(e["plan"].get("filters"))) for e in ex.load()}
    assert ("prior", False) in kinds and any(f for _, f in kinds)


# ---- context: default off, volatile-only when on ------------------------

@pytest.fixture
def _books():
    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    arun.reset()
    yield
    arun.reset()


def _blocks(question: str):
    book = arun.for_domain(dom.CORPORATE)
    principal = {"tenant_id": lake.DEFAULT_TENANT, "user_id": "u1", "roles": ["analyst"]}
    return ctx_mod.build(
        question=question, principal=principal, scope=book.read_scope(principal),
        catalog=book.catalog, limits=config_mod.ANALYTICAL_STANDARD_LIMITS,
        mode="standard", release_summary=book.release_summary(),
        session=book.session, analytical=True).system_blocks


def test_default_off_context_is_identical_and_on_touches_only_the_volatile_block(
        _books, monkeypatch):
    monkeypatch.delenv(ex.ENV_FLAG, raising=False)
    off = _blocks(WORKED_QUESTION)
    assert "worked_examples" not in json.dumps(off)

    monkeypatch.setenv(ex.ENV_FLAG, "on")
    on = _blocks(WORKED_QUESTION)
    assert len(on) == len(off)
    assert on[:-1] == off[:-1], "stable prefix must be untouched"
    volatile_off, volatile_on = json.loads(off[-1]["text"]), json.loads(on[-1]["text"])
    added = volatile_on.pop("worked_examples")
    assert volatile_on == volatile_off
    assert added["worked_examples"] and "never copy periods" in added["note"]
    size = len(json.dumps(added, ensure_ascii=False).encode())
    assert size < 4800, size  # about 1.2k tokens


def test_on_with_no_close_neighbour_adds_nothing(_books, monkeypatch):
    monkeypatch.delenv(ex.ENV_FLAG, raising=False)
    off = _blocks("Tell me a joke about the weather")
    monkeypatch.setenv(ex.ENV_FLAG, "on")
    assert _blocks("Tell me a joke about the weather") == off


def test_finalization_strips_the_worked_examples_and_keeps_everything_else(_books, monkeypatch):
    monkeypatch.setenv(ex.ENV_FLAG, "on")
    on = _blocks(WORKED_QUESTION)
    assert "worked_examples" in on[-1]["text"]
    final = ctx_mod.finalization_system(on, domain_id="corporate", question=WORKED_QUESTION)
    assert "worked_examples" not in json.dumps(final)
    monkeypatch.delenv(ex.ENV_FLAG)
    off_final = ctx_mod.finalization_system(_blocks(WORKED_QUESTION), domain_id="corporate",
                                            question=WORKED_QUESTION)
    assert final == off_final
