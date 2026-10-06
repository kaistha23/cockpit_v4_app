"""
The ledger: counters, cost reservations, and the run deadline.

The property this module has to have
------------------------------------
Every loop in the orchestration consumes something here that is never
replenished. Not "should consume" -- `spend_generation`, `spend_submission`,
`open_round`, `spend_format_recovery` and `spend_answer_correction` all raise
`BudgetExceeded` at zero, and the orchestrator has no path back to the model
that does not call one of them.

Cost is reserved BEFORE a paid attempt and settled after, at the verified
price. A cancelled or disconnected call settles as UNCERTAIN and its
reservation is held pending, because the provider may still have billed it.
Booking that as zero is how a run reports $0.00 having spent real money.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

from backend.cockpit_v4.capability import Capability
from backend.cockpit_v4.config import SETTLEMENT_MARGIN_SECONDS, Limits
from backend.cockpit_v4.states import (ACTION_FORMAT_EXHAUSTED,
                                       ANSWER_FORMAT_EXHAUSTED,
                                       CALL_LIMIT, COST_LIMIT,
                                       DEADLINE_EXPIRED, EXECUTION_LIMIT,
                                       NO_PROGRESS,
                                       PLAN_FORMAT_EXHAUSTED,
                                       PROVIDER_UNAVAILABLE, ROUND_LIMIT)


class BudgetExceeded(RuntimeError):
    """A bound was reached. Carries the code so the stop names the guardrail."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Counters:
    generation_attempts: int = 0
    provider_attempts: int = 0
    execution_submissions: int = 0
    analysis_rounds: int = 0
    catalog_calls: int = 0
    artifact_reads: int = 0
    steps_attempted: int = 0
    plan_retries: int = 0
    #: Recovering a malformed or truncated response is bounded PER PHASE.
    #: One counter used to serve both, and a hiccup while authoring the
    #: analysis silently disarmed the recovery that writing the answer would
    #: later need -- so a live run executed its SQL correctly and was then
    #: refused at publication. The two phases cost different things: an
    #: action retry happens before any work exists, an answer retry happens
    #: after the query has already run and been paid for.
    action_format_recoveries: int = 0
    answer_format_recoveries: int = 0
    answer_corrections: int = 0
    transport_retries: int = 0


@dataclass
class Ledger:
    """One run's bounded resources. Server-owned; the model cannot widen it."""

    limits: Limits
    capability: Capability
    store: Any
    run_id: str
    started_monotonic: float = field(default_factory=time.monotonic)
    counters: Counters = field(default_factory=Counters)
    #: Reservation ids opened and not yet settled, so a cancel can mark them.
    open_reservations: list[str] = field(default_factory=list)
    #: A round is "open" once an execution batch has run successfully and its
    #: results reached the analyst. See `open_round`.
    _round_open: bool = False
    cancelled: bool = False

    # -- time ------------------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_monotonic

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.limits.deadline_seconds - self.elapsed_seconds)

    #: The smallest response allowance worth making a paid call for. Below
    #: this the run cannot produce a usable answer, so it fails closed rather
    #: than buying a truncated one.
    MIN_RESPONSE_TOKENS = 1_024

    def adopt(self, limits: Limits) -> dict[str, Any]:
        """Widen this run's time and cost allowance. Never narrows.

        Called when the analyst DECLARES a data analysis, because the mode is
        not known at intake. Widening mid-run is safe in a way narrowing
        would not be: nothing has been spent against the larger bound, and
        every counter keeps its value.

        EVERY time field widens, not just the deadline. This used to move
        only the deadline and the spend ceiling, so an upgraded run had a
        180-second clock but still the product-help call windows and a
        20-second reserve -- the reserve was never wider than the allowance
        it was held back from. Each field takes the larger of old and new.
        """
        fields = ("deadline_seconds", "action_call_seconds",
                  "answer_call_seconds", "finalization_reserve_seconds",
                  "spend_ceiling_usd")
        before = {name: getattr(self.limits, name) for name in fields}
        self.limits = replace(
            self.limits,
            **{name: max(before[name], getattr(limits, name))
               for name in fields})
        after = {name: getattr(self.limits, name) for name in fields}
        return {"before": before, "after": after,
                "changed": before != after}

    def affordable_output_tokens(self, *, input_tokens: int,
                                 wanted: int, cache_write_tokens: int = 0
                                 ) -> int:
        """The response allowance this run can still pay for.

        The reserve is the exposure, so it is the number actually sent as
        `max_tokens` -- reserving less than the model is permitted to emit
        would be a projection that is not true. What this does instead is
        stop ASKING for a maximum-length answer when the budget cannot carry
        one: the allowance is reduced to what is affordable, the request is
        capped at exactly that, and the run fails closed only when even
        `MIN_RESPONSE_TOKENS` will not fit. "Cannot afford 4,096 tokens" is
        not the same fact as "cannot afford an answer".
        """
        price = self.capability.price
        spend = self.spend()
        headroom = (self.limits.spend_ceiling_usd
                    - spend["committed_usd"] - spend["pending_usd"])
        fixed = price.cost(input_tokens=input_tokens, output_tokens=0,
                           cache_write_tokens=cache_write_tokens)
        for_output = headroom - fixed
        if for_output <= 0:
            return 0
        per_token = price.cost(input_tokens=0, output_tokens=1_000_000) / 1e6
        if per_token <= 0:
            return wanted
        return max(0, min(wanted, int(for_output / per_token)))

    #: Time left for settling a call, writing its events and stopping
    #: cleanly. A timeout equal to the WHOLE remainder leaves nothing for
    #: any of that, which is how a run ends past its own deadline even
    #: though every individual bound was respected.
    SETTLEMENT_MARGIN_SECONDS = SETTLEMENT_MARGIN_SECONDS

    def call_timeout_seconds(self, *, phase: str = "action") -> float:
        """How long a provider call may block, bounded by the run's clock.

        Never longer than the time the run has left, less a settlement
        margin. A socket that outlives the run's own watchdog is how a
        120-second run reaches 143 seconds.

        And, for an ACTION, never longer than one action is allowed to take.
        This used to hand the whole remainder to every call, so the first
        action of a 120-second run could block for 118 seconds: by the time
        it came back malformed there was no time to ask again, and the run
        reported a call limit for what was really one call that was allowed
        to eat the budget. An action that cannot be authored in its own
        window is cancelled while there is still a window left to try again
        in -- which is the whole point of holding one.

        The LAST answer attempt keeps the full remainder. It is the last
        call the run makes, and cutting it short would throw away work that
        has already been done and paid for.

        An answer attempt that still has a successor does not. That is the
        live failure: two answer attempts took 43s and 47s of a 120-second
        run, and the re-ask the run was entitled to had 7.8 seconds to live
        in. Whichever of the two would have succeeded, neither was given the
        room, because the first was allowed to spend the second's time.

        So the rule is a question about the future, not about the phase:
        *can CreditProbe ask again?* While it can, no single attempt may eat
        the clock. Once it cannot, the attempt in hand is the last one and
        gets everything that is left.

        An ACTION is also bounded by the finalization reserve once the run
        is under way. `check_call_window` only refuses to START an action
        inside the reserve; it says nothing about how long a started action
        may run, and an action allowed `action_call_seconds` could outlive
        the reserve it was admitted to protect. So after the first
        generation (the same condition `check_call_window` uses) the
        action's bound also leaves the reserve and the settlement margin.
        """
        usable = self.remaining_seconds - self.SETTLEMENT_MARGIN_SECONDS
        bound = min(usable, self.limits.deadline_seconds)
        if phase != "answer":
            bound = min(bound, self.limits.action_call_seconds)
            if self.counters.generation_attempts >= 1:
                bound = min(bound, usable
                            - self.limits.finalization_reserve_seconds)
        elif self.answer_reask_remains():
            bound = min(bound, self.limits.answer_call_seconds)
        return max(1.0, bound)

    def answer_reask_remains(self) -> bool:
        """True while a truncated or malformed answer could be asked again.

        Read by `call_timeout_seconds` to decide whether the attempt in hand
        is the last one. Kept as its own name because "is this the last
        attempt" is the question, and `answer_format_recoveries <
        answer_format_regenerations` is only how it happens to be answered.
        """
        return (self.counters.answer_format_recoveries
                < self.limits.answer_format_regenerations)

    def check_call_window(self, *, phase: str = "action") -> None:
        """Refuse a call there is no longer time to use.

        `check_deadline` fires only at zero, so a generation launched with a
        few seconds left was allowed to consume them -- and the run then
        discovered it had no time to write the answer. Time spent reaching a
        result nobody receives is time wasted twice.

        An ACTION call must leave the finalization reserve intact. The ANSWER
        call may spend it: that is what it was held for.
        """
        remaining = self.remaining_seconds
        floor = self.limits.min_call_seconds
        # The reserve protects writing up an analysis that succeeded, so it
        # applies once the run is under way. Applying it to the FIRST
        # generation would be wrong twice over: nothing exists to protect,
        # and the run is still on the tight product-help allowance it widens
        # only after the analyst declares the turn analytical -- so an
        # analytical question with a slow first turn would be stopped before
        # it could ever claim the allowance meant for it.
        if phase != "answer" and self.counters.generation_attempts >= 1:
            floor += self.limits.finalization_reserve_seconds
        if remaining < floor:
            raise BudgetExceeded(
                DEADLINE_EXPIRED,
                f"{remaining:.0f}s of the {self.limits.deadline_seconds:.0f}s "
                f"allowance remain, which is not enough to run another "
                f"action and still publish an answer "
                f"({self.limits.finalization_reserve_seconds:.0f}s is held "
                f"back to write one).")

    def action_window_closed(self) -> bool:
        """Is there still time for another ACTION, or only for an answer?

        The same arithmetic as `check_call_window`, asked rather than
        enforced. The reserve exists so a run can write up what it has; a
        reserve that is only ever discovered by an exception is a reserve
        that expires unspent, which is what it was created to prevent.
        """
        if self.counters.generation_attempts < 1:
            return False
        floor = (self.limits.min_call_seconds
                 + self.limits.finalization_reserve_seconds)
        return self.remaining_seconds < floor

    def answer_window_open(self) -> bool:
        """Is there still time to write an answer at all?"""
        return self.remaining_seconds >= self.limits.min_call_seconds

    def check_deadline(self) -> None:
        if self.remaining_seconds <= 0:
            raise BudgetExceeded(
                DEADLINE_EXPIRED,
                f"the {self.limits.deadline_seconds:.0f}-second "
                f"{self.limits.mode} deadline passed.")

    # -- calls -----------------------------------------------------------

    def spend_generation(self) -> int:
        self.check_deadline()
        if self.counters.generation_attempts >= self.limits.generation_attempts:
            raise BudgetExceeded(
                CALL_LIMIT,
                f"all {self.limits.generation_attempts} generation attempts "
                f"for this run were used.")
        self.counters.generation_attempts += 1
        return self.counters.generation_attempts

    def spend_provider_attempt(self) -> int:
        """Every actual HTTP attempt, including token counting and retries.

        The SDK's own retries are disabled in `provider.py`; if that ever
        changes, this counter is where the omission shows up.
        """
        if self.counters.provider_attempts >= self.limits.provider_attempts:
            raise BudgetExceeded(
                CALL_LIMIT,
                f"all {self.limits.provider_attempts} provider HTTP attempts "
                f"for this run were used.")
        self.counters.provider_attempts += 1
        return self.counters.provider_attempts

    def spend_catalog_call(self) -> int:
        self.check_deadline()
        if self.counters.catalog_calls >= self.limits.catalog_calls:
            raise BudgetExceeded(
                CALL_LIMIT,
                f"all {self.limits.catalog_calls} catalog calls were used.")
        self.counters.catalog_calls += 1
        return self.counters.catalog_calls

    def spend_artifact_read(self) -> int:
        self.check_deadline()
        if self.counters.artifact_reads >= self.limits.artifact_reads:
            raise BudgetExceeded(
                CALL_LIMIT,
                f"all {self.limits.artifact_reads} artifact reads were used.")
        self.counters.artifact_reads += 1
        return self.counters.artifact_reads

    def spend_submission(self) -> int:
        """Counted for ANY fully received execute_analysis request.

        Including one that fails validation. A rejected candidate cost a
        generation and a validation pass, and letting invalid submissions be
        free is an unbounded loop with a counter that never moves.
        """
        self.check_deadline()
        if (self.counters.execution_submissions
                >= self.limits.execution_submissions):
            raise BudgetExceeded(
                EXECUTION_LIMIT,
                f"all {self.limits.execution_submissions} execution "
                f"submissions for this run were used.")
        self.counters.execution_submissions += 1
        return self.counters.execution_submissions

    def spend_plan_retry(self) -> int:
        """Reserve one bounded correction for a plan rejected before SQL.

        A malformed plan is still a paid model turn, but it is not an SQL
        submission.  Keeping the limits separate prevents three compiler
        errors from consuming the five executions the reader asked for.
        """
        self.check_deadline()
        if self.counters.plan_retries >= self.limits.plan_retries:
            raise BudgetExceeded(
                PLAN_FORMAT_EXHAUSTED,
                f"all {self.limits.plan_retries} plan correction attempts were used. "
                "No SQL was executed.")
        self.counters.plan_retries += 1
        return self.counters.plan_retries

    def open_round(self) -> int:
        """Rounds, by the mechanical rule in section 11.

        Round 1 starts at the first submission. A failed or partly failed
        batch may be repaired WITHIN that round. Only after a batch completes
        successfully and its results reach the analyst does the next execution
        batch open a new round. Metadata reads and finalization open nothing.
        """
        if not self._round_open:
            if self.counters.analysis_rounds >= self.limits.analysis_rounds:
                raise BudgetExceeded(
                    ROUND_LIMIT,
                    f"all {self.limits.analysis_rounds} analysis rounds were "
                    f"used.")
            self.counters.analysis_rounds += 1
            self._round_open = True
        return self.counters.analysis_rounds

    def close_round(self) -> None:
        """A batch completed successfully; the next batch opens a new round."""
        self._round_open = False

    def spend_steps(self, count: int) -> None:
        if self.counters.steps_attempted + count > self.limits.total_steps:
            raise BudgetExceeded(
                EXECUTION_LIMIT,
                f"this batch would attempt "
                f"{self.counters.steps_attempted + count} steps and the run "
                f"allows {self.limits.total_steps}. Nothing was executed and "
                f"no step was dropped.")
        self.counters.steps_attempted += count

    def spend_format_recovery(self, *, phase: str = "action") -> None:
        """One structure recovery per PHASE, not one per run.

        `phase` is "action" while the analyst is choosing and authoring what
        to do, and "answer" once an analysis has succeeded and it is writing
        the response. They are bounded separately because they fail for
        different reasons at different costs.
        """
        if phase == "answer":
            used = self.counters.answer_format_recoveries
            allowed = self.limits.answer_format_regenerations
            code = ANSWER_FORMAT_EXHAUSTED
            what = ("the one structure-regeneration attempt for the FINAL "
                    "ANSWER was already used. The analysis itself succeeded "
                    "and its result is preserved.")
        else:
            used = self.counters.action_format_recoveries
            allowed = self.limits.format_regenerations
            # NOT a call limit. See `states.ACTION_FORMAT_EXHAUSTED`: the
            # run that produced this had generations, provider attempts,
            # time and money all left. What ran out was the re-ask.
            code = ACTION_FORMAT_EXHAUSTED
            what = ("the one re-ask for a complete action was already used. "
                    "Both attempts came back without a usable action, so "
                    "nothing was run.")
        if used >= allowed:
            raise BudgetExceeded(code, what)
        if phase == "answer":
            self.counters.answer_format_recoveries += 1
        else:
            self.counters.action_format_recoveries += 1

    def spend_answer_correction(self) -> None:
        if self.counters.answer_corrections >= self.limits.answer_corrections:
            raise BudgetExceeded(
                CALL_LIMIT,
                "the one answer-only correction for this run was already "
                "used.")
        self.counters.answer_corrections += 1

    def spend_transport_retry(self) -> None:
        """The one retry a network fault gets, and what a second one IS.

        THIS USED TO RAISE `CALL_LIMIT`, WHICH WAS NOT TRUE.

        The live M06 turn 3 settled as CALL_LIMIT with "the run used its
        model-call allowance before the answer was written". It had used 4
        of 12 generations, 4 of 24 provider attempts, 1 of 5 submissions and
        1 of 3 rounds. Nothing about the model-call allowance was exhausted.
        The provider failed twice, and the retry this method bounds was the
        only thing that ran out -- which is a fact about the network, not
        about the run's budget.

        The distinction is already written down one caller away, in the
        message the FIRST failure prints: "a turn that ran past the time one
        action is allowed is not the same event as a network fault, and
        neither of them is a call limit." The code then reported exactly the
        call limit that comment denies, and a diagnosis reading the ledger
        for an exhausted allowance finds a healthy one.

        So the second transport failure settles under PROVIDER_UNAVAILABLE,
        the code that already exists for the provider being unreachable.
        `finalization.RESULT_ONLY_REASON` carries it, so a run whose query
        already succeeded still publishes its rows -- which is what the live
        turn did, correctly, under the wrong name.

        The allowance itself is UNCHANGED: one retry per run. Two network
        failures in one run is not a budget to widen.
        """
        if self.counters.transport_retries >= 1:
            raise BudgetExceeded(
                PROVIDER_UNAVAILABLE,
                "the provider failed twice on this run and the one retry a "
                "transport fault is allowed was already used. This is not a "
                "budget: generations, provider attempts, submissions and "
                "rounds all remain.")
        self.counters.transport_retries += 1

    # -- cost ------------------------------------------------------------

    def spend(self) -> dict[str, Any]:
        return self.store.spend(self.run_id)

    def affordable(self, *, input_tokens: int, output_tokens: int,
                   cache_write_tokens: int = 0) -> float:
        """The worst-case cost of the next attempt, at the verified price."""
        return self.capability.price.cost(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_write_tokens=cache_write_tokens)

    def reserve(self, *, purpose: str, input_tokens: int, output_tokens: int,
                cache_write_tokens: int = 0) -> str:
        """Atomic reservation at the worst applicable price. Fails closed.

        Reserving the MAXIMUM output rather than an expected output is
        deliberate: the run must be able to afford the response it asked for,
        not the response it hopes for.
        """
        worst = self.affordable(input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                cache_write_tokens=cache_write_tokens)
        current = self.spend()
        projected = current["committed_usd"] + current["pending_usd"] + worst
        if projected > self.limits.spend_ceiling_usd:
            raise BudgetExceeded(
                COST_LIMIT,
                f"this attempt would reserve ${worst:.4f} against a "
                f"${self.limits.spend_ceiling_usd:.2f} ceiling with "
                f"${current['committed_usd'] + current['pending_usd']:.4f} "
                f"already committed or pending.")
        reservation_id = self.store.reserve(
            run_id=self.run_id, purpose=purpose, reserved_usd=worst)
        self.open_reservations.append(reservation_id)
        return reservation_id

    def settle(self, reservation_id: str, *, usage: dict[str, Any],
               uncertain: bool = False) -> float | None:
        """Settle at actual usage, or hold pending when the outcome is unknown."""
        if reservation_id in self.open_reservations:
            self.open_reservations.remove(reservation_id)
        if uncertain:
            self.store.settle(reservation_id, settled_usd=None, usage=usage,
                              uncertain=True)
            return None
        actual = self.capability.price.cost(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_tokens") or 0))
        self.store.settle(reservation_id, settled_usd=actual, usage=usage,
                          uncertain=False)
        return actual

    def cancel(self) -> None:
        """Mark every open reservation uncertain. A cancelled provider call
        may still be billed; pretending otherwise understates real spend."""
        self.cancelled = True
        for reservation_id in list(self.open_reservations):
            self.store.settle(reservation_id, settled_usd=None,
                              usage={"cancelled": True}, uncertain=True)
        self.open_reservations.clear()

    # -- no-progress -----------------------------------------------------

    def no_progress_check(self, key: str) -> None:
        """Refuse the same deterministic failure twice.

        The key includes the error CLASS, so a transient environment failure
        is not permanently cached as an invalid query -- a genuine recovery
        can retry, an identical deterministic failure cannot.
        """
        if key and key in self.store.no_progress_keys(self.run_id):
            raise BudgetExceeded(
                NO_PROGRESS,
                "this exact code, parameters, release and failure class were "
                "already tried in this run and nothing about the environment "
                "changed. It was not run again.")

    # -- reporting -------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        spend = self.spend()
        c, lim = self.counters, self.limits
        return {
            "mode": lim.mode,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "remaining_seconds": round(self.remaining_seconds, 3),
            "generation_attempts": [c.generation_attempts,
                                    lim.generation_attempts],
            "provider_attempts": [c.provider_attempts, lim.provider_attempts],
            "execution_submissions": [c.execution_submissions,
                                      lim.execution_submissions],
            "analysis_rounds": [c.analysis_rounds, lim.analysis_rounds],
            "catalog_calls": [c.catalog_calls, lim.catalog_calls],
            "artifact_reads": [c.artifact_reads, lim.artifact_reads],
            "steps_attempted": [c.steps_attempted, lim.total_steps],
            "plan_retries": [c.plan_retries, lim.plan_retries],
            "action_format_recoveries": [c.action_format_recoveries,
                                         self.limits.format_regenerations],
            "answer_format_recoveries": [
                c.answer_format_recoveries,
                self.limits.answer_format_regenerations],
            "format_recoveries": [c.action_format_recoveries,
                                  lim.format_regenerations],
            "answer_corrections": [c.answer_corrections,
                                   lim.answer_corrections],
            "spend": spend,
            "spend_ceiling_usd": lim.spend_ceiling_usd,
            # The EFFECTIVE deadline, which a declared analysis widens. A
            # panel that shows the intake value while the run is working to a
            # different one is telling the reader something untrue.
            "deadline_seconds": lim.deadline_seconds,
            "response_tokens_reserved": lim.reserved_output_tokens,
            # An "enforced" badge is only honest when the price is verified
            # AND nothing is held as an unknown amount.
            "cost_enforced": (self.capability.price is not None
                              and not spend["uncertain"]),
        }


__all__ = ["BudgetExceeded", "Counters", "Ledger"]
