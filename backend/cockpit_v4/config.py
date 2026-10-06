"""
The V4 configuration contract, read from the environment and validated.

Why V4 reads the environment itself rather than extending `backend.config`
--------------------------------------------------------------------------
`backend/config.py` is shared by every module in the product. A V4 setting
added there is a setting every other runtime parses on startup, and the whole
point of this build is that V4 cannot disturb V3, EWS, What-if or anything
else that is running. So V4 owns its own names, all prefixed `COCKPIT_V4_`,
and reads them here.

Nothing in this module invents a value that would cost money or grant access.
Paths and unused ports get documented safe defaults, because a wrong path is
an inconvenience. A model id, a price, a key, a tenant, a release id and a
permission have NO default at all: a runtime that cannot say which model it
pays for does not get to guess, and `validate()` reports it as missing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace, field
from pathlib import Path
from typing import Any

from backend.cockpit_v4 import DEEP, MODES, STANDARD
from backend.cockpit_v4 import release as release_mod

#: The only credential V4 will use. There is deliberately no fallback to
#: ANTHROPIC_API_KEY, to an SDK default, or to any other module's key: a
#: Cockpit that can quietly bill someone else's account is not isolated.
CREDENTIAL_VAR = "COCKPIT_ANTHROPIC_API_KEY"

#: The analyst model. Reused from V3's name because it means the same thing.
#: `AI_COCKPIT_PREPROCESS_MODEL` is deliberately NOT read: V4 has no
#: mandatory preprocessing call for it to configure.
REASONING_MODEL_VAR = "AI_COCKPIT_REASONING_MODEL"

DEFAULT_API_PORT = 8414
DEFAULT_UI_PORT = 5414

#: Directory names V4 must never write into, whatever configuration says.
FORBIDDEN_ROOTS = ("data/curated", "data/raw", "metadata", "data/analytics")


class ConfigurationInvalid(RuntimeError):
    """A V4 setting is missing or unusable. Named, never printed."""

    def __init__(self, message: str, *, variables: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.variables = variables
        self.status = "MODEL_CONFIGURATION_MISSING"


def _flag(name: str, default: str = "false") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {
        "1", "true", "yes", "on"}


def _text(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _port(name: str, default: int) -> int:
    raw = _text(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationInvalid(
            f"{name} is not a port number.", variables=(name,)) from exc
    if not 1 <= value <= 65535:
        raise ConfigurationInvalid(
            f"{name} is outside the valid port range.", variables=(name,))
    return value


@dataclass(frozen=True)
class Limits:
    """Section 11, as data. Every field is a bound something checks."""

    mode: str
    deadline_seconds: float
    execution_submissions: int
    analysis_rounds: int
    generation_attempts: int
    provider_attempts: int
    catalog_calls: int
    artifact_reads: int
    steps_per_batch: int
    total_steps: int
    step_seconds: float
    python_memory_mib: int
    sql_memory_mib: int
    output_bytes_per_step: int
    preview_rows: int
    preview_columns: int
    #: Corrections to a malformed declarative analysis plan.  These happen
    #: before SQL is produced, so they are deliberately distinct from an
    #: execution submission.
    plan_retries: int
    format_regenerations: int
    #: Structure recovery for the FINAL ANSWER turn, bounded separately from
    #: the action turns. Not a widening: each phase still gets exactly one.
    answer_format_regenerations: int
    answer_corrections: int
    spend_ceiling_usd: float
    charts: int
    soft_input_tokens: int
    #: What the FINAL ANSWER may be. Narrative, claims, a presentation plan
    #: and follow-ups: the turn that writes for a reader.
    reserved_output_tokens: int
    #: What an ACTION may be. An action is a decision -- which tool, with
    #: which arguments -- and the analysis itself happens in DuckDB
    #: afterwards, so it does not need an answer's allowance.
    #:
    #: It is not small, and deliberately: on a model that thinks before it
    #: answers, the thinking is spent from this same allowance, and an
    #: allowance too tight to finish thinking in is an allowance that buys a
    #: truncation every time. It is smaller than the answer's, bounded, and
    #: measured -- which is the part that was missing when one number served
    #: both and an action turn was free to write four thousand tokens of
    #: analysis nobody had asked for.
    action_output_tokens: int
    #: The longest ONE action generation may block.
    #:
    #: Derived, not guessed: the run must be able to afford a second action
    #: attempt and still write the answer. At Standard that is
    #: 2 x 40s + 20s reserve inside 120s. Before this, a single action call
    #: was handed the whole remaining deadline, so the first one could spend
    #: eighty seconds and leave the run with nothing to recover with.
    action_call_seconds: float
    #: The longest ONE answer generation may block WHILE A RE-ASK REMAINS.
    #:
    #: The answer turn used to keep the whole remainder unconditionally, and
    #: the live failure is what that costs: two attempts took 43s and 47s of
    #: a 120s deadline and the third had 7.8s to live in. Bounding the last
    #: attempt would be wrong -- it is the one that must not be cut short --
    #: so this bounds only the attempts that still have a successor. See
    #: `Ledger.call_timeout_seconds`.
    answer_call_seconds: float
    #: The most an ACTION may be allowed after a TRUNCATION, and never
    #: before one.
    #:
    #: A truncated action is direct evidence that the allowance was the
    #: binding constraint on that turn -- not the schemas, not the context,
    #: not the ambiguity, all of which were removed first. Raising it on the
    #: re-ask is the one lever measurement actually points at, and it is
    #: bounded: it may reach the answer's allowance and no further.
    action_output_ceiling: int
    #: The most the FINAL ANSWER may be allowed after a TRUNCATION, and
    #: never before one. The action side's twin, for the phase that needed
    #: it more.
    #:
    #: Measured, not guessed. A correct answer to a four-step analysis --
    #: sixteen claims, a narrative that references them, four tables and two
    #: charts -- serializes to about 12.6 KB, which the run's own estimator
    #: puts at ~5,700 tokens BEFORE the model thinks. Against a 4,096
    #: allowance that object could not be emitted at any effort setting, and
    #: the live truncation was not marginal: it was arithmetic.
    answer_output_ceiling: int
    #: How hard the model works before it answers, per phase. Empty means
    #: "do not send it": see `Capability.supports_effort_control`.
    action_effort: str
    answer_effort: str
    #: Effort on a RE-ASK after the answer was cut off.
    #:
    #: Thinking is spent from the same allowance as the answer, so a
    #: truncated turn is one where thinking and the object competed and the
    #: object lost. The re-ask lowers the former to buy room for the latter:
    #: by then the analysis is done and the numbers are chosen, and what is
    #: left is serialising a decision already taken.
    answer_recovery_effort: str
    #: Time held back so a finished analysis can still be written up. An
    #: action call is refused once the run is inside this window; the ANSWER
    #: call may spend it, because it is what the window was held for.
    finalization_reserve_seconds: float
    #: Below this there is not enough time for any useful provider call.
    min_call_seconds: float


STANDARD_LIMITS = Limits(
    mode=STANDARD, deadline_seconds=60.0, execution_submissions=5,
    analysis_rounds=3, generation_attempts=12, provider_attempts=24,
    catalog_calls=4, artifact_reads=6, steps_per_batch=6, total_steps=12,
    step_seconds=15.0, python_memory_mib=512, sql_memory_mib=512,
    output_bytes_per_step=25 * 1024 * 1024, preview_rows=100,
    preview_columns=32, plan_retries=3, format_regenerations=2,
    answer_format_regenerations=2, answer_corrections=1,
    spend_ceiling_usd=1.0, charts=6, soft_input_tokens=6_000,
    reserved_output_tokens=8_192, action_output_tokens=3_072,
    action_output_ceiling=4_096, answer_output_ceiling=12_288,
    action_call_seconds=30.0, answer_call_seconds=55.0,
    action_effort="low", answer_effort="medium",
    answer_recovery_effort="low",
    finalization_reserve_seconds=20.0, min_call_seconds=5.0)

DEEP_LIMITS = Limits(
    mode=DEEP, deadline_seconds=120.0, execution_submissions=5,
    analysis_rounds=3, generation_attempts=16, provider_attempts=32,
    catalog_calls=6, artifact_reads=10, steps_per_batch=8, total_steps=24,
    step_seconds=30.0, python_memory_mib=1024, sql_memory_mib=1024,
    output_bytes_per_step=50 * 1024 * 1024, preview_rows=100,
    preview_columns=32, plan_retries=3, format_regenerations=2,
    answer_format_regenerations=2, answer_corrections=1,
    spend_ceiling_usd=2.0, charts=8, soft_input_tokens=10_000,
    reserved_output_tokens=12_288, action_output_tokens=4_096,
    action_output_ceiling=6_144, answer_output_ceiling=16_384,
    action_call_seconds=45.0, answer_call_seconds=90.0,
    action_effort="medium", answer_effort="high",
    answer_recovery_effort="low",
    finalization_reserve_seconds=25.0, min_call_seconds=5.0)


#: What a DATA_ANALYSIS run gets once the analyst declares one.
#:
#: The live evidence: a Standard analytical run hit the 60-second deadline
#: while the model was still writing SQL, and another stopped at COST_LIMIT
#: with about $0.71 committed because a further ~$0.30 response reservation
#: did not fit under $1.00. Neither bound was wrong for a product question;
#: both were wrong for an analysis that reads metadata, writes a query,
#: repairs it and then writes an answer.
#:
#: The cost figures are derived rather than chosen. At the verified Opus price
#: card ($15 / Mtok input, $75 / Mtok output), one analytical generation with
#: a ~12k-token assembled request and a 4,096-token response costs about
#: $0.18 + $0.31 = $0.49 worst case. A realistic analysis is metadata,
#: submission, one repair and a finalization -- four generations -- so about
#: $1.96 worst case and well under that in practice once responses settle at
#: actual usage. $1.50 covers the common three-generation path with the
#: reservation headroom the ledger needs; Deep's $3.00 covers the four-
#: generation path with a repair.
#: 120 seconds was measured against a run that writes ONE query and ONE
#: answer. Two live runs on a real book spent 25s, 21s and 52s on three
#: generations -- reading the question, authoring a repaired submission, and
#: writing the answer -- before the answer correction had even started, and
#: both were reaped mid-sentence. The failure mode was the defect and is
#: fixed elsewhere in this change: the run now settles cooperatively and
#: publishes its rows. This is the other half. A question that needs a
#: repair and a correction is an ordinary analytical question, not an abuse
#: of the allowance, and 180 seconds is what one costs.
#:
#: The cost ceiling is unchanged. Time was the bound that bit; spend was
#: nowhere near $1.50 in either run, and raising a ceiling nothing reached
#: would only weaken it.
ANALYTICAL_STANDARD_LIMITS = replace(
    STANDARD_LIMITS, deadline_seconds=180.0, spend_ceiling_usd=1.50)

ANALYTICAL_DEEP_LIMITS = replace(
    DEEP_LIMITS, deadline_seconds=240.0, spend_ceiling_usd=3.00)


#: Names the limits profile an operator selects. Read at CALL time, never at
#: import: a dotenv loaded after this module, or a test that sets it, must
#: take effect on the next run without a re-import.
PROFILE_VAR = "COCKPIT_V4_LIMITS_PROFILE"

#: Time the ledger keeps back to settle a call and stop cleanly. Owned here
#: because the profile invariants need it and `budgets` imports this module,
#: not the reverse; `Ledger.SETTLEMENT_MARGIN_SECONDS` is this same value.
SETTLEMENT_MARGIN_SECONDS = 2.0

#: The families a profile can name, in the order they widen.
FAMILY_KEYS = ("standard", "deep", "analytical_standard", "analytical_deep")
_ANALYTICAL_KEYS = ("analytical_standard", "analytical_deep")
_BASE_ATTRS = {
    "standard": "STANDARD_LIMITS", "deep": "DEEP_LIMITS",
    "analytical_standard": "ANALYTICAL_STANDARD_LIMITS",
    "analytical_deep": "ANALYTICAL_DEEP_LIMITS",
}

#: Per-family field overrides, by profile. "default" is empty on purpose: it
#: is exactly the constants above.
#:
#: "local" exists because the constants were sized for a hosted model and a
#: local oMLX Qwen is slower. Measured on it: roughly 43 output tokens per
#: second, answer latency p50 21s / p90 61s / p95 76s, action p95 30s. The
#: default 30s action and 55s answer windows cut off a healthy local call
#: about one time in five, and a run that is cut off is retried inside the
#: same deadline. Only the ANALYTICAL families move: product help is a short
#: question with a short answer, and giving it five minutes would only slow
#: the way it fails.
_LOCAL_ANALYTICAL = {
    "deadline_seconds": 300.0, "action_call_seconds": 45.0,
    "answer_call_seconds": 100.0, "finalization_reserve_seconds": 90.0}
PROFILES: dict[str, dict[str, dict[str, float]]] = {
    "default": {},
    "local": {key: dict(_LOCAL_ANALYTICAL) for key in _ANALYTICAL_KEYS},
}


def active_profile() -> str:
    """The selected profile name. Unset or empty means "default"."""
    name = _text(PROFILE_VAR).lower() or "default"
    if name not in PROFILES:
        raise ConfigurationInvalid(
            f"{PROFILE_VAR}={name!r} is not a known limits profile "
            f"(expected one of: {', '.join(sorted(PROFILES))}).")
    return name


def _check_invariants(key: str, limits: Limits, profile: str) -> None:
    """Refuse a limits combination that cannot run its own plan.

    An analytical run is built for two actions, then the reserve held to
    write the answer, then the settlement margin. A combination where those
    do not fit inside the deadline would pass startup and fail on the first
    slow call. Product help is not built for two actions plus a reserve, so
    the first rule is applied to the analytical families only.
    """
    where = f"{PROFILE_VAR}={profile!r}, family {key!r}"
    if key in _ANALYTICAL_KEYS:
        need = (2 * limits.action_call_seconds
                + limits.finalization_reserve_seconds
                + SETTLEMENT_MARGIN_SECONDS)
        if need > limits.deadline_seconds:
            raise ConfigurationInvalid(
                f"{where}: two actions ({limits.action_call_seconds:g}s "
                f"each) plus the finalization reserve "
                f"({limits.finalization_reserve_seconds:g}s) plus the "
                f"{SETTLEMENT_MARGIN_SECONDS:g}s settlement margin need "
                f"{need:g}s, which exceeds the {limits.deadline_seconds:g}s "
                f"deadline.")
    if limits.answer_call_seconds > (limits.deadline_seconds
                                     - limits.min_call_seconds):
        raise ConfigurationInvalid(
            f"{where}: answer_call_seconds "
            f"({limits.answer_call_seconds:g}s) exceeds the deadline "
            f"({limits.deadline_seconds:g}s) less min_call_seconds "
            f"({limits.min_call_seconds:g}s).")


def effective_limits(family_key: str) -> Limits:
    """The limits a family runs under: the constants plus the profile.

    The module attribute is read at call time (`globals()`), not captured,
    because `scripts/cockpit_v4/example_traces.py` replaces
    `STANDARD_LIMITS` on this module to force a short deadline, and a copy
    taken at import would ignore it.
    """
    if family_key not in _BASE_ATTRS:
        raise KeyError(f"unknown limits family {family_key!r}")
    profile = active_profile()
    limits = globals()[_BASE_ATTRS[family_key]]
    overrides = PROFILES[profile].get(family_key)
    if overrides:
        limits = replace(limits, **overrides)
        # Validated only where a profile changed something. The constants
        # are what shipped (and a script may deliberately shrink one to
        # force a deadline, which no invariant should veto); a profile is
        # operator input and is the thing that can be wrong.
        _check_invariants(family_key, limits, profile)
    return limits


def limits_for(mode: str) -> Limits:
    return effective_limits("deep" if str(mode).lower() == DEEP
                            else "standard")


def analytical_limits_for(mode: str) -> Limits:
    """The same mode's limits, widened for a declared DATA_ANALYSIS.

    Applied when the analyst declares the mode, not at intake: a Product Help
    question keeps the tight allowance, and nothing is narrowed by this.
    """
    return effective_limits("analytical_deep" if str(mode).lower() == DEEP
                            else "analytical_standard")


@dataclass(frozen=True)
class V4Config:
    """The resolved V4 runtime settings. Values, never secrets."""

    enabled: bool
    provider: str
    reasoning_model: str
    runtime_dir: Path
    state_database: str
    release_id: str
    api_port: int
    ui_port: int
    local_demo_auth: bool
    price_card_path: str
    memory_enabled: bool
    memory_model: str
    default_mode: str
    heartbeat_seconds: float
    lease_heartbeat_seconds: float
    lease_stale_seconds: float
    supervisor_poll_seconds: float
    #: Present/absent only. The value never leaves `credential.require()`.
    credential_present: bool
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def artifacts_dir(self) -> Path:
        return self.runtime_dir / "artifacts"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_dir / "logs"

    @property
    def pids_dir(self) -> Path:
        return self.runtime_dir / "pids"

    def to_dict(self) -> dict[str, Any]:
        """Safe for diagnostics: no secret, and no secret-bearing URL."""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "reasoning_model": self.reasoning_model,
            "runtime_dir": str(self.runtime_dir),
            "state_database": redact_database_url(self.state_database),
            "release_id": self.release_id,
            "api_port": self.api_port,
            "ui_port": self.ui_port,
            "local_demo_auth": self.local_demo_auth,
            "price_card_path": self.price_card_path,
            "memory_enabled": self.memory_enabled,
            "memory_model": self.memory_model,
            "default_mode": self.default_mode,
            "credential": "PRESENT" if self.credential_present else "MISSING",
            "missing_settings": list(self.missing),
        }


def redact_database_url(url: str) -> str:
    """Keep the scheme and host; drop anything that could be a password."""
    text = str(url or "")
    if "://" not in text:
        return text
    scheme, _, rest = text.partition("://")
    if "@" in rest:
        _, _, host = rest.rpartition("@")
        return f"{scheme}://***@{host}"
    return text


def default_runtime_dir() -> Path:
    return Path(_text("COCKPIT_V4_RUNTIME_DIR")
                or Path.home() / ".creditprobe" / "cockpit_v4").expanduser()


def check_write_target(path: Path | str, cfg: "V4Config") -> Path:
    """Refuse a write outside the V4 runtime directory, before the first byte.

    A branch isolates code. It does not isolate a Parquet lake, a SQLite file
    or a log directory, so this is where runtime isolation is actually
    enforced -- and it is enforced by path containment, not by trusting that
    every caller passed the right directory.
    """
    target = Path(path).expanduser().resolve()
    root = cfg.runtime_dir.expanduser().resolve()
    text = str(target).replace("\\", "/")
    for forbidden in FORBIDDEN_ROOTS:
        if f"/{forbidden}/" in f"{text}/":
            raise ConfigurationInvalid(
                f"{target} is inside {forbidden}, which belongs to the rest "
                f"of the product. V4 writes only under its runtime directory.")
    if root not in target.parents and target != root:
        raise ConfigurationInvalid(
            f"{target} is outside the V4 runtime directory {root}. A build "
            f"that can write outside its own runtime is not isolated, "
            f"whatever branch it is on.")
    return target


def load() -> V4Config:
    """Read the environment. Reports what is missing; never invents it."""
    runtime_dir = default_runtime_dir()
    mode = _text("COCKPIT_V4_DEFAULT_MODE", STANDARD).lower()
    if mode not in MODES:
        mode = STANDARD

    reasoning_model = _text(REASONING_MODEL_VAR)
    # The Saudi demonstration release is the DEFAULT, not a fallback. An
    # explicitly configured release is always honoured, and a configured
    # release that is missing from the runtime is an error rather than an
    # excuse to quietly load this one. What this removes is the third case:
    # a runtime with nothing configured, which used to refuse to start and
    # now starts on the release the demonstration is built around.
    release_id = _text("COCKPIT_V4_RELEASE_ID") or release_mod.DEFAULT_RELEASE_ID
    price_card = _text("COCKPIT_V4_PRICE_CARD")
    memory_enabled = _flag("COCKPIT_V4_MEMORY_ENABLED", "false")
    memory_model = _text("COCKPIT_V4_MEMORY_MODEL")

    missing: list[str] = []
    if not reasoning_model:
        missing.append(REASONING_MODEL_VAR)
    if not price_card:
        missing.append("COCKPIT_V4_PRICE_CARD")
    if memory_enabled and not memory_model:
        missing.append("COCKPIT_V4_MEMORY_MODEL")
    provider = _text("AI_PROVIDER", "anthropic").lower()
    if provider == "anthropic" and not os.environ.get(CREDENTIAL_VAR, "").strip():
        missing.append(CREDENTIAL_VAR)

    state_db = _text("COCKPIT_V4_STATE_DATABASE") or str(
        runtime_dir / "state" / "cockpit_v4.sqlite3")

    return V4Config(
        enabled=_flag("COCKPIT_AGENTIC_V4", "false"),
        provider=provider,
        reasoning_model=reasoning_model,
        runtime_dir=runtime_dir,
        state_database=state_db,
        release_id=release_id,
        api_port=_port("COCKPIT_V4_API_PORT", DEFAULT_API_PORT),
        ui_port=_port("COCKPIT_V4_UI_PORT", DEFAULT_UI_PORT),
        local_demo_auth=_flag("COCKPIT_V4_LOCAL_DEMO_AUTH", "false"),
        price_card_path=price_card,
        memory_enabled=memory_enabled,
        memory_model=memory_model,
        default_mode=mode,
        heartbeat_seconds=float(_text("COCKPIT_V4_HEARTBEAT_SECONDS", "5")),
        lease_heartbeat_seconds=2.0,
        lease_stale_seconds=10.0,
        supervisor_poll_seconds=2.0,
        credential_present=(bool(os.environ.get(CREDENTIAL_VAR, "").strip())
                            if provider == "anthropic" else True),
        missing=tuple(missing))


def validate(cfg: V4Config | None = None) -> dict[str, Any]:
    """Name every missing setting at once. Never prints a value."""
    cfg = cfg or load()
    return {
        "ok": not cfg.missing and cfg.enabled,
        "enabled": cfg.enabled,
        "missing": list(cfg.missing),
        "settings": cfg.to_dict(),
    }


__all__ = ["ANALYTICAL_DEEP_LIMITS", "ANALYTICAL_STANDARD_LIMITS",
           "CREDENTIAL_VAR", "ConfigurationInvalid", "DEEP_LIMITS",
           "analytical_limits_for", "PROFILE_VAR", "PROFILES",
           "SETTLEMENT_MARGIN_SECONDS", "FAMILY_KEYS", "active_profile",
           "effective_limits",
           "DEFAULT_API_PORT", "DEFAULT_UI_PORT", "Limits",
           "REASONING_MODEL_VAR", "STANDARD_LIMITS", "V4Config",
           "check_write_target", "default_runtime_dir", "limits_for", "load",
           "redact_database_url", "validate"]
