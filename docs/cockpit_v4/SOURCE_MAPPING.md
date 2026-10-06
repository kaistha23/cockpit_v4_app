# Source mapping — what was kept, adapted, replaced, and what is unverified

The V4 base is `015de742890e895026ed8558822f4bff87ddd447` on
`claude/cockpit-agentic-v3-fhg4r0`, which is the checkpoint the master
specification names (`015de74`), verified against the remote rather than
assumed.

`backend/cockpit_agentic/` is **byte-identical to the base** on this branch.
`git diff <base> HEAD -- backend/cockpit_agentic backend/api backend/llm
backend/config.py` is empty. V4 reaches V3 only through imports.

## KEEP — reused unchanged, through imports

| V3 module | What V4 uses it for | V4 caller |
|---|---|---|
| `cockpit_agentic/catalog.py` | the relation/field catalog for the pinned release | `catalog_tool.py`, `context.py` |
| `cockpit_agentic/fields.py` | the field dictionary: 991 addressable definitions, units, grain, aggregation, availability | via `catalog` |
| `cockpit_agentic/calendar.py` | the 20-quarter reporting calendar | via `store` |
| `cockpit_agentic/scope.py` | the effective read scope for a principal (`for_principal`) | `service.Runtime.scope_for` |
| `cockpit_agentic/sql.py` | session construction with file access locked *before* submitted SQL is admitted; `check_structure`, `bind`, `multiplication_risk`, `execute` | `execute_tool.py` |
| `cockpit_agentic/store.py` | read-only release access: `read_manifest`, `load_calendar`, `read_relation` | `service.py` |
| `cockpit_agentic/generate.py` | the synthetic release generator (for the isolated V4 UAT release only) | `scripts/cockpit_v4/seed_release.py` |
| `cockpit_agentic/profile.py` | measured coverage/missingness profiles | `service.coverage_for` |
| `cockpit_agentic/registry.py` | the functionality ownership registry and real route ids | `context.py` |
| `llm/base.py`, `llm/anthropic_provider.py` | the `converse` protocol that preserves assistant blocks and tool ids verbatim | `provider.py`, `service.py` |

The SQL layer is the most valuable thing V3 built and V4 reuses it wholesale.
Its security argument — materialize the authorized relations while file access
still works, then disable file access and lock the configuration *before* any
model-authored SQL is admitted — is exactly right and is not re-implemented.

## ADAPT — same idea, new implementation

| Concern | V3 | V4 |
|---|---|---|
| Context assembly | `context.py` builds a gate packet and a full-dictionary planning packet | `context.py` builds a compact index; definitions are fetched on request |
| Provider adapter | `opus.py` `Conversation` with per-stage contracts | `provider.py` `Analyst`: one loop, four tools, explicit capability, SDK retries off |
| Budgets | `ledger.py` with configured prices | `budgets.py` + `capability.py`: verified price card, fail-closed, pending reservations |
| Thread state | in-process dict keyed by a composite string | durable `threads`/`turns`/`summaries` tables, server-generated ids |
| Result packaging | `SqlResult` previews | artifacts with explicit projection and omission markers |
| Status/diagnostics | one readiness flag | three separate capability checks |
| Frontend answer panel | `components/ask/cockpit-agentic.tsx` | `components/cockpit-v4/*` — new surface, V3's left untouched |

## REPLACE — removed from the V4 path

| V3 behaviour | Why it is gone |
|---|---|
| Two mandatory Sonnet passes (`sonnet.clean`, `sonnet.normalize`) | Two paid calls before anything can answer, and a rewritten question replacing the user's own words |
| The standalone ownership gate round trip | Intent is declared with the first action; no extra call |
| Full-catalogue gate and planning input | A help question paid for the entire data domain |
| The six-score functionality essay and 70/10 thresholds | Uncalibrated numbers presented as a decision procedure |
| Summary-before-answer on the delivery path | A summary failure could take the answer with it |
| Truncation as semantic repair | Clipping a plan produces a number, and the number is wrong |
| Silent/spinning HTTP request-response | Replaced by durable runs, SSE with replay, and explicit terminal states |

## UNVERIFIED — stated rather than claimed

| Item | Status |
|---|---|
| Live provider behaviour of the four tool schemas | Not exercised against a real provider in this environment: no credential. The wire schemas are flattened for a restricted dialect, but acceptance by a specific model version is unverified. |
| Python analysis capability | `pyrunner.probe()` reports UNAVAILABLE here. The self-test found the jail does not block network access, and the runner correctly refuses to certify itself. |
| Production PostgreSQL behaviour | `run_store` is exercised on SQLite/WAL. A local SQLite test is not proof of production PostgreSQL behaviour. |
| The V3 field-name/annex differences | The catalog is V3's, unchanged. Where the master spec's annex and the deployed field names differ, the deployed names are authoritative and were not renamed. No gap ledger entry was needed because nothing was rebuilt. |
| The `docx`/`psycopg`/`python-multipart` dependencies | Missing from this container at session start; installed to run the V3 suite. Not a code change. |

## Isolation, in practice

| Resource | V3 | V4 |
|---|---|---|
| Branch | `claude/cockpit-agentic-v3-fhg4r0` | `claude/cockpit-single-agent-v4-h8fsbq` |
| Worktree | untouched | separate checkout |
| Data namespace | `cockpit_agentic_v3` | `cockpit_v4` |
| Release | as published | `v4-uat-20q-v1`, seeded separately |
| State store | in-process | `$COCKPIT_V4_RUNTIME_DIR/state/cockpit_v4.sqlite3` |
| API / UI ports | as configured | 8414 / 5414, verified before use, never forcibly freed |
| Feature switch | `COCKPIT_AGENTIC_V3` | `COCKPIT_AGENTIC_V4` — does not enable or alter V3 |
| Credential | `COCKPIT_ANTHROPIC_API_KEY` | the same variable, and no other |

A branch isolates code. It does not isolate a Parquet lake, a SQLite file, a
port or a log directory — which is why each of those is namespaced and why
`config.check_write_target` refuses a write outside the V4 runtime directory
before the first byte.
