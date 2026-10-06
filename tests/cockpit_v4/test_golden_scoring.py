"""Contract scoring of golden runs: the failure modes the bag-of-numbers match let through. UNIT, NO MODEL."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cockpit_v4"))
import golden_scoring as gs  # noqa: E402

COLS = ["sector", "ecl", "ead"]
GOLD = [("Energy", 100.0, 1000.0), ("Retail", 50.0, 400.0)]
CONTRACT = {"keys": ["sector"], "measures": {"ecl": "ecl", "ead": "ead"},
            "cardinality": "exact", "scale": {}, "tolerance": {"rel": 0.005, "abs": 1e-6}}


def score(run, contract=CONTRACT, gold=GOLD, cols=COLS):
    return gs.score_contract(gold, cols, [run], contract)


def test_exact_match_passes_with_case_and_alias_differences():
    run = [{"SECTOR": " energy ", "Total_ECL": 100.2, "EAD": 1000},
           {"SECTOR": "RETAIL", "Total_ECL": 50, "EAD": 400}]
    contract = {**CONTRACT, "measures": {"ecl": "total_ecl", "ead": "ead"}}
    assert score(run, contract)["verdict"] == "pass"


def test_superset_fails_under_exact_passes_under_subset():
    run = [{"sector": "Energy", "ecl": 100, "ead": 1000},
           {"sector": "Retail", "ecl": 50, "ead": 400},
           {"sector": "Mining", "ecl": 7, "ead": 70}]
    assert score(run)["verdict"] == "fail"
    assert score(run)["extra"]
    assert score(run, {**CONTRACT, "cardinality": "subset"})["verdict"] == "pass"


def test_missing_entity_fails_even_under_subset():
    run = [{"sector": "Energy", "ecl": 100, "ead": 1000}]
    res = score(run, {**CONTRACT, "cardinality": "subset"})
    assert res["verdict"] == "fail" and res["missing"]


def test_label_swap_fails_although_every_number_is_present():
    run = [{"sector": "Energy", "ecl": 50, "ead": 400},
           {"sector": "Retail", "ecl": 100, "ead": 1000}]
    assert score(run)["verdict"] == "fail"
    assert gs.legacy_result_match(GOLD, COLS, run) == 1.0     # the old scorer passed this


def test_measures_swapped_between_columns_fails():
    run = [{"sector": "Energy", "ecl": 1000, "ead": 100},
           {"sector": "Retail", "ecl": 400, "ead": 50}]
    assert score(run)["verdict"] == "fail"


def test_duplicate_keys_fail():
    run = [{"sector": "Energy", "ecl": 100, "ead": 1000},
           {"sector": "energy", "ecl": 100, "ead": 1000},
           {"sector": "Retail", "ecl": 50, "ead": 400}]
    res = score(run)
    assert res["verdict"] == "fail" and "duplicate" in res["reasons"][0]


def test_null_key_matches_null():
    gold = [(None, 5.0, 9.0), ("Retail", 50.0, 400.0)]
    run = [{"sector": None, "ecl": 5, "ead": 9}, {"sector": "Retail", "ecl": 50, "ead": 400}]
    assert score(run, gold=gold)["verdict"] == "pass"
    assert score([{"sector": "None", "ecl": 5, "ead": 9},
                  {"sector": "Retail", "ecl": 50, "ead": 400}], gold=gold)["verdict"] == "fail"


def test_integer_labels_match_across_types():
    gold = [(1, 10.0), (2, 20.0)]
    contract = {"keys": ["stage"], "measures": {"n": "n"}, "cardinality": "exact"}
    run = [{"stage": "1", "n": 10}, {"stage": 2.0, "n": 20}]
    assert gs.score_contract(gold, ["stage", "n"], [run], contract)["verdict"] == "pass"


def test_percent_scale_accepts_fraction_only_when_declared():
    gold = [("Energy", 12.5)]
    cols = ["sector", "share"]
    run = [{"sector": "Energy", "share": 0.125}]
    base = {"keys": ["sector"], "measures": {"share": "share"}, "cardinality": "exact"}
    assert gs.score_contract(gold, cols, [run], {**base, "scale": {"share": "percent"}})["verdict"] == "pass"
    assert gs.score_contract(gold, cols, [run], {**base, "scale": {}})["verdict"] == "fail"
    same = [{"sector": "Energy", "share": 12.5}]
    assert gs.score_contract(gold, cols, [same], {**base, "scale": {"share": "percent"}})["verdict"] == "pass"


def test_best_table_is_chosen_and_recorded():
    scratch = [{"sector": "Energy", "ecl": 1, "ead": 1}]
    good = [{"sector": "Energy", "ecl": 100, "ead": 1000}, {"sector": "Retail", "ecl": 50, "ead": 400}]
    res = gs.score_contract(GOLD, COLS, [scratch, good], CONTRACT)
    assert res["verdict"] == "pass" and res["table_index"] == 1


def test_no_artifacts_or_no_contract_is_unscorable():
    assert gs.score_contract(GOLD, COLS, [], CONTRACT)["verdict"] == "unscorable"
    assert gs.score_contract(GOLD, COLS, [[]], CONTRACT)["verdict"] == "unscorable"
    assert gs.score_contract(GOLD, COLS, [[{"sector": "x"}]], None)["verdict"] == "unscorable"


def test_missing_measure_column_fails():
    run = [{"sector": "Energy", "ecl": 100}, {"sector": "Retail", "ecl": 50}]
    assert score(run)["verdict"] == "fail"


def test_derive_contract_for_legacy_entries():
    c = gs.derive_contract(GOLD, COLS)
    assert c["keys"] == ["sector"] and set(c["measures"]) == {"ecl", "ead"}


def test_score_values_is_weak():
    res = gs.score_values({"values": [100, 50]}, [{"a": 100.1, "b": 50}])
    assert res["verdict"] == "pass" and res["weak"]
    assert gs.score_values({"values": [100, 51]}, [{"a": 100, "b": 50}])["verdict"] == "fail"
    assert gs.score_values({"values": [1]}, [])["verdict"] == "unscorable"
    assert gs.score_values({"text_contains": ["Energy"]}, [], "Energy leads")["verdict"] == "pass"


def _final(*claims):
    return {"numeric_claims": list(claims)}


def _claim(entity, col, display, prec=0, unit="SAR million"):
    return {"claim_id": f"{entity}_{col}", "unit": unit, "display_precision": prec,
            "evidence": {"artifact_id": "a", "row_key": entity, "column_id": col},
            "display_value": display}


def test_headline_pass_fail_unscorable():
    ok = _final(_claim("Energy", "ecl", "SAR 100 million"), _claim("Retail", "ead", "400"))
    assert gs.score_headline(ok, GOLD, CONTRACT, COLS)["verdict"] == "pass"
    bad = _final(_claim("Energy", "ecl", "SAR 50 million"))
    assert gs.score_headline(bad, GOLD, CONTRACT, COLS)["verdict"] == "fail"
    other = _final(_claim("Mining", "ecl", "7"))
    assert gs.score_headline(other, GOLD, CONTRACT, COLS)["verdict"] == "unscorable"
    assert gs.score_headline({}, GOLD, CONTRACT, COLS)["verdict"] == "unscorable"


def test_headline_resolves_identity_row_ids_through_artifacts():
    claim = {"claim_id": "c", "unit": "SAR million", "display_precision": 0, "display_value": "50",
             "evidence": {"artifact_id": "", "row_key": "", "column_id": ""},
             "derivation": {"operation": "identity",
                            "operands": [{"artifact_id": "art1", "column_id": "ecl", "row_ids": ["r1"]}]}}
    arts = {"art1": [{"sector": "Energy", "ecl": 100}, {"sector": "Retail", "ecl": 50}]}
    assert gs.score_headline(_final(claim), GOLD, CONTRACT, COLS, arts)["verdict"] == "pass"


def test_total_prefix_matches_by_normalised_name_and_is_noted():
    run = [{"sector": "Energy", "total_ecl": 100, "sum_ead": 1000},
           {"sector": "Retail", "total_ecl": 50, "sum_ead": 400}]
    res = score(run)
    assert res["verdict"] == "pass"
    assert any("matched run column total_ecl by normalised name" in r for r in res["reasons"])


def test_ambiguous_normalised_match_fails_listing_candidates():
    run = [{"sector": "Energy", "total_ecl": 100, "sum_ecl": 100, "ead": 1000},
           {"sector": "Retail", "total_ecl": 50, "sum_ecl": 50, "ead": 400}]
    res = score(run)
    assert res["verdict"] == "fail"
    assert "ambiguous" in res["reasons"][0] and "total_ecl" in res["reasons"][0]


def test_token_superset_match_and_one_to_one_assignment():
    run = [{"sector": "Energy", "ecl_sar_mn": 100, "ead_sar_mn": 1000}]
    gold = [("Energy", 100.0, 1000.0)]
    assert score(run, gold=gold)["verdict"] == "pass"
    # two gold measures may not share one run column
    one = [{"sector": "Energy", "ecl_ead_mn": 100}]
    assert score(one, gold=gold)["verdict"] == "fail"


def test_swapped_measures_still_fail_under_normalised_names():
    run = [{"sector": "Energy", "total_ecl": 1000, "total_ead": 100},
           {"sector": "Retail", "total_ecl": 400, "total_ead": 50}]
    assert score(run)["verdict"] == "fail"


def test_unit_suffix_is_normalised_but_values_still_decide() -> None:


    assert gs.norm_column("total_ecl") == gs.norm_column("ecl_sar_mn") == "ecl"
    contract = {"keys": ["product"], "measures": {"ecl_sar_mn": "ecl_sar_mn"},
                "cardinality": "exact", "tolerance": {"rel": 0.005, "abs": 1e-6}}
    gold = [("cards", 10.0), ("auto", 5.0)]
    ok = gs.score_contract(gold, ["product", "ecl_sar_mn"],
                           [[{"product": "cards", "total_ecl": 10.0}, {"product": "auto", "total_ecl": 5.0}]], contract)
    bad = gs.score_contract(gold, ["product", "ecl_sar_mn"],
                            [[{"product": "cards", "total_ecl": 11.0}, {"product": "auto", "total_ecl": 5.0}]], contract)
    assert ok["verdict"] == "pass" and bad["verdict"] == "fail"
