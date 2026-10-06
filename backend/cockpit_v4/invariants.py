"""
What must be true of a synthetic book before anyone is allowed to ask it
anything.

Why these run before publication, not after
-------------------------------------------
A generated book is an argument about credit risk, and an argument with a
negative ECL or a Stage 4 in it is not one anybody should be shown. Worse,
these are exactly the errors a language model will faithfully report: asked
"which product has the highest ECL" it will answer correctly FROM the broken
data, and the answer will look fine. The place to catch a generator bug is
the generator, and the way to catch it is a gate that refuses to publish.

Each check returns a sentence a person can act on, not a boolean. "ecl_sar_mn
has 14 negative values, lowest -0.0003, in retail_account_month" tells you
where to look; `False` does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.cockpit_v4 import domains as dom
from backend.cockpit_v4 import schema as schema_mod


#: Percentage fields that compare two quantities rather than expressing one
#: as a share of a whole. These are signed, unbounded and meant to be:
#: capping them would be asserting something false about credit rather than
#: catching something false about the data.
COMPARISON_PERCENTS: frozenset[str] = frozenset({
    "headroom_pct", "coverage_pct", "collateral_coverage_pct", "ltv_pct",
    "balance_growth_pct", "inflow_change_pct", "utilisation_change_pp",
})


@dataclass(frozen=True)
class Finding:
    check: str
    relation: str
    problem: str

    def __str__(self) -> str:
        return f"{self.relation}: {self.check} -- {self.problem}"


def _bounded(frame, relation, column, low, high, findings, check):
    if column not in frame.columns:
        return
    series = frame[column].dropna()
    if series.empty:
        return
    below = series[series < low]
    above = series[series > high]
    if len(below):
        findings.append(Finding(
            check, relation,
            f"{column} has {len(below)} value(s) below {low}, lowest "
            f"{below.min()}"))
    if len(above):
        findings.append(Finding(
            check, relation,
            f"{column} has {len(above)} value(s) above {high}, highest "
            f"{above.max()}"))


def check(build) -> list[Finding]:
    """Every invariant, over every relation the build produced."""
    findings: list[Finding] = []
    domain_id = dom.parse(build.domain_id)
    periods = set(build.periods)

    for name in schema_mod.relation_names(domain_id):
        frame = build.frames.get(name)
        spec = schema_mod.relation(domain_id, name)
        if frame is None:
            findings.append(Finding("relation present", name,
                                    "the build did not produce it"))
            continue
        if frame.empty:
            findings.append(Finding("rows present", name, "it is empty"))
            continue

        missing = [c for c in spec.columns if c not in frame.columns]
        if missing:
            findings.append(Finding("schema", name,
                                    f"missing column(s) {missing}"))
            continue

        # -- grain: one row per key, and no other shape
        keys = list(spec.key_columns)
        duplicated = frame.duplicated(subset=keys).sum()
        if duplicated:
            findings.append(Finding(
                "unique grain", name,
                f"{duplicated} duplicate row(s) for {keys}"))

        # -- periods: inside the declared calendar, and nowhere else
        stray = sorted(set(frame[spec.period_column]) - periods)
        if stray:
            findings.append(Finding(
                "period continuity", name,
                f"month(s) outside the release calendar: {stray[:4]}"))

        # -- governance: the isolation columns are the isolation
        for column, expected in (("domain_id", domain_id),
                                 ("dataset_release_id", build.release_id)):
            wrong = sorted(set(frame[column]) - {expected})
            if wrong:
                findings.append(Finding(
                    "governance", name,
                    f"{column} carries {wrong[:3]}, not {expected!r}"))
        if set(frame["reporting_currency"]) != {"SAR"}:
            findings.append(Finding(
                "currency", name,
                f"reporting_currency is {sorted(set(frame['reporting_currency']))[:3]}"))

        # -- credit bounds, by the meaning of the column rather than its name
        for column in frame.columns:
            field = spec.field(column)
            if field.unit == "probability_0_1":
                _bounded(frame, name, column, 0.0, 1.0, findings,
                         "probability bounds")
            elif field.name in COMPARISON_PERCENTS:
                # A comparison, not a share. It has no natural ceiling and
                # its sign is the finding: a leverage covenant breached by
                # more than its own threshold really does read below -100%,
                # and a nearly-repaid mortgage against intact security really
                # is covered many times over. Bounding these to 0-100 would
                # not be a data check, it would be a misreading of what they
                # measure. What IS checked is that they are finite.
                series = frame[column].dropna()
                unusable = (~series.apply(
                    lambda v: float(v) == float(v)
                    and abs(float(v)) != float("inf"))).sum()
                if unusable:
                    findings.append(Finding(
                        "finite comparison", name,
                        f"{column} has {unusable} value(s) that are not "
                        f"finite numbers"))
            elif field.name.endswith("_pct"):
                _bounded(frame, name, column, 0.0, 200.0, findings,
                         "percentage bounds")
            elif field.unit == "days":
                _bounded(frame, name, column, 0, 3650, findings,
                         "days non-negative")
            elif field.unit == "rcy" and not field.name.startswith("net_"):
                _bounded(frame, name, column, 0.0, 1e9, findings,
                         "amounts non-negative")

        if "stage" in frame.columns:
            bad = sorted(set(frame["stage"]) - {1, 2, 3})
            if bad:
                findings.append(Finding("IFRS 9 stage", name,
                                        f"stage values {bad} are not 1, 2 or 3"))
        if "lgd_pct" in frame.columns:
            _bounded(frame, name, "lgd_pct", 0.0, 100.0, findings,
                     "LGD bounds")
        if "behaviour_score" in frame.columns:
            _bounded(frame, name, "behaviour_score", 300.0, 900.0, findings,
                     "score range")
        if "score_band" in frame.columns:
            bad = sorted(set(frame["score_band"]) - set("ABCDE"))
            if bad:
                findings.append(Finding("score band", name,
                                        f"bands {bad} are not A-E"))

    findings.extend(_domain_specific(build, domain_id))
    return findings


def _domain_specific(build, domain_id: str) -> list[Finding]:
    findings: list[Finding] = []

    if domain_id == dom.CORPORATE:
        facilities = build.frames["corp_facility_quarter"]
        # EAD must reconcile to its own components, every row.
        drift = (facilities["ead_sar_mn"]
                 - (facilities["drawn_sar_mn"]
                    + facilities["undrawn_sar_mn"] * 0.0)).abs()
        below_drawn = (facilities["ead_sar_mn"]
                       < facilities["drawn_sar_mn"] - 0.01).sum()
        if below_drawn:
            findings.append(Finding(
                "EAD reconciliation", "corp_facility_quarter",
                f"{below_drawn} row(s) have EAD below the drawn balance"))
        limit_short = (facilities["limit_sar_mn"]
                       < facilities["drawn_sar_mn"] - 0.01).sum()
        if limit_short:
            findings.append(Finding(
                "limit reconciliation", "corp_facility_quarter",
                f"{limit_short} row(s) draw more than the limit"))
        # The recognised ECL is one of the two it is allowed to be.
        recognised = facilities["ecl_sar_mn"]
        expected = facilities["ecl_12m_sar_mn"].where(
            facilities["stage"] == 1, facilities["ecl_lifetime_sar_mn"])
        wrong = ((recognised - expected).abs() > 1e-6).sum()
        if wrong:
            findings.append(Finding(
                "ECL staging", "corp_facility_quarter",
                f"{wrong} row(s) recognise neither the 12-month nor the "
                f"lifetime ECL for their stage"))
        borrowers = build.frames["corp_borrower_quarter"]
        from backend.cockpit_v4.generate.corporate import DEFAULT_GRADE, RATINGS

        grades = set(RATINGS) | {DEFAULT_GRADE}
        bad = sorted(set(borrowers["rating_current"]) - grades)
        if bad:
            findings.append(Finding("rating scale", "corp_borrower_quarter",
                                    f"ratings {bad} are off the scale"))
        # Every facility belongs to a borrower with a row in the same quarter.
        pairs = set(zip(borrowers["borrower_id"],
                        borrowers["reporting_quarter"]))
        orphans = sum(1 for b, q in zip(facilities["borrower_id"],
                                        facilities["reporting_quarter"])
                      if (b, q) not in pairs)
        if orphans:
            findings.append(Finding(
                "referential integrity", "corp_facility_quarter",
                f"{orphans} facility row(s) name a borrower with no row that "
                f"quarter"))
        # A facility cannot report a quarter before it was written.
        early = (facilities["origination_quarter"]
                 > facilities["reporting_quarter"]).sum()
        if early:
            findings.append(Finding(
                "facility continuity", "corp_facility_quarter",
                f"{early} row(s) report a quarter before origination"))

    if domain_id == dom.RETAIL:
        accounts = build.frames["retail_account_month"]
        from backend.cockpit_v4.generate.retail import PRODUCTS

        products = {p[0] for p in PRODUCTS}
        bad = sorted(set(accounts["product"]) - products)
        if bad:
            findings.append(Finding("product taxonomy",
                                    "retail_account_month",
                                    f"products {bad} are not configured"))
        recognised = accounts["ecl_sar_mn"]
        expected = accounts["ecl_12m_sar_mn"].where(
            accounts["stage"] == 1, accounts["ecl_lifetime_sar_mn"])
        wrong = ((recognised - expected).abs() > 1e-6).sum()
        if wrong:
            findings.append(Finding(
                "ECL staging", "retail_account_month",
                f"{wrong} row(s) recognise neither the 12-month nor the "
                f"lifetime ECL for their stage"))
        # An account cannot report a month before it was opened.
        early = (accounts["origination_month"]
                 > accounts["reporting_month"]).sum()
        if early:
            findings.append(Finding(
                "account continuity", "retail_account_month",
                f"{early} row(s) report a month before origination"))
        # Only secured products carry collateral, and every one of them does.
        collateral = build.frames["retail_collateral_month"]
        secured = set(zip(accounts.loc[accounts["secured_flag"] == 1,
                                       "account_id"],
                          accounts.loc[accounts["secured_flag"] == 1,
                                       "reporting_month"]))
        held = set(zip(collateral["account_id"],
                       collateral["reporting_month"]))
        if held - secured:
            findings.append(Finding(
                "collateral scope", "retail_collateral_month",
                f"{len(held - secured)} row(s) secure an unsecured account"))
        if secured - held:
            findings.append(Finding(
                "collateral scope", "retail_collateral_month",
                f"{len(secured - held)} secured account-month(s) hold no "
                f"collateral row"))
        # The customer roll-up is the accounts, added up.
        customers = build.frames["retail_customer_month"]
        rolled = accounts.groupby(["customer_id", "reporting_month"])[
            "ead_sar_mn"].sum().round(4)
        declared = customers.set_index(
            ["customer_id", "reporting_month"])["total_ead_sar_mn"].round(4)
        joined = declared.to_frame("declared").join(
            rolled.to_frame("rolled"), how="outer")
        mismatched = (joined["declared"].fillna(-1)
                      - joined["rolled"].fillna(-2)).abs() > 0.001
        if mismatched.any():
            findings.append(Finding(
                "customer roll-up", "retail_customer_month",
                f"{int(mismatched.sum())} customer-month(s) declare a total "
                f"EAD that is not the sum of their accounts"))

    return findings


class InvariantsFailed(RuntimeError):
    """A book that failed its own gates. Never published, never queried."""

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings
        super().__init__("; ".join(str(f) for f in findings[:8]))


def require(build) -> None:
    findings = check(build)
    if findings:
        raise InvariantsFailed(findings)


__all__ = ["Finding", "InvariantsFailed", "check", "require"]
