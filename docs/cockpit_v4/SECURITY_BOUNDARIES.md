# Security boundaries

## What is actually enforced, and by what

| Boundary | Enforced by | Not enforced by |
|---|---|---|
| Which relations are readable | the DuckDB session materializes **only** the authorized relations, then file access is disabled and the configuration locked before any submitted SQL is admitted (`cockpit_agentic/sql.open_session`) | the SQL text check, which is an early, precise refusal — not the boundary |
| Which tenant's rows | server-derived scope from the authenticated principal (`scope.for_principal`); artifacts are tenant-checked on read | anything the model can say |
| Which domain | there is exactly one: `corporate_cockpit`. EWS, scoring, scorecard validation, What-if, Lenses, Playbook and Planner stores are not in the session at all | a keyword list |
| Python execution | a bounded subprocess jail, or **UNAVAILABLE**. There is no in-process `exec` path | the AST/import allowlist, which is supplementary |
| The credential | `COCKPIT_ANTHROPIC_API_KEY` only. No fallback to `ANTHROPIC_API_KEY`, an SDK default, or another module's key | — |
| Cross-tenant API access | every run, event stream, cancel, artifact and thread is tenant-checked; a mismatch returns 404, never "not permitted" | — |

## The honest limit

Isolation prevents the analyst from **reading** another domain. It does not
mathematically prove that an arbitrary program never **encodes** a
calculation that belongs elsewhere — a sufficiently determined SQL statement
over authorized columns can compute something a What-if user would recognise.

Intent declaration, the ownership check on the execution path, and adversarial
tests strengthen that boundary. None of them proves semantic safety for
arbitrary programs, and this document does not claim otherwise.

## Ownership gate on execution

`execute_analysis` runs only when the intent declared *in that same
submission* is `DATA_ANALYSIS` **and** `COCKPIT` **and** carries no unresolved
ambiguity. A previous turn's owner authorizes nothing: the check is recomputed
from the current declaration (`contracts.Intent.may_execute`,
`execute_tool.validate_batch`).

Tested for `THEORY_CONCEPT + COCKPIT`, `DATA_ANALYSIS + WHAT_IF`,
`PRODUCT_HELP + COCKPIT` and for a data analysis carrying an unresolved
ambiguity — all refused with `SECURITY_DENIED`.

## SQL isolation

Refused in `tests/cockpit_v4/test_ownership_and_security.py`, each against a
real session:

- a relation outside the domain (`ews_alerts`)
- `read_csv_auto('/etc/passwd')` — host file read
- `ATTACH` — another database
- `INSTALL httpfs` — an extension
- `COPY … TO` — writing out
- a stacked statement after a semicolon

Grain checks are separate from safety checks. `multiplication_risk` refuses a
demonstrable repetition trap (a borrower balance sheet summed once per
facility, a shared collateral asset counted whole per allocation) and warns
where static analysis cannot prove correctness — it never silently removes a
join.

The V4 grain check PARSES the submitted statement to decide which relations
are joined to each other and whether an aggregate de-duplicates. That parse
runs on a separate, empty in-memory connection that holds no book: the only
statement ever executed on it is `SELECT json_serialize_sql(?)`, with the
analyst's text as a bound PARAMETER. The text is never concatenated into
SQL and is never executed — `json_serialize_sql` returns the parse tree of
its argument and evaluates nothing. Deciding this from the text instead is
what refused a correct live query for a join it did not contain, and
excused a double-counting one because a comment contained the word
"distinct".

## Python

`pyrunner.probe()` reports the capability honestly and runs an escape
self-test before certifying it: network, filesystem, subprocess and
environment access are each attempted in the jail, and if **any** succeeds the
capability is reported UNAVAILABLE with the reason.

In this environment the self-test found network access was not blocked, so
Python analysis is `available: false`. A Python step therefore fails with
`PYTHON_UNAVAILABLE` and is explicitly **not** rewritten as SQL. A UAT that
reports "Python passed" while the analyst only ever wrote SQL is not evidence
of a Python capability, and `ready_for_python_analysis` is a separate
diagnostic flag for exactly that reason.

## Secrets in traces

`orchestration._redact` runs on the way **in**, before an operator detail is
persisted, so a downloadable trace cannot leak what was never stored. It
redacts any key whose name contains `api_key`, `apikey`, `authorization`,
`cookie`, `token`, `secret`, `password` or `credential`, recursively, plus any
string beginning `sk-` or `Bearer `.

Diagnostics report the credential as `PRESENT`/`MISSING` and never its value.
The launcher reads the key from the environment or the macOS Keychain, or
prompts with `getpass` — it is never echoed, never written into the
repository, never placed in a command line, and never requested in a chat
window.

Private reasoning is not exposed. Event `public_message` fields are business
language; operator details carry model ids, request ids, sizes, checks and the
submitted code — not model thinking.

## Untrusted content

Catalog descriptions, qualitative answers, covenant text, database error
strings, stored thread records and result rows are **data**. The analyst
prompt says so explicitly, and more importantly tool authorization does not
consult the model's opinion: an injected instruction cannot widen scope,
because scope is derived server-side from the principal and pinned for the
run.

No guarantee is offered that prompting alone defeats every injection. The
guarantee is that following an injected instruction cannot grant access.

## Local demo auth

`COCKPIT_V4_LOCAL_DEMO_AUTH` issues a server-controlled demo principal, and:

- only for loopback clients (`127.0.0.1`, `::1`);
- scoped to the pinned synthetic release's own tenant, resolved server-side;
- off by default;
- unable to authorize real-data access, another module, public binding or
  production mode.

It does not touch V3's or the product's authentication. `REQUIRE_LOGIN` is not
set globally by anything in this build.

## The one unauthenticated route

`GET /api/v1/health` is served without a session, deliberately and to the same
contract the main backend uses. It carries no tenant data, no run, no artifact
and no configuration value — component names, statuses, plain-English details,
the API port, the startup SHA and the release id, all of which the diagnostics
endpoint already exposes.

It is unauthenticated because a status indicator that needs a session cannot
say "I am up" on the screen where that matters most. Every other V4 route —
runs, events, cancel, artifacts, threads — remains tenant-checked.

## Real-data egress

Published real data may not be sent to a provider without the deployment's
approval. Permission to use the synthetic demo release is not permission to
send bank data. Nothing in V4 asserts a regulatory certification or a
bank-approved credit policy.

## Rendered answers

The answer panel renders Markdown. It does **not** build an HTML string.
`markdown-parse.ts` parses the text into a plain data tree and `markdown.tsx`
maps that tree to React elements, so there is no `dangerouslySetInnerHTML`
anywhere on the path and nothing to sanitize — raw HTML in an answer is text,
not markup. Link targets go through `safeHref`, which allow-lists `https:`,
`http:`, `mailto:`, a site-relative `/` and an in-page `#`, and drops
everything else (`javascript:` and `data:` included) rather than rendering an
inert-looking link.

No new rendering dependency was added. The frontend had no safe Markdown
renderer to reuse, and pulling one in for this would have widened the
dependency surface for a parser that fits in two files.

## The Product Knowledge Pack

The pack is product documentation, generated once from the functionality deck
by `scripts/cockpit_v4/ingest_product_deck.py` and committed for review. It
carries no credential, no connection string, no tenant identifier, no customer
name and no real portfolio figure — the numbers in it are the deck's own
illustrations and are labelled as such, so the analyst cannot present one as a
live value.

It is also not an instruction channel. Retrieved sections reach the model as
tool results, under the same rule as dataset text: content is content, never a
directive.

## The home feed

The attention feed reads the pinned Cockpit V4 release through
`v3_sql.open_session`, under a scope derived from the principal exactly as an
analysis is. The release is server-pinned, the tenant comes from the principal,
and neither can be widened by a request. Its SQL is server-authored and fixed;
the only thing a request parameterises is `refresh`.

The cache is keyed by `(release_id, tenant_id)`. A tenant can never be served a
feed computed under another tenant's authorization, and a new release is a new
key rather than a stale entry someone has to remember to clear.

It reads only the `corporate_cockpit` domain. No Early Warning score, alert or
trigger is read, imported or imitated to populate it — a test greps the
executed SQL for those names.

`thread_context`, which carries an attention item into a seeded conversation,
is written by the server and stamped with the tenant. Reading it requires the
matching tenant. Nothing in a request or a model response can set it, and its
contents reach the analyst as CONTEXT, explicitly not as an instruction and
explicitly not as an answer — the packet tells the analyst to re-derive any
number it states from its own executed query.

## What is done to a question

`intake.normalize_question` applies Unicode NFC, folds Unicode spaces, removes
zero-width characters, removes bidirectional override and embedding controls
(U+202A–U+202E, U+2066–U+2069), normalises line breaks and trims. Nothing else.

The bidi controls are removed because they instruct a renderer to display
characters in an order other than their logical one: left in place, text reads
one way to the user and another way to everything downstream. Removing them
changes no word.

No spelling correction, no translation, no transliteration, no case folding,
no digit conversion, no abbreviation expansion, no clause reordering. When
anything at all is changed, the original and a report naming each
transformation are persisted as run detail and an event says so; when nothing
is changed — the common case — nothing is recorded, because a trace that
claims a normalisation that did not happen is the same defect as one that
hides one.

## Proving a query without running it

`sqlbind.prove_bindable` runs `EXPLAIN`, which parses, binds and plans and
executes nothing — an EXPLAIN of an unguarded cross join over the whole book
returns in about three milliseconds. Validation therefore costs no analytical
work and reads no rows, which is what makes it safe to do on every submission
before announcing that a query was validated.

Every diagnostic returned from it is sanitized before it reaches a model or a
reader: `read_parquet('…')` calls and filesystem paths are replaced with "the
authorized view", and only the first line is kept. The operator detail keeps
the DuckDB exception type, the binder's message, the unresolved name, the
submission ordinal, the stage, the submitted SQL and its parameters — and no
credential, connection string or path.

Parameters are bound by the engine, never interpolated into the SQL text by
CreditProbe. A placeholder with no value, a value with no placeholder or a
mixed placeholder style is refused with a named reason rather than repaired.

## The analytical allowance

A run's time and cost allowance widens only upward, only once, and only when
the analyst declares a DATA_ANALYSIS. Nothing in a request or a model response
can set it: the values come from `config.analytical_limits_for`, the model
declares a mode and the server decides what that mode is worth. Counters —
submissions, rounds, generations, catalog calls, steps — are untouched by the
widening.
