"""
A generator produces one book, whatever interpreter runs it.

UNIT · REPRODUCTION · INDEPENDENT ORACLE. No model call, no paid provider
call, and no lake: every build here is in memory.

The defect this exists for
--------------------------
`v4-saudi-retail-20m-v4` was two different books under one id:

    Python 3.10 / 3.11   content digest 65524d5dd04d2d2e
    Python 3.12 / 3.13   content digest 9d444d6969e5e860

Same commit, same seed, same lake; only the interpreter differed. One line
did it:

    score = sum(r["behaviour_score"] for r in rows) / len(rows)

CPython 3.12 changed the builtin `sum` to use Neumaier compensated
summation for floats (gh-100425). The difference is a few parts in 10^16
and would normally vanish, but the summands were themselves `round(_, 2)`
values, so the mean landed exactly on a half-cent tie over and over and
`round(score, 2)` fell whichever way the last bit of the sum pointed.

It was invisible from the outside. Row counts, entity counts, field counts
and periods were identical, so both manifests looked right and only a
digest of the values caught it -- after the release had been published,
reported and pulled onto another machine.

Two guards, because one is not enough
-------------------------------------
The source rule fails the moment the construct comes back. The
cross-interpreter build fails for the construct nobody thought of: it is
the test that would have caught this on day one, and it is calibrated --
at 300 customers the OLD code diverges between 3.11 and 3.12, so this book
is small enough to be quick and large enough to be sensitive.
"""

from __future__ import annotations

import ast
import pathlib
import shutil
import subprocess
import sys

import pytest

from backend.cockpit_v4.generate import totals

GENERATORS = pathlib.Path(
    __file__).resolve().parents[2] / "backend" / "cockpit_v4" / "generate"

#: Customers in the reduced book the cross-interpreter test builds.
#:
#: MEASURED, not guessed. With the pre-fix code this size produces a
#: different digest on 3.11 and on 3.12 (it was checked at 200, 400, 1000
#: and 2000 and diverges at every one of them), so a book this small still
#: catches the defect it is here for. The full book is 26,000 and takes a
#: minute and a half per interpreter; this takes a couple of seconds.
PROBE_CUSTOMERS = 300


# ---- the source rule ----------------------------------------------------

def test_no_generator_adds_floats_with_the_builtin():
    """`sum` is banned from the generators, and `totals` says why.

    Not a style rule. The builtin has two implementations -- one before
    CPython 3.12 and one after -- and which one runs decides what a
    published release contains. `exact_total` and `exact_mean` are
    `math.fsum`, which is correctly rounded by an exact algorithm and
    therefore the same everywhere; `whole_total` is the builtin over
    integers, where there is no float path and nothing to differ.

    `totals.py` is exempt: `whole_total` is where the one legitimate call
    lives.
    """
    offences: list[str] = []
    for path in sorted(GENERATORS.glob("*.py")):
        if path.name == "totals.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offences += [
            f"{path.name}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "sum"]

    assert not offences, (
        f"the builtin `sum` is back in a generator at {offences}. Use "
        f"`exact_total`/`exact_mean` for floats and `whole_total` for "
        f"counts -- see backend/cockpit_v4/generate/totals.py.")


def test_the_one_allowed_builtin_call_is_the_integer_one():
    """Guards the exemption: `totals.py` may call `sum`, once, in the
    function whose whole job is integer addition. An exemption nobody
    checks is a hole."""
    tree = ast.parse((GENERATORS / "totals.py").read_text(encoding="utf-8"))
    inside = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "sum" for n in ast.walk(node))}
    assert inside == {"whole_total"}, inside


# ---- what the helpers promise -------------------------------------------

def test_exact_mean_is_the_same_double_on_every_interpreter():
    """Pinned by `float.hex()`, which is exact.

    These are the two-decimal values the Retail book actually averages, and
    the naive left-to-right sum of them is not the correctly rounded one.
    `math.fsum` is, on every conforming build, which is the entire
    property being bought here.
    """
    from fractions import Fraction

    values = [700.12, 700.13, 700.11, 700.14, 700.15, 700.16]

    # The TOTAL is the nearest double to the exact sum. Checked against
    # rational arithmetic rather than against the builtin: "differs from
    # naive" would be the wrong claim -- for many inputs they agree -- and
    # it would pin this test to whichever CPython runs it, which is the
    # very thing that went wrong.
    exact = Fraction(0)
    for value in values:
        exact += Fraction(value)
    assert totals.exact_total(values) == float(exact)

    # The MEAN is that total divided, and the division rounds once more, so
    # it is not in general the nearest double to the true mean -- here it
    # is 700.1349999999999 where the true mean rounds to 700.135. That is
    # fine and is stated rather than papered over: what this buys is not
    # the best possible answer, it is the SAME answer on every
    # interpreter. An implementation that chased the last bit would have to
    # change the published book again to get it.
    assert totals.exact_mean(values) == totals.exact_total(values) / 6
    assert totals.exact_mean(values).hex() == "0x1.5e1147ae147adp+9"
    assert totals.exact_mean(values) != float(exact / len(values))


def test_exact_total_beats_a_naive_accumulation_where_it_matters():
    """A case built to separate them, so the helper is demonstrably doing
    something rather than wrapping an accumulation.

    THE COMPARATOR IS AN EXPLICIT LEFT FOLD, NOT THE BUILTIN `sum`.

    This test used to assert `sum(values) != 2.0`, which is the very
    assumption the module it guards exists to distrust. CPython 3.12 gave
    the builtin Neumaier compensated summation (gh-100425), so from 3.12
    onwards `sum` returns 2.0 as well and the discriminator silently
    stopped discriminating: the test still passed on 3.11 while proving
    nothing, and failed on 3.12 while the code under test was correct.

    It surfaced on the runtime the dependencies actually require -- numpy
    2.5.0 declares `Requires-Python >=3.12` -- which is where the whole
    suite has to run.

    `acc += value` is plain IEEE-754 double addition and has never
    changed in any release. 1.0 is lost twice against 1e16, so the fold
    lands on 0.0 on every interpreter, and `math.fsum` lands on 2.0 on
    every interpreter. That is the difference this test is about.
    """
    values = [1.0, 1e16, 1.0, -1e16]

    naive = 0.0
    for value in values:
        naive += value

    assert naive == 0.0
    assert totals.exact_total(values) == 2.0
    assert totals.exact_total(values) != naive


def test_that_guard_fails_against_a_naive_implementation(monkeypatch):
    """The mutation check.

    A guard nobody has watched fail is a guard whose comparator may simply
    have stopped comparing -- which is exactly what happened to the
    version above. Replace `exact_total` with the left fold it is supposed
    to beat and the assertions must go red.
    """
    def naive_total(values):
        accumulated = 0.0
        for value in values:
            accumulated += value
        return accumulated

    monkeypatch.setattr(totals, "exact_total", naive_total)
    with pytest.raises(AssertionError):
        test_exact_total_beats_a_naive_accumulation_where_it_matters()


def test_a_mean_of_nothing_is_refused_rather_than_zero():
    with pytest.raises(ValueError):
        totals.exact_mean([])


def test_whole_total_refuses_floats():
    """The trap this split exists for. `sum(COHORT_WEIGHTS)` and
    `sum(weights)` are the INTEGER modulus of `_stable`; a float there
    would reassign every cohort and every product type in the Corporate
    book and fail nothing on the way out."""
    assert totals.whole_total([60, 15, 13, 6, 4, 2]) == 100
    with pytest.raises(TypeError):
        totals.whole_total([1, 2.0])


# ---- the build that would have caught it --------------------------------

#: Built in a subprocess so each interpreter runs it in its own process,
#: with `pandas` stubbed out: `build()` only needs it to wrap its rows, and
#: the question here is about the generator's arithmetic, not about dtype
#: inference or parquet. Floats are hashed as `float.hex()`, which is exact.
PROBE = """
import hashlib, sys, types
stub = types.ModuleType("pandas")
stub.DataFrame = list
stub.__version__ = "stub"
sys.modules["pandas"] = stub
sys.path.insert(0, {root!r})
from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import schema as schema_mod
from backend.cockpit_v4.generate import retail

retail.CUSTOMERS = {customers}

def cell(value):
    if isinstance(value, bool):
        return "b%d" % int(value)
    if isinstance(value, float):
        return "f" + value.hex()
    if isinstance(value, int):
        return "i%d" % value
    if value is None:
        return "n"
    return "s" + str(value)

build = retail.build(release_id="determinism-probe")
digest = hashlib.sha256()
for name in sorted(build.frames):
    columns = list(schema_mod.relation(dom.RETAIL, name).columns)
    for row in build.frames[name]:
        digest.update(chr(31).join(cell(row[c]) for c in columns).encode())
print(digest.hexdigest())
"""


def _interpreters() -> list[str]:
    """Every CPython on PATH that could build a release here.

    Includes the one running the tests, so a machine with a single
    interpreter still checks that it agrees with itself across processes --
    which is what would catch a `PYTHONHASHSEED` dependency.
    """
    found = [sys.executable]
    for minor in range(9, 20):
        path = shutil.which(f"python3.{minor}")
        if path and path not in found:
            found.append(path)
    return found


def test_every_interpreter_on_this_machine_builds_the_same_book():
    """THE ONE THAT WOULD HAVE CAUGHT IT.

    A release id has to name one book. The only way to know it does is to
    build it more than once, under everything to hand, and compare the
    values rather than the row counts -- the row counts were identical
    while the book was wrong.
    """
    interpreters = _interpreters()
    if len(interpreters) < 2:
        pytest.skip(
            "only one interpreter on PATH, so there is nothing to compare "
            "it against; this machine cannot run the cross-version half")

    root = str(pathlib.Path(__file__).resolve().parents[2])
    source = PROBE.format(root=root, customers=PROBE_CUSTOMERS)

    digests: dict[str, str] = {}
    for interpreter in interpreters:
        done = subprocess.run([interpreter, "-c", source], check=False,
                              capture_output=True, text=True, timeout=900)
        assert done.returncode == 0, (
            f"{interpreter} could not build the probe book:\n{done.stderr}")
        digests[interpreter] = done.stdout.strip()

    assert len(set(digests.values())) == 1, (
        "the Retail generator produced different books on different "
        "interpreters, which is how v4-saudi-retail-20m-v4 came to mean two "
        f"things: {digests}")
