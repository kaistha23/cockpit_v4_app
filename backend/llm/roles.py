"""
Which model does which job, and how much of it to spend.

Why roles rather than one model
-------------------------------
CreditProbe asks a model four different things, and they are not the same
difficulty:

* **routing** — is this a catalogue question or an analysis? Short, structured,
  and answered correctly by a fast model almost every time.
* **planning** — read a compound multi-domain request into a governed plan. The
  hardest thing in the product, and where an error is most expensive: a plan
  that is subtly wrong produces a confident, reconciled, wrong answer.
* **interpretation** — say in a sentence what a computed result means. Bounded,
  because the figures are already fixed and the model may not add any.
* **critic** — repair a plan the validator rejected, told what was wrong.

Sending all four to one model means either paying planning prices for routing
or accepting planning quality from a routing model. Neither is a decision worth
making by accident, so the roles are configuration.

No invented model ids
---------------------
Nothing here names a model. Every id comes from the environment; where a role
has no id of its own it falls back to `AI_MODEL`, and where that is empty the
provider's own pinned default applies. A configured id that the provider cannot
serve is a **configuration failure that says so**, not a silent substitution —
a demo answered by a different model from the one that was certified is a demo
whose certification means nothing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

ROUTER = "router"
PLANNER = "planner"
#: §22. The routine planner and the complex planner are separate roles because
#: they are separate decisions: an administrator who wants a stronger model for
#: a forensic ECL decomposition should not have to pay for it on every "what is
#: total EAD by sector". Splitting the variable is what makes that possible;
#: leaving one AI_PLANNER_MODEL forces the choice to be all or nothing.
COMPLEX_PLANNER = "complex_planner"
INTERPRETATION = "interpretation"
CRITIC = "critic"
#: §22 lists this as optional, and it stays optional: nothing calls it until
#: Arabic exists (§49). Declared now so the configuration surface does not have
#: to change on the day it does.
TRANSLATION = "translation"

#: R2 §16's class B and class C, as configurable roles.
#:
#: The investigation loop was one job served by one model. It is two: choosing
#: and sequencing governed tool calls, which a strong cost-efficient model does
#: well, and forming a credit judgement on what came back, which is the work
#: worth paying for. Splitting the variable is what lets an administrator pay
#: for the second without paying for the first on every "show me the top 20 by
#: PD" — and, before this split, every such question was served at the deep
#: rate because there was nowhere else for it to go.
INVESTIGATOR = "investigator"
ANALYST = "analyst"

#: Cockpit Agentic V3's two model jobs, configured independently of the seven
#: legacy roles above. The Cockpit's contract is a two-model one -- one model
#: preprocesses and summarises, a stronger one decides ownership, plans,
#: authors every query and reviews -- and binding those to `planner` or
#: `analyst` would mean an administrator could not change one without changing
#: the other.
#:
#: Named for the JOB, not for a model family, because a role called after a
#: model reads as though it were pinned to one. Nothing here names a model and
#: every id still comes from the environment.
COCKPIT_PREPROCESS = "cockpit_preprocess"
COCKPIT_REASONING = "cockpit_reasoning"

ROLES: tuple[str, ...] = (ROUTER, PLANNER, COMPLEX_PLANNER, INVESTIGATOR,
                          ANALYST, INTERPRETATION, CRITIC, TRANSLATION,
                          COCKPIT_PREPROCESS, COCKPIT_REASONING)

#: Roles the product calls today. TRANSLATION is declared but unused, and a
#: report that counted it as unconfigured would be reporting a gap that is not
#: one.
ACTIVE_ROLES: tuple[str, ...] = (ROUTER, PLANNER, COMPLEX_PLANNER,
                                 INVESTIGATOR, ANALYST, INTERPRETATION,
                                 CRITIC, COCKPIT_PREPROCESS, COCKPIT_REASONING)

#: Which environment variable names each role's model, and how hard it should
#: think. Effort is passed through only where the provider supports it.
_ENV: dict[str, tuple[str, str]] = {
    ROUTER: ("AI_ROUTER_MODEL", "AI_ROUTER_EFFORT"),
    PLANNER: ("AI_PLANNER_MODEL", "AI_PLANNER_EFFORT"),
    COMPLEX_PLANNER: ("AI_COMPLEX_PLANNER_MODEL",
                      "AI_COMPLEX_PLANNER_EFFORT"),
    INTERPRETATION: ("AI_INTERPRETATION_MODEL", "AI_INTERPRETATION_EFFORT"),
    CRITIC: ("AI_CRITIC_MODEL", "AI_CRITIC_EFFORT"),
    INVESTIGATOR: ("AI_INVESTIGATOR_MODEL", "AI_INVESTIGATOR_EFFORT"),
    ANALYST: ("AI_ANALYST_MODEL", "AI_ANALYST_EFFORT"),
    TRANSLATION: ("AI_TRANSLATION_MODEL", "AI_TRANSLATION_EFFORT"),
    COCKPIT_PREPROCESS: ("AI_COCKPIT_PREPROCESS_MODEL", "AI_COCKPIT_PREPROCESS_EFFORT"),
    COCKPIT_REASONING: ("AI_COCKPIT_REASONING_MODEL", "AI_COCKPIT_REASONING_EFFORT"),
}

#: §22 asks for backward compatibility. A deployment that set only
#: AI_PLANNER_MODEL before this change still gets a working complex planner:
#: the complex role falls back to the routine planner's id before it falls back
#: to AI_MODEL. Without this the upgrade would silently move complex planning
#: onto the shared default, which §23 forbids in the other direction and would
#: be no better here.
_FALLBACK_ROLE: dict[str, str] = {
    COMPLEX_PLANNER: PLANNER,
    # A deployment that has not configured the two new roles keeps working:
    # tool orchestration falls back to the routine planner's model and
    # judgement to the complex planner's, which is where each of them
    # belongs. Without this the split would silently move both onto the
    # shared default and the routing would be a claim rather than a fact.
    INVESTIGATOR: PLANNER,
    ANALYST: COMPLEX_PLANNER,
}

#: Roles that must be configured explicitly or not at all. No fallback to
#: another role, none to AI_MODEL, none to the provider's default.
#:
#: The rest of CreditProbe runs unconfigured by design -- the deterministic
#: reader does the reading, and a resolver that refused to find a model would
#: be refusing a supported mode. The Cockpit has no deterministic reader: every
#: answer it gives is authored by a model, so an id nobody chose produces an
#: answer nobody can attribute. `backend/cockpit_agentic/models.py` is where
#: that refusal is enforced at execution time; this set is here so the Settings
#: page and the preflight report the same thing the runtime will do, instead of
#: showing an inherited model for a role that will not use one.
STRICT_ROLES: frozenset[str] = frozenset({COCKPIT_PREPROCESS,
                                          COCKPIT_REASONING})

#: What each role is for, shown in Settings so an administrator configuring
#: four model ids knows which is which.
PURPOSE: dict[str, str] = {
    ROUTER: "Reads what kind of request this is. Short and structured.",
    PLANNER: "Turns an ordinary request into a governed analytical plan.",
    COMPLEX_PLANNER: "Plans the hard ones — broad investigations, "
                     "decompositions, multi-domain forensics. The job where "
                     "an error is most expensive.",
    TRANSLATION: "Reads a question asked in another language. Unused until "
                 "Arabic is implemented.",
    INTERPRETATION: "Says what a computed result means, without adding a "
                    "figure to it.",
    CRITIC: "Repairs a plan the validator rejected, told what was wrong.",
    INVESTIGATOR: "Chooses and sequences governed tool calls for a question "
                  "whose answer is in the data. Orchestration, not judgement.",
    ANALYST: "Forms a credit judgement on gathered evidence — cause, "
             "materiality, what to do about it. The job worth paying for.",
    COCKPIT_PREPROCESS: "Cockpit preprocessing and summary: cleans and "
                        "translates the question, normalises it into a "
                        "business request, and updates the rolling thread "
                        "summary. It chooses no method and computes nothing. "
                        "A fast, cheap model does this well.",
    COCKPIT_REASONING: "Cockpit reasoning: decides whether the Cockpit owns "
                       "the question at all, then owns the analysis plan, the "
                       "method, every query candidate including every repair, "
                       "the sufficiency review and the final interpretation. "
                       "The job worth paying for.",
}

#: Effort levels a provider may be asked for. Ordered.
EFFORTS: tuple[str, ...] = ("low", "medium", "high")


@dataclass(frozen=True)
class Role:
    """One configured job, and the model that does it."""

    name: str
    model: str
    effort: str = ""
    #: True when nothing was configured for this role and it inherited AI_MODEL
    #: or the provider default. Reported rather than hidden: an administrator
    #: who set three of four ids should be able to see the fourth.
    inherited: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.name, "model": self.model, "effort": self.effort,
                "inherited": self.inherited, "purpose": PURPOSE.get(self.name, "")}


class ConfigurationError(RuntimeError):
    """A role is configured with something the provider cannot serve."""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def role(name: str) -> Role:
    """The model and effort for one job."""
    from backend.config import settings

    model_var, effort_var = _ENV.get(name, ("", ""))
    configured = _env(model_var) if model_var else ""
    effort = (_env(effort_var) if effort_var else "").lower()
    if effort and effort not in EFFORTS:
        logger.warning("%s=%r is not one of %s; ignoring it.",
                       effort_var, effort, ", ".join(EFFORTS))
        effort = ""

    if configured:
        return Role(name=name, model=configured, effort=effort, inherited=False)

    if name in STRICT_ROLES:
        # Empty, and NOT "inherited": nothing was inherited. A role reported as
        # inheriting a model an operator never chose is the failure this whole
        # change is about.
        return Role(name=name, model="", effort=effort, inherited=False)

    fallback = _FALLBACK_ROLE.get(name)
    if fallback:
        inherited_var, inherited_effort_var = _ENV[fallback]
        borrowed = _env(inherited_var)
        if borrowed:
            return Role(name=name, model=borrowed,
                        effort=effort or _env(inherited_effort_var).lower(),
                        inherited=True)

    shared = (settings.ai_model or "").strip()
    return Role(name=name, model=shared, effort=effort, inherited=True)


def all_roles(*, include_inactive: bool = False) -> list[Role]:
    names = ROLES if include_inactive else ACTIVE_ROLES
    return [role(name) for name in names]


def describe() -> dict[str, Any]:
    """What Settings shows about model configuration. Never a key."""
    from backend.config import settings

    configured = all_roles()
    distinct = sorted({r.model for r in configured if r.model})
    inherited = all(r.inherited for r in configured)
    return {
        "provider": (settings.ai_provider or "").strip().lower(),
        "shared_model": (settings.ai_model or "").strip(),
        "roles": [r.to_dict() for r in configured],
        "distinct_models": distinct,
        "all_inherited": inherited,
        "differentiated": len(distinct) > 1,
        "summary": _summary(configured, distinct, inherited),
    }


def _summary(configured: list[Role], distinct: list[str],
             inherited: bool) -> str:
    """Whether the roles are actually differentiated, said plainly.

    Four role names in a settings page imply four models. Where they all
    resolve to the same one — which is the ordinary case, because three of the
    four variables are usually blank — saying so is the difference between an
    honest report and an architecture diagram.
    """
    named = [r for r in configured if not r.inherited]
    if len(distinct) > 1:
        return (f"{len(distinct)} different models serve the "
                f"{len(configured)} roles: " + ", ".join(distinct) + ".")
    if distinct:
        one = distinct[0]
        if named:
            return (f"All {len(configured)} roles resolve to {one}. "
                    f"{len(named)} of them name it explicitly and the rest "
                    "inherit it; the routing is recorded but the model is the "
                    "same for every stage.")
        return (f"All {len(configured)} roles inherit {one}. The routing "
                "decision is still made and recorded, and the same model "
                "serves every stage.")
    return ("No model id is configured for any role, so every stage is served "
            "by the provider's own default. The routing decision is still made "
            "and recorded.")


def verify(provider: Any) -> list[str]:
    """Problems with how the roles are configured, in plain sentences.

    Returned rather than raised. A misconfigured role must be *visible* —
    Settings shows it, the release gate refuses on it — but it must not stop
    the application from starting, because an administrator cannot fix a
    configuration on a server that will not boot.
    """
    problems: list[str] = []
    supported = set(getattr(provider, "supported_models", None) or ())
    for configured in all_roles():
        if configured.name in STRICT_ROLES and not configured.model:
            problems.append(
                f"{_ENV[configured.name][0]} is not set. The Cockpit will not "
                f"run without it and will not borrow a model from another "
                f"role, from AI_MODEL or from the provider's default.")
            continue
        if configured.inherited or not configured.model:
            continue
        if supported and configured.model not in supported:
            problems.append(
                f"{_ENV[configured.name][0]} is set to {configured.model!r}, "
                f"which {getattr(provider, 'name', 'the provider')} does not "
                "list. CreditProbe will not silently use a different model: "
                "fix the id or clear the variable to inherit AI_MODEL.")
    return problems


# ---------------------------------------------------------------------------
# §29 — provider model validation
# ---------------------------------------------------------------------------

#: What a preflight can say about a role.
OK = "OK"
INHERITED = "INHERITED"
UNAVAILABLE = "UNAVAILABLE"
UNVERIFIED = "UNVERIFIED"
#: Nothing is set for this role and nothing is set to inherit. Not a failure:
#: CreditProbe runs offline by design, with the deterministic reader doing the
#: reading, and a preflight that refuses an unconfigured deployment would be
#: refusing the supported way to run it. §29 validates the ids that ARE
#: configured; it does not require any.
UNCONFIGURED = "UNCONFIGURED"
#: A role that must be configured explicitly and is not. Distinct from
#: UNCONFIGURED, which means "the provider's default serves it": nothing serves
#: this one. Reported rather than blocking, because the feature that needs it
#: is off by default and a preflight that refused to start would leave an
#: administrator unable to reach the page where they would fix it.
REQUIRED_UNSET = "REQUIRED_UNSET"


def preflight(provider: Any) -> dict[str, Any]:
    """What each role is configured to use, and whether the provider can serve
    it. §29.

    Spends nothing. Every answer here comes from configuration and from
    whatever the provider can say about itself without a call — §29 is
    explicit that startup validation must not spend large credits, and a
    preflight that costs a call per role is one an administrator learns to
    skip.

    UNVERIFIED is not a failure. A provider that cannot enumerate its models
    leaves every configured id unverified, and reporting that honestly is
    better than either guessing they are fine or refusing to start.
    """
    supported = set(getattr(provider, "supported_models", None) or ())
    efforts = set(getattr(provider, "supported_efforts", None) or EFFORTS)
    structured = bool(getattr(provider, "supports_structured_output", True))
    context = int(getattr(provider, "context_tokens", 0) or 0)

    rows: list[dict[str, Any]] = []
    for configured in all_roles(include_inactive=True):
        if not configured.model and configured.name in STRICT_ROLES:
            # Not the same UNCONFIGURED as the rest, and not UNAVAILABLE
            # either. For every other role an empty id means the provider's
            # default serves it; for these two it means nothing serves them
            # and the Cockpit refuses to answer.
            #
            # It does NOT fail the preflight. The Cockpit is off by default
            # and the rest of the product runs without it, so refusing to
            # start would leave an administrator unable to reach the settings
            # page on which they would fix this. The state is reported and the
            # Cockpit enforces it where it matters: at its own first request.
            state = REQUIRED_UNSET
            note = (f"{_ENV[configured.name][0]} is not set, and this role does "
                    "not inherit. The Cockpit stops every question with "
                    "MODEL_CONFIGURATION_MISSING until it is set. Nothing else "
                    "is affected.")
        elif not configured.model:
            state = UNCONFIGURED
            note = ("Nothing is configured for this role, so the provider's "
                    "own default serves it.")
        elif configured.inherited:
            state = INHERITED
            note = f"Inherits {configured.model}."
        elif not supported:
            state = UNVERIFIED
            note = (f"{getattr(provider, 'name', 'The provider')} does not "
                    "publish a model list, so the id cannot be checked here.")
        elif configured.model in supported:
            state = OK
            note = ""
        else:
            state = UNAVAILABLE
            note = (f"{configured.model!r} is not one the provider lists. "
                    "CreditProbe will not silently use a different model.")

        rows.append({
            **configured.to_dict(),
            "active": configured.name in ACTIVE_ROLES,
            "state": state,
            "note": note,
            "effort_supported": (not configured.effort
                                 or configured.effort in efforts),
            "structured_output": structured,
            "context_tokens": context,
        })

    blocking = [r for r in rows if r["active"] and r["state"] == UNAVAILABLE]
    return {
        "roles": rows,
        "structured_output": structured,
        "context_tokens": context,
        "efforts": sorted(efforts),
        "ok": not blocking,
        "problems": [f"{r['role']}: {r['note']}" for r in blocking],
    }


__all__ = [
    "ACTIVE_ROLES",
    "INHERITED",
    "REQUIRED_UNSET",
    "STRICT_ROLES",
    "OK",
    "UNAVAILABLE",
    "UNCONFIGURED",
    "UNVERIFIED",
    "preflight",
    "COMPLEX_PLANNER",
    "CRITIC",
    "EFFORTS",
    "TRANSLATION",
    "INTERPRETATION",
    "PLANNER",
    "PURPOSE",
    "ROLES",
    "ROUTER",
    "ConfigurationError",
    "Role",
    "all_roles",
    "describe",
    "role",
    "verify",
]
