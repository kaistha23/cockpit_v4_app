"""Which model serves each Cockpit role, resolved so it cannot be guessed.

Why this module exists separately from `backend/llm/roles.py`
-------------------------------------------------------------
The shared role resolver is deliberately forgiving. It falls back from a role
to a nearer role, then to `AI_MODEL`, then to the provider SDK's own default,
and it reports that it did. That is correct for the rest of CreditProbe, where
an unconfigured deployment still runs: the deterministic reader does the
reading and refusing to start would be refusing a supported mode.

The Cockpit has no deterministic reader. Every answer it gives is authored by
a model, so "which model" is not a preference here, it is the whole claim. A
Cockpit that quietly ran its reasoning on whatever the SDK happened to ship
would produce answers nobody could attribute, and a commissioning run against
it would be measuring an unknown.

So these two roles fail CLOSED. Both ids are read from their own environment
variables and from nowhere else:

    AI_COCKPIT_PREPROCESS_MODEL     the two preprocessing passes and the
                                    rolling-summary update
    AI_COCKPIT_REASONING_MODEL      functionality selection, planning, every
                                    query and repair, the sufficiency review
                                    and the final interpretation

There is no fallback to `AI_MODEL`, no fallback to another role, and no
fallback to the SDK default. Missing, blank or malformed stops the request
with MODEL_CONFIGURATION_MISSING; configured but rejected by the provider
stops it with MODEL_UNAVAILABLE. Neither stop is ever answered from a
deterministic substitute.

The credential
--------------
Nothing here reads, holds, logs or reports `ANTHROPIC_API_KEY`. The model ids
are configuration and are reported; the key is a secret and is not.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_agentic.contracts import (MODEL_CONFIGURATION_MISSING,
                                               MODEL_UNAVAILABLE)

#: The role names, kept identical to the shared registry's so an administrator
#: reading Settings and an engineer reading this file are talking about the
#: same two jobs.
PREPROCESS_ROLE = "cockpit_preprocess"
REASONING_ROLE = "cockpit_reasoning"

PREPROCESS_VAR = "AI_COCKPIT_PREPROCESS_MODEL"
REASONING_VAR = "AI_COCKPIT_REASONING_MODEL"

#: Every variable a Cockpit deployment must set, in the order an operator
#: would work through them.
REQUIRED_VARS: tuple[tuple[str, str, str], ...] = (
    (PREPROCESS_ROLE, PREPROCESS_VAR,
     "the two preprocessing passes and the rolling-summary update"),
    (REASONING_ROLE, REASONING_VAR,
     "functionality selection, planning, every query and every repair, the "
     "sufficiency review and the final interpretation"),
)

#: A model id is an opaque provider string, so this checks shape and not
#: membership: no spaces, no quotes, no shell leftovers, a sane length. It
#: exists to catch `AI_COCKPIT_REASONING_MODEL="claude-opus-5 # the good one"`
#: and `$AI_MODEL` reaching the provider as a literal, not to second-guess
#: what Anthropic will accept. Whether the provider ACCEPTS the id is a
#: different question and `verify_live` asks it.
_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{2,127}$")


class CockpitModelError(RuntimeError):
    """The Cockpit cannot run because of how its models are configured."""

    status = MODEL_CONFIGURATION_MISSING

    def __init__(self, message: str, *, variables: tuple[str, ...] = ()
                 ) -> None:
        super().__init__(message)
        self.variables = tuple(variables)


class ModelConfigurationMissing(CockpitModelError):
    """A required model id is absent, blank or malformed."""

    status = MODEL_CONFIGURATION_MISSING


class ModelUnavailable(CockpitModelError):
    """A configured model id exists and the provider will not serve it."""

    status = MODEL_UNAVAILABLE


@dataclass(frozen=True)
class CockpitModels:
    """The two ids this request will actually use.

    `provider` and both ids go into every run's metadata, so an answer can be
    attributed to the model that produced it months later.
    """

    preprocess: str
    reasoning: str
    provider: str = "anthropic"
    #: How each id was obtained. Always the variable's own name -- there is no
    #: other route -- and recorded so the metadata cannot be read as implying
    #: a fallback happened.
    source: dict[str, str] = field(default_factory=dict)
    verified: bool = False
    verification: dict[str, Any] = field(default_factory=dict)

    def for_role(self, role: str) -> str:
        if role == PREPROCESS_ROLE:
            return self.preprocess
        if role == REASONING_ROLE:
            return self.reasoning
        raise ModelConfigurationMissing(
            f"{role!r} is not a Cockpit model role. The Cockpit has exactly "
            f"two: {PREPROCESS_ROLE} and {REASONING_ROLE}.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "preprocess_model": self.preprocess,
            "reasoning_model": self.reasoning,
            "source": dict(self.source),
            "fallback_used": False,
            "verified_with_provider": self.verified,
            "verification": dict(self.verification),
        }


def _read(variable: str) -> str:
    return (os.environ.get(variable) or "").strip()


def resolve() -> CockpitModels:
    """The two ids, or a refusal that names the variable to set.

    Reads the environment directly rather than going through the shared role
    resolver, because that resolver's job is to find SOMETHING to run with and
    this function's job is the opposite.
    """
    found: dict[str, str] = {}
    missing: list[str] = []
    malformed: list[tuple[str, str]] = []

    for role, variable, purpose in REQUIRED_VARS:
        value = _read(variable)
        if not value:
            missing.append(f"{variable} (for {purpose})")
            continue
        if not _SHAPE.match(value):
            malformed.append((variable, value))
            continue
        found[role] = value

    if missing or malformed:
        parts = []
        if missing:
            parts.append("not set: " + "; ".join(missing))
        if malformed:
            # The value is echoed because an operator needs to see the stray
            # quote or trailing comment they typed. These are model ids, not
            # secrets, and nothing else from the environment is echoed here.
            parts.append("not a usable model id: " + "; ".join(
                f"{name}={value!r}" for name, value in malformed))
        raise ModelConfigurationMissing(
            "The Cockpit cannot run because its model roles are not "
            "configured — " + ", and ".join(parts) + ". Both must be set "
            "explicitly. There is no fallback to AI_MODEL, to another role, "
            "or to the provider's own default: an answer nobody can attribute "
            "to a named model is not an answer this application will give.",
            variables=tuple([v.split(" ")[0] for v in missing]
                            + [name for name, _ in malformed]))

    return CockpitModels(
        preprocess=found[PREPROCESS_ROLE],
        reasoning=found[REASONING_ROLE],
        source={PREPROCESS_ROLE: PREPROCESS_VAR,
                REASONING_ROLE: REASONING_VAR})


def require(model: str, *, role: str) -> str:
    """The id a call is about to use, or a refusal.

    The last gate before dispatch. `AnthropicProvider.converse` substitutes its
    own default for an empty model — correct for the legacy paths, forbidden
    here — so an empty id must never reach it from the Cockpit.
    """
    identifier = (model or "").strip()
    if not identifier:
        variable = {PREPROCESS_ROLE: PREPROCESS_VAR,
                    REASONING_ROLE: REASONING_VAR}.get(role, "")
        raise ModelConfigurationMissing(
            f"No model id was resolved for the Cockpit {role} role, so the "
            f"request was not sent. The provider would have substituted its "
            f"own default and the answer would have come from a model nobody "
            f"chose."
            + (f" Set {variable}." if variable else ""),
            variables=(variable,) if variable else ())
    return identifier


def verify_live(provider: Any, models: CockpitModels) -> CockpitModels:
    """Ask the provider whether it will actually serve these two ids.

    Uses the token-counting endpoint: it names the exact model, costs no
    generation, and a wrong id fails there exactly as it would on a real call.
    A provider that cannot count tokens leaves the ids UNVERIFIED, which is
    reported rather than treated as either a pass or a failure.
    """
    if not getattr(provider, "configured", False):
        return models
    if not hasattr(provider, "count_tokens"):
        return CockpitModels(
            preprocess=models.preprocess, reasoning=models.reasoning,
            provider=getattr(provider, "name", models.provider),
            source=dict(models.source), verified=False,
            verification={"method": "unavailable",
                          "why": "the provider cannot count tokens, so the "
                                 "ids could not be checked without spending a "
                                 "generation"})

    results: dict[str, Any] = {}
    rejected: list[str] = []
    for role, identifier in ((PREPROCESS_ROLE, models.preprocess),
                             (REASONING_ROLE, models.reasoning)):
        try:
            results[identifier] = provider.count_tokens(
                system="ok", messages=[{"role": "user", "content": "ok"}],
                model=identifier)
        except Exception as e:                               # noqa: BLE001
            results[identifier] = f"REJECTED: {type(e).__name__}: {e}"[:400]
            rejected.append(f"{identifier} (for {role})")

    if rejected:
        raise ModelUnavailable(
            "The provider will not serve " + " or ".join(rejected)
            + ". The id is configured and wrong, or the account cannot reach "
              "it. The Cockpit stops here rather than falling back to a model "
              "that would answer.",
            variables=tuple(
                variable for role, variable, _ in REQUIRED_VARS
                if any(role in item for item in rejected)))

    return CockpitModels(
        preprocess=models.preprocess, reasoning=models.reasoning,
        provider=getattr(provider, "name", models.provider),
        source=dict(models.source), verified=True,
        verification={"method": "provider_count_tokens", "counts": results})


#: Verification is cached per (provider, id pair). A wrong id is wrong for the
#: life of the process, and paying a count-tokens call on every request to
#: rediscover that would be a tax on the correct case. Cleared by `reset` in
#: tests and whenever an operator changes the configuration.
_VERIFIED: dict[tuple[str, str, str], CockpitModels] = {}


def ensure_available(provider: Any, models: CockpitModels) -> CockpitModels:
    """Confirm once per process that the provider will serve these two ids.

    Without this, a configured-but-wrong id is discovered only when the first
    real call fails, and it arrives as a generic provider error. "The provider
    did not answer" and "the model you named does not exist here" have
    different remedies, and an operator is entitled to be told which.
    """
    if not getattr(provider, "configured", False):
        return models
    key = (str(getattr(provider, "name", "")), models.preprocess,
           models.reasoning)
    cached = _VERIFIED.get(key)
    if cached is not None:
        return cached
    verified = verify_live(provider, models)      # raises ModelUnavailable
    _VERIFIED[key] = verified
    return verified


def reset() -> None:
    """Forget the cached verification. For tests and for a reconfiguration."""
    _VERIFIED.clear()


def status() -> dict[str, Any]:
    """What the diagnostics endpoint reports. Ids, never a credential."""
    try:
        models = resolve()
    except CockpitModelError as e:
        return {"configured": False, "status": e.status, "reason": str(e),
                "variables_to_set": list(e.variables),
                "required": {variable: purpose
                             for _role, variable, purpose in REQUIRED_VARS}}
    body = models.to_dict()
    body.update({"configured": True, "status": "OK",
                 "required": {variable: purpose
                              for _role, variable, purpose in REQUIRED_VARS}})
    return body


__all__ = ["CockpitModelError", "CockpitModels", "ModelConfigurationMissing",
           "ModelUnavailable", "PREPROCESS_ROLE", "PREPROCESS_VAR",
           "REASONING_ROLE", "REASONING_VAR", "REQUIRED_VARS",
           "ensure_available", "require", "reset", "resolve", "status",
           "verify_live"]
