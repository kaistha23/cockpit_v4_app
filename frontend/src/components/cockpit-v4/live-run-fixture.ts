/**
 * The recorded live run `run-f1cf5a5ea98440b384f1ad99a7e1514e`.
 *
 * FIXTURE, from a real Opus run. The backend persisted these fifteen events
 * correctly and the browser rendered "Answered in 0s" with every stage still
 * "not started", because the server sends NAMED SSE frames and the client
 * listened only for `message`.
 *
 * Kept as data, in the order and shape the server produced, so the regression
 * is against what actually happened rather than against what we think happens.
 */

import type { RunEvent, RunStatus } from "./client";

export const LIVE_RUN_ID = "run-f1cf5a5ea98440b384f1ad99a7e1514e";

function event(
  seq: number,
  event_type: string,
  stage: string,
  operation: string,
  status: RunEvent["status"],
  elapsed_ms: number,
  public_message: string,
  attempt = 0,
): RunEvent {
  return {
    schema_version: "v4.1",
    event_id: `ev-${seq}`,
    run_id: LIVE_RUN_ID,
    seq,
    event_type,
    stage,
    operation,
    status,
    occurred_at: "2026-09-10T00:00:00.000Z",
    elapsed_ms,
    attempt,
    submission: 0,
    round: 0,
    public_message,
    detail_ref: `dt-${seq}`,
    error_id: "",
    trace_id: "tr-live",
    span_id: `sp-${seq}`,
    parent_span_id: "",
  };
}

/** The fifteen events, exactly as recorded. */
export const LIVE_RUN_EVENTS: RunEvent[] = [
  event(1, "run.accepted", "accepted", "intake", "ok", 0,
    "Request accepted · release v4-saudi-20q-v1"),
  event(2, "run.started", "accepted", "start", "ok", 416,
    "Working on your question."),
  event(3, "context.ready", "accepted", "context", "ok", 416,
    "Authorized context assembled."),
  event(4, "model.requested", "understanding", "generate", "started", 417,
    "Understanding the request", 1),
  event(5, "model.response_received", "understanding", "generate", "ok", 16632,
    "Response received.", 1),
  event(6, "model.parsed", "understanding", "parse", "ok", 16633,
    "Next action: finalize_response."),
  event(7, "tool.requested", "publishing", "finalize_response", "started",
    16633, "Validating and publishing answer"),
  event(8, "tool.failed", "publishing", "finalize_response", "rejected", 16634,
    "The response did not pass its contract check."),
  event(9, "model.requested", "understanding", "generate", "started", 16635,
    "Preparing the next action", 2),
  event(10, "model.response_received", "understanding", "generate", "ok", 29386,
    "Response received.", 2),
  event(11, "model.parsed", "understanding", "parse", "ok", 29387,
    "Next action: finalize_response."),
  event(12, "tool.requested", "publishing", "finalize_response", "started",
    29387, "Validating and publishing answer"),
  event(13, "intent.validated", "understanding", "intent", "ok", 29388,
    "About CreditProbe · owned by Cockpit"),
  event(14, "answer.validated", "publishing", "answer_check", "ok", 29390,
    "Answer checked against 0 evidence-bound value(s)."),
  event(15, "answer.ready", "publishing", "publish", "ok", 29391,
    "Answer ready."),
];

/** The authoritative status the run settled with. */
export const LIVE_RUN_STATUS: RunStatus = {
  run_id: LIVE_RUN_ID,
  thread_id: "th-live",
  state: "COMPLETED",
  mode: "standard",
  release_id: "v4-saudi-20q-v1",
  error_code: "",
  error_id: "",
  operation: "",
  final_response: {
    disposition: "answer",
    narrative: "CreditProbe is an intelligent credit-investigation layer.",
    intent: {
      query_mode: "PRODUCT_HELP",
      owner: "COCKPIT",
      understood_request: "who this assistant is",
    },
    coverage: [],
    numeric_claims: [],
    tables: [],
    charts: [],
    limitations: [],
    suggested_questions: [],
    clarification_question: "",
    clarification_options: [],
    referral_owner: "",
    referral_reason: "",
    executed: false,
  } as unknown as RunStatus["final_response"],
  budget: { elapsed_seconds: 29.391 },
  terminal: true,
  last_event_seq: 15,
  delivered_at: "",
};
