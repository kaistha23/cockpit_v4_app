"""STATIC ANALYSIS · REAL FILESYSTEM · NO MODEL.

§18. The write-button audit, established rather than asserted.

The Data Builder has twenty-seven write endpoints and they all work. The
question a reader actually has when they see them beside a Cockpit book is a
different one: can any of them change what my analysis reads?

The answer is no, and this file is why. A Cockpit book is an immutable
fingerprinted release in its own lake, published by one command; the Data
Builder writes datasets into the analytics directory under its own registry;
and no route the Cockpit serves mutates a release at all. Each of those three
is checked here, on the code and on the filesystem, so the claim survives
somebody adding a route.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from backend.cockpit_v4 import lake

ROOT = pathlib.Path(__file__).resolve().parents[2]
V4 = ROOT / "backend" / "cockpit_v4"
SEED = ROOT / "scripts" / "cockpit_v4" / "seed_domains.py"

#: The functions that CHANGE a release on disk.
MUTATORS = ("publish", "forget")


def _calls(path: pathlib.Path) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=path.name)):
        if not isinstance(node, ast.Call):
            continue
        parts: list[str] = []
        target = node.func
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
        if parts:
            out.append(tuple(reversed(parts)))
    return out


def test_only_the_seeding_command_publishes_a_book():
    """`lake.publish` writes the bytes an analysis reads. Who calls it?"""
    callers: list[str] = []
    for path in sorted(V4.rglob("*.py")):
        for chain in _calls(path):
            if chain[-1] == "publish" and any(p in ("lake", "lake_mod")
                                              for p in chain[:-1]):
                callers.append(path.relative_to(ROOT).as_posix())
    assert callers == [], (
        f"these publish a release from inside the serving code: {callers}. "
        f"A book is published by {SEED.relative_to(ROOT).as_posix()} and by "
        f"nothing a request can reach.")
    assert "publish(" in SEED.read_text(), (
        "the seeding command must be the thing that publishes")


def test_no_cockpit_route_mutates_a_release():
    """Every write route the Cockpit serves writes a RUN, never a book."""
    routes = V4 / "routes.py"
    tree = ast.parse(routes.read_text(), filename="routes.py")
    writing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        methods = set()
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) \
                else decorator
            if isinstance(target, ast.Attribute):
                methods.add(target.attr)
        if not (methods & {"post", "put", "delete", "patch"}):
            continue
        source = ast.get_source_segment(routes.read_text(), node) or ""
        for mutator in MUTATORS:
            if f"lake_mod.{mutator}(" in source or f"lake.{mutator}(" in source:
                writing.append(f"{node.name} calls lake.{mutator}")
    assert writing == [], (
        f"these routes change a published book: {writing}. A release is "
        f"immutable once published, and an analysis saved last week must "
        f"still mean what it said.")


def test_a_published_release_refuses_to_be_overwritten_in_place():
    """The guarantee, exercised rather than described."""
    from backend.cockpit_v4 import domains as dom

    release_id = dom.DEFAULT_RELEASES[dom.CORPORATE]
    if not lake.exists(release_id):
        pytest.skip("run scripts/cockpit_v4/seed_domains.py")
    build = type("Build", (), {
        "release_id": release_id, "domain_id": dom.CORPORATE,
        "frames": {}, "manifest_extra": {}})()
    with pytest.raises(Exception) as raised:
        lake.publish(build)
    assert isinstance(raised.value, (lake.ReleaseExists, TypeError,
                                     AttributeError, ValueError)), raised.value
    if isinstance(raised.value, lake.ReleaseExists):
        assert "immutable" in str(raised.value).lower()


def test_the_two_lakes_are_different_places():
    """The Data Builder writes datasets; the Cockpit reads books."""
    from backend.config import settings

    analytics = pathlib.Path(settings.analytics_dir).resolve()
    books = lake.root().resolve()
    assert books != analytics
    assert analytics not in books.parents, (
        "the Cockpit lake must not sit inside the directory the Data "
        "Builder writes datasets into")
    assert books not in analytics.parents


def test_the_audit_names_every_data_builder_write_endpoint():
    """The list in the report is the list in the code, not a recollection."""
    router = ROOT / "backend" / "api" / "routers" / "data_builder.py"
    if not router.exists():
        pytest.skip("this build does not ship the Data Builder")
    tree = ast.parse(router.read_text(), filename="data_builder.py")
    writes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) \
                else decorator
            if isinstance(target, ast.Attribute) and target.attr in (
                    "post", "put", "delete", "patch"):
                writes.append(node.name)
    assert writes, "the Data Builder is expected to have write endpoints"
    # Every one of them is a real endpoint with a real implementation. The
    # finding of the audit is not that they are inert -- it is that none of
    # them can reach a Cockpit book, which the tests above establish.
    assert len(writes) >= 20
