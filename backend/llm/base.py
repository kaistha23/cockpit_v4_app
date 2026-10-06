"""
What CreditProbe needs from a language model, and nothing more.

The orchestrator asks a model for exactly one thing: a **structured document**
that conforms to a schema CreditProbe supplied. It never asks for prose that will
be parsed into a decision, and it never asks for a figure. That constraint is
what lets the provider be swapped without touching the analytical path, and it
is why this interface has one method.

Why schema-constrained rather than "return JSON"
------------------------------------------------
A model asked for JSON in prose returns JSON *most* of the time. The failure is
not that parsing throws — that is recoverable — but that a plausible-looking
object with a misspelled key silently loses a filter, and the analysis then
answers a slightly different question with complete confidence. So the schema is
enforced at the provider boundary: the model is given a tool whose input schema
IS the contract, and a reply that does not call that tool is an error rather
than something to salvage.

Why no streaming
----------------
Nothing downstream can start until the whole plan is known — the validator
rejects partial plans by construction. Streaming would add a failure mode and
buy nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    """The provider could not produce a conforming answer.

    Deliberately not split into subclasses per failure kind. Every caller does
    the same thing — records that the model was unavailable and degrades — and a
    taxonomy nobody switches on is decoration.
    """


@dataclass(frozen=True)
class LLMResult:
    """One structured answer, with what it cost to get it."""

    data: dict[str, Any]
    model: str
    #: Wall-clock, so a slow provider is visible in the Trace rather than felt.
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: Input tokens served from the provider's prompt cache, and written to it.
    #: Carried on the result — not only in the telemetry ledger — because R2
    #: §16 measures cost per QUESTION, and a question is several calls whose
    #: caching behaviour differs: the first pays the write, the rest read.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    #: Set when the provider retried. Recorded because a plan that took three
    #: attempts is worth knowing about even though it succeeded.
    attempts: int = 1
    #: The provider's own identifier for the call. Safe to show, and the only
    #: handle a provider can trace a request by on their side.
    request_id: str = ""


@dataclass
class ConverseResult:
    """One turn of a multi-turn conversation, with its blocks preserved.

    `structured` returns the tool input and throws the rest away, which is
    right for a single-shot schema-constrained call and wrong for a loop. A
    conversation that continues has to send the assistant's own blocks back
    verbatim, in order, paired with the tool results that answer them --
    including any opaque signature block the provider requires and nobody may
    invent. So this carries the raw content list as well as the parsed calls.

    Nothing here interprets the blocks. `assistant_blocks` goes back into the
    next request exactly as it arrived.
    """

    #: The assistant's content blocks, exactly as the provider returned them.
    #: Appended to `messages` verbatim on the next turn.
    assistant_blocks: list[Any] = field(default_factory=list)
    #: Text the model wrote outside any tool call.
    text: str = ""
    #: Parsed tool calls: {"id", "name", "input"}. Every one MUST be answered
    #: with a matching tool_result on the next turn, in the same order.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    model: str = ""
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    attempts: int = 1
    request_id: str = ""

    @property
    def truncated(self) -> bool:
        """The model ran out of output tokens mid-answer.

        Treated as INCOMPLETE by callers rather than as a short answer: a
        truncated plan is not a smaller plan, it is half of one.
        """
        return self.stop_reason == "max_tokens"

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True)
class ProviderStatus:
    """What Settings and the Cockpit show about the AI.

    `configured` is the only field anything branches on. The rest is display,
    and none of it may ever carry the key — `detail` is written for a screen a
    user can screenshot.
    """

    provider: str
    model: str
    #: A key exists. Necessary for the model to answer, and never sufficient —
    #: `state` is what says whether it actually does.
    configured: bool
    #: offline | configured | connected | degraded. See backend/llm/telemetry.
    state: str
    detail: str
    #: The full observed health, for Settings and the header chip. Empty for a
    #: provider that has never been called.
    health: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        from backend.llm import telemetry

        return telemetry.LABELS.get(self.state, self.state.upper())

    @property
    def live(self) -> bool:
        """Whether a real structured response has actually come back."""
        from backend.llm import telemetry

        return self.state == telemetry.CONNECTED

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model,
                "configured": self.configured, "state": self.state,
                "label": self.label, "live": self.live, "detail": self.detail,
                "health": dict(self.health)}


class LLMProvider(Protocol):
    """A model CreditProbe can orchestrate with."""

    name: str
    model: str

    @property
    def configured(self) -> bool:
        """Whether this provider can actually be called."""
        ...

    def status(self) -> ProviderStatus:
        ...

    def count_tokens(self, *, system: Any, messages: list[dict[str, Any]],
                     tools: list[dict[str, Any]] | None = None,
                     model: str = "") -> int:
        """Input tokens for this exact request, per the provider's own counter.

        Optional on a provider: callers check `hasattr` and fall back to a
        documented local estimate, recording which they used.
        """
        ...

    def converse(self, *, system: Any, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]] | None = None,
                 max_tokens: int = 4096, model: str = "",
                 purpose: str = "conversation", role: str = "",
                 effort: str = "", timeout: float = 0.0,
                 allow_retry: bool = True,
                 tool_choice: dict[str, Any] | None = None,
                 output_config: dict[str, Any] | None = None,
                 temperature: float | None = None) -> "ConverseResult":
        """One turn of a multi-turn conversation.

        Optional on a provider: callers check `hasattr` and report the
        capability as unavailable rather than degrading silently, because a
        repair loop that cannot preserve tool blocks is not a repair loop.
        """
        ...

    def structured(self, *, system: str, prompt: str, schema: dict[str, Any],
                   tool_name: str, tool_description: str,
                   max_tokens: int = 2000,
                   purpose: str = "reading",
                   model: str = "",
                   role: str = "", effort: str = "") -> LLMResult:
        """Return a document conforming to `schema`, or raise LLMError.

        `role` and `effort` are recorded on the call rather than inferred from
        `purpose`. An administrator who configured four models needs to see
        which one actually answered, and a product that reports differentiated
        routing it is not performing is one whose certification means nothing.

        `model` names the model to serve THIS call, so a configured role can be
        answered by the model an administrator chose for it. Empty means the
        provider's configured default. A provider that cannot serve the named
        model must fail rather than substitute one: an answer from a different
        model than the one certified is an answer with no certification.

        `purpose` names the stage the call belongs to — reading, repair,
        interpretation, validation — so a failure can be attributed to a stage
        rather than to "the AI".
        """
        ...


@dataclass
class NullProvider:
    """No model is configured.

    It raises rather than inventing an answer. The product's offline behaviour
    is decided one level up, where it can be *labelled* — a provider that
    quietly returned something plausible would make LIMITED OFFLINE MODE
    unreportable, which is the specific dishonesty this class exists to avoid.
    """

    name: str = "none"
    model: str = ""
    reason: str = "No AI provider key is configured."

    @property
    def configured(self) -> bool:
        return False

    def status(self) -> ProviderStatus:
        from backend.llm import telemetry

        return ProviderStatus(
            provider="none", model="", configured=False,
            state=telemetry.OFFLINE,
            # Not "LIMITED OFFLINE MODE". A deployment with no external
            # provider is not a broken deployment: the deterministic reader
            # parses the question and the governed runtime executes it, and
            # on a bank network that refuses egress it is the only permitted
            # configuration. The detail names the mode and its one real
            # limitation - phrasing, not capability - rather than reading to
            # a client as an outage.
            detail=(self.reason + " CreditProbe is running as a GOVERNED "
                    "LOCAL READER: questions are read by a deterministic "
                    "semantic planner over the governed catalogue, which "
                    "understands credit concepts but not arbitrary "
                    "phrasing."),
            health=telemetry.health(provider="none", model="",
                                    configured=False))

    def count_tokens(self, **_: Any) -> int:
        raise LLMError(
            "No intelligence provider is configured, so tokens cannot be "
            "counted against a model. The caller falls back to a local "
            "estimate and records that it did.")

    def converse(self, **_: Any) -> ConverseResult:
        raise LLMError(
            "No intelligence provider is configured, so no conversation can "
            "be held. This is reported rather than substituted: there is no "
            "deterministic stand-in for a model-authored analysis.")

    def structured(self, **_: Any) -> LLMResult:
        raise LLMError(self.reason)


#: Registered provider factories, by the value of AI_PROVIDER.
_FACTORIES: dict[str, Any] = {}


def register(name: str, factory: Any) -> None:
    _FACTORIES[name] = factory


def factories() -> dict[str, Any]:
    return dict(_FACTORIES)


__all__ = ["LLMError", "LLMProvider", "LLMResult", "NullProvider",
           "ProviderStatus", "factories", "register"]
