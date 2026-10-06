"""The gate in front of every paid request, tested for the first time.

UNIT · REPRODUCTION. No model call, no database, no money.

`load_price_card` is the function that decides whether a paid run may be
reserved. It had no test of any kind. What it did have was a docstring --
"Every failure is explicit and fails closed" -- and a shipped card whose own
note repeats the promise: "A missing or unverified entry fails closed: no
paid request runs and no 'cost enforced' badge is shown."

A MISSING entry did fail closed. An UNVERIFIED one did not.

`config/cockpit_v4/price_card.json` is a template. Its single entry is
`REPLACE-WITH-YOUR-MODEL-ID`, `source: "PLACEHOLDER"`, `verified_at: ""`,
and all four billing classes 0.0, under a `source` line that says in
capitals that these are NOT verified pricing. It loaded. `verified_at` was
read into the `Capability`, surfaced in `to_dict()`, and then read by
nothing anywhere in the product -- a field whose only purpose is to stop an
unpriced paid run, gating nothing.

What that costs, concretely. Someone preparing a live UAT copies the
template, changes the model id to the one they are about to spend money on,
and does not fill in the date. Every request is then costed at zero. A spend
cap can never be reached. The run reports no cost. And nothing, anywhere,
says the numbers were never real -- which is the exact failure the card
exists to prevent, arriving through the card itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.cockpit_v4 import capability as cap

#: A complete, honest entry. Every test below starts here and breaks one
#: thing, so what each one proves is the one thing it broke.
GOOD = {
    "provider": "anthropic",
    "sdk_version": "1.2.3",
    "context_tokens": 200000,
    "max_output_tokens": 8192,
    "supports_tools": True,
    "supports_token_counting": True,
    "source": "the provider's published schedule for this account",
    "verified_at": "2026-09-10T00:00:00Z",
    "price": {"input_usd_per_mtok": 3.0, "output_usd_per_mtok": 15.0,
              "cache_write_usd_per_mtok": 3.75,
              "cache_read_usd_per_mtok": 0.3},
}


def card(tmp_path: Path, **over) -> str:
    entry = {**GOOD, **over}
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(
        {"card_version": "t1", "provider": "anthropic",
         "models": {"a-model": entry}}), encoding="utf-8")
    return str(path)


def load(path: str):
    return cap.load_price_card(path, model_id="a-model", provider="anthropic")


# ---- the card the repository actually ships ----------------------------

SHIPPED = (Path(__file__).resolve().parents[2]
           / "config" / "cockpit_v4" / "price_card.json")


def test_the_shipped_card_is_still_a_template() -> None:
    """If someone fills this in, the test below stops meaning what it says,
    so it is checked rather than assumed."""
    doc = json.loads(SHIPPED.read_text(encoding="utf-8"))
    [(model, entry)] = doc["models"].items()
    assert model == "REPLACE-WITH-YOUR-MODEL-ID"
    assert entry["verified_at"] == ""
    assert "PLACEHOLDER" in entry["source"]
    assert set(entry["price"].values()) == {0.0}


def test_the_shipped_template_cannot_reserve_a_paid_request() -> None:
    """The defect, in one line. This used to return a Capability."""
    with pytest.raises(cap.CapabilityUnverified):
        cap.load_price_card(SHIPPED, model_id="REPLACE-WITH-YOUR-MODEL-ID",
                            provider="anthropic")


# ---- an unverified price is not a price --------------------------------

def test_an_entry_with_no_verified_date_is_refused(tmp_path) -> None:
    with pytest.raises(cap.CapabilityUnverified) as caught:
        load(card(tmp_path, verified_at=""))
    assert "verified_at" in str(caught.value)


@pytest.mark.parametrize("value", ["soon", "TBD", "yes", "pending", "-",
                                   "2026-13-45"])
def test_a_verified_date_that_is_not_a_date_is_refused(tmp_path,
                                                       value: str) -> None:
    """"Verified: soon" is worse than blank. It reads, to anyone scanning
    the card, as though someone had checked."""
    with pytest.raises(cap.CapabilityUnverified) as caught:
        load(card(tmp_path, verified_at=value))
    assert "not a date" in str(caught.value)


@pytest.mark.parametrize("value", ["2026-09-10", "2026-09-10T00:00:00Z",
                                   "2026-09-10T11:22:33+03:00"])
def test_the_date_forms_a_person_would_actually_write_are_accepted(
        tmp_path, value: str) -> None:
    assert load(card(tmp_path, verified_at=value)).verified_at == value


def test_a_placeholder_source_is_refused(tmp_path) -> None:
    with pytest.raises(cap.CapabilityUnverified) as caught:
        load(card(tmp_path, source="PLACEHOLDER — replace before any run"))
    assert "placeholder" in str(caught.value)


def test_an_empty_source_is_refused(tmp_path) -> None:
    """Where a number came from is part of the number."""
    with pytest.raises(cap.CapabilityUnverified):
        load(card(tmp_path, source=""))


def test_a_zero_price_is_allowed_when_somebody_verified_it(tmp_path) -> None:
    """The rule is about VERIFICATION, not about the digits.

    A free tier, a promotional rate or a fixed-fee arrangement is a real
    price of zero, and refusing it would be inventing a rule about the
    provider's business. What is refused is a zero nobody vouched for.
    """
    free = {k: 0.0 for k in GOOD["price"]}
    loaded = load(card(tmp_path, price=free, source="a signed fixed-fee "
                                                    "agreement"))
    assert loaded.price.input_usd_per_mtok == 0.0


# ---- what already worked, pinned so it keeps working -------------------

def test_a_complete_entry_loads(tmp_path) -> None:
    loaded = load(card(tmp_path))
    assert loaded.model_id == "a-model"
    assert loaded.verified_at == GOOD["verified_at"]
    assert loaded.live_verified is False


def test_a_missing_file_is_refused(tmp_path) -> None:
    with pytest.raises(cap.CapabilityUnverified):
        cap.load_price_card(tmp_path / "nope.json", model_id="a-model",
                            provider="anthropic")


def test_a_missing_model_is_refused(tmp_path) -> None:
    with pytest.raises(cap.CapabilityUnverified) as caught:
        cap.load_price_card(card(tmp_path), model_id="another-model",
                            provider="anthropic")
    assert "not this model's price" in str(caught.value)


def test_a_different_provider_is_refused(tmp_path) -> None:
    with pytest.raises(cap.CapabilityUnverified):
        cap.load_price_card(card(tmp_path), model_id="a-model",
                            provider="someone-else")


@pytest.mark.parametrize("key", ["input_usd_per_mtok", "output_usd_per_mtok",
                                 "cache_write_usd_per_mtok",
                                 "cache_read_usd_per_mtok"])
def test_every_billing_class_must_be_priced(tmp_path, key: str) -> None:
    """Cache writes and reads are billed separately. A card that prices
    three of the four is a card that under-reports every cached run."""
    price = {k: v for k, v in GOOD["price"].items() if k != key}
    with pytest.raises(cap.CapabilityUnverified) as caught:
        load(card(tmp_path, price=price))
    assert key in str(caught.value)


# ---- §16 mutation check ------------------------------------------------

def test_the_old_loader_would_have_taken_the_template(tmp_path) -> None:
    """Reproduces the checks as they were -- file, entry, provider, the four
    price keys, numeric, positive token counts -- and shows the shipped
    template satisfies every one of them. If this ever fails, the defect
    this suite is about was not the defect that was there.
    """
    doc = json.loads(SHIPPED.read_text(encoding="utf-8"))
    entry = doc["models"]["REPLACE-WITH-YOUR-MODEL-ID"]
    assert str(entry.get("provider") or doc.get("provider")) == "anthropic"
    price = entry["price"]
    assert all(k in price for k in cap._REQUIRED_PRICE_KEYS)
    assert all(isinstance(float(price[k]), float)
               for k in cap._REQUIRED_PRICE_KEYS)
    for key in ("context_tokens", "max_output_tokens"):
        assert isinstance(entry.get(key), int) and entry[key] > 0
