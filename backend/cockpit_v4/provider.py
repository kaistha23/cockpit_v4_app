"""
The provider boundary: one explicit model, native tool calls, real deadlines.

What this module refuses to do
------------------------------
It does not choose a model. `AI_COCKPIT_REASONING_MODEL` names one id, it is
verified against the provider, and if it is missing or will not serve, the run
fails with a configuration error. There is no "latest", no inheriting
`AI_MODEL`, no substituting a cheaper model when the named one is unavailable,
and no second model to fall back to on a refusal.

It also does not extract JSON from prose. A tool call arrives as a complete
tool_use block or it does not arrive; a regex over a half-written sentence is
how a truncated plan becomes an executed query.

Deadlines are enforced OUTSIDE the client. An HTTP read timeout bounds one
socket read, not an end-to-end run, and an SDK that retries internally can
turn a 30-second timeout into three minutes. SDK retries are switched off here
so every actual HTTP attempt passes through the ledger.
"""

from __future__ import annotations

import hashlib
import os
import re
import json
import time
from dataclasses import dataclass, field
from typing import Any

from backend.cockpit_v4.budgets import BudgetExceeded, Ledger
from backend.cockpit_v4.capability import Capability
from backend.cockpit_v4.states import (COST_LIMIT, INPUT_CONTEXT_LIMIT,
                                       INVALID_MODEL_OUTPUT, OUTPUT_LIMIT,
                                       PROVIDER_AUTH, PROVIDER_RATE_LIMIT,
                                       PROVIDER_REQUEST_INVALID,
                                       PROVIDER_UNAVAILABLE,
                                       TOOL_SCHEMA_INVALID)

#: Stop reasons that mean the model finished a turn we can act on.
COMPLETE_STOPS: frozenset[str] = frozenset({"tool_use", "end_turn",
                                            "stop_sequence"})
TRUNCATED_STOP = "max_tokens"
REFUSAL_STOPS: frozenset[str] = frozenset({"refusal"})


class ProviderFailure(RuntimeError):
    """A provider or protocol failure, with the code the run stops under."""

    def __init__(self, code: str, message: str, *,
                 retry_class: str = "none",
                 detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_class = retry_class
        self.detail = dict(detail or {})


class OutputTruncated(ProviderFailure):
    """The response hit its output allowance. INCOMPLETE, not shorter.

    Nothing in the truncated turn executes. Half a tool call is not a tool
    call, and the partial assistant message is rolled out of history so the
    next request is not malformed by an unanswered tool_use.
    """

    def __init__(self, message: str, *, limit: int) -> None:
        super().__init__(OUTPUT_LIMIT, message, retry_class="format")
        self.limit = limit


class InputTooLarge(ProviderFailure):
    def __init__(self, message: str, *, counted: int, capacity: int) -> None:
        super().__init__(INPUT_CONTEXT_LIMIT, message)
        self.counted = counted
        self.capacity = capacity


@dataclass
class ToolCall:
    """One complete tool_use block. Never a fragment."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Turn:
    """One provider round trip, with everything the ledger and trace need."""

    assistant_blocks: list[Any]
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    model: str
    request_id: str
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    counted_input_tokens: int
    count_method: str

    def usage(self) -> dict[str, Any]:
        return {"input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens}


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        name = str(row.get(key) or "")
        out[name] = out.get(name, 0) + 1
    return out


@dataclass
class Analyst:
    """The one model conversation. Owns the history and its tool pairing.

    `messages` is the canonical provider history. Assistant blocks go back
    verbatim and in order -- including any opaque signature block the provider
    requires and nobody may invent -- and every accepted tool_use is answered
    by its matching tool_result in the same order on the next turn.
    """

    provider: Any
    capability: Capability
    ledger: Ledger
    system: list[dict[str, Any]] | str
    tools: list[dict[str, Any]]
    messages: list[dict[str, Any]] = field(default_factory=list)
    #: tool_use ids awaiting their tool_result, in the order they arrived.
    _pending: list[str] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    #: What the last generation asked for and what the budget granted.
    response_allowance: dict[str, Any] = field(default_factory=dict)
    #: Whether an ACTION turn may be required to be a tool call, and whether
    #: effort may be set. Both start from the capability and are turned OFF
    #: for the rest of the run the first time the provider rejects a request
    #: naming one -- so a model that does not accept them costs this run one
    #: repeated attempt, not the run.
    forced_tool_use: bool = True
    effort_control: bool = True
    #: The call-report row for the most recent generation, so what happened
    #: to its result can be written back onto it. See `annotate`.
    _last_entry: dict[str, Any] | None = None

    # -- history ---------------------------------------------------------

    def user(self, text: str) -> None:
        if self._pending:
            raise ProviderFailure(
                INVALID_MODEL_OUTPUT,
                f"{len(self._pending)} tool call(s) are unanswered; a user "
                f"turn cannot be appended before their tool results.")
        # Appended to the turn that is already there when the last message is
        # the reader's side. A run that answers a tool call and then has
        # something more to say -- "there is no time for another action,
        # write the answer" -- is still ONE user turn, and sending two in a
        # row is a malformed conversation on some providers and a silently
        # merged one on others.
        if (self.messages and self.messages[-1]["role"] == "user"
                and isinstance(self.messages[-1].get("content"), list)):
            self.messages[-1]["content"].append(
                {"type": "text", "text": text})
            return
        self.messages.append({"role": "user", "content": text})

    def tool_result(self, tool_use_id: str, payload: Any, *,
                    is_error: bool = False) -> None:
        """Answer one tool_use. Order is checked, not assumed.

        A history with a tool_use that has no matching tool_result is
        malformed on the next request; a history whose results arrive out of
        order is accepted by some providers and silently misattributed. Both
        are refused here.
        """
        if not self._pending:
            raise ProviderFailure(
                INVALID_MODEL_OUTPUT,
                "there is no outstanding tool call to answer.")
        expected = self._pending[0]
        if tool_use_id != expected:
            raise ProviderFailure(
                INVALID_MODEL_OUTPUT,
                f"tool results must follow their calls in order: expected "
                f"{expected}, got {tool_use_id}.")
        self._pending.pop(0)
        body = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False, default=str)
        block = {"type": "tool_result", "tool_use_id": tool_use_id,
                 "content": body}
        if is_error:
            block["is_error"] = True
        if (self.messages and self.messages[-1]["role"] == "user"
                and isinstance(self.messages[-1].get("content"), list)):
            self.messages[-1]["content"].append(block)
        else:
            self.messages.append({"role": "user", "content": [block]})

    @property
    def awaiting_results(self) -> tuple[str, ...]:
        return tuple(self._pending)

    # -- counting --------------------------------------------------------

    #: How long the last `count_input` took, and how long serializing the
    #: request for measurement took. Separate numbers because they are
    #: separate defects: "the action turn took 80 seconds" is not a finding,
    #: and neither counting nor serialization was ever visible enough to be
    #: ruled out.
    _last_count_ms: int = 0
    _last_serialize_ms: int = 0

    def count_input(self) -> tuple[int, str]:
        """Count the WHOLE assembled request against the serving model.

        Includes the system blocks and the tool schemas, because both are
        input the provider bills and the context window holds. A local
        character estimate is labelled an estimate; it is never presented as
        a calibrated truth.
        """
        started = time.monotonic()
        counter = getattr(self.provider, "count_tokens", None)
        if callable(counter) and self.capability.supports_token_counting:
            try:
                self.ledger.spend_provider_attempt()
                value = counter(system=self.system, messages=self.messages,
                                tools=self.tools,
                                model=self.capability.model_id)
                self._last_count_ms = int((time.monotonic() - started) * 1000)
                return int(value), "provider_count_tokens"
            except Exception:  # noqa: BLE001 - fall back, but say so
                pass
        serialize_started = time.monotonic()
        payload = json.dumps(
            {"system": self.system, "messages": self.messages,
             "tools": self.tools}, ensure_ascii=False, default=str)
        self._last_serialize_ms = int(
            (time.monotonic() - serialize_started) * 1000)
        self._last_count_ms = int((time.monotonic() - started) * 1000)
        # Deliberately conservative: over-estimating stops a run early, and
        # under-estimating sends a request the model cannot hold.
        return int(len(payload) / 2.2) + 1, "local_conservative_estimate"

    def fits(self, *, reserved_output: int) -> tuple[bool, int, str]:
        counted, method = self.count_input()
        margin = max(1024, int(counted * 0.02))
        return (counted + reserved_output + margin
                <= self.capability.context_tokens), counted, method

    def payload_bytes(self) -> dict[str, int]:
        """Where the request's size actually sits, in bytes on the wire.

        Tokens are the billed unit but they are counted for the whole
        request, so a token count cannot say whether a call is large because
        of the tool schemas, the standing context or the conversation. Bytes
        can, and the three parts are separately fixable.
        """
        def _size(obj: Any) -> int:
            if isinstance(obj, str):
                return len(obj.encode("utf-8"))
            return len(json.dumps(obj, ensure_ascii=False,
                                  default=str).encode("utf-8"))

        system = _size(self.system)
        tools = _size(self.tools)
        messages = _size(self.messages)
        return {"system": system, "tools": tools, "messages": messages,
                "total": system + tools + messages}

    # -- the call report -------------------------------------------------

    def _record(self, **fields: Any) -> dict[str, Any]:
        """Append one generation ATTEMPT to the report and return it.

        Every attempt, not every success. A call refused before it was sent
        and a call whose socket died both cost the run something -- an
        allowance, a deadline, an operator's afternoon -- and a report that
        lists only the attempts that came back cannot explain where a run
        went.
        """
        entry: dict[str, Any] = {"seq": len(self.turns) + 1}
        entry.update(fields)
        self.turns.append(entry)
        return entry

    def call_report(self) -> dict[str, Any]:
        """Every generation attempt this run made, and what each one cost.

        Separates PROVIDER time from everything else. A run that overran its
        deadline is a different defect depending on which of the two grew,
        and before this the only number available was the total.
        """
        provider_ms = sum(int(t.get("provider_ms") or 0) for t in self.turns)
        return {
            "calls": list(self.turns),
            "generations": len(self.turns),
            "provider_ms": provider_ms,
            "by_purpose": _tally(self.turns, "purpose"),
            "by_phase": _tally(self.turns, "phase"),
        }

    # -- the call --------------------------------------------------------

    #: A 400 that names one of the two request parameters this module added.
    #: Matched on the provider's own words, and never on anything else: a
    #: rejection about a TOOL SCHEMA is a different fault with a different
    #: fix, and retrying it without `tool_choice` would hide it.
    _PARAM_REFUSED = re.compile(
        r"tool_choice|output_config|\beffort\b", re.I)

    def _temperature(self) -> float | None:
        """COCKPIT_V4_TEMPERATURE (default 0), or None when not permitted."""
        if not self.capability.supports_sampling_control:
            return None
        raw = os.environ.get("COCKPIT_V4_TEMPERATURE", "").strip()
        try:
            value = float(raw) if raw else 0.0
        except ValueError:
            value = 0.0
        return min(1.0, max(0.0, value))

    def _converse(self, *, choice: dict[str, Any] | None,
                  config: dict[str, Any] | None, entry: dict[str, Any],
                  max_tokens: int, purpose: str, timeout: float) -> Any:
        """Send the turn; drop a refused request parameter exactly once.

        The parameters are model-gated: a model that does not know
        `tool_choice: {"type": "any"}` answers 400 rather than ignoring it.
        Reaching that 400 should cost this run one immediate re-send without
        the parameter, and every later turn in the run goes without it --
        recorded on the call report, because a run that quietly stopped
        requiring tool calls is a run whose action turns can go back to
        writing essays and nobody would know.
        """
        def send(with_choice, with_config):
            self.ledger.spend_provider_attempt()
            kwargs: dict[str, Any] = {}
            if with_choice:
                kwargs["tool_choice"] = with_choice
            if with_config:
                kwargs["output_config"] = with_config
            # Only where the registry says the model accepts it, and kept
            # out of the refusal-drop on purpose: a model not known to take
            # a temperature must never be sent one to find out.
            temperature = self._temperature()
            if temperature is not None:
                kwargs["temperature"] = temperature
                entry["temperature"] = temperature
            return self.provider.converse(
                system=self.system, messages=self.messages, tools=self.tools,
                max_tokens=max_tokens, model=self.capability.model_id,
                purpose=purpose, role="cockpit_v4_analyst", timeout=timeout,
                # Every HTTP attempt must pass through the ledger, so the
                # SDK is not allowed to retry behind our back.
                allow_retry=False, **kwargs)

        try:
            return send(choice, config)
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is ours
            if not (choice or config):
                raise
            text = str(exc)
            failure = _classify(exc)
            rejected = (failure.code in (PROVIDER_REQUEST_INVALID,
                                         TOOL_SCHEMA_INVALID)
                        or "400" in text)
            if not (rejected and self._PARAM_REFUSED.search(text)):
                raise
            if choice:
                self.forced_tool_use = False
            if config:
                self.effort_control = False
            entry["request_parameters_refused"] = _sanitize(text)[:200]
            entry["tool_choice"] = "auto"
            entry["effort"] = ""
            return send(None, None)

    def ask(self, *, purpose: str, max_output_tokens: int,
            tool_choice: str = "any", phase: str = "action",
            effort: str = "", require: str = "") -> Turn:
        """One generation attempt. Bounded, reserved, counted and settled.

        `tool_choice` is now SENT. It was a parameter this method accepted
        and dropped on the floor: every turn went out unconstrained, so an
        ACTION turn -- whose only legal outcome is choosing the next action
        -- was free to answer in prose, and on an open analytical question
        it did. The turn then took as long as an essay takes, ended at the
        output allowance, and nothing from it ran.

        `require` names ONE tool the turn must call. It is the difference
        between "call something" and "call this": the first still asks the
        model to choose among everything on the request, and choosing is
        what a live run ran out of output doing. What to put IN the call --
        the SQL, the steps, the filters, the method -- is untouched by it.

        `effort` bounds how hard the model works before it answers. An
        action turn is a decision, not an analysis: the analysis happens in
        DuckDB afterwards.

        Both are model-gated. If the provider rejects the request naming one
        of them, it is dropped for the rest of the run and the attempt is
        made again once -- recorded, not silent. Failing a run closed over a
        latency optimisation would be worse than the latency.
        """
        serialize_started = time.monotonic()
        sizes = self.payload_bytes()
        serialize_ms = int((time.monotonic() - serialize_started) * 1000)
        entry = self._record(
            purpose=purpose, phase=phase, attempt=0,
            tools_offered=[str(t.get("name") or "") for t in self.tools],
            context_bytes=sizes, serialize_ms=serialize_ms,
            outcome="refused_before_send")
        # Set before anything can fail, so a truncated or refused attempt is
        # still the row an annotation lands on.
        self._last_entry = entry
        try:
            self.ledger.check_deadline()
            # And refused outright if there is no longer time to use the
            # answer it would produce. An ACTION must leave the finalization
            # reserve intact; the ANSWER call may spend it.
            self.ledger.check_call_window(phase=phase)
        except BudgetExceeded as exc:
            entry["refusal"] = exc.code
            raise
        attempt = self.ledger.spend_generation()
        entry["attempt"] = attempt

        reserved_output = min(max_output_tokens,
                              self.capability.max_output_tokens)
        ok, counted, method = self.fits(reserved_output=reserved_output)
        entry.update(counted_input_tokens=counted, count_method=method,
                     count_ms=self._last_count_ms)
        if not ok:
            entry["refusal"] = INPUT_CONTEXT_LIMIT
            raise InputTooLarge(
                f"the assembled request measured {counted:,} tokens "
                f"({method}) and, with {reserved_output:,} tokens reserved "
                f"for the response, does not fit the "
                f"{self.capability.context_tokens:,}-token capacity of "
                f"{self.capability.model_id}. Nothing was sent.",
                counted=counted, capacity=self.capability.context_tokens)

        # Ask for the answer the budget can actually carry. Reserving the
        # maximum and then refusing the run was how a real analysis stopped
        # at COST_LIMIT with $0.71 committed: a shorter answer was affordable
        # and nobody offered one. The cap SENT to the provider is reduced to
        # match, so the reservation stays a true projection.
        affordable = self.ledger.affordable_output_tokens(
            input_tokens=counted, wanted=reserved_output)
        self.response_allowance = {
            "wanted": reserved_output, "granted": min(reserved_output,
                                                      affordable),
            "reduced": affordable < reserved_output}
        if affordable < reserved_output:
            if affordable < self.ledger.MIN_RESPONSE_TOKENS:
                entry["refusal"] = COST_LIMIT
                raise BudgetExceeded(
                    COST_LIMIT,
                    f"the remaining budget affords {affordable:,} response "
                    f"tokens against a {self.ledger.MIN_RESPONSE_TOKENS:,}-"
                    f"token minimum, so no useful answer could be bought. "
                    f"Nothing was sent.")
            reserved_output = affordable
            ok, counted, method = self.fits(reserved_output=reserved_output)
            entry.update(counted_input_tokens=counted, count_method=method)

        reservation = self.ledger.reserve(
            purpose=purpose, input_tokens=counted,
            output_tokens=reserved_output)

        # The deadline the CLIENT gets is bounded by the time the RUN has
        # left, less a settlement margin, so a stalled socket cannot outlive
        # the run's own watchdog.
        timeout = self.ledger.call_timeout_seconds(phase=phase)
        # WHAT THIS TURN IS ALLOWED TO BE.
        #
        # Three settings, in order of strength. A NAMED tool when the run's
        # state leaves exactly one legal transition: the turn calls that
        # tool and the only question left is what to put in it. ANY tool
        # when more than one is legal but prose is not. And nothing at all
        # on an answer turn, because writing prose is what it is for.
        named = bool(require) and self.forced_tool_use and (
            self.capability.supports_named_tool_forcing)
        forced = (phase != "answer" and self.forced_tool_use
                  and self.capability.supports_forced_tool_use)
        if named:
            choice: dict[str, Any] | None = {"type": "tool", "name": require}
        elif forced:
            choice = {"type": "any"}
        else:
            choice = None
        if choice is not None and self.capability.supports_single_tool_per_turn:
            # One action per turn. A batch of parallel calls against a
            # single-transition state is a batch the state machine would
            # have to reject anyway.
            choice["disable_parallel_tool_use"] = True
        config = ({"effort": effort}
                  if effort and self.effort_control
                  and self.capability.supports_effort_control else None)
        entry.update(output_allowance=dict(self.response_allowance),
                     call_timeout_seconds=round(timeout, 3),
                     tool_choice=(dict(choice) if choice else "auto"),
                     required_tool=require if named else "",
                     effort=(config or {}).get("effort", ""),
                     outcome="sent")
        started = time.monotonic()
        try:
            self.ledger.spend_provider_attempt()
            result = self._converse(
                choice=choice, config=config, entry=entry,
                max_tokens=reserved_output, purpose=purpose, timeout=timeout)
        except BudgetExceeded:
            # OUR accounting refused the attempt, so nothing was sent and
            # nothing can have been billed. Settled at zero, and the budget
            # code is passed through: a run that used its attempt allowance
            # stopped on CALL_LIMIT, and reporting that as an outage sends an
            # operator looking for a provider that is working perfectly.
            self.ledger.settle(reservation, usage={}, uncertain=False)
            entry.update(outcome="refused_before_send",
                         provider_ms=int((time.monotonic() - started) * 1000))
            raise
        except Exception as exc:  # noqa: BLE001
            # The request may have reached the provider and been billed.
            # Held pending, never booked as zero.
            self.ledger.settle(reservation, usage={"error": str(exc)[:200]},
                               uncertain=True)
            # A provider failure that arrives as an EXCEPTION still has a
            # name. Calling every one of them a transport error is how a
            # truncated response -- which was served, billed and complete as
            # far as the socket was concerned -- reads in the report as a
            # network fault nobody can reproduce.
            cut_off = isinstance(exc, OutputTruncated)
            entry.update(
                outcome=("truncated" if cut_off
                         else exc.code if isinstance(exc, ProviderFailure)
                         else "transport_error"),
                truncated=cut_off,
                parse_status="incomplete" if cut_off else "not_returned",
                tool_names=[], tool_calls=0, usable=False,
                provider_ms=int((time.monotonic() - started) * 1000),
                error=str(exc)[:200])
            raise _classify(exc) from exc

        self.ledger.settle(reservation, usage={
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
            "cache_read_tokens": int(
                getattr(result, "cache_read_tokens", 0) or 0),
            "cache_write_tokens": int(
                getattr(result, "cache_write_tokens", 0) or 0)})

        stop_reason = str(getattr(result, "stop_reason", "") or "")
        blocks = list(getattr(result, "assistant_blocks", []) or [])

        turn = Turn(
            assistant_blocks=blocks, text=str(getattr(result, "text", "") or ""),
            tool_calls=[], stop_reason=stop_reason,
            model=str(getattr(result, "model", "") or
                      self.capability.model_id),
            request_id=str(getattr(result, "request_id", "") or ""),
            duration_ms=int((time.monotonic() - started) * 1000),
            input_tokens=int(getattr(result, "input_tokens", 0) or 0),
            output_tokens=int(getattr(result, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(result, "cache_read_tokens", 0) or 0),
            cache_write_tokens=int(getattr(result, "cache_write_tokens", 0)
                                   or 0),
            counted_input_tokens=counted, count_method=method)
        entry.update(
            outcome=("truncated" if stop_reason == TRUNCATED_STOP
                     else "ok" if stop_reason in COMPLETE_STOPS
                     else stop_reason or "unknown_stop"),
            stop_reason=stop_reason, request_id=turn.request_id,
            reported_input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            cache_read_tokens=turn.cache_read_tokens,
            cache_write_tokens=turn.cache_write_tokens,
            provider_ms=turn.duration_ms)

        if stop_reason == TRUNCATED_STOP:
            # The partial turn does NOT enter history. Keeping it would leave
            # an unanswered tool_use and make the next request malformed.
            entry.update(truncated=True, parse_status="incomplete",
                         tool_calls=0, tool_names=[], usable=False)
            raise OutputTruncated(
                f"the response reached its {reserved_output:,}-token output "
                f"allowance, so the {purpose} is incomplete. A truncated "
                f"tool call is not a smaller one, and nothing from it ran.",
                limit=reserved_output)
        if stop_reason in REFUSAL_STOPS:
            raise ProviderFailure(
                PROVIDER_UNAVAILABLE,
                "the model declined to answer this request. No other model "
                "was tried.", detail={"stop_reason": stop_reason})
        if stop_reason and stop_reason not in COMPLETE_STOPS:
            # An unknown finish reason is a defined failure, not a reason to
            # keep going and hope.
            raise ProviderFailure(
                INVALID_MODEL_OUTPUT,
                f"the provider returned an unrecognised stop reason "
                f"{stop_reason!r}. The turn was not treated as complete.",
                detail={"stop_reason": stop_reason})

        # Only now, with a complete turn, does history advance.
        if blocks:
            self.messages.append({"role": "assistant", "content": blocks})
        for raw in list(getattr(result, "tool_calls", []) or []):
            arguments = raw.get("input")
            if not isinstance(arguments, dict):
                raise ProviderFailure(
                    INVALID_MODEL_OUTPUT,
                    f"tool call {raw.get('name')!r} arrived without a "
                    f"complete argument object.")
            turn.tool_calls.append(ToolCall(
                id=str(raw.get("id") or ""), name=str(raw.get("name") or ""),
                arguments=dict(arguments)))
        self._pending = [c.id for c in turn.tool_calls]
        # §13. What actually came back, per attempt: which tools it called,
        # whether the arguments parsed, and whether it was cut off. The
        # SCHEMA validation happens downstream and is recorded by the caller
        # through `annotate`, so one row of the call report answers "what
        # did this generation produce and what happened to it".
        entry.update(
            truncated=False,
            tool_names=[c.name for c in turn.tool_calls],
            tool_calls=len(turn.tool_calls),
            parse_status=("tool_call" if turn.tool_calls
                          else "no_tool_call"),
            response_text_chars=len(turn.text or ""))
        return turn

    def annotate(self, **fields: Any) -> None:
        """Record what happened to the LAST generation, after it returned.

        Schema validation, execution acceptance and the orchestration
        transition all happen after `ask` has handed the turn back. Without
        this the call report could say a tool call arrived and never say
        whether it was usable, which is exactly the gap that made the live
        second attempt impossible to classify from the trace alone.
        """
        if self._last_entry is not None:
            self._last_entry.update(fields)

    def rollback_last_turn(self) -> None:
        """Drop an assistant turn that must not be replayed."""
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages.pop()
        self._pending.clear()


#: `tools.0.custom.input_schema` and friends: which tool the provider refused.
_SCHEMA_PATH = re.compile(r"tools\.(\d+)\.[\w.]*input_schema[\w.]*")
#: The keywords the provider names when it rejects a tool schema.
_UNSUPPORTED_KEYWORD = re.compile(
    r"does not support ([^.;]+)", re.I)
#: Anything that looks like a key. Never echoed into a message.
_SECRETISH = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+)")


def _sanitize(text: str) -> str:
    return _SECRETISH.sub("[redacted]", text)


def _classify(exc: Exception) -> ProviderFailure:
    """Map a transport exception to the code an operator can act on.

    The distinction that matters most here is between "the provider could not
    be reached" and "the provider answered, and what it said was that our
    request was malformed". A live UAT showed PROVIDER_UNAVAILABLE for an
    HTTP 400 naming an unsupported tool-schema keyword — so an operator went
    looking for an outage while the actual fault was in the schema
    CreditProbe published, and nothing in the trace said the request had been
    rejected BEFORE any inference.

    A failure that ALREADY carries a code is returned untouched. Re-reading
    its message would throw away a classification made by the layer that had
    the provider's own response in hand, and re-derive a worse one from the
    string: that is exactly how a rejected credential or a rate limit ends up
    reported as an outage.
    """
    if isinstance(exc, ProviderFailure):
        return exc
    text = str(exc)
    lowered = text.lower()
    name = type(exc).__name__.lower()

    is_400 = ("400" in text or "invalid_request_error" in lowered
              or "badrequest" in name)
    if is_400:
        schema_path = _SCHEMA_PATH.search(text)
        keyword = _UNSUPPORTED_KEYWORD.search(text)
        detail = {
            "status_code": 400,
            "provider_error_type": "invalid_request_error",
            "rejected_before_inference": True,
            "provider_message": _sanitize(text)[:400],
        }
        if schema_path:
            detail["failing_schema_path"] = schema_path.group(0)
            detail["failing_tool_index"] = int(schema_path.group(1))
        if keyword:
            detail["unsupported_keywords"] = [
                k.strip() for k in keyword.group(1).split(",") if k.strip()]
        if schema_path or "input_schema" in lowered or "tools." in lowered:
            return ProviderFailure(
                TOOL_SCHEMA_INVALID,
                "CreditProbe sent a tool definition this provider does not "
                "accept, so the request was rejected before the model saw "
                "it. Nothing was inferred, nothing was billed for inference, "
                "and no other model was tried.",
                retry_class="none", detail=detail)
        return ProviderFailure(
            PROVIDER_REQUEST_INVALID,
            "The provider rejected this request as malformed before running "
            "it. This is a fault in what CreditProbe sent, not an outage.",
            retry_class="none", detail=detail)

    if "authentication" in lowered or "401" in text or "invalid api key" in lowered:
        return ProviderFailure(
            PROVIDER_AUTH,
            "the provider rejected this deployment's Cockpit credential. No "
            "other credential was tried.", retry_class="none",
            detail={"status_code": 401,
                    "rejected_before_inference": True})
    if "permission" in lowered or "403" in text:
        return ProviderFailure(
            PROVIDER_AUTH,
            "the provider refused this deployment's Cockpit credential for "
            "the configured model.", retry_class="none")
    if "rate" in lowered and "limit" in lowered or "429" in text:
        return ProviderFailure(
            PROVIDER_RATE_LIMIT,
            "the provider rate-limited this request.",
            retry_class="transport",
            detail={"status_code": 429,
                    "rejected_before_inference": True})
    # A call that ran out of time is a TRANSPORT failure, and the run may
    # try once more. It was matching "timed out" against the exception's
    # CLASS NAME -- where the phrase has no space, so `TimeoutError` never
    # matched -- and the words in the message were never checked at all. A
    # stalled action therefore failed the whole run instead of being asked
    # again, which is exactly the outcome the per-action window exists to
    # prevent.
    timed_out = ("timeout" in lowered or "timed out" in lowered
                 or "timeout" in name or "deadline" in lowered)
    if (timed_out or "connection" in lowered or "503" in text
            or "502" in text or "overloaded" in lowered):
        return ProviderFailure(
            PROVIDER_UNAVAILABLE,
            f"the provider request did not complete: {text[:200]}",
            retry_class="transport",
            detail={"timed_out": timed_out})
    return ProviderFailure(
        PROVIDER_UNAVAILABLE,
        f"the provider request failed: {_sanitize(text)[:200]}",
        retry_class="none",
        detail={"provider_exception_type": type(exc).__name__,
                "rejected_before_inference": False})


def code_digest(code: str) -> str:
    """The digest that proves what executed equals what was authored."""
    return "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()


__all__ = ["Analyst", "InputTooLarge", "OutputTruncated", "ProviderFailure",
           "ToolCall", "Turn", "code_digest"]
