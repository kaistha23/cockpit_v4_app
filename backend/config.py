"""
Central application configuration.

A single `settings` object, loaded once at import, is the one source of truth for
host/port, environment, filesystem paths, AI-backend endpoints, and (from later
phases) the database URL and Flask secret. Read it everywhere via
`from config import settings` instead of scattering literals or `os.environ`
lookups through the codebase.

Precedence: real process environment variables win; a local `.env` file fills in
the gaps in development. In production the NSSM service injects these as env vars
and no `.env` file is present.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# config.py lives in backend/, so the project root (where .env, logs/, uploads/
# and the data workbooks live) is one level up.
BASE_DIR = Path(__file__).resolve().parent.parent

# Dev convenience only: load .env if present. Real env vars always take precedence
# (override=False), so production service-injected values are never shadowed.
load_dotenv(BASE_DIR / ".env", override=False)


def _get(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val is not None and val != "" else default


def _int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    """A configured decimal, or the default. A malformed price must not become
    a silently different price: an unparseable value falls back to the default,
    and the default for every price is 0.0, which the ledger reads as "not
    configured" rather than "free"."""
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


def _resolve_dir(value: str) -> Path:
    """Resolve a configured directory relative to the project root unless it is
    already absolute. The directory is created if missing."""
    p = Path(value)
    if not p.is_absolute():
        p = BASE_DIR / p
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass(frozen=True)
class Settings:
    env: str
    host: str
    port: int
    log_dir: Path
    upload_dir: Path
    max_upload_mb: int
    ollama_base_url: str
    anthropic_api_key: str
    omlx_url: str
    omlx_api_key: str
    #: Which LLM provider orchestrates Ask CreditProbe. 'anthropic' by default;
    #: 'offline' forces LIMITED OFFLINE MODE regardless of any key present.
    ai_provider: str
    #: Pins the model. Empty means the provider's own pinned default, which is
    #: a fixed id rather than an alias so a provider-side change cannot alter
    #: how CreditProbe reads a question without a release.
    ai_model: str
    #: Cockpit Intelligence V2. Cockpit-scoped and OFF unless explicitly set,
    #: so every other feature — Early Warning, What-if, Scorecards, Planner,
    #: Lenses, Playbook — keeps the behaviour it has today whatever this branch
    #: does. Nothing outside `backend/cockpit_v2/` and the four guarded call
    #: sites listed in docs/cockpit_v2/INTEGRATION_NOTES.md reads it.
    cockpit_intelligence_v2: bool
    #: The namespace every V2 dataset name, seed target and cache key carries.
    #: The startup guard refuses to write anywhere that does not contain it.
    cockpit_v2_namespace: str
    #: Cockpit Agentic V3. Cockpit-scoped and OFF unless explicitly set, for
    #: exactly the reason the V2 switch is: Early Warning, Stress Testing,
    #: Scorecard Validation, Lenses, Playbooks and the Planner must behave
    #: identically whether this is on or off, and the flag-off path is tested
    #: rather than assumed. Nothing outside `backend/cockpit_agentic/` and the
    #: guarded call sites listed in docs/cockpit_agentic_v3/INTEGRATION.md
    #: reads it.
    cockpit_agentic_v3: bool
    #: The namespace every V3 dataset name, release directory and cache key
    #: carries. The startup guard refuses to write anywhere without it.
    cockpit_agentic_v3_namespace: str
    #: Standard or Deep. The mode a request runs under when the caller does not
    #: choose one. Never silently upgraded (spec 9.6).
    cockpit_agentic_v3_default_mode: str
    #: The tenant a principal resolves to when the deployment is single-tenant
    #: and carries no tenant on its principals. Named explicitly rather than
    #: defaulted to a magic string, because artifact keys, cache keys and the
    #: release's own rows all carry it and a mismatch must be visible.
    cockpit_agentic_v3_default_tenant: str
    #: Explicit administrator overrides for the numeric budget limits. Zero
    #: means "use the specification's value". Section 9 forbids the MODEL and
    #: the BROWSER from raising a limit, and section 9.6 explicitly
    #: contemplates the other case: profile the packets during UAT and
    #: "request an explicit administrative configuration change if necessary".
    #: These are that request's destination. They are inert unless set, every
    #: override is reported in the diagnostics and the handoff, and the five
    #: submissions and three analysis rounds have no override at all because
    #: sections 1.7 and 9.2 state them as architectural invariants rather than
    #: as starting values.
    cockpit_agentic_v3_standard_input_tokens: int
    cockpit_agentic_v3_deep_input_tokens: int
    cockpit_agentic_v3_standard_total_tokens: int
    cockpit_agentic_v3_deep_total_tokens: int
    cockpit_agentic_v3_standard_deadline_seconds: float
    cockpit_agentic_v3_deep_deadline_seconds: float
    cockpit_agentic_v3_standard_model_requests: int
    cockpit_agentic_v3_deep_model_requests: int
    #: USD per million input/output tokens for the two Cockpit roles. Spending
    #: ceilings are only meaningful against configured prices; when these are
    #: zero the ledger reports cost as UNKNOWN and refuses to claim the cost
    #: limit is reliable (spec 9.1).
    cockpit_agentic_v3_sonnet_input_usd_per_mtok: float
    cockpit_agentic_v3_sonnet_output_usd_per_mtok: float
    cockpit_agentic_v3_opus_input_usd_per_mtok: float
    cockpit_agentic_v3_opus_output_usd_per_mtok: float
    # Populated in later phases; empty until then.
    database_url: str
    secret_key: str
    #: When true every API call needs a signed session, and the header-based
    #: role switcher is refused. Off by default so a local run works out of the
    #: box; on for any deployment where the browser is not the only client.
    require_login: bool

    # ---- API (FastAPI) ----
    api_host: str
    api_port: int
    cors_origins: tuple[str, ...]

    # ---- analytical data layers (see docs/ARCHITECTURE.md §4.2) ----
    # raw:       source files exactly as received — never modified
    # curated:   mapped to governed field names, typed, validated
    # analytics: business-ready Parquet the engine reads through the DAL
    raw_dir: Path
    curated_dir: Path
    analytics_dir: Path
    metadata_dir: Path

    @property
    def is_prod(self) -> bool:
        return self.env.lower() == "prod"

    @property
    def debug(self) -> bool:
        return not self.is_prod

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def has_database(self) -> bool:
        """False in environments where Postgres has not been configured yet. The
        API degrades to a clearly-reported 'not configured' state rather than
        failing to start, so a non-developer can boot the app before setting the
        database up."""
        return bool(self.database_url)


def _load() -> Settings:
    return Settings(
        env=_get("ENV", "dev"),
        host=_get("HOST", "127.0.0.1"),
        port=_int("PORT", 8050),
        log_dir=_resolve_dir(_get("LOG_DIR", "logs")),
        upload_dir=_resolve_dir(_get("UPLOAD_DIR", "uploads")),
        max_upload_mb=_int("MAX_UPLOAD_MB", 25),
        ollama_base_url=_get("OLLAMA_BASE_URL", "http://localhost:11434"),
        anthropic_api_key=_get("ANTHROPIC_API_KEY", ""),
        omlx_url=_get("OMLX_URL", "http://localhost:9906/v1"),
        omlx_api_key=_get("OMLX_API_KEY", ""),
        ai_provider=_get("AI_PROVIDER", "anthropic"),
        ai_model=_get("AI_MODEL", ""),
        cockpit_intelligence_v2=_get("COCKPIT_INTELLIGENCE_V2", "false")
        .strip().lower() in ("1", "true", "yes", "on"),
        cockpit_v2_namespace=_get("COCKPIT_V2_NAMESPACE", "cockpit_v2"),
        cockpit_agentic_v3=_get("COCKPIT_AGENTIC_V3", "false").strip().lower()
        in ("1", "true", "yes", "on"),
        cockpit_agentic_v3_namespace=_get("COCKPIT_AGENTIC_V3_NAMESPACE",
                                          "cockpit_agentic_v3"),
        cockpit_agentic_v3_default_mode=_get("COCKPIT_AGENTIC_V3_DEFAULT_MODE",
                                             "standard"),
        cockpit_agentic_v3_default_tenant=_get(
            "COCKPIT_AGENTIC_V3_DEFAULT_TENANT", "demo-tenant"),
        cockpit_agentic_v3_standard_input_tokens=_int(
            "COCKPIT_AGENTIC_V3_STANDARD_INPUT_TOKENS", 0),
        cockpit_agentic_v3_deep_input_tokens=_int(
            "COCKPIT_AGENTIC_V3_DEEP_INPUT_TOKENS", 0),
        cockpit_agentic_v3_standard_total_tokens=_int(
            "COCKPIT_AGENTIC_V3_STANDARD_TOTAL_TOKENS", 0),
        cockpit_agentic_v3_deep_total_tokens=_int(
            "COCKPIT_AGENTIC_V3_DEEP_TOTAL_TOKENS", 0),
        cockpit_agentic_v3_standard_deadline_seconds=_float(
            "COCKPIT_AGENTIC_V3_STANDARD_DEADLINE_SECONDS", 0.0),
        cockpit_agentic_v3_deep_deadline_seconds=_float(
            "COCKPIT_AGENTIC_V3_DEEP_DEADLINE_SECONDS", 0.0),
        cockpit_agentic_v3_standard_model_requests=_int(
            "COCKPIT_AGENTIC_V3_STANDARD_MODEL_REQUESTS", 0),
        cockpit_agentic_v3_deep_model_requests=_int(
            "COCKPIT_AGENTIC_V3_DEEP_MODEL_REQUESTS", 0),
        cockpit_agentic_v3_sonnet_input_usd_per_mtok=_float(
            "COCKPIT_AGENTIC_V3_SONNET_INPUT_USD_PER_MTOK", 0.0),
        cockpit_agentic_v3_sonnet_output_usd_per_mtok=_float(
            "COCKPIT_AGENTIC_V3_SONNET_OUTPUT_USD_PER_MTOK", 0.0),
        cockpit_agentic_v3_opus_input_usd_per_mtok=_float(
            "COCKPIT_AGENTIC_V3_OPUS_INPUT_USD_PER_MTOK", 0.0),
        cockpit_agentic_v3_opus_output_usd_per_mtok=_float(
            "COCKPIT_AGENTIC_V3_OPUS_OUTPUT_USD_PER_MTOK", 0.0),
        database_url=_get("DATABASE_URL", ""),
        secret_key=_get("SECRET_KEY", ""),
        # Signing in is compulsory by default. A credit-risk product where
        # anybody who reaches the page is silently an Analyst is not a product
        # anybody can deploy, and a security default that has to be turned on is
        # a security default that gets forgotten. Set REQUIRE_LOGIN=false only
        # for a throwaway local session where nobody has seeded an account.
        require_login=_get("REQUIRE_LOGIN", "true").strip().lower()
        in ("1", "true", "yes", "on"),
        api_host=_get("API_HOST", "127.0.0.1"),
        api_port=_int("API_PORT", 8000),
        # Both spellings of the same machine. Somebody running the backend
        # directly and typing 127.0.0.1 into the browser is doing nothing
        # unusual, and a blocked preflight there looks like a broken product
        # rather than a CORS default. In Docker neither is used: the browser
        # calls the page's own origin and Next forwards it.
        cors_origins=tuple(
            o.strip() for o in _get(
                "CORS_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000",
            ).split(",") if o.strip()
        ),
        raw_dir=_resolve_dir(_get("DATA_RAW_DIR", "data/raw")),
        curated_dir=_resolve_dir(_get("DATA_CURATED_DIR", "data/curated")),
        analytics_dir=_resolve_dir(_get("DATA_ANALYTICS_DIR", "data/analytics")),
        metadata_dir=_resolve_dir(_get("METADATA_DIR", "metadata")),
    )


settings = _load()
