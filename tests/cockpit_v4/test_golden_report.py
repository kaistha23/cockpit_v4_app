"""Golden report math: rates, intervals, paired comparison. UNIT, NO MODEL."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cockpit_v4"))
import golden_report as gr  # noqa: E402


def rec(i, verdict, state="COMPLETED", status="ready", tags=None, domain="corporate"):
    return {"id": i, "verdict": verdict, "state": state, "status": status,
            "tags": tags or [], "domain": domain}


def test_wilson_known_values():
    lo, hi = gr.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    lo, hi = gr.wilson(0, 4)
    assert lo == 0.0 and hi == pytest.approx(0.4899, abs=1e-3)
    assert gr.wilson(0, 0) == (0.0, 1.0)


def test_mcnemar_exact_small_tables():
    assert gr.mcnemar_exact(0, 0) == 1.0
    assert gr.mcnemar_exact(5, 5) == 1.0
    assert gr.mcnemar_exact(0, 5) == pytest.approx(2 / 32)
    assert gr.mcnemar_exact(1, 6) == pytest.approx(2 * (1 + 7) / 128)


def test_coverage_differs_from_eligible_accuracy():
    records = [rec("a", "pass"), rec("b", "pass"), rec("c", "fail"),
               rec("d", "unscorable"), rec("e", "pending", status="pending_formula"),
               rec("f", "fail", state="FAILED")]
    r = gr.build_report(records)
    assert r["total"] == 6 and r["eligible"] == 4
    assert r["pending_formula"] == 1 and r["unscorable"] == 1
    assert r["eligible_accuracy"]["rate"] == pytest.approx(2 / 4)
    assert r["total_coverage"]["rate"] == pytest.approx(2 / 6)
    assert r["wrong_answer_rate"]["k"] == 1 and r["failure_rate"]["k"] == 1
    assert r["total_coverage"]["ci95"][0] < 2 / 6 < r["total_coverage"]["ci95"][1]


def test_unmatched_runs_only_feed_states_and_categories_use_first_tag():
    records = [rec("a", "pass", tags=["ecl", "x"]), rec("b", "fail", domain="retail"),
               {"id": None, "verdict": "unmatched", "state": "FAILED"}]
    r = gr.build_report(records)
    assert r["total"] == 2 and r["unmatched_runs"] == 1 and r["states"]["FAILED"] == 1
    assert set(r["categories"]) == {"ecl", "retail"}


def test_compare_transitions_regressions_and_p_value():
    base = [rec(f"i{n}", "pass", tags=["t"]) for n in range(6)] + [rec("z", "fail", tags=["u"])]
    cand = [rec("i0", "fail", tags=["t"])] + [rec(f"i{n}", "pass", tags=["t"]) for n in range(1, 6)] \
        + [rec("z", "pass", tags=["u"])]
    c = gr.compare(base, cand)
    assert c["transitions"] == {"pass_pass": 5, "pass_fail": 1, "fail_pass": 1, "fail_fail": 0}
    assert c["regressions"] == ["i0"] and c["improvements"] == ["z"]
    assert c["mcnemar_p"] == 1.0
    assert c["categories"]["u"]["delta"] == 1.0
    assert c["categories"]["t"]["delta"] == pytest.approx(-1 / 6)


def test_compare_ignores_unscorable_and_unshared_items():
    c = gr.compare([rec("a", "pass"), rec("b", "unscorable")], [rec("a", "pass"), rec("c", "pass")])
    assert c["paired_items"] == 1


def test_markdown_renders_comparison():
    r = gr.build_report([rec("a", "pass")])
    md = gr.to_markdown(r, gr.compare([rec("a", "pass")], [rec("a", "fail")]))
    assert "TOTAL" not in md and "McNemar" in md and "regressions: a" in md
