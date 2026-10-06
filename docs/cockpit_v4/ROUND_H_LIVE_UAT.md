# Round H live UAT — pre-flight record

Product code frozen at `2b2a5b7`; docs and this package on top.
Harness: `scripts/cockpit_v4/live_uat.py`. Dry-run evidence:
`docs/cockpit_v4/evidence/live_uat_dry_run.json`.

**Status: PRE-FLIGHT BLOCKED — no paid call has been made.**

---

## 1 · The cache-write question, answered from the code

The price-card schema carries one `cache_write_usd_per_mtok`, while Anthropic
publishes two write prices (5-minute $6.25/MTok, 1-hour $10/MTok for Claude
Opus 5). The instruction was to prove which the runtime incurs, and to stop if
it can incur both.

**It incurs neither. The runtime never requests prompt caching.**

Four independent checks, none of them inference from naming:

1. `grep -rn cache_control backend/cockpit_v4/` returns nothing.
2. The assembled V4 request was built and serialised: every system block has
   exactly the keys `{type, text}`, and the strings `cache_control` and
   `ephemeral` appear nowhere in the payload.
3. `backend/llm/anthropic_provider.py` — the client V4 actually calls through
   `resolve_provider` — contains **zero** occurrences of `cache_control`. It
   calls `caching.usage()`, which READS `cache_creation_input_tokens` and
   `cache_read_input_tokens` off the response. It never calls
   `caching.compose()`, which is the only function in the codebase that sets
   `cache_control`. The two callers of `compose()` are `backend/analyst/
   session.py` and `backend/ai_studio/tabs.py`, neither on the V4 path.
4. `converse()` passes `"system": system` verbatim — the blocks `context.py`
   built, unmodified.

So `cache_creation_input_tokens` is always 0, `cache_write_tokens` settles at
0 in the ledger, and `cache_write_usd_per_mtok` is multiplied by zero on every
run.

**Decision: record the 5-minute price, $6.25/MTok.** Not because it is the
cheaper of the two — it is — but because it is the only TTL this codebase can
produce: its own constant is `EPHEMERAL = {"type": "ephemeral"}` with no
`ttl` key, which is the 5-minute default. If caching is ever switched on
without a `ttl`, $6.25 is the price that will apply.

**This is not reported as a pricing-model defect**, because the condition for
one — a runtime that can incur both TTLs against a schema that can hold one —
is not met. It would become one the moment any V4 code path sets
`cache_control` with a `ttl`, and that is worth a guard in a later round.

## 2 · Verified pricing

Read from the official schedule on 2026-09-21:
`platform.claude.com/docs/en/about-claude/pricing`, "Model pricing" table,
row **Claude Opus 5**; API id, context window and max output cross-checked
against `platform.claude.com/docs/en/about-claude/models/overview`.

| Field | Published | In the card |
|---|---|---|
| Base input | $5 / MTok | `input_usd_per_mtok: 5.0` |
| Output | $25 / MTok | `output_usd_per_mtok: 25.0` |
| 5m cache writes | $6.25 / MTok | `cache_write_usd_per_mtok: 6.25` |
| 1h cache writes | $10 / MTok | not representable; not incurred (§1) |
| Cache hits and refreshes | $0.50 / MTok | `cache_read_usd_per_mtok: 0.5` |
| API id | `claude-opus-5` | `models.claude-opus-5` |
| Context window | 1M tokens | `context_tokens: 1000000` |
| Max output | 128K tokens | `max_output_tokens: 128000` |

All five figures the user supplied were confirmed verbatim.

**Modifiers that do NOT apply, checked in code rather than assumed.** Fast
mode ($10/$50) needs `speed: "fast"`; US-only data residency applies a 1.1x
multiplier via `inference_geo`. `grep` for `"speed"`, `inference_geo` and
`fast` across `backend/cockpit_v4/` and `backend/llm/anthropic_provider.py`
returns nothing, so standard first-party pricing applies. The Batch API is
not used.

**Note for budget reading:** `config.py`'s budget arithmetic was derived at
$15/$75 — the retired Opus 4.1 schedule. Opus 5 is 3x cheaper per token,
partly offset by the newer tokenizer (Claude 4.7 and later) producing ~30%
more tokens for the same text. Net effect: the unchanged $1.50 per-run
ceiling is materially more generous in real terms than when it was set.

## 3 · Pre-flight results

| # | Check | Result |
|---|---|---|
| 1 | `config.validate()` | **one** item missing: `COCKPIT_ANTHROPIC_API_KEY` |
| 2 | `load_price_card("…claude-opus-5.json", model_id="claude-opus-5")` | loads; `verified_at 2026-09-21` |
| 3 | `verify_live()` | **BLOCKED** — needs the credential |
| 4 | Harness dry run, full matrix | 41 runs, all COMPLETED, $0.7425 |
| 5 | Both releases and fingerprints | both match the approved pair |

Release identity, confirmed:

- corporate `v4-saudi-corporate-20q-v4`, fingerprint `e37236d0f6d4e494…750f`, quarterly, SAR million
- retail `v4-saudi-retail-20m-v5`, fingerprint `a1e797dcc73236b7…06cb`, monthly, SAR million

### Cost accounting uses the verified card

A scripted turn reports 1,200 input and 300 output tokens. The ledger
committed **$0.0270** for a two-generation run:

    2 × (1200 × $5/1e6 + 300 × $25/1e6) = 2 × $0.0135 = $0.0270

which is the card's arithmetic exactly.

### The gates, proven by making them fire

| Gate | Test | Result |
|---|---|---|
| Stop-at | cap $25, stop-at $0.20 | halted after 8 runs at $0.2160, before starting the 9th |
| Hard cap | cap $1.00, per-run ceiling $1.50 | refused to start **any** run: $0 + a possible $1.50 > $1.00 |
| Run count | `--max-runs 3` | halted at 3 |
| Wrong model | `check_served_model("claude-sonnet-5", "claude-opus-5")` | raises `wrong_model` |
| Release mismatch | resolver vs approved pair | both match; a mismatch raises `source_release_mismatch` |
| Cap arithmetic | committed $19.99 / $20.00 | allowed / `stop_at` |

The hard-cap gate is the important one: it refuses a run that *could* cross
the cap rather than reporting the breach afterwards.

**The cumulative cap is a harness control, not a product guarantee.**
`spend_ceiling_usd` is per run and `store.spend()` is keyed by `run_id`;
there is no tenant or session ledger anywhere in the product. Nothing in
CreditProbe would stop the fortieth run because the first thirty-nine were
expensive. `Guard` does, and it is named as harness code.

## 4 · Two blocking items

**B1 — `COCKPIT_ANTHROPIC_API_KEY` is absent.** No fallback exists: V4 reads
that variable and deliberately not `ANTHROPIC_API_KEY`, so that a Cockpit
cannot quietly bill another application's account.

**B2 — the approved matrix is 41 runs, not 23.** The proposal's spend table
said "Run count 23", and the cell beside it said "18 single-turn + ~23 chain
turns". Those two cannot both be true: 23 was the chain-turn subtotal written
into the total's cell. The dry run settles it — 23 journeys, **41 runs**.
The error is mine and the approval was given against the wrong number.

Projected worst case at 41 runs: 41 × $1.50 = $61.50, over the $25 cap, so
the harness would stop at $20.00 partway through. Realistically, at $5/$25 a
completed analytical run costs a fraction of its ceiling, so 41 runs plausibly
land well inside $25 — but "plausibly" is not a spend control, and the run
count is a control the user set.

