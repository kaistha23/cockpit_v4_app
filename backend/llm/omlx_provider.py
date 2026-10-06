"""OpenAI-compatible oMLX adapter for the provider-neutral LLM boundary."""
from __future__ import annotations

import ast
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import uuid
from dataclasses import dataclass, field
from typing import Any

try:  # requests is pinned in production; stdlib keeps lightweight test tools usable.
    import requests
except ModuleNotFoundError:  # pragma: no cover - depends on the runtime image
    requests = None  # type: ignore[assignment]

from backend.llm import telemetry
from backend.llm.base import ConverseResult, LLMError, LLMResult, ProviderStatus, register

logger = logging.getLogger(__name__)


def _is_timeout(exc: BaseException | None) -> bool:
    """True when `exc`, or anything it was raised from, is a timeout.

    `requests` wraps urllib3/socket timeouts (ReadTimeout, ConnectTimeout),
    the stdlib path surfaces socket.timeout / TimeoutError or a URLError whose
    `reason` is one, and either may arrive wrapped, so the cause/context chain
    is walked and the message is checked as a last resort. `requests` is
    optional in this module, hence the guarded attribute lookup.
    """
    timeout_types: tuple[type, ...] = (socket.timeout, TimeoutError)
    if requests is not None:
        timeout_types += (requests.exceptions.Timeout,)
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, timeout_types):
            return True
        if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, timeout_types):
            return True
        if not isinstance(exc, LLMError) and "timed out" in str(exc).lower():
            return True
        exc = exc.__cause__ or exc.__context__
    return False


#: Below this much remaining window a transport retry cannot plausibly finish.
_MIN_RETRY_WINDOW = 1.0
#: Below this much remaining window a schema re-ask is not worth starting.
_MIN_REASK_WINDOW = 5.0


_RECORD_LOCK = threading.Lock()


def _record(payload: dict[str, Any], response: Any) -> None:
    """Append one exchange to OMLX_RECORD_DIR, for offline replay.

    Opt-in. Stores the request body and the raw response text -- never the
    key, which is only ever in the headers. The files hold prompts and
    results from the local book, so they stay on this machine.
    """
    folder = os.environ.get("OMLX_RECORD_DIR", "").strip()
    if not folder:
        return
    try:
        os.makedirs(folder, exist_ok=True)
        line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "request": payload,
                           "status": getattr(response, "status_code", 0),
                           "response": getattr(response, "text", "")},
                          ensure_ascii=False)
        with _RECORD_LOCK, open(os.path.join(folder, f"omlx_{time.strftime('%Y%m%d')}.jsonl"),
                                "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        logger.warning("oMLX recording to %s failed; the call itself is unaffected", folder)


#: Concurrent requests allowed per oMLX server. A single local inference
#: server degrades sharply under parallel load, so the default is small;
#: raise it with OMLX_MAX_CONCURRENCY once measured on the actual hardware.
_SLOTS: dict[str, threading.BoundedSemaphore] = {}
_SLOTS_LOCK = threading.Lock()


def _slots_for(base_url: str) -> threading.BoundedSemaphore:
    with _SLOTS_LOCK:
        if base_url not in _SLOTS:
            try:
                size = max(1, int(os.environ.get("OMLX_MAX_CONCURRENCY", "2")))
            except ValueError:
                size = 2
            _SLOTS[base_url] = threading.BoundedSemaphore(size)
        return _SLOTS[base_url]


@dataclass
class OMLXProvider:
    api_key: str = field(repr=False, default="")
    model: str = ""
    base_url: str = "http://localhost:9906/v1"
    name: str = "omlx"
    timeout: float = 60.0
    session: Any = field(default=None, repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def status(self) -> ProviderStatus:
        observed = telemetry.health(provider=self.name, model=self.model, configured=self.configured)
        return ProviderStatus(self.name, self.model, self.configured, observed["state"], observed["detail"], observed)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _get(self, url: str, *, timeout: float) -> Any:
        if self.session is not None:
            return self.session.get(url, headers=self._headers(), timeout=timeout)
        return _stdlib_request("GET", url, self._headers(), None, timeout)

    def _post(self, url: str, payload: dict[str, Any], *, timeout: float) -> Any:
        # One local server serves every run, so concurrent requests queue here
        # rather than all hitting it at once. Time spent waiting for a slot is
        # taken out of the caller's own deadline: queueing can never make a
        # call outlive the budget the run reserved for it.
        slots = _slots_for(self.base_url)
        waited = time.monotonic()
        if not slots.acquire(timeout=max(timeout, 0.001)):
            raise LLMError("oMLX is busy: no request slot became free within the call deadline.")
        try:
            remaining = timeout - (time.monotonic() - waited)
            if remaining <= 0:
                raise LLMError("oMLX call deadline was spent waiting for a request slot.")
            if self.session is not None:
                response = self.session.post(url, json=payload, headers=self._headers(), timeout=remaining)
            else:
                response = _stdlib_request("POST", url, self._headers(), payload, remaining)
            _record(payload, response)
            return response
        finally:
            slots.release()

    def list_models(self, timeout: float = 3.0) -> list[str]:
        try:
            response = self._get(f"{self.base_url.rstrip('/')}/models", timeout=timeout)
            if not response.ok:
                raise LLMError(f"oMLX HTTP {response.status_code}")
            ids = [str(item.get("id") or "") for item in response.json().get("data", [])]
            tails = {item.split("--", 1)[1] for item in ids if "--" in item}
            deduped = [item for item in ids if item and ("--" in item or item not in tails)]
            # Embedding/rerank models answer /v1/models but cannot serve chat
            # completions; offering them in the selector produces a confusing
            # runtime failure rather than a clear "not a chat model" message.
            # Restored if filtering would otherwise empty the list, since a
            # false-positive name match is better than hiding every model.
            chat_only = [item for item in deduped
                        if "embed" not in item.lower() and "rerank" not in item.lower()]
            return chat_only or deduped
        except Exception as exc:
            raise LLMError("oMLX model discovery failed") from exc

    def structured(self, *, system: str, prompt: str, schema: dict[str, Any], tool_name: str,
                   tool_description: str, max_tokens: int = 2000, purpose: str = "reading",
                   model: str = "", role: str = "", effort: str = "", **_: Any) -> LLMResult:
        result = self.converse(system=system, messages=[{"role": "user", "content": prompt}],
            tools=[{"name": tool_name, "description": tool_description, "input_schema": schema}],
            tool_choice={"type": "tool", "name": tool_name}, max_tokens=max_tokens,
            model=model, purpose=purpose, role=role, effort=effort)
        call = next((item for item in result.tool_calls if item["name"] == tool_name), None)
        if not call or not isinstance(call["input"], dict):
            raise LLMError("The orchestrator answered in prose rather than calling the required tool.")
        return LLMResult(call["input"], result.model, result.duration_ms, result.input_tokens,
                         result.output_tokens, result.cache_read_tokens, result.cache_write_tokens,
                         result.attempts, result.request_id)

    def converse(self, *, system: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                 max_tokens: int = 4096, model: str = "", purpose: str = "conversation", role: str = "",
                 effort: str = "", timeout: float = 0.0, allow_retry: bool = True,
                 tool_choice: dict[str, Any] | None = None, output_config: dict[str, Any] | None = None,
                 temperature: float | None = None) -> ConverseResult:
        chosen = (model or self.model).strip()
        if not chosen:
            raise LLMError("No oMLX model is selected.")
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": chosen, "messages": _messages(_with_guidance(system, tools), messages),
            "max_tokens": max_tokens,
            # Deterministic decoding for structured tool calls, as slm_v1
            # uses for every plan/intent call.
            "temperature": 0,
            # Qwen-family templates often emit hidden reasoning by default.
            # It consumes output allowance but must never reach a reader.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema", {})}} for t in tools]
        if tool_choice:
            kind = tool_choice.get("type")
            payload["tool_choice"] = ({"type": "function", "function": {"name": tool_choice["name"]}} if kind == "tool" else "required" if kind == "any" else "auto")
        try:
            response = self._post(f"{self.base_url.rstrip('/')}/chat/completions", payload, timeout=timeout or self.timeout)
            if not response.ok:
                raise LLMError(f"oMLX HTTP {response.status_code}")
            body = response.json(); choice = (body.get("choices") or [{}])[0]; message = choice.get("message") or {}
            calls, blocks = [], []
            text = str(message.get("content") or "")
            if text: blocks.append({"type": "text", "text": text})
            seen: set[str] = set()
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}; raw = fn.get("arguments", "{}")
                try: args = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError as exc: raise LLMError("oMLX returned invalid tool arguments.") from exc
                if not isinstance(args, dict): raise LLMError("oMLX returned non-object tool arguments.")
                declared = next((t for t in tools or [] if t.get("name") == str(fn.get("name") or "")), None)
                before = _shape(args)
                if declared: args = _coerce(args, declared.get("input_schema"))
                _debug_args(str(fn.get("name") or ""), before, _shape(args))
                call_id = str(call.get("id") or "") or f"toolu_{uuid.uuid4().hex}"
                if call_id in seen: call_id = f"toolu_{uuid.uuid4().hex}"
                seen.add(call_id); name = str(fn.get("name") or "")
                calls.append({"id": call_id, "name": name, "input": args})
                blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": args})
            finish = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}.get(choice.get("finish_reason"))
            if not finish: raise LLMError(f"oMLX returned an unknown completion state ({choice.get('finish_reason')!r}).")
            # Some oMLX builds report "stop" for a turn that DID call a tool.
            # A turn carrying tool calls is a tool_use turn, which is what the
            # Analyst and the repair checks below key on.
            if calls and finish == "end_turn":
                finish = "tool_use"
            usage = body.get("usage") or {}; elapsed = int((time.perf_counter()-started)*1000)
            # `disable_parallel_tool_use` is how the Analyst says a turn has
            # exactly one legal transition (provider.py's single-tool-per-turn
            # trait). oMLX has no request-side equivalent, so a model that
            # ignores the single-tool intent and returns several calls is
            # rejected rather than silently narrowed to the first one -- a
            # batch against a single-transition state is not a smaller batch,
            # it is evidence the model did not honour the constraint.
            if (tool_choice or {}).get("disable_parallel_tool_use") and len(calls) > 1:
                raise LLMError(
                    "oMLX returned more than one tool call for a turn that "
                    "allows exactly one; none of them were treated as valid.")
            # Some local models honour tool forcing on the first turn but
            # answer prose after a tool result. A strict schema gives the
            # state machine the same fail-closed structured boundary without
            # changing its Anthropic-shaped history, for BOTH forcing shapes:
            # a named tool (`{"type":"tool"}`) and "any legal tool"
            # (`{"type":"any"}`), which native "required" mode does not
            # reliably enforce once history includes a prior tool result.
            # A declared object/array argument that is STILL a string after
            # decoding (e.g. malformed JSON that cannot be repaired) is asked
            # again once through strict schema output, which guarantees
            # well-formed JSON, rather than burning one of the run's own
            # execution submissions on a call the validator will reject.
            # Forcing ignored: the model called a tool it was not offered, or
            # a different tool from the one the turn names. Native forcing is
            # not reliably honoured once history holds earlier calls (seen
            # live: execute_analysis repeated in the answer phase where only
            # read_artifact/finalize_response were offered). Re-asked once for
            # the tool the turn actually requires; never passed on.
            kind = (tool_choice or {}).get("type")
            required = str((tool_choice or {}).get("name") or "")
            offered = {str(t.get("name") or "") for t in tools or []}
            reasked = False
            if calls and kind in ("tool", "any") and any(
                    c["name"] not in offered or (kind == "tool" and c["name"] != required)
                    for c in calls):
                if _debug_on():
                    logger.warning("oMLX ignored forcing: called %s, offered %s, required %s; re-asking",
                                   [c["name"] for c in calls], sorted(offered), required or "any")
                calls, blocks, text, finish, usage = self._reask_or_keep(
                    (calls, blocks, text, finish, usage), kind=kind, required=required,
                    tools=tools or [], payload=payload, timeout=timeout, started=started)
                reasked = True
            if not reasked and len(calls) == 1 and finish == "tool_use":
                declared = next((t for t in tools or [] if t.get("name") == calls[0]["name"]), None)
                bad = _unfixed_path(calls[0]["input"], declared.get("input_schema"), calls[0]["name"]) if declared else ""
                if bad and _debug_on():
                    logger.warning("oMLX shape still wrong at %s after repair; re-asking", bad)
                if bad:
                    calls, blocks, text, finish, usage = self._reask_or_keep(
                        (calls, blocks, text, finish, usage), kind="tool",
                        required=calls[0]["name"], tools=tools or [],
                        payload=payload, timeout=timeout, started=started)
            if kind in ("tool", "any") and not calls and finish == "end_turn":
                calls, blocks, text, finish, usage = self._reask_or_keep(
                    (calls, blocks, text, finish, usage), kind=kind,
                    required=str(tool_choice.get("name") or ""),
                    tools=tools or [], payload=payload, timeout=timeout, started=started)
            telemetry.record_success(provider=self.name, model=chosen, purpose=purpose, role=role, effort=effort, latency_ms=elapsed,
                request_id=str(body.get("id") or ""), attempts=1, input_tokens=int(usage.get("prompt_tokens") or 0), output_tokens=int(usage.get("completion_tokens") or 0))
            return ConverseResult(blocks, text, calls, finish, chosen, elapsed, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0), request_id=str(body.get("id") or ""))
        except Exception as exc:  # transport libraries use several exception bases
            window = timeout or self.timeout
            timed_out = not isinstance(exc, LLMError) and _is_timeout(exc)
            remaining = window - (time.perf_counter() - started)
            # A timeout is NEVER retried here. The caller's window comes from
            # the V4 ledger; retrying with the same full timeout turned a 30 s
            # action window into 60 s (seen in live data), and V4's own
            # ledger-bounded transport retry is the layer that may ask again.
            # Only a non-timeout transport fault (dropped connection, broken
            # chunked stream) earns the one internal retry, and it gets only
            # the window that is LEFT, so the pair together never outlive it.
            if allow_retry and not timed_out and not isinstance(exc, LLMError) and remaining >= _MIN_RETRY_WINDOW:
                # One visible, bounded retry for a dropped local HTTP stream.
                # The caller's provider-attempt budget still bounds the turn.
                return self.converse(system=system, messages=messages, tools=tools,
                    max_tokens=max_tokens, model=model, purpose=purpose, role=role,
                    effort=effort, timeout=remaining, allow_retry=False,
                    tool_choice=tool_choice, output_config=output_config)
            telemetry.record_failure(provider=self.name, model=chosen, purpose=purpose, role=role, effort=effort, latency_ms=int((time.perf_counter()-started)*1000), error=exc, attempts=1)
            if isinstance(exc, LLMError):
                raise exc
            # The wording matters: V4's _classify marks a failure as a
            # retryable transport fault only when the text says "timed out" /
            # "connection" etc., so a bare "oMLX request failed" was never
            # retried by the ledger-bounded path.
            if timed_out:
                raise LLMError(f"oMLX request timed out after {window:.0f}s") from exc
            raise LLMError(f"oMLX request failed ({type(exc).__name__})") from exc

    def _reask_or_keep(self, original: tuple, *, kind: str, required: str,
                       tools: list[dict[str, Any]], payload: dict[str, Any],
                       timeout: float, started: float) -> tuple:
        """One best-effort schema re-ask; on any failure, the model's original turn.

        Measured live: a strict-schema re-ask of a large tool (execute_analysis)
        averages ~130 s and is often cut off. Raising there failed the WHOLE
        run; returning the original turn lets V4 reject that one call and the
        run continue. The re-ask is therefore capped (OMLX_REASK_TIMEOUT,
        default 45 s) and never fatal.
        """
        calls, blocks, text, finish, usage = original
        try:
            cap = float(os.environ.get("OMLX_REASK_TIMEOUT", "45"))
        except ValueError:
            cap = 45.0
        # The re-ask runs on top of the time the call already spent, so it is
        # bounded by what is LEFT of the caller's window, not by a fresh one;
        # otherwise a call could outlive its ledger window by up to `cap`.
        remaining = (timeout or self.timeout) - (time.perf_counter() - started)
        if remaining < _MIN_REASK_WINDOW:
            if _debug_on():
                logger.warning("oMLX re-ask skipped: %.1fs of the call window left; keeping the original turn", remaining)
            return original
        try:
            return self._force_tool_by_schema(
                kind=kind, required=required, tools=tools, payload=payload,
                usage=usage, timeout=min(cap, remaining))
        except LLMError as exc:
            if _debug_on():
                logger.warning("oMLX re-ask did not help (%s); keeping the original turn", exc)
            return original

    def _force_tool_by_schema(self, *, kind: str, required: str, tools: list[dict[str, Any]],
                              payload: dict[str, Any], usage: dict[str, Any],
                              timeout: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str, dict[str, Any]]:
        """Re-ask with `response_format` json_schema after prose defeated forcing.

        A NAMED turn (`kind == "tool"`) enforces one already-known tool's own
        schema. An ANY turn (`kind == "any"`) does not know which of several
        legal tools the model would have picked, so with more than one tool
        on offer it wraps them in a `oneOf` of `{tool, input}` shapes and
        reads back which one was chosen; with exactly one tool offered it is
        the same as a named call. This wrapper shape is unverified against
        real oMLX schema-validation limits outside the qualification spike --
        re-check it there before relying on it for the "any", multi-tool case.
        """
        if kind == "tool":
            selected = next((tool for tool in tools if tool.get("name") == required), None)
            if selected is None:
                raise LLMError("The required oMLX tool is not declared.")
            schema = {"name": required, "strict": True, "schema": selected.get("input_schema", {})}
            wrapped = False
        elif len(tools) == 1:
            required = str(tools[0].get("name") or "")
            schema = {"name": required, "strict": True, "schema": tools[0].get("input_schema", {})}
            wrapped = False
        elif tools:
            schema = {"name": "forced_tool_choice", "strict": True, "schema": {
                "type": "object", "oneOf": [
                    {"type": "object", "additionalProperties": False,
                     "properties": {"tool": {"const": tool.get("name")},
                                    "input": tool.get("input_schema", {})},
                     "required": ["tool", "input"]}
                    for tool in tools]}}
            wrapped = True
        else:
            raise LLMError("No tool is declared for a turn that requires one.")

        schema_payload = {key: value for key, value in payload.items() if key not in {"tools", "tool_choice"}}
        schema_payload["response_format"] = {"type": "json_schema", "json_schema": schema}
        retry = self._post(f"{self.base_url.rstrip('/')}/chat/completions", schema_payload, timeout=timeout or self.timeout)
        if not retry.ok:
            raise LLMError(f"oMLX HTTP {retry.status_code} while enforcing a required tool call")
        retry_body = retry.json(); retry_choice = (retry_body.get("choices") or [{}])[0]
        if retry_choice.get("finish_reason") == "length":
            raise LLMError("oMLX truncated a required tool response.")
        raw = (retry_choice.get("message") or {}).get("content") or ""
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LLMError("oMLX did not return valid JSON for the required tool.") from exc
        if wrapped:
            if not isinstance(parsed, dict) or "tool" not in parsed or "input" not in parsed:
                raise LLMError("oMLX did not return a tool selection for the required turn.")
            name = str(parsed.get("tool") or "")
            arguments = parsed.get("input")
            if name not in {str(tool.get("name") or "") for tool in tools}:
                raise LLMError(f"oMLX selected an undeclared tool {name!r}.")
        else:
            name, arguments = required, parsed
        if not isinstance(arguments, dict):
            raise LLMError("oMLX returned non-object JSON for the required tool.")
        chosen_tool = next((t for t in tools if t.get("name") == name), None)
        if chosen_tool:
            before = _shape(arguments)
            arguments = _coerce(arguments, chosen_tool.get("input_schema"))
            _debug_args(f"{name} (re-ask)", before, _shape(arguments))
            # oMLX may not enforce `response_format`, so the re-ask is not a
            # guarantee. A shape still wrong after it is a provider failure
            # named by field, never passed on for the validator to spend a
            # submission on, and never re-asked a second time.
            bad = _unfixed_path(arguments, chosen_tool.get("input_schema"), name)
            if bad and _debug_on():
                logger.warning("oMLX re-ask still wrong at %s", bad)
            if bad:
                raise LLMError(f"oMLX returned {bad} as a string where the tool declares an object or array, even after a re-ask.")
        call_id = f"toolu_{uuid.uuid4().hex}"
        calls = [{"id": call_id, "name": name, "input": arguments}]
        blocks = [{"type": "tool_use", "id": call_id, "name": name, "input": arguments}]
        retry_usage = retry_body.get("usage") or {}
        merged_usage = {"prompt_tokens": int(usage.get("prompt_tokens") or 0) + int(retry_usage.get("prompt_tokens") or 0),
                       "completion_tokens": int(usage.get("completion_tokens") or 0) + int(retry_usage.get("completion_tokens") or 0)}
        return calls, blocks, "", "tool_use", merged_usage


#: Rules the V4 execution contract enforces but its tool description leaves
#: implicit. Claude infers them; local models observed in live runs did not
#: (cross-step table references, invented columns, undefined aliases), and
#: each miss spends one of the run's execution submissions. Added for oMLX
#: only, so Claude's prompts and the V4 contract are unchanged.
_EXECUTION_GUIDANCE = (
    "Rules for execute_analysis that the server enforces:\n"
    "1. Read only the relations the catalogue lists for this book, and only "
    "the columns it lists for them. If you are not sure a column exists, call "
    "inspect_catalog first instead of guessing a name.\n"
    "2. Every table alias you use must be defined in the same query's FROM or "
    "JOIN clause.\n"
    "3. Each SQL step is self-contained: never read another step's output; use "
    "a WITH clause inside the step instead.")


def _with_guidance(system: Any, tools: list[dict[str, Any]] | None) -> Any:
    if not any(t.get("name") == "execute_analysis" for t in tools or []):
        return system
    if isinstance(system, list):
        return [*system, {"type": "text", "text": _EXECUTION_GUIDANCE}]
    return f"{system}\n\n{_EXECUTION_GUIDANCE}" if system else _EXECUTION_GUIDANCE


def _close_open_brackets(text: str) -> str | None:
    """`text` plus exactly the closing brackets it is missing, or None.

    Only for a string that is well formed up to its end and merely stops
    short of closing: no open string literal, no mismatched closer. The
    appended characters are structural; no value is added or changed.
    """
    stack: list[str] = []
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped: escaped = False
            elif ch == "\\": escaped = True
            elif ch == '"': in_string = False
        elif ch == '"': in_string = True
        elif ch in "{[": stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack.pop() != ch: return None
    if in_string or not stack: return None
    return text + "".join(reversed(stack))


def _unfixed_path(value: Any, schema: Any, path: str = "") -> str:
    """The first argument path still a string where an object/array is declared, or ""."""
    if not isinstance(schema, dict):
        return ""
    types = _schema_types(schema)
    if isinstance(value, str):
        return path if (types & {"object", "array"}) and "string" not in types else ""
    if isinstance(value, dict) and "array" in types and "object" not in types:
        return path
    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        for key, item in value.items():
            found = _unfixed_path(item, schema["properties"].get(key), f"{path}.{key}" if path else key)
            if found:
                return found
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            found = _unfixed_path(item, schema["items"], f"{path}[{index}]")
            if found:
                return found
    return ""


def _unfixed(value: Any, schema: Any) -> bool:
    """True when a declared object/array argument is still a plain string."""
    return bool(_unfixed_path(value, schema, "args"))


def _shape(args: Any) -> dict[str, str]:
    """Argument names and value TYPES only -- never values, which may hold data."""
    return {str(key): type(item).__name__ for key, item in args.items()} if isinstance(args, dict) else {}


def _debug_on() -> bool:
    return os.environ.get("OMLX_DEBUG_ARGS", "").strip() in {"1", "true", "yes"}


def _debug_args(tool: str, before: dict[str, str], after: dict[str, str]) -> None:
    # WARNING, not INFO: the app's backend loggers are not configured to show
    # INFO, and this output is opt-in diagnostics that must actually appear.
    if _debug_on():
        logger.warning("oMLX tool args %s: sent=%s after_repair=%s", tool, before, after)


def _schema_types(schema: Any) -> set[str]:
    kind = schema.get("type") if isinstance(schema, dict) else None
    return set(kind) if isinstance(kind, list) else ({kind} if isinstance(kind, str) else set())


def _coerce(value: Any, schema: Any) -> Any:
    """Undo JSON double-encoding of nested arguments, guided by the tool schema.

    Local tool-call parsers often return a nested object or array as a JSON
    STRING (`"steps": "[{...}]"`). Where the declared schema wants an object
    or array and the string decodes to exactly that, the decoded value is
    what the model meant; nothing is invented. A string that does not decode
    to the declared type is left untouched, so the caller's own validation
    still rejects it rather than this layer guessing.
    """
    if not isinstance(schema, dict):
        return value
    types = _schema_types(schema)
    if isinstance(value, str) and types & {"object", "array", "null"}:
        text = value.strip()
        if text == "null" and "null" in types:
            return None
        if text and text[:1] in "{[":
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = _decode_lenient(text)
                if decoded is None:
                    return value
            if (isinstance(decoded, dict) and "object" in types) or (
                    isinstance(decoded, list) and "array" in types):
                return _coerce(decoded, schema)
            return value
        # A bare string where a list of strings is declared is that one item.
        # Not applied to JSON-looking text (handled above), to an empty string
        # (which would invent an item), or to lists of objects.
        if "array" in types and "string" not in types and text and \
                "string" in _schema_types(schema.get("items")):
            return [value]
        if not text and "null" in types:
            return None
        return value
    # One object where a LIST of objects is declared is that one item -- the
    # same rule as a bare string for a list of strings.
    if isinstance(value, dict) and "array" in types and "object" not in types and \
            "object" in _schema_types(schema.get("items")):
        return [_coerce(value, schema["items"])]
    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        return {key: _coerce(item, schema["properties"].get(key)) for key, item in value.items()}
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [_coerce(item, schema["items"]) for item in value]
    return value


def _decode_lenient(text: str) -> Any:
    """Decode JSON that is only missing closing brackets, or a Python literal.

    `ast.literal_eval` evaluates literals only (no names, calls or operators),
    so a single-quoted `{'a': [1]}` is read as data and nothing is executed.
    Returns None when neither reading yields a dict or list.
    """
    for candidate in (_close_open_brackets(text), text):
        if not candidate:
            continue
        for reader in (json.loads, ast.literal_eval):
            try:
                decoded = reader(candidate)
            except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                continue
            if isinstance(decoded, (dict, list)):
                return decoded
    return None


def _text_of(content: Any) -> str:
    """Plain text for a block's content, without re-encoding text that is already JSON.

    Tool results arrive as a string that is often itself JSON. Encoding it a
    second time hands a local model escaped text (`"{\\"status\\": ...}"`),
    which it reads far worse than the JSON itself.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) if isinstance(part, dict) and part.get("type") == "text"
                         else (part if isinstance(part, str) else json.dumps(part, ensure_ascii=False))
                         for part in content)
    return json.dumps(content, ensure_ascii=False)


def _messages(system: Any, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic-shaped history as OpenAI chat messages.

    - The system prompt is one string: several local chat templates do not
      accept a list of content parts in the system role.
    - An assistant turn is ONE message carrying its text and its tool calls,
      so nothing sits between a tool call and its result.
    - Tool results come before any user text in the same turn (they answer
      the previous assistant message), and an error result says so.
    """
    system_text = _text_of(system) if system else ""
    out = [{"role": "system", "content": system_text}] if system_text else []
    for item in messages:
        role = item.get("role", "user")
        content = item.get("content", "")
        if not isinstance(content, list):
            out.append({"role": role, "content": _text_of(content)})
            continue
        texts = [str(b.get("text", "")) for b in content if b.get("type") == "text"]
        if role == "assistant":
            calls = [{"id": b.get("id", ""), "type": "function", "function": {
                        "name": b.get("name", ""), "arguments": json.dumps(b.get("input", {}), ensure_ascii=False)}}
                     for b in content if b.get("type") == "tool_use"]
            message: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts)}
            if calls:
                message["tool_calls"] = calls
            out.append(message)
            continue
        for b in content:
            if b.get("type") == "tool_result":
                body = _text_of(b.get("content", ""))
                out.append({"role": "tool", "tool_call_id": b.get("tool_use_id", ""),
                            "content": f"ERROR: {body}" if b.get("is_error") else body})
        if texts:
            out.append({"role": role, "content": "\n".join(texts)})
    return out


def _stdlib_request(method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None, timeout: float) -> Any:
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, headers={**headers, "Content-Type": "application/json"}, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - server URL is operator configured
            text = response.read().decode("utf-8")
            return _Response(response.status, text)
    except HTTPError as exc:
        return _Response(exc.code, exc.read().decode("utf-8", errors="replace"))


class _Response:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code, self.text = status_code, text
        self.ok = 200 <= status_code < 300

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


def _build(key: str, model: str) -> OMLXProvider:
    from backend.config import settings
    return OMLXProvider(api_key=key, model=model, base_url=settings.omlx_url)


register("omlx", _build)
