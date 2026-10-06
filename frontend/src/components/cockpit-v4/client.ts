/**
 * The V4 browser client: start, watch, reconnect, cancel, acknowledge.
 *
 * Two rules this file exists to keep.
 *
 * A disconnect does not cancel paid work, and a reconnect never resubmits the
 * question. `watch` reconnects to the SAME run with the last sequence it saw
 * and replays the committed events it missed. There is no path here that
 * POSTs /runs again because a stream dropped.
 *
 * Connection loss is a CLIENT state. After the bounded reconnect attempts are
 * spent, the panel shows CONNECTION_LOST with the last stage it actually saw
 * and offers a status re-read. It never renders that as a server failure,
 * because it does not know that the server failed.
 */

export type RunEvent = {
  schema_version: string;
  event_id: string;
  run_id: string;
  seq: number;
  event_type: string;
  stage: string;
  operation: string;
  status: "started" | "ok" | "failed" | "rejected";
  occurred_at: string;
  elapsed_ms: number;
  attempt: number;
  submission: number;
  round: number;
  public_message: string;
  detail_ref: string;
  error_id: string;
  trace_id: string;
  span_id: string;
  parent_span_id: string;
  /**
   * The stage state machine, decided by the server. §33-§36.
   *
   * `stage_instance_id` names WHICH RUN of the stage this event belongs to,
   * because a run can re-enter `preparing` after a failed submission and two
   * passes through it are two things that happened. `closed_stages` lists
   * the instances this event ended -- usually the one it displaced, and on a
   * terminal event its own as well, so a browser that reconnects after the
   * last frame is never left with a stage spinning.
   *
   * Optional so a stream from an older server still renders.
   */
  stage_instance_id?: string;
  stage_started_ms?: number;
  stage_state?: "running" | "done" | "failed";
  stage_failures?: number;
  closed_stages?: ClosedStage[];
};

/** One stage instance, as it ended. */
export type ClosedStage = {
  stage: string;
  stage_instance_id: string;
  started_ms: number;
  ended_ms: number;
  failures?: number;
  state: "done" | "failed";
};

export type RunStatus = {
  run_id: string;
  thread_id: string;
  state: string;
  mode: string;
  release_id: string;
  provider?: string;
  model?: string;
  error_code: string;
  error_id: string;
  operation: string;
  final_response: FinalResponse | null;
  budget: Record<string, unknown>;
  terminal: boolean;
  last_event_seq: number;
  delivered_at: string;
};

export type FinalResponse = {
  disposition:
    | "answer"
    | "partial_answer"
    | "referral"
    | "clarification"
    | "unsupported"
    | "safe_failure";
  narrative: string;
  intent: { query_mode: string; owner: string; understood_request: string };
  coverage: { subquestion: string; status: string }[];
  numeric_claims: {
    claim_id: string;
    /** The analyst's optional cross-check, at full precision. Not for display. */
    decimal_value?: string;
    /** What CreditProbe published for a reader: rounded, in this unit. */
    display_value?: string;
    unit: string;
    display_precision: number;
    /** Present on a DIRECT claim: the one result cell it was read from. */
    evidence?: { artifact_id: string; row_key: string; column_id: string };
    /** Present on a DERIVED claim -- a total, a share, a movement. The
     *  server recomputed this value from the cells named here before the
     *  answer was allowed to publish. */
    derivation?: {
      operation: string;
      /*
        An operand names its cells one of two ways and never both: every row
        of the result (`rows: "all"`), or the ids it means. Both are optional
        here because only one of them is ever present.
      */
      operands: {
        artifact_id: string;
        column_id: string;
        row_ids?: string[];
        rows?: string;
      }[];
    };
  }[];
  /**
   * Tables and charts as the SERVER rendered them.
   *
   * The analyst chose the table -- its title, which result, which columns.
   * Every value in `rows`/`points` came out of the stored artifact and was
   * formatted by the one display policy, with the canonical value carried
   * beside the displayed one. Nothing here passed through the model, and
   * the frontend does no arithmetic and no rounding of its own.
   */
  tables: RenderedTable[];
  charts: RenderedChart[];
  limitations: string[];
  suggested_questions: { question: string; kind: string }[];
  clarification_question: string;
  clarification_options: string[];
  referral_owner: string;
  referral_reason: string;
  executed: boolean;
  /**
   * True when CreditProbe published the stored result on its own because the
   * written answer could not be produced. The narrative, the tables and the
   * limitations on such a response are server-written or server-rendered;
   * no model text and no model-supplied number reaches the reader.
   */
  result_only?: boolean;
  /** Why the written answer could not be produced, in a reader's words. */
  result_only_reason?: string;
  validation?: { warnings: string[] };
};

/** One published row: what the arithmetic used, and what a reader sees. */
export type RenderedRow = {
  row_id: string;
  canonical: Record<string, unknown>;
  display: Record<string, unknown>;
};

export type RenderedTable = {
  title: string;
  artifact_id: string;
  columns: string[];
  /** Absent when the server could not resolve the artifact. */
  rows?: RenderedRow[];
  row_count?: number;
  column_units?: Record<string, string>;
  rendered_by?: string;
  note?: string;
};

export type ChartPoint = {
  row_id: string;
  label: unknown;
  values: Record<string, unknown>;
  display: Record<string, unknown>;
};

/**
 * A from/to result as the grid it is, built by the server.
 *
 * `cells` is keyed `row|column`. A pair the query never reported is ABSENT
 * rather than zero: an empty cell means "nobody made that move", and a zero
 * would assert something the result never said.
 */
export type ChartMatrix = {
  row_axis: string;
  column_axis: string;
  measure: string;
  unit: string;
  rows: string[];
  columns: string[];
  square: boolean;
  cells: Record<string, number | null>;
  display: Record<string, string>;
};

/** The five numbers of one box, computed server-side like every figure. */
export type ChartBox = {
  label: string;
  count: number;
  unit: string;
  minimum: string;
  q1: string;
  median: string;
  q3: string;
  maximum: string;
  display: Record<string, string>;
};

/**
 * One number on an axis, chosen AND WRITTEN by the server.
 *
 * `value` positions the tick; `display` is what the reader sees, written
 * by the same display policy that wrote every claim in the answer. The
 * browser must never derive the second from the first -- a figure rounded
 * twice by two implementations is a figure the bank cannot defend.
 */
export type ChartTick = { value: number; display: string };

/**
 * The scale a chart is read against.
 *
 * `kind: "category"` carries no ticks: its categories ARE the points, and
 * a second copy of those strings would be free to drift from the first.
 */
export type ChartAxis = {
  kind: "measure" | "category";
  /** The catalogue's business name -- "Exposure at default", not `ead_sar_mn`. */
  label: string;
  column: string;
  unit: string;
  ticks: ChartTick[];
};

export type RenderedChart = {
  kind: "bar" | "line" | "waterfall" | "scatter" | "heatmap" | "box" | string;
  title: string;
  artifact_id: string;
  x_column: string;
  y_columns: string[];
  /** The second categorical axis: a heatmap's ROW axis. Unused elsewhere. */
  series_column?: string;
  unit: string;
  points?: ChartPoint[];
  series_units?: Record<string, string>;
  /** Present on a heatmap. Built beside the points, never from them. */
  matrix?: ChartMatrix;
  /** Present on a box plot, one entry per box. */
  boxes?: ChartBox[];
  /**
   * The scales, computed by the server.
   *
   * Optional because a saved thread holds answers rendered before they
   * existed, and a reader's own history must keep drawing. A form with no
   * scale -- a pie, which divides a whole -- carries none by design.
   */
  x_axis?: ChartAxis;
  y_axis?: ChartAxis;
  /** A combo's second scale: the rate, over the volumes it is a rate of. */
  y_axis_secondary?: ChartAxis;
  /**
   * Why this FORM, in the analyst's own words.
   *
   * Optional twice over: the contract does not require it, and a saved
   * thread holds answers written before the field existed. When it is
   * absent nothing is shown -- a caption the browser wrote would read as
   * the analyst's reasoning and be evidence of nothing.
   */
  why_this_chart?: string;
  rendered_by?: string;
};

export const API_PREFIX = "/api/v1/cockpit-v4";

/**
 * Every event type the backend emits, by name.
 *
 * This list is load-bearing, not documentation. The server sends NAMED SSE
 * frames (`event: run.accepted`), and `EventSource.onmessage` fires only for
 * frames whose name is `message` or absent. Without a listener per name the
 * browser silently discards every analytical event and keeps only the ones it
 * was explicitly told to listen for — which is exactly how a run with fifteen
 * correctly persisted events rendered as "Answered in 0s" with every stage
 * still marked "not started".
 *
 * Kept in step with `backend/cockpit_v4/events.py:EVENT_TYPES`; a test
 * asserts the two agree, so a new backend event cannot go unrendered.
 */
export const EVENT_TYPES = [
  "run.accepted",
  "run.started",
  "context.ready",
  "model.requested",
  "model.response_received",
  "model.parsed",
  "intent.validated",
  "tool.requested",
  "tool.validated",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "retry.requested",
  "answer.validated",
  "answer.ready",
  // The analysis finished and its result was kept, but the written answer
  // could not be published. Distinct from run.failed so the panel does not
  // report a successful query as a failed one.
  "analysis.preserved",
  "run.failed",
  "run.cancelled",
  "run.expired",
  "run.interrupted",
  "memory.started",
  "memory.completed",
  "memory.failed",
] as const;

export type RunMode = "standard" | "deep";

/**
 * Where the active run is remembered across a refresh.
 *
 * A run is durable on the server; reloading the page must not orphan it. The
 * id and the last sequence the browser actually rendered are kept here, so a
 * refresh reconnects to the SAME run from where it left off rather than
 * re-asking the question and paying for it twice.
 *
 * `sessionStorage` rather than `localStorage`: this is about one tab's current
 * work, and a second tab opening onto a run it never started would show
 * progress the reader did not ask for.
 */
const ACTIVE_RUN_KEY = "cockpit-v4:active-run";

export type ActiveRun = {
  runId: string;
  threadId: string;
  cursor: number;
  /**
   * The question this run is answering.
   *
   * Carried so a thread resumed after a refresh can show the user's own
   * words above the process panel. Without it the turn renders blank while
   * the run it belongs to is visibly working, which reads as a bug in the
   * conversation rather than as a reload.
   */
  question?: string;
};

export function rememberRun(run: ActiveRun): void {
  try {
    sessionStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify(run));
  } catch {
    /* a browser that refuses storage loses replay, not the run */
  }
}

export function recallRun(): ActiveRun | null {
  try {
    const raw = sessionStorage.getItem(ACTIVE_RUN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ActiveRun>;
    if (typeof parsed.runId !== "string" || !parsed.runId) return null;
    return {
      runId: parsed.runId,
      threadId: typeof parsed.threadId === "string" ? parsed.threadId : "",
      cursor: typeof parsed.cursor === "number" ? parsed.cursor : 0,
      question: typeof parsed.question === "string" ? parsed.question : "",
    };
  } catch {
    return null;
  }
}

export function forgetRun(): void {
  try {
    sessionStorage.removeItem(ACTIVE_RUN_KEY);
  } catch {
    /* nothing to clean up */
  }
}

/**
 * Where an open investigation is remembered across a refresh.
 *
 * The SEED lives on the server, attached to the thread; this is only the
 * pointer to it, so a reload comes back to the same conversation instead of
 * dropping the reader into a blank Ask box after they clicked a card.
 */
const ACTIVE_INVESTIGATION_KEY = "cockpit-v4:active-investigation";

export type RememberedInvestigation = {
  threadId: string;
  itemId: string;
  suggested: string[];
};

export function rememberInvestigation(open: RememberedInvestigation): void {
  try {
    sessionStorage.setItem(ACTIVE_INVESTIGATION_KEY, JSON.stringify(open));
  } catch {
    /* a browser that refuses storage loses the banner, not the thread */
  }
}

export function recallInvestigation(): RememberedInvestigation | null {
  try {
    const raw = sessionStorage.getItem(ACTIVE_INVESTIGATION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<RememberedInvestigation>;
    if (typeof parsed.threadId !== "string" || !parsed.threadId) return null;
    if (typeof parsed.itemId !== "string" || !parsed.itemId) return null;
    return {
      threadId: parsed.threadId,
      itemId: parsed.itemId,
      suggested: Array.isArray(parsed.suggested)
        ? parsed.suggested.filter((s): s is string => typeof s === "string")
        : [],
    };
  } catch {
    return null;
  }
}

export function forgetInvestigation(): void {
  try {
    sessionStorage.removeItem(ACTIVE_INVESTIGATION_KEY);
  } catch {
    /* nothing to clean up */
  }
}

/** One item with its full technical evidence. Used to restore after a reload. */
export async function readAttentionItem(
  itemId: string,
): Promise<{ item: AttentionItem }> {
  return json(
    await fetch(
      `${base()}${API_PREFIX}/attention/${encodeURIComponent(itemId)}`,
      { credentials: "include" },
    ),
  );
}

/** Bounded backoff, then a status fallback. Not an unlimited retry loop. */
export const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 8000, 8000];

/**
 * Raised when the V4 API address is not configured.
 *
 * Deliberately an error rather than a default. `lib/api.ts` falls back to
 * `http://127.0.0.1:8000` when its own variable is unset, which is right for
 * the main backend and wrong for this one: silently sending a Cockpit V4 run
 * to the legacy backend produces a 404 that looks like a Cockpit fault, and
 * on a machine where something IS listening on 8000 it would send an
 * authenticated question to a service that never agreed to receive it.
 */
export class CockpitV4NotConfigured extends Error {
  constructor() {
    super(
      "NEXT_PUBLIC_COCKPIT_V4_API is not set, so this build does not know " +
        "where the Cockpit V4 API is. It is deliberately NOT assumed: the " +
        "V4 API runs on its own port and must never be confused with the " +
        "main CreditProbe backend. Start the Cockpit with " +
        "scripts/cockpit_v4/START_COCKPIT_V4.command, which sets it to the " +
        "port it actually selected.",
    );
    this.name = "CockpitV4NotConfigured";
  }
}

/**
 * The V4 API origin.
 *
 * Reads ONE variable. It never consults `NEXT_PUBLIC_API_URL`, and it has no
 * numeric default — the two rules that together make a silent fall back to
 * the legacy backend impossible rather than merely unlikely.
 *
 * `same-origin` is the explicit opt-in for a deployment that proxies the V4
 * API through the page's own origin. Spelling it out is the point: an empty
 * string reached by accident and an empty string chosen on purpose behave
 * identically at runtime and mean opposite things to a reviewer.
 */
function base(): string {
  const configured = process.env.NEXT_PUBLIC_COCKPIT_V4_API?.trim();
  if (!configured) throw new CockpitV4NotConfigured();
  if (configured === "same-origin") return "";
  return configured.replace(/\/$/, "");
}

/**
 * Is this build running as a Cockpit V4 runtime?
 *
 * Read at module scope rather than in a hook, because the answer decides WHICH
 * page component is instantiated. A hook-based check would have to run inside
 * the legacy page — and by then its own hooks have already fired off the
 * legacy requests this mode exists to avoid.
 */
export function cockpitV4Enabled(): boolean {
  return Boolean(process.env.NEXT_PUBLIC_COCKPIT_V4_API?.trim());
}

/** The configured origin, or a reason it is unusable. For diagnostics. */
export function apiOrigin(): { ok: true; origin: string } | {
  ok: false;
  reason: string;
} {
  try {
    return { ok: true, origin: base() };
  } catch (error) {
    return {
      ok: false,
      reason:
        error instanceof Error ? error.message : "The V4 API is not configured.",
    };
  }
}

async function json<T>(response: Response): Promise<T> {
  const text = await response.text();
  if (!response.ok) {
    let detail: unknown = text;
    try {
      detail = JSON.parse(text);
    } catch {
      /* the body was not JSON; the text is the detail */
    }
    throw Object.assign(new Error(`request failed (${response.status})`), {
      status: response.status,
      detail,
    });
  }
  return JSON.parse(text) as T;
}

/**
 * Open a conversation in a book.
 *
 * The domain is sent HERE, not left to the first run to settle. A thread
 * created without one was stamped with the default book, so a reader on
 * Retail opened a Corporate conversation -- and the questions it offered
 * them before their first message were the Corporate book's.
 */
export async function createThread(
  domain?: DomainId,
): Promise<{ thread_id: string; domain_id?: DomainId }> {
  return json(
    await fetch(`${base()}${API_PREFIX}/threads`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(domain ? { domain } : {}),
    }),
  );
}

export type AvailableModel = {
  provider: string;
  model: string;
  label: string;
  available: boolean;
  qualified: boolean;
  reason?: string;
};

export async function readModels(): Promise<{
  models: AvailableModel[];
  selection: { provider: string; model: string } | null;
}> {
  return json(await fetch(`${base()}${API_PREFIX}/models`, { credentials: "include" }));
}

export async function setModelSetting(provider: string, model: string): Promise<void> {
  await json(await fetch(`${base()}${API_PREFIX}/settings/model`, {
    method: "PUT", credentials: "include", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model }),
  }));
}

export async function startRun(input: {
  question: string;
  threadId?: string;
  mode?: RunMode;
  /** Which book. Read only when OPENING a conversation: a thread that
   *  already exists decides for itself, and a request naming the other book
   *  is refused with 409 rather than obeyed or ignored. */
  domain?: DomainId;
  filters?: Record<string, unknown>;
  idempotencyKey?: string;
  provider?: string;
  model?: string;
}): Promise<{ run_id: string; thread_id: string; duplicate: boolean }> {
  return json(
    await fetch(`${base()}${API_PREFIX}/runs`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(input.idempotencyKey
          ? { "Idempotency-Key": input.idempotencyKey }
          : {}),
      },
      body: JSON.stringify({
        question: input.question,
        thread_id: input.threadId ?? "",
        mode: input.mode ?? "standard",
        domain: input.domain ?? "",
        ui_filters: input.filters ?? {},
        provider: input.provider ?? "",
        model: input.model ?? "",
      }),
    }),
  );
}

export async function readStatus(runId: string): Promise<RunStatus> {
  return json(
    await fetch(`${base()}${API_PREFIX}/runs/${runId}`, {
      credentials: "include",
    }),
  );
}

export async function cancelRun(runId: string): Promise<void> {
  await fetch(`${base()}${API_PREFIX}/runs/${runId}/cancel`, {
    method: "POST",
    credentials: "include",
  });
}

/** Acknowledge that the answer was actually RENDERED, not merely received. */
export async function acknowledge(runId: string): Promise<void> {
  await fetch(`${base()}${API_PREFIX}/runs/${runId}/delivered`, {
    method: "POST",
    credentials: "include",
  }).catch(() => {
    /* a failed acknowledgement must never change the analytical status */
  });
}

// ---- who is asking, and what they can reopen ---------------------------

export type RecentThread = {
  thread_id: string;
  /** What was asked in it, or the name someone gave it. */
  title: string;
  turns: number;
  last_activity_at: string;
  last_question: string;
  last_disposition: string;
  origin: string;
  attention_item: string;
  segment: string;
  /** Which book it is in. Reopening a conversation puts the reader back in
   *  that book -- a thread reopened under the other one refuses their next
   *  question for a reason that has nothing to do with what they typed. */
  domain_id?: DomainId;
  release_id?: string;
};

export type SessionSummary = {
  /** A person's name, or "". Never a deployment or profile label. */
  display_name: string;
  /** "Local UAT", a service-account name -- shown in the operator view,
   *  never used to greet anybody. */
  profile_label?: string;
  tenant: string;
  release_id: string;
  recent_threads: RecentThread[];
};

export async function readSession(): Promise<SessionSummary> {
  return json(
    await fetch(`${base()}${API_PREFIX}/session`, { credentials: "include" }),
  );
}

export type ThreadTurn = {
  turn_id: string;
  /** The run that produced this turn. Without it a past turn has no trace. */
  run_id: string;
  ordinal: number;
  question: string;
  answer: FinalResponse & Record<string, unknown>;
  created_at: string;
};

export type ThreadTranscript = {
  domain_id: DomainId;
  domain_label: string;
  domain_short_label: string;
  thread_id: string;
  title: string;
  /** EVERY turn, oldest first. Not a window onto the last few. */
  turns: ThreadTurn[];
  turn_count: number;
  created_at: string;
  context: { kind?: string; body?: Record<string, unknown> };
  /**
   * What to ask, before anybody has asked anything.
   *
   * Served only while the thread is empty. A thread seeded from an attention
   * card carries that card's own five questions; any other thread carries
   * the book's deterministic openers. Both are computed server-side from the
   * release, so neither costs a model call and neither can name a field the
   * book does not hold.
   */
  opening_questions?: { question: string; kind: string }[];
  /** What the numbers in this conversation mean. */
  release?: Record<string, unknown>;
};

export async function readThread(
  threadId: string,
): Promise<ThreadTranscript> {
  return json(
    await fetch(
      `${base()}${API_PREFIX}/threads/${encodeURIComponent(threadId)}`,
      { credentials: "include" },
    ),
  );
}

export type RunTrace = {
  run_id: string;
  thread_id: string;
  question: string;
  state: string;
  mode: string;
  error_code: string;
  created_at: string;
  events: RunEvent[];
  budget: Record<string, unknown>;
  release?: Record<string, unknown>;
};

/**
 * What a past run did. §28.
 *
 * The committed events -- the same material the live stream delivered --
 * plus what the run cost and which release answered it. No model call, and
 * no model reasoning: public operations and their outcomes only.
 */
export async function readTrace(runId: string): Promise<RunTrace> {
  return json(
    await fetch(
      `${base()}${API_PREFIX}/runs/${encodeURIComponent(runId)}/trace`,
      { credentials: "include" },
    ),
  );
}

/** Rename a conversation. Tenant-checked server-side; a URL is not access. */
export async function renameThread(
  threadId: string,
  title: string,
): Promise<{ thread_id: string; title: string }> {
  return json(
    await fetch(
      `${base()}${API_PREFIX}/threads/${encodeURIComponent(threadId)}/title`,
      {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      },
    ),
  );
}

// ---- the Cockpit home feed ---------------------------------------------

export type AttentionNumber = {
  label: string;
  value: string;
  raw: number | null;
};

export type AttentionDriver = {
  relationship: string;
  statement: string;
  metric: string;
  delta: number | null;
  strength: number;
};

export type AttentionDrilldown = {
  /** How many counterparties the segment holds, and what to call them.
   *
   * The book decides the noun: a corporate sector holds borrowers, a retail
   * product holds customers. The page must not name one of them.
   */
  entity_count: number;
  entity_label: string;
  available: string[];
  unavailable: string[];
  note: string;
  /** The same number under the name the drawer used before there were two
   * books. Kept so an existing reader of this payload is not broken. */
  borrower_count: number;
  suggested_questions: unknown[];
};

export type AttentionItem = {
  item_id: string;
  section: string;
  /** "segment" | "portfolio" | "borrower" — what the item is ABOUT. */
  scope: string;
  headline: string;
  one_line: string;
  /** The book's own identifier for the segment: `asset_finance`. The seed
   *  filters on it, so it stays exactly as the release spells it. */
  segment: string;
  /** The same segment written for a reader: `Asset Finance`. Shown; never
   *  filtered on. */
  segment_label?: string;
  segment_dimension: string;
  metric: string;
  metric_label: string;
  family: string;
  /** The period this card reports, whatever kind of period this book keeps.
   *  Read THIS. The `reporting_quarter` / `reporting_month` pair below is
   *  served for older readers and only ONE of them is ever filled -- the one
   *  belonging to this book's calendar -- so indexing the wrong one silently
   *  yields an empty string rather than a wrong date. */
  reporting_period: string;
  comparison_period: string;
  /** "quarter" for the Corporate book, "month" for Retail. */
  period_noun: string;
  reporting_quarter?: string;
  reporting_month?: string;
  comparison_quarter?: string;
  comparison_month?: string;
  comparison_basis: string;
  movement: string;
  severity: string;
  score: number | null;
  why_it_appeared: string;
  what_changed: string;
  key_numbers: AttentionNumber[];
  possible_drivers: AttentionDriver[];
  what_to_review_next: string[];
  evidence: Record<string, unknown>;
  evidence_url: string;
  drilldown?: AttentionDrilldown;
};

export type AttentionFeed = {
  domain_id: DomainId;
  domain_label: string;
  attention_label: string;
  highlights_label: string;
  release_id: string;
  release_fingerprint: string;
  /** The feed's own period, in this book's calendar. */
  reporting_period: string;
  comparison_period: string;
  period_noun: string;
  reporting_quarter?: string;
  reporting_month?: string;
  comparison_quarter?: string;
  comparison_month?: string;
  comparison_basis?: string;
  reporting_currency: string;
  generated_at: string;
  computed_ms?: number;
  model_calls: number;
  cached: boolean;
  segments_requiring_attention: AttentionItem[];
  ecl_highlights: AttentionItem[];
  segment_note: string;
  ownership?: { functionality: string; basis: string; note: string };
};

export type InvestigationSeed = {
  item_id: string;
  origin: string;
  release_id: string;
  headline: string;
  segment: string;
  reporting_period: string;
  comparison_period: string;
  period_noun?: string;
  reporting_quarter?: string;
  comparison_quarter?: string;
  metric_label: string;
  drilldown?: AttentionDrilldown;
};

/** Which analytical book a question is about. Closed set, server-checked. */
export type DomainId = "corporate" | "retail";

export type DomainStatus = {
  domain_id: DomainId;
  domain_label: string;
  release_id: string;
  ready: boolean;
  /** Published and browsable. Not the same as answerable -- a book can be
   *  read in Data Builder before the analytical path can query it. */
  analysis_ready?: boolean;
  reason?: string;
  latest_period?: string;
  periods?: string[];
  /** Where the book reports from, as the release published it. */
  country?: string;
  reporting_currency?: string;
  amount_scale?: string;
  reporting_frequency?: string;
  /** What one period IS in this book: "quarter" or "month". Stated by the
   *  release, so no reader has to infer it from the period's shape. */
  period_noun?: string;
  period_column?: string;
  relation_count?: number;
  field_count?: number;
  row_counts?: Record<string, number>;
  entity_counts?: Record<string, number>;
  release_fingerprint?: string;
  provision_command?: string;
};

export type DomainAvailability = {
  domains: DomainStatus[];
  default_domain: DomainId;
  ready: DomainId[];
  analysis_domains?: DomainId[];
};

/** The books this runtime serves, and whether each can be asked anything.
 *
 * Read once when the page opens. One book being unpublished disables its
 * control and says why; it never makes the other stand in for it.
 */
export async function readDomains(): Promise<DomainAvailability> {
  return json(
    await fetch(`${base()}${API_PREFIX}/domains`, { credentials: "include" }),
  );
}

/** One book's home sections. Deterministic, cached server-side, no model call.
 *
 * The domain is a QUERY, not a filter applied to a shared answer: the server
 * opens that book's release and computes its own dashboard, so switching
 * changes the numbers rather than the labels over them.
 */
export async function readAttention(
  domain?: DomainId,
): Promise<AttentionFeed> {
  const query = domain ? `?domain=${encodeURIComponent(domain)}` : "";
  return json(
    await fetch(`${base()}${API_PREFIX}/attention${query}`, {
      credentials: "include",
    }),
  );
}

export type SchemaField = {
  name: string;
  /** What a person calls this column. The `name` is what SQL filters on. */
  label?: string;
  dtype: string;
  unit: string;
  aggregation: string;
  group: string;
  definition: string;
  /** A CLOSED set: the column may hold these values and no others, so a
   *  reader may filter on any of them. Present only for category columns. */
  governed_values?: string[];
  /** The same values written for a reader: `project_finance` → `Project
   *  Finance`. Keyed by the governed value. */
  value_labels?: Record<string, string>;
  distinct_values?: number;
  /** A handful of real values from a column that is NOT a closed set --
   *  shown so the shape is visible, never to be read as the whole list. */
  sample_values?: string[];
  distinct_values_at_least?: number;
};

/** A heading this book's columns are organised under. */
export type SubjectArea = {
  area: string;
  columns: number;
  relations: string[];
};

export type SchemaRelation = {
  relation: string;
  name: string;
  grain: string;
  about: string;
  description: string;
  period_column: string;
  key_columns: string[];
  columns: number;
  rows: number;
};

export type SchemaJoin = {
  left: string;
  right: string;
  on: string[];
  cardinality?: string;
  note?: string;
  warning?: string;
};

export type BookSchema = {
  domain_id: DomainId;
  domain_label: string;
  release_id: string;
  release_fingerprint: string;
  country?: string;
  country_name?: string;
  reporting_currency: string;
  amount_scale: string;
  reporting_frequency: string;
  /** "quarter" or "month". This book's, never assumed. */
  period_noun?: string;
  period_column?: string;
  reporting_periods: string[];
  earliest_period?: string;
  latest_period: string;
  previous_period?: string;
  period_count?: number;
  /** Borrowers and facilities, or customers and accounts. */
  entity_counts?: Record<string, number>;
  status?: string;
  not_client_data?: string;
  geography_name?: string;
  subject_areas?: SubjectArea[];
  relations?: SchemaRelation[];
  joins?: SchemaJoin[];
  total_rows?: number;
  note?: string;
  /** Present only when one relation was asked for. Note that
   *  `period_column` above is the BOOK's period column and is the same
   *  value, which is why it is not repeated here. */
  relation?: string;
  grain?: string;
  description?: string;
  key_columns?: string[];
  rows?: number;
  fields?: SchemaField[];
};

/** What one book contains. Without a relation, the shape; with one, the
 * columns.
 *
 * The domain is a query, not a filter: each book publishes a different
 * release with different relations, and asking for the other book's relation
 * is refused rather than answered from this one.
 */
export async function readSchema(
  domain: DomainId,
  relation?: string,
): Promise<BookSchema> {
  const query = relation
    ? `?domain=${encodeURIComponent(domain)}&relation=${encodeURIComponent(relation)}`
    : `?domain=${encodeURIComponent(domain)}`;
  return json(
    await fetch(`${base()}${API_PREFIX}/schema${query}`, {
      credentials: "include",
    }),
  );
}

/**
 * The governance record: how this question became a number.
 *
 * Every field is a STORED fact assembled by the server. Nothing in it is
 * computed in the browser and nothing in it may be: the whole point of
 * the record is that it is what happened, not a reconstruction of it.
 */
export type GovernanceRecord = {
  run_id: string;
  thread_id: string;
  state: string;
  mode: string;
  error_code: string;
  created_at: string;
  question: {
    /** As typed. */
    asked: string;
    /** As analysed -- the same sentence unless normalisation touched it. */
    analysed: string;
    changed: boolean;
    normalisation: { applied?: string[]; changed?: boolean };
    /**
     * Said plainly, because the obvious assumption is the wrong one: a
     * question is NEVER spell-corrected or rewritten. What normalisation
     * does is mechanical -- Unicode composition, zero-width removal,
     * whitespace folding.
     */
    policy: string;
  };
  interpretation: {
    intent_id: string;
    understood_request: string;
    query_mode: string;
    owner: string;
    domain_id: string;
    domain_label: string;
    period: Record<string, unknown>;
    canonical_mappings: string[];
    resolved_assumptions: string[];
    blocking_ambiguities: string[];
    excluded_parts: string[];
    open_questions: Record<string, unknown>[];
    /**
     * What the reader's own words resolved to. THIS is the correction
     * that actually happens -- a phrase to a governed value, never a
     * spelling fix on the sentence.
     */
    value_resolutions: {
      you_typed: string;
      resolved_to: string;
      on_field: string;
      relation: string;
      exact: boolean;
      recorded_as: string;
    }[];
    rationale: string;
  };
  submissions: {
    ordinal: number;
    submission_id: string;
    round: number;
    status: string;
    /** False for a submission the binder refused, which never executed. */
    ran: boolean;
    refusal: { stage: string; error_code: string; field: string;
               message: string } | null;
    objective: string;
    subquestions: string[];
    fields_required: string[];
    expected_grain: string;
    expected_units: Record<string, string>;
    repair_of: string;
    bind_proof: { bound_now?: string[]; bound_at_run_time?: string[] };
    failed_step: string;
    created_at: string;
    steps: {
      step_id: string;
      purpose: string;
      language: string;
      /** The query, or the sentence saying it is withheld. */
      code: string;
      code_shown: boolean;
      parameters: Record<string, unknown>;
      depends_on: string[];
      input_artifact_ids: string[];
      artifact_id: string;
      rows_out: number | null;
      columns_out: string[];
      code_digest: string;
      /** What the query READ, not the whole authorized set. */
      relations_read: string[];
      relations_authorized: string[];
      complete: boolean | null;
      failed: { phase: string; executed: boolean; failed_check: string;
                error_code: string; message: string } | null;
    }[];
  }[];
  waterfall: {
    release: Record<string, unknown>;
    artifacts: {
      artifact_id: string;
      step_id: string;
      language: string;
      relations_read: string[];
      release_id: string;
      release_fingerprint: string;
      columns: string[];
      rows: number | null;
      complete: boolean | null;
      code_digest: string;
      published_as: { kind: string; title: string; index?: number;
                      form?: string; rows_shown?: number; points?: number;
                      x_axis?: string; y_axis?: string;
                      why_this_chart?: string }[];
    }[];
    claims: {
      claim_id: string;
      published: string;
      unit: string;
      from_cell: { artifact_id: string; row_key: string;
                   column_id: string } | null;
      from_arithmetic: Record<string, unknown> | null;
    }[];
    /** Where provenance stops, stated rather than implied. */
    limit: string;
  };
  validation: { warnings: string[]; limitations: string[];
                claims_checked: number };
  cost: Record<string, unknown>;
  /** Whether this reader may see the queries. */
  sql_visible: boolean;
  /** Why they may not, when they may not. Empty otherwise. */
  sql_policy: string;
};

export async function readGovernance(runId: string): Promise<GovernanceRecord> {
  return json(
    await fetch(
      `${base()}${API_PREFIX}/runs/${encodeURIComponent(runId)}/governance`,
      { credentials: "include" },
    ),
  );
}

/** Where an export of this run can be fetched from.
 *
 * Links rather than fetch-and-blob: the browser downloads the document
 * straight from the API with the session cookie it already has, so nothing
 * about the file passes through this code and nothing here can reformat a
 * figure on its way out.
 */
export function exportLinks(runId: string): {
  analysis: string;
  table: (artifactId: string, rows?: "all" | "displayed") => string;
  chart: (index: number) => string;
  /** The governance pack: the document, the JSON and the stored rows. */
  governance: string;
} {
  const root = `${base()}${API_PREFIX}/runs/${encodeURIComponent(runId)}`;
  return {
    analysis: `${root}/export`,
    table: (artifactId, rows = "all") =>
      `${root}/artifacts/${encodeURIComponent(artifactId)}/export?rows=${rows}`,
    chart: (index) => `${root}/charts/${index}/export`,
    governance: `${root}/governance/export`,
  };
}

export type EclStage = {
  stage: number;
  label: string;
  ead: number;
  ecl: number;
  display_ead: string;
  display_ecl: string;
  exposures: number;
  share_of_ead: number;
  display_share_of_ead: string;
  coverage: number;
  display_coverage: string;
  display_ead_movement: string;
  display_ecl_movement: string;
};

export type EclProfile = {
  domain_id: DomainId;
  domain_label: string;
  release_id: string;
  release_fingerprint: string;
  reporting_period: string;
  comparison_period: string;
  period_noun: string;
  reporting_month?: string;
  comparison_month?: string;
  money_unit: string;
  exposure_grain: string;
  relation: string;
  display_total_ead: string;
  display_total_ecl: string;
  display_coverage: string;
  display_ecl_movement: string;
  stages: EclStage[];
  note: string;
  model_calls: number;
};

export type EclComponent = {
  component_id: string;
  label: string;
  amount: number;
  display_amount: string;
  exposures: number;
  explanation: string;
};

export type EclDecomposition = {
  domain_id: DomainId;
  domain_label: string;
  reporting_period: string;
  comparison_period: string;
  period_noun: string;
  reporting_month?: string;
  comparison_month?: string;
  display_opening: string;
  display_closing: string;
  display_movement: string;
  movement: number;
  components: EclComponent[];
  residual: number;
  reconciles: boolean;
  method: string;
  model_calls: number;
};

export type EclPanel = {
  /** Which book this panel is about, at the top level. A caller that has
   *  to reach into `profile.domain_id` to find out is a caller that
   *  eventually does not. */
  domain_id?: DomainId;
  domain_label?: string;
  release_id?: string;
  release_fingerprint?: string;
  reporting_frequency?: string;
  period_noun?: string;
  profile: EclProfile;
  decomposition: EclDecomposition;
};

/** Where this book's ECL is, and what moved it. Server-computed, no model.
 *
 * Every figure arrives as a STRING the server already formatted, alongside
 * the number it was formatted from. The page renders the string; it never
 * decides a currency, a scale or a decimal place.
 */
export async function readEcl(domain: DomainId): Promise<EclPanel> {
  return json(
    await fetch(`${base()}${API_PREFIX}/ecl?domain=${encodeURIComponent(domain)}`, {
      credentials: "include",
    }),
  );
}

/** Open a thread seeded with this item, so follow-ups keep its context. */
export async function investigate(itemId: string): Promise<{
  thread_id: string;
  item_id: string;
  seed: InvestigationSeed;
  suggested_questions: string[];
}> {
  return json(
    await fetch(
      `${base()}${API_PREFIX}/attention/${encodeURIComponent(itemId)}/investigate`,
      { method: "POST", credentials: "include" },
    ),
  );
}

export type WatchHandlers = {
  onEvent: (event: RunEvent) => void;
  onSettled: (status: RunStatus) => void;
  onConnectionState: (state: "open" | "retrying" | "lost") => void;
};

/**
 * Follow one run. Returns a stop function.
 *
 * `EventSource` cannot send a Last-Event-ID header on the first connect, so
 * the cursor is passed as a query parameter and the header is used by the
 * browser on its own automatic reconnects. Both resume from committed events;
 * neither invents a missing one.
 */
export function watch(
  runId: string,
  handlers: WatchHandlers,
  startCursor = 0,
): () => void {
  let cursor = startCursor;
  let attempt = 0;
  let stopped = false;
  let source: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    if (stopped) return;
    source = new EventSource(
      `${base()}${API_PREFIX}/runs/${runId}/events?cursor=${cursor}`,
      { withCredentials: true },
    );

    source.onopen = () => {
      attempt = 0;
      handlers.onConnectionState("open");
    };

    source.addEventListener("run.settled", (raw) => {
      stopped = true;
      source?.close();
      try {
        handlers.onSettled(JSON.parse((raw as MessageEvent).data) as RunStatus);
      } catch {
        void readStatus(runId).then(handlers.onSettled).catch(() => {});
      }
    });

    const deliver = (raw: MessageEvent) => {
      let event: RunEvent;
      try {
        event = JSON.parse(raw.data) as RunEvent;
      } catch {
        return;
      }
      // A replayed or duplicated event is not new information. Applying it
      // twice would double-count a substep and move elapsed time backwards.
      if (event.seq <= cursor) return;
      // A gap means events were missed, not that they did not happen. The
      // authoritative status is re-read rather than reconstructed.
      if (event.seq > cursor + 1 && cursor !== 0) {
        void readStatus(runId).then(handlers.onSettled).catch(() => {});
      }
      cursor = Math.max(cursor, event.seq);
      handlers.onEvent(event);
    };

    // A listener per named event, plus `onmessage` for any unnamed frame.
    for (const name of EVENT_TYPES) {
      source.addEventListener(name, (raw) => deliver(raw as MessageEvent));
    }
    source.onmessage = deliver;

    source.onerror = () => {
      source?.close();
      if (stopped) return;
      if (attempt >= RECONNECT_DELAYS_MS.length) {
        // Six failed attempts. This is what the CLIENT knows; the run may
        // well be fine, and the panel says so.
        handlers.onConnectionState("lost");
        return;
      }
      handlers.onConnectionState("retrying");
      timer = setTimeout(open, RECONNECT_DELAYS_MS[attempt]);
      attempt += 1;
    };
  };

  open();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    source?.close();
  };
}

/* ---- what a credit officer does with an answer ---------------------- */

export type SavedAnalysis = {
  saved_id: string;
  title: string;
  note: string;
  question: string;
  run_id: string;
  thread_id: string;
  release_id: string;
  created_at: string;
};

export type Investigation = {
  investigation_id: string;
  title: string;
  summary: string;
  status: string;
  origin: string;
  updated_at: string;
  items?: { entry_id: string; kind: string; ref_id: string; label: string }[];
};

export type Comment = {
  comment_id: string;
  subject_kind: string;
  subject_id: string;
  author_id: string;
  body: string;
  created_at: string;
};

/**
 * One outbox row. `delivered` is the only field the UI may render as
 * "sent": `state` alone is not a delivery claim, and a RECORDED or REFUSED
 * row carries the reason it went nowhere.
 */
export type Notification = {
  notification_id: string;
  recipient: string;
  subject: string;
  state: "RECORDED" | "REFUSED" | "SENT" | "FAILED";
  transport: string;
  reason: string;
  delivered: boolean;
  created_at: string;
};

export type DeliveryPosture = {
  transport: string | null;
  /** Whether this build can deliver OUTSIDE the product. Unchanged. */
  can_deliver: boolean;
  /**
   * Whether it can deliver INSIDE it.
   *
   * Separate from `can_deliver` because one boolean cannot answer both
   * once the answer is "inside, yes; outside, no" -- and a single flag
   * that went true when in-app delivery arrived would have reported a
   * build as able to mail people it cannot mail.
   */
  can_deliver_in_app?: boolean;
  in_app_transport?: string | null;
  recipient_policy: string;
  authorized_recipients: string[];
  authorized_domains: string[];
};

/** A person or team inside CreditProbe, as the picker offers them. */
export type Recipient = { handle: string; label: string };

export async function readRecipients(): Promise<{
  people: Recipient[];
  teams: Recipient[];
  /** What the directory does NOT contain. Shown, not swallowed. */
  note: string;
}> {
  return json(
    await fetch(`${base()}${API_PREFIX}/recipients`, {
      credentials: "include",
    }),
  );
}

/** One message a reader was sent, body and all. */
export type InboxMessage = Notification & {
  actor_id: string;
  body: string;
  subject_kind: string;
  subject_id: string;
};

/**
 * The messages addressed to THIS reader.
 *
 * Not `readOutbox`, which is the tenant's whole outbox without the
 * bodies -- what an operator wants, and the opposite of what a person
 * reading their own post wants.
 */
export async function readInbox(): Promise<{
  me: string;
  handles: string[];
  messages: InboxMessage[];
  delivery: DeliveryPosture;
}> {
  return json(
    await fetch(`${base()}${API_PREFIX}/inbox`, { credentials: "include" }),
  );
}

async function send<T>(path: string, body: unknown): Promise<T> {
  return json(
    await fetch(`${base()}${API_PREFIX}${path}`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function saveAnalysis(input: {
  run_id: string;
  title?: string;
  note?: string;
}): Promise<SavedAnalysis> {
  return send("/saved-analyses", input);
}

export async function listSavedAnalyses(): Promise<SavedAnalysis[]> {
  const body = await json<{ saved: SavedAnalysis[] }>(
    await fetch(`${base()}${API_PREFIX}/saved-analyses`, {
      credentials: "include",
    }),
  );
  return body.saved ?? [];
}

export async function createInvestigation(input: {
  title: string;
  summary?: string;
  origin?: string;
  thread_id?: string;
  saved_ids?: string[];
}): Promise<Investigation> {
  return send("/investigations", input);
}

export async function listInvestigations(): Promise<Investigation[]> {
  const body = await json<{ investigations: Investigation[] }>(
    await fetch(`${base()}${API_PREFIX}/investigations`, {
      credentials: "include",
    }),
  );
  return body.investigations ?? [];
}

export async function addInvestigationItem(
  investigationId: string,
  input: { kind: string; ref_id: string; label?: string },
): Promise<{ entry_id: string }> {
  return send(`/investigations/${encodeURIComponent(investigationId)}/items`, input);
}

export async function addComment(input: {
  subject_kind: string;
  subject_id: string;
  body: string;
}): Promise<Comment> {
  return send("/comments", input);
}

export async function listComments(
  subjectKind: string,
  subjectId: string,
): Promise<Comment[]> {
  const query = new URLSearchParams({
    subject_kind: subjectKind,
    subject_id: subjectId,
  });
  const body = await json<{ comments: Comment[] }>(
    await fetch(`${base()}${API_PREFIX}/comments?${query.toString()}`, {
      credentials: "include",
    }),
  );
  return body.comments ?? [];
}

export async function shareItem(input: {
  subject_kind: string;
  subject_id: string;
  audience_id: string;
  message?: string;
  notify_email?: string;
  /** A colleague or team inside CreditProbe: `user:<id>` or `team:<id>`. */
  notify_in_app?: string;
}): Promise<{
  share: { share_id: string; audience_id: string };
  notification: Notification | null;
  /** One per recipient addressed. Each carries its own outcome. */
  notifications?: Notification[];
  delivery: DeliveryPosture;
}> {
  return send("/shares", input);
}

/**
 * One saved analysis, by id.
 *
 * The inbox needs it to answer "where is the thing that was sent to me":
 * a share carries the saved id, and the CONVERSATION it belongs to is on
 * the saved record.
 */
export async function readSavedAnalysis(savedId: string): Promise<
  SavedAnalysis & { body?: Record<string, unknown> }
> {
  return json(
    await fetch(
      `${base()}${API_PREFIX}/saved-analyses/${encodeURIComponent(savedId)}`,
      { credentials: "include" },
    ),
  );
}

export async function readOutbox(): Promise<{
  delivery: DeliveryPosture;
  notifications: Notification[];
}> {
  return json(
    await fetch(`${base()}${API_PREFIX}/notifications`, {
      credentials: "include",
    }),
  );
}

/**
 * The sentence the UI is allowed to show about one notification.
 *
 * There is exactly one wording that claims delivery, and it is reachable
 * only when the server said `delivered`. Everything else names what did not
 * happen, so no screen can render "email sent" over a message that sat in
 * the outbox.
 */
export function deliveryWording(notification: Notification | null): string {
  if (!notification) return "No notification was requested.";
  if (notification.delivered) {
    return `Sent to ${notification.recipient} via ${notification.transport}.`;
  }
  if (notification.state === "REFUSED") {
    return `Not sent. ${notification.recipient} is not an authorised recipient for this deployment.`;
  }
  if (notification.state === "FAILED") {
    return `Not sent. The transport refused it: ${notification.reason}`;
  }
  return `Recorded, not sent. ${notification.reason}`;
}
