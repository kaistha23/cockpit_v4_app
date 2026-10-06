"""The Cockpit's own Anthropic credential, and the only place it is read.

Why the Cockpit does not use ANTHROPIC_API_KEY
----------------------------------------------
`ANTHROPIC_API_KEY` is the conventional name, and in this deployment it is
also the name the Claude Code agent uses for its own provider access. Sharing
it would mean the CreditProbe application's calls were authenticated and
billed against whatever account happened to be driving the tooling — with no
way afterwards to tell which spend was the product's and which was an
engineer's, and no way to revoke one without breaking the other.

So the Cockpit reads `COCKPIT_ANTHROPIC_API_KEY` and nothing else. Not
`ANTHROPIC_API_KEY`, not the SDK's implicit credential discovery, not a
legacy application setting, not a test fixture. Missing means the request
stops before the first provider call, with `PROVIDER_CREDENTIAL_MISSING`.

The rest of CreditProbe is unaffected. Its paths keep their existing provider
configuration, which is correct for them: this isolation is a requirement of
the Cockpit, not a change of convention for the product.

How the secret is handled here
------------------------------
It is read from the environment on demand and never stored — no module
global, no dataclass field, no settings attribute, nothing that a `repr`,
an `asdict`, a log formatter or a traceback could reach. `require()` returns
it exactly once, into the provider constructor, and every other function in
this module returns a boolean or the word PRESENT.

There is deliberately no masking helper. A prefix, a suffix, a length or a
hash are all things that look like discretion and are not: they narrow a
search, they end up in tickets, and the only safe amount of a secret to
report is none of it.
"""

from __future__ import annotations

import os
from typing import Any

#: The one variable. Named for the application, not for the SDK, because the
#: whole point is that it is not the SDK's conventional name.
COCKPIT_CREDENTIAL_VAR = "COCKPIT_ANTHROPIC_API_KEY"

#: Names the Cockpit must NOT fall back to. Listed rather than merely unused,
#: so `test_credential.py` can assert each one is inert and a future edit that
#: reintroduced one would fail rather than pass quietly.
FORBIDDEN_FALLBACKS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_API_KEY",
    "ANTHROPIC_API_KEY_FILE",
    "AI_API_KEY",
)

PRESENT = "PRESENT"
MISSING = "MISSING"

#: The typed configuration state. A sibling of MODEL_CONFIGURATION_MISSING
#: rather than the same thing: one means nobody said which model, the other
#: means nobody said which account, and the remedies are different.
PROVIDER_CREDENTIAL_MISSING = "PROVIDER_CREDENTIAL_MISSING"


class ProviderCredentialMissing(RuntimeError):
    """No Cockpit Anthropic credential is configured.

    Raised before the first provider request. The message names the variable
    to set and never quotes any value from the environment -- not the missing
    one, and not the differently-named one that happens to be present, since
    confirming that `ANTHROPIC_API_KEY` is set is itself a fact about the
    environment that this error has no reason to disclose.
    """

    status = PROVIDER_CREDENTIAL_MISSING

    def __init__(self, message: str = "") -> None:
        super().__init__(message or (
            f"The Cockpit has no Anthropic credential. Set "
            f"{COCKPIT_CREDENTIAL_VAR} in the runtime environment. It is "
            f"deliberately separate from ANTHROPIC_API_KEY so this "
            f"application's provider access and billing are its own, and "
            f"nothing here falls back to that or to any other credential."))
        self.variables = (COCKPIT_CREDENTIAL_VAR,)


def _read() -> str:
    """The value, straight from the environment. The only read in the package.

    Not routed through `backend.config.settings`: that object is a dataclass
    which is serialized in places, and a secret on it would be one `asdict`
    away from a log line.
    """
    return (os.environ.get(COCKPIT_CREDENTIAL_VAR) or "").strip()


def present() -> bool:
    """Whether a credential is configured. A boolean, never the value."""
    return bool(_read())


def require() -> str:
    """The credential, or a refusal.

    The single point at which the secret leaves this module, and it goes
    directly into the provider constructor. Nothing stores what it returns.
    """
    value = _read()
    if not value:
        raise ProviderCredentialMissing()
    return value


def status() -> str:
    """PRESENT or MISSING. There is no third answer and no partial one."""
    return PRESENT if present() else MISSING


def report() -> dict[str, Any]:
    """What diagnostics may say about the credential, and all it may say."""
    configured = present()
    return {
        "variable": COCKPIT_CREDENTIAL_VAR,
        "status": status(),
        "configured": configured,
        "note": (
            "Configured. The value is never reported -- not a prefix, a "
            "suffix, a length, a hash or a masked form."
            if configured else
            f"Not configured. Set {COCKPIT_CREDENTIAL_VAR} in the runtime "
            f"environment. The Cockpit does not fall back to "
            f"ANTHROPIC_API_KEY or to any other credential, and answers "
            f"nothing without one."),
    }


__all__ = ["COCKPIT_CREDENTIAL_VAR", "FORBIDDEN_FALLBACKS", "MISSING",
           "PRESENT", "PROVIDER_CREDENTIAL_MISSING",
           "ProviderCredentialMissing", "present", "report", "require",
           "status"]
