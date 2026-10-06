"""
The request budget ledger. Specification section 9.

Every guardrail lives here, and nowhere else. Prompting Opus to "try only five
times" is not enforcement; neither is a value object recomputed on each call,
which is what Cockpit V2's `budgets.py` was. This ledger is the single atomic
counter for one user request, it persists, and it refuses.

Three rules the shape of this module exists to make true
--------------------------------------------------------
**One counter, not a tree.** All execution submissions share ONE request
counter. Five total candidate submissions, not five per analysis round. A new
plan does not reset it; `note_submission` does not know or care which plan
asked.

**The earliest bound wins.** `reserve` checks every limit before every
provider call: calls, tokens, spend, deadline, and the per-call input and
output caps. A request may stop on the deadline at submission two and never
see attempt five. Five is a permission, not a promise.

**A reservation is taken before the call, not after.** Worst-case output
tokens and worst-case cost are held at dispatch and settled against actual
usage on return. A call that dies in flight has still consumed its
reservation, because the provider may well have served it.

Cost when price is unknown
--------------------------
When no price is configured the ledger reports spend as UNKNOWN and
`cost_enforced` is False. It does not invent an estimate and it does not claim
the cost ceiling is reliable — section 9.1 is explicit that an unconfigured
price means the limit is not a control.
"""

from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.cockpit_agentic import DEEP, MODES, STANDARD

# --------------------------------------------------------------- guardrails

@dataclass(frozen=True)
class Limits:
    """Section 9.1, as data. Configurable starting limits, not measured optima.

    The UAT configuration, and what moved
    -------------------------------------
    The specification's own 12,000/20,000-token per-call input caps and its
    35,000/70,000-token cumulative ceilings cannot hold this domain's mandatory
    field catalogue, which measures about 22,000 tokens on its own. That was
    measured, documented in docs/cockpit_agentic_v3/CONTEXT_SIZING.md, and put
    to the owner rather than closed in code.

    The owner's decision, applied here as the UAT configuration:

    * Standard: 64,000-token input packet, 250,000-token cumulative ceiling.
    * Deep:     96,000-token input packet, 500,000-token cumulative ceiling.

    These are still application guardrails and still configurable. What did NOT
    move, and cannot:

    * five Opus-authored execution submissions;
    * three substantive analysis rounds;
    * the 60-second Standard and 120-second Deep deadlines;
    * the total model-call ceilings;
    * the spending ceilings, which stay where they were so live usage can be
      measured against them before anyone proposes new ones;
    * no automatic escalation from Standard to Deep;
    * the earliest bound wins.

    The finalization reserve grew with the ceiling for one reason: it has to be
    able to fund a terminal explanation that carries the same catalogue, and a
    6,000-token reserve could not.
    """

    mode: str
    sonnet_preprocessing_calls: int
    sonnet_summary_calls: int
    execution_submissions: int
    analysis_rounds: int
    total_provider_requests: int
    metadata_tool_requests: int
    steps_per_submission: int
    steps_per_request: int
    deadline_seconds: float
    total_tokens: int
    max_input_tokens_per_call: int
    max_opus_output_tokens: int
    max_sonnet_pass1_output_tokens: int
    max_sonnet_pass2_output_tokens: int
    max_summary_output_tokens: int
    recent_pairs_default: int
    recent_pairs_expanded: int
    recent_pairs_hard_cap: int
    recent_history_tokens: int
    sample_rows_per_dataset: int
    step_wall_seconds: float
    summary_wall_seconds: float
    max_charts: int
    spend_ceiling_usd: float
    finalization_token_reserve: int
    python_memory_mib: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


STANDARD_LIMITS = Limits(
    mode=STANDARD,
    sonnet_preprocessing_calls=2, sonnet_summary_calls=1,
    execution_submissions=5, analysis_rounds=3,
    total_provider_requests=12, metadata_tool_requests=2,
    steps_per_submission=6, steps_per_request=12,
    deadline_seconds=60.0, total_tokens=250_000,
    max_input_tokens_per_call=64_000, max_opus_output_tokens=4_096,
    max_sonnet_pass1_output_tokens=800, max_sonnet_pass2_output_tokens=1_200,
    max_summary_output_tokens=1_000,
    recent_pairs_default=3, recent_pairs_expanded=5, recent_pairs_hard_cap=8,
    recent_history_tokens=4_000, sample_rows_per_dataset=10,
    step_wall_seconds=15.0, summary_wall_seconds=8.0, max_charts=2,
    spend_ceiling_usd=1.00, finalization_token_reserve=25_000,
    python_memory_mib=512)

DEEP_LIMITS = Limits(
    mode=DEEP,
    sonnet_preprocessing_calls=2, sonnet_summary_calls=1,
    execution_submissions=5, analysis_rounds=3,
    total_provider_requests=16, metadata_tool_requests=3,
    steps_per_submission=8, steps_per_request=24,
    deadline_seconds=120.0, total_tokens=500_000,
    max_input_tokens_per_call=96_000, max_opus_output_tokens=6_144,
    max_sonnet_pass1_output_tokens=800, max_sonnet_pass2_output_tokens=1_200,
    max_summary_output_tokens=1_000,
    recent_pairs_default=3, recent_pairs_expanded=5, recent_pairs_hard_cap=8,
    recent_history_tokens=8_000, sample_rows_per_dataset=10,
    step_wall_seconds=30.0, summary_wall_seconds=8.0, max_charts=3,
    spend_ceiling_usd=2.00, finalization_token_reserve=40_000,
    python_memory_mib=1_024)

LIMITS: dict[str, Limits] = {STANDARD: STANDARD_LIMITS, DEEP: DEEP_LIMITS}


#: Which limits an administrator may raise, and the setting that does it.
#: Deliberately NOT here: execution_submissions and analysis_rounds. Sections
#: 1.7 and 9.2 state five and three as architectural invariants -- "neither
#: limit resets" -- rather than as starting values, so there is no override for
#: them at any level and no code path that can change them.
OVERRIDABLE: dict[str, tuple[str, str]] = {
    "max_input_tokens_per_call": ("cockpit_agentic_v3_standard_input_tokens",
                                  "cockpit_agentic_v3_deep_input_tokens"),
    "total_tokens": ("cockpit_agentic_v3_standard_total_tokens",
                     "cockpit_agentic_v3_deep_total_tokens"),
}

#: Never overridable, at any level. Listed so each property is testable rather
#: than merely absent from the table above.
#:
#: The two counters are architectural invariants (sections 1.7 and 9.2). The
#: deadlines and the model-call ceilings are here because the owner fixed them
#: for this UAT configuration while the token budgets moved: a deadline that
#: could be raised to make a slow analysis fit would stop measuring anything.
INVARIANT: tuple[str, ...] = ("execution_submissions", "analysis_rounds",
                              "deadline_seconds", "total_provider_requests",
                              "spend_ceiling_usd")


def limits_for(mode: str) -> Limits:
    """The mode's limits, with any explicit administrator overrides applied.

    Section 9's rule is that neither the MODEL nor the BROWSER may raise a
    limit, and nothing here lets them: an override comes only from deployment
    configuration, only raises, and is reported. Section 9.6 contemplates
    exactly this -- profile the packets during UAT and "request an explicit
    administrative configuration change if necessary" -- and these settings are
    where such a request lands.

    The five execution submissions and three analysis rounds are not among
    them. Sections 1.7 and 9.2 state those as invariants, and there is no
    setting, no argument and no code path that changes either.
    """
    from backend.config import settings

    base = LIMITS.get(str(mode or STANDARD).lower(), STANDARD_LIMITS)
    changes: dict[str, Any] = {}
    for field_name, (standard_setting, deep_setting) in OVERRIDABLE.items():
        setting = deep_setting if base.mode == DEEP else standard_setting
        configured = getattr(settings, setting, 0) or 0
        current = getattr(base, field_name)
        if configured and configured > current:
            changes[field_name] = type(current)(configured)
    return dataclasses.replace(base, **changes) if changes else base


def overrides_in_force(mode: str) -> dict[str, dict[str, Any]]:
    """Which limits this deployment is running raised, and by how much.

    Surfaced in the diagnostics and in the UAT handoff, so a measured result is
    never read as if it had been obtained under the specification's own
    numbers.
    """
    base = LIMITS.get(str(mode or STANDARD).lower(), STANDARD_LIMITS)
    effective = limits_for(mode)
    return {
        name: {"specification": getattr(base, name),
               "configured": getattr(effective, name)}
        for name in OVERRIDABLE
        if getattr(effective, name) != getattr(base, name)}


def input_cap_is_overridden(mode: str) -> bool:
    return "max_input_tokens_per_call" in overrides_in_force(mode)


# ------------------------------------------------------------ stop reasons

STOP_SUBMISSIONS = "submissions_exhausted"
STOP_ROUNDS = "analysis_rounds_exhausted"
STOP_CALLS = "provider_calls_exhausted"
STOP_TOKENS = "token_ceiling_reached"
STOP_SPEND = "spend_ceiling_reached"
STOP_DEADLINE = "deadline_reached"
STOP_STEPS = "step_ceiling_reached"
STOP_METADATA = "metadata_requests_exhausted"
STOP_INPUT_TOO_LARGE = "input_packet_too_large"
STOP_NO_PROGRESS = "no_progress"
STOP_CANCELLED = "cancelled"

#: Reasons that mean "no more model work", as opposed to "no more of one kind".
HARD_STOPS = frozenset({STOP_CALLS, STOP_TOKENS, STOP_SPEND, STOP_DEADLINE,
                        STOP_CANCELLED})


class DuplicateCandidate(RuntimeError):
    """The same code, parameters and release as a candidate that already
    failed. Section 22.

    Not a budget stop: the submission is consumed and the request continues,
    because Opus may still write something different. It becomes a stop only
    when the five are gone, and then it is `STOPPED_EXECUTION_LIMIT` like any
    other exhaustion.
    """

    def __init__(self, submission_number: int, message: str) -> None:
        super().__init__(message)
        self.submission_number = submission_number


class BudgetExceeded(RuntimeError):
    """A reservation the ledger refuses. Carries the reason that fired."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# ------------------------------------------------------------------ prices

@dataclass(frozen=True)
class Prices:
    """USD per million tokens, from configuration. Zero means not configured."""

    sonnet_input: float = 0.0
    sonnet_output: float = 0.0
    opus_input: float = 0.0
    opus_output: float = 0.0

    @property
    def configured(self) -> bool:
        return all(v > 0 for v in (self.sonnet_input, self.sonnet_output,
                                   self.opus_input, self.opus_output))

    def cost(self, *, family: str, input_tokens: int,
             output_tokens: int) -> float | None:
        if not self.configured:
            return None
        if str(family).lower().startswith("opus"):
            i, o = self.opus_input, self.opus_output
        else:
            i, o = self.sonnet_input, self.sonnet_output
        return (input_tokens * i + output_tokens * o) / 1_000_000.0


def prices_from_settings() -> Prices:
    from backend.config import settings

    return Prices(
        sonnet_input=float(settings.cockpit_agentic_v3_sonnet_input_usd_per_mtok),
        sonnet_output=float(settings.cockpit_agentic_v3_sonnet_output_usd_per_mtok),
        opus_input=float(settings.cockpit_agentic_v3_opus_input_usd_per_mtok),
        opus_output=float(settings.cockpit_agentic_v3_opus_output_usd_per_mtok))


# ------------------------------------------------------------ call records

@dataclass
class CallRecord:
    """One provider call, as it actually happened."""

    call_id: str
    role: str
    family: str
    purpose: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    settled: bool = False
    #: True when the call may have been served but the outcome is unknown -- a
    #: timeout after dispatch. Section 9.3: record the uncertain usage rather
    #: than treating it as free.
    uncertain: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Reservation:
    """A held allowance. Settled with actual usage, or released as uncertain."""

    call_id: str
    role: str
    family: str
    purpose: str
    input_tokens: int
    reserved_output_tokens: int
    reserved_cost_usd: float | None


# ----------------------------------------------------------------- ledger

class Ledger:
    """One request's budget. Atomic, persisted, and the only thing that refuses.

    Thread-safe because a request may be cancelled from another thread while a
    provider call is in flight, and because section 9.3 requires that
    concurrent workers cannot start a second free budget for the same request.
    """

    def __init__(self, *, request_id: str = "", mode: str = STANDARD,
                 prices: Prices | None = None,
                 clock: Any = time.monotonic,
                 store: "LedgerStore | None" = None) -> None:
        mode = str(mode or STANDARD).lower()
        if mode not in MODES:
            mode = STANDARD
        self.request_id = request_id or f"req-{uuid.uuid4().hex[:12]}"
        self.mode = mode
        self.limits = limits_for(mode)
        self.prices = prices if prices is not None else prices_from_settings()
        self._clock = clock
        self._store = store
        self._lock = threading.RLock()
        self._started = float(clock())

        self.sonnet_preprocessing_calls = 0
        self.sonnet_summary_calls = 0
        self.submissions = 0
        self.analysis_rounds = 0
        self.provider_requests = 0
        self.metadata_requests = 0
        self.steps = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.spend_usd = 0.0
        self.reserved_tokens = 0
        self.reserved_spend_usd = 0.0

        self.calls: list[CallRecord] = []
        self.stopped: str = ""
        self.stop_detail: str = ""
        self.cancelled = False
        #: Normalized fingerprints of submitted code, so an identical no-progress
        #: candidate is blocked BEFORE it is executed rather than after.
        self.fingerprints: list[str] = []

    # -- time ----------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return float(self._clock()) - self._started

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.limits.deadline_seconds - self.elapsed)

    def expired(self) -> bool:
        return self.remaining_seconds <= 0.0

    # -- accounting ----------------------------------------------------

    @property
    def tokens_used(self) -> int:
        """Cumulative model tokens. Cached reads count: section 9.3 is explicit
        that caching lowers cost, not logical context size."""
        return (self.input_tokens + self.output_tokens
                + self.cache_read_tokens + self.cache_write_tokens)

    @property
    def tokens_committed(self) -> int:
        return self.tokens_used + self.reserved_tokens

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.limits.total_tokens - self.tokens_committed)

    @property
    def spend_committed(self) -> float:
        return self.spend_usd + self.reserved_spend_usd

    @property
    def cost_enforced(self) -> bool:
        """False when no price is configured. The ceiling is then not a control
        and this ledger says so rather than pretending."""
        return self.prices.configured

    @property
    def submissions_remaining(self) -> int:
        return max(0, self.limits.execution_submissions - self.submissions)

    @property
    def rounds_remaining(self) -> int:
        return max(0, self.limits.analysis_rounds - self.analysis_rounds)

    @property
    def calls_remaining(self) -> int:
        return max(0, self.limits.total_provider_requests
                   - self.provider_requests)

    # -- stopping ------------------------------------------------------

    def stop(self, reason: str, detail: str = "") -> None:
        """Record the first stop reason. A later one does not overwrite it: the
        user is told what actually stopped the request, not what happened next."""
        with self._lock:
            if not self.stopped:
                self.stopped = reason
                self.stop_detail = detail
            if reason == STOP_CANCELLED:
                self.cancelled = True
            self._persist()

    def cancel(self, detail: str = "cancelled by the user") -> None:
        self.stop(STOP_CANCELLED, detail)

    #: Stops that end EVERYTHING, including the bounded terminal explanation.
    #: There is no time left, no permission left, or no call left to make it
    #: with, so section 8.3's "reserve a final explanation if affordable" is
    #: not affordable and the server-generated stop envelope is used instead.
    _ABSOLUTE = (STOP_CANCELLED, STOP_DEADLINE, STOP_CALLS)

    def _hard_stop_reason(self, *, finalization: bool = False) -> str:
        if self.cancelled:
            return STOP_CANCELLED
        if self.expired():
            return STOP_DEADLINE
        if self.calls_remaining <= 0:
            return STOP_CALLS
        if finalization:
            # The token and spend arithmetic for a finalization call is done in
            # `reserve`, against the allowance held back FOR it. Reporting the
            # ordinary-work ceiling here would refuse the very call the reserve
            # exists to fund.
            return ""
        if self.tokens_remaining <= 0:
            return STOP_TOKENS
        if self.cost_enforced and self.spend_committed >= self.limits.spend_ceiling_usd:
            return STOP_SPEND
        return ""

    def may_continue(self, *, finalization: bool = False) -> str:
        """"" when work may continue, else the reason that stops it.

        A latched soft stop -- tokens, spend, submissions, rounds, steps -- ends
        analytical work but does not end the request's ability to explain
        itself. Only the absolute stops do that.
        """
        latched = self.stopped
        if finalization and latched not in self._ABSOLUTE:
            latched = ""
        return latched or self._hard_stop_reason(finalization=finalization)

    # -- counters ------------------------------------------------------

    def note_submission(self, fingerprint: str = "") -> int:
        """Consume one of the five. Raises when there is no sixth.

        The counter is global. It does not reset when Opus changes its analysis
        plan, and CreditProbe never consumes one, because CreditProbe never
        authors a candidate.
        """
        with self._lock:
            if self.submissions >= self.limits.execution_submissions:
                self.stop(STOP_SUBMISSIONS,
                          f"{self.limits.execution_submissions} execution "
                          f"submissions have been used")
                raise BudgetExceeded(
                    STOP_SUBMISSIONS,
                    f"No further execution: all "
                    f"{self.limits.execution_submissions} submissions for this "
                    f"question have been used. A new analysis plan does not "
                    f"grant more.")
            duplicate = bool(fingerprint) and fingerprint in self.fingerprints
            if fingerprint and not duplicate:
                self.fingerprints.append(fingerprint)
            # Section 22: a duplicate consumes the submission and is NOT run.
            # It is not, by itself, the end of the request -- Opus may still
            # author something different with the attempts that remain, and
            # taking those away for one repeat would be a stricter rule than
            # the one that was agreed.
            self.submissions += 1
            self._persist()
            if duplicate:
                raise DuplicateCandidate(
                    self.submissions,
                    "This candidate is identical to one already submitted and "
                    "failed. It was not run again, and the attempt is spent.")
            return self.submissions

    def note_analysis_round(self) -> int:
        """Consume one of the three substantive rounds, including the first.

        A syntax or binding repair is NOT a round: it uses a submission and
        leaves this untouched.
        """
        with self._lock:
            if self.analysis_rounds >= self.limits.analysis_rounds:
                self.stop(STOP_ROUNDS,
                          f"{self.limits.analysis_rounds} analysis rounds have "
                          f"been used")
                raise BudgetExceeded(
                    STOP_ROUNDS,
                    f"No further analysis: all {self.limits.analysis_rounds} "
                    f"rounds for this question have been used.")
            self.analysis_rounds += 1
            self._persist()
            return self.analysis_rounds

    def note_steps(self, count: int) -> None:
        with self._lock:
            count = int(count)
            if count > self.limits.steps_per_submission:
                raise BudgetExceeded(
                    STOP_STEPS,
                    f"{count} executable steps exceeds the "
                    f"{self.limits.steps_per_submission} permitted in one "
                    f"submission in {self.mode} mode.")
            if self.steps + count > self.limits.steps_per_request:
                self.stop(STOP_STEPS, "the request step ceiling was reached")
                raise BudgetExceeded(
                    STOP_STEPS,
                    f"{self.steps + count} executable steps would exceed the "
                    f"{self.limits.steps_per_request} permitted for the whole "
                    f"request.")
            self.steps += count
            self._persist()

    def note_metadata_request(self) -> int:
        with self._lock:
            if self.metadata_requests >= self.limits.metadata_tool_requests:
                raise BudgetExceeded(
                    STOP_METADATA,
                    f"All {self.limits.metadata_tool_requests} metadata "
                    f"look-ups have been used. Work from the catalogue already "
                    f"supplied, or ask the user for the scope.")
            self.metadata_requests += 1
            self._persist()
            return self.metadata_requests

    # -- provider calls ------------------------------------------------

    def reserve(self, *, role: str, family: str, purpose: str,
                input_tokens: int, max_output_tokens: int,
                finalization: bool = False) -> Reservation:
        """Hold the allowance for one provider call, or refuse it.

        `finalization` marks the bounded terminal explanation and the summary,
        which may draw on the reserve held INSIDE the total allowance rather
        than in addition to it.
        """
        with self._lock:
            reason = self.may_continue(finalization=finalization)
            if reason:
                raise BudgetExceeded(reason, self._stop_message(reason))

            cap = self.limits.max_input_tokens_per_call
            if input_tokens > cap:
                raise BudgetExceeded(
                    STOP_INPUT_TOO_LARGE,
                    f"The assembled input is {input_tokens} tokens against a "
                    f"{cap}-token per-call cap in {self.mode} mode. Required "
                    f"scope is not silently dropped to fit.")

            want = int(input_tokens) + int(max_output_tokens)
            available = self.limits.total_tokens - self.tokens_committed
            if not finalization:
                available -= self.limits.finalization_token_reserve
            if want > available:
                self.stop(STOP_TOKENS,
                          f"{want} tokens needed, {max(0, available)} available "
                          f"within the {self.limits.total_tokens}-token ceiling"
                          + ("" if finalization else
                             f" after holding "
                             f"{self.limits.finalization_token_reserve} back "
                             f"for the final explanation"))
                raise BudgetExceeded(STOP_TOKENS, self.stop_detail)

            cost = self.prices.cost(family=family, input_tokens=input_tokens,
                                    output_tokens=max_output_tokens)
            if cost is not None:
                if self.spend_committed + cost > self.limits.spend_ceiling_usd:
                    # Cents, like every other figure a person reads. The
                    # sub-cent precision this used to print was real -- model
                    # calls cost fractions of a cent -- but it bought nothing:
                    # what an operator needs to know is that the ceiling is
                    # reached, and by how much the remaining allowance falls
                    # short of one more call.
                    self.stop(STOP_SPEND,
                              f"the next call needs about "
                              f"${cost:.2f} and only "
                              f"${max(0.0, self.limits.spend_ceiling_usd - self.spend_committed):.2f} "
                              f"of the ${self.limits.spend_ceiling_usd:.2f} "
                              f"ceiling is left")
                    raise BudgetExceeded(STOP_SPEND, self.stop_detail)

            call_id = f"call-{len(self.calls) + 1:02d}"
            self.provider_requests += 1
            self.reserved_tokens += want
            if cost is not None:
                self.reserved_spend_usd += cost
            self.calls.append(CallRecord(
                call_id=call_id, role=role, family=family, purpose=purpose))
            self._persist()
            return Reservation(call_id=call_id, role=role, family=family,
                               purpose=purpose, input_tokens=input_tokens,
                               reserved_output_tokens=int(max_output_tokens),
                               reserved_cost_usd=cost)

    def settle(self, reservation: Reservation, *, input_tokens: int = -1,
               output_tokens: int = 0, cache_read_tokens: int = 0,
               cache_write_tokens: int = 0, error: str = "",
               uncertain: bool = False) -> CallRecord:
        """Replace a reservation with what the call actually used."""
        with self._lock:
            record = next(c for c in self.calls
                          if c.call_id == reservation.call_id)
            actual_in = (reservation.input_tokens if input_tokens < 0
                         else int(input_tokens))
            record.input_tokens = actual_in
            record.output_tokens = int(output_tokens)
            record.cache_read_tokens = int(cache_read_tokens)
            record.cache_write_tokens = int(cache_write_tokens)
            record.error = error
            record.uncertain = bool(uncertain)
            record.settled = True

            self.reserved_tokens -= (reservation.input_tokens
                                     + reservation.reserved_output_tokens)
            self.reserved_tokens = max(0, self.reserved_tokens)
            if reservation.reserved_cost_usd is not None:
                self.reserved_spend_usd = max(
                    0.0, self.reserved_spend_usd - reservation.reserved_cost_usd)

            self.input_tokens += actual_in
            self.output_tokens += int(output_tokens)
            self.cache_read_tokens += int(cache_read_tokens)
            self.cache_write_tokens += int(cache_write_tokens)

            cost = self.prices.cost(
                family=reservation.family,
                input_tokens=actual_in + int(cache_read_tokens)
                + int(cache_write_tokens),
                output_tokens=int(output_tokens))
            record.cost_usd = cost
            if cost is not None:
                self.spend_usd += cost

            if reservation.purpose.startswith("sonnet_pass"):
                self.sonnet_preprocessing_calls += 1
            elif reservation.purpose == "sonnet_summary":
                self.sonnet_summary_calls += 1
            self._persist()
            return record

    def _stop_message(self, reason: str) -> str:
        return {
            STOP_CANCELLED: "The request was cancelled.",
            STOP_DEADLINE: (f"The {self.limits.deadline_seconds:.0f}-second "
                            f"{self.mode} deadline was reached. That is a "
                            f"cancellation limit, not a promise of completion."),
            STOP_CALLS: (f"All {self.limits.total_provider_requests} model "
                         f"requests permitted for this question have been used."),
            STOP_TOKENS: (f"The {self.limits.total_tokens}-token ceiling for "
                          f"this question has been reached."),
            STOP_SPEND: (f"The ${self.limits.spend_ceiling_usd:.2f} spending "
                         f"ceiling for this question has been reached."),
        }.get(reason, self.stop_detail or reason)

    # -- persistence ---------------------------------------------------

    def _persist(self) -> None:
        if self._store is not None:
            self._store.save(self)

    # -- reporting -----------------------------------------------------

    def budget_view(self) -> dict[str, Any]:
        """What Opus is told about its remaining allowance. Section 7.4-J."""
        return {
            "request_id": self.request_id,
            "mode": self.mode,
            "submissions_used": self.submissions,
            "submissions_remaining": self.submissions_remaining,
            "analysis_rounds_used": self.analysis_rounds,
            "analysis_rounds_remaining": self.rounds_remaining,
            "model_requests_used": self.provider_requests,
            "model_requests_remaining": self.calls_remaining,
            "metadata_requests_remaining": max(
                0, self.limits.metadata_tool_requests - self.metadata_requests),
            "executable_steps_used": self.steps,
            "executable_steps_remaining": max(
                0, self.limits.steps_per_request - self.steps),
            "tokens_used": self.tokens_used,
            "tokens_remaining": self.tokens_remaining,
            "max_input_tokens_per_call": self.limits.max_input_tokens_per_call,
            "max_output_tokens_per_call": self.limits.max_opus_output_tokens,
            "seconds_remaining": round(self.remaining_seconds, 2),
            "step_wall_seconds": self.limits.step_wall_seconds,
            "max_charts": self.limits.max_charts,
            "spend_usd": (round(self.spend_usd, 6) if self.cost_enforced
                          else "UNKNOWN"),
            "spend_ceiling_usd": (self.limits.spend_ceiling_usd
                                  if self.cost_enforced else "NOT_ENFORCED"),
            "cost_enforced": self.cost_enforced,
            "stopped": self.stopped,
        }

    def to_dict(self) -> dict[str, Any]:
        view = self.budget_view()
        view.update({
            "limits": self.limits.to_dict(),
            "calls": [c.to_dict() for c in self.calls],
            "stop_detail": self.stop_detail,
            "cancelled": self.cancelled,
            "elapsed_seconds": round(self.elapsed, 3),
            "duplicate_fingerprints_seen": len(self.fingerprints),
            "cost_note": (
                "Spend is reported from configured prices and provider usage "
                "fields." if self.cost_enforced else
                "No model price is configured for this deployment, so spend is "
                "UNKNOWN and the spending ceiling is NOT a control. Configure "
                "the per-million-token prices before relying on it."),
        })
        return view


# ------------------------------------------------------------------ store

class LedgerStore:
    """Where a ledger survives a restart. Section 9.3.

    The in-memory implementation is the default and is correct for a single
    process; it exists as a seam so a deployment with concurrent workers binds
    a shared store without any caller changing. `open` is the idempotency
    point: the same request id returns the SAME ledger, so a double click or a
    restarted worker continues one budget instead of starting a second.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: dict[str, dict[str, Any]] = {}
        self._live: dict[str, Ledger] = {}

    def open(self, *, request_id: str, mode: str = STANDARD,
             prices: Prices | None = None, clock: Any = time.monotonic
             ) -> tuple[Ledger, bool]:
        """Return `(ledger, resumed)`. Never a second budget for one request."""
        with self._lock:
            existing = self._live.get(request_id)
            if existing is not None:
                return existing, True
            ledger = Ledger(request_id=request_id, mode=mode, prices=prices,
                            clock=clock, store=self)
            self._live[request_id] = ledger
            self._state[request_id] = ledger.to_dict()
            return ledger, False

    def save(self, ledger: Ledger) -> None:
        with self._lock:
            self._state[ledger.request_id] = ledger.to_dict()

    def read(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._state.get(request_id)
            return dict(state) if state else None

    def close(self, request_id: str) -> None:
        with self._lock:
            self._live.pop(request_id, None)


#: The process-wide store. One per deployment.
STORE = LedgerStore()


__all__ = [
    "BudgetExceeded", "DuplicateCandidate", "CallRecord", "DEEP_LIMITS", "HARD_STOPS",
           "Ledger", "LedgerStore", "Limits", "Prices", "Reservation",
           "STANDARD_LIMITS", "STOP_CALLS", "STOP_CANCELLED", "STOP_DEADLINE",
           "STOP_INPUT_TOO_LARGE", "STOP_METADATA", "STOP_NO_PROGRESS",
           "STOP_ROUNDS", "STOP_SPEND", "STOP_STEPS", "STOP_SUBMISSIONS",
           "STOP_TOKENS", "STORE", "INVARIANT", "OVERRIDABLE",
           "input_cap_is_overridden", "limits_for", "overrides_in_force",
           "prices_from_settings"]
