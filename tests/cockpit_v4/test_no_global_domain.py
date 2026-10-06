"""NO MODEL · STATIC ANALYSIS · REAL RUNTIME.

§45. The audit that stops the defect coming back.

A data-bound global domain is a module-level name that decides WHICH BOOK a
request reads. `DOMAIN = "corporate_cockpit"` was one, and it is how a Retail
thread could be badged Retail and still read corporate relations.

A static display label is not one. `LABELS[CORPORATE] = "Corporate Credit"`
decides what a button says, not what a query reads, and it is allowed.

This file states which modules are on the analytical path, and refuses any of
them the four shapes the defect takes: importing a module-level DOMAIN,
reading the process runtime's single catalogue, opening the pre-domain SQL
session, or holding a module-level catalogue, session or release of its own.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from backend.cockpit_v4 import analytical_runtime as arun
from backend.cockpit_v4 import domains as dom

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "backend" / "cockpit_v4"

#: Everything a run touches between "a question arrived" and "an answer was
#: written". These must be able to serve two books in one process.
ANALYTICAL_PATH = (
    "analytical_runtime.py", "attention_v2.py", "catalog.py",
    "catalog_tool.py", "context.py", "domain_resolver.py", "domains.py",
    "execute_tool.py", "finalization.py", "orchestration.py", "schema.py",
    "semantics.py", "sql.py", "worker.py",
)

#: Modules that legitimately name the pre-domain book, because that book is
#: what they are about. Each is listed with the reason, so adding to this
#: tuple is a decision someone makes here rather than a quiet exemption.
LEGACY_BY_DESIGN = {
    "service.py": ("builds the runtime for the PRE-DOMAIN release and "
                   "reports its summary; the domain it names is that "
                   "release's own"),
    "routes.py": ("serves /attention-legacy from the pre-domain release "
                  "alongside the per-domain /attention"),
}


def _tree(name: str) -> ast.Module:
    return ast.parse((BACKEND / name).read_text(), filename=name)


@pytest.mark.parametrize("module", ANALYTICAL_PATH)
def test_no_analytical_module_imports_a_global_domain(module):
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.ImportFrom):
            imported = {a.name for a in node.names}
            assert "DOMAIN" not in imported, (
                f"{module} imports a module-level DOMAIN from "
                f"{node.module}. The book a request reads comes from the "
                f"request, not from a constant.")


#: Reading the process runtime's ONE catalogue, ONE release summary or the
#: pre-domain session. Each is a way of answering "which book?" with "the
#: one this process started with".
PROCESS_WIDE = {
    ("self", "runtime", "catalog"):
        "the process runtime holds ONE catalogue; a run's catalogue comes "
        "from its own book",
    ("self", "runtime", "coverage"):
        "the process coverage profile measures ONE release",
    ("self", "runtime", "release_summary"):
        "the process release summary describes ONE release",
    ("runtime", "catalog"):
        "same catalogue, reached through a local name",
}

#: Functions allowed to reach it, each with the reason it is not a
#: substitution. Anything not listed here is a failure, so an exemption is a
#: decision made in this file rather than a quiet one.
PROCESS_WIDE_ALLOWED = {
    ("worker.py", "_LegacyBook"):
        "wraps the PRE-DOMAIN release for a run accepted against it, and "
        "`Worker._book_for` reaches it only after proving the run's release "
        "id is the one this process configured",
}


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


@pytest.mark.parametrize("module", ANALYTICAL_PATH)
def test_no_analytical_module_reads_the_process_catalogue(module):
    """Checked on the CODE, never on a comment that mentions the defect."""
    tree = _tree(module)
    owners: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            for child in ast.walk(node):
                owners.setdefault(id(child), node.name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = _attribute_chain(node)
        why = PROCESS_WIDE.get(chain)
        if why is None:
            continue
        owner = owners.get(id(node), "<module>")
        if (module, owner) in PROCESS_WIDE_ALLOWED:
            continue
        raise AssertionError(
            f"{module}.{owner} reads {'.'.join(chain)}: {why}")


@pytest.mark.parametrize("module", ANALYTICAL_PATH)
def test_no_analytical_module_opens_the_pre_domain_session(module):
    """`v3_sql.open_session` materializes corporate relations, by constant."""
    for node in ast.walk(_tree(module)):
        if not isinstance(node, ast.Call):
            continue
        chain = _attribute_chain(node.func)
        if chain[-2:] != ("v3_sql", "open_session"):
            continue
        assert module == "worker.py", (
            f"{module} opens the pre-domain session. A domain run's session "
            f"comes from `catalog.open_session` on its own book.")


@pytest.mark.parametrize("module", ANALYTICAL_PATH)
def test_no_analytical_module_holds_a_module_level_book(module):
    """A module-level catalogue, session or release is a global book.

    A module-level CACHE keyed by domain is not: it can hold both books at
    once and cannot serve one for the other. The difference is the key, so
    the check is on the assignment, not on the word.
    """
    banned = {"CATALOG", "SESSION", "RELEASE", "CURRENT_CATALOG",
              "CURRENT_SESSION", "CURRENT_RELEASE", "_CATALOG", "_SESSION",
              "_RELEASE"}
    for node in _tree(module).body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id in banned:
                raise AssertionError(
                    f"{module} holds a module-level {target.id}. One book "
                    f"per process is the defect this round removed.")


def test_the_legacy_exemptions_are_named_and_still_true():
    """The exemption list is not a place to hide a new global."""
    for module, _reason in LEGACY_BY_DESIGN.items():
        assert module not in ANALYTICAL_PATH, (
            f"{module} is exempt AND on the analytical path, which is the "
            f"one combination that must never hold")
        assert (BACKEND / module).exists()


def test_two_books_are_open_in_one_process_at_the_same_time():
    """The property all of the above exists to protect."""
    from backend.cockpit_v4 import lake

    for domain_id in dom.DOMAIN_IDS:
        if not lake.exists(dom.DEFAULT_RELEASES[domain_id]):
            pytest.skip("run scripts/cockpit_v4/seed_domains.py")

    corporate = arun.for_domain(dom.CORPORATE)
    retail = arun.for_domain(dom.RETAIL)
    assert corporate is not retail
    assert corporate.session is not retail.session
    assert corporate.catalog is not retail.catalog
    assert corporate.release_id != retail.release_id
    assert corporate.release_fingerprint != retail.release_fingerprint
    assert not (set(corporate.session.relations)
                & set(retail.session.relations))
    # And reopening the first does not disturb the second.
    assert arun.for_domain(dom.CORPORATE) is corporate
    assert arun.for_domain(dom.RETAIL) is retail
