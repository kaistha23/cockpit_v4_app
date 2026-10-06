# Why the catalogue loop did not converge, and what stops it now

A live UAT asked:

> What is total exposure at default by sector in the latest quarter?

The semantics were resolved correctly — EAD, ECL, stage, sector and PD all
mapped, the period resolved to 2026Q2 against 2026Q1 with a 2025Q2 reference —
and then the run made three `inspect_catalog` calls, hit a truncated
generation at about 94 seconds, and expired without reaching SQL.

Four causes, each reproduced against the pinned release before anything was
changed.

## 1. A relation expansion became a 991-field paging loop

`inspect_catalog` expanded `relation_ids` into every column of every named
relation when `field_ids` was empty. Reproduced:

| Request | Fields returned | `next_cursor` | `omitted.remaining_fields` |
|---|---:|---:|---:|
| `detail=["discovery","fields"]`, all 11 relations | **60** | 60 | **931** |
| `detail=["fields"]`, `cockpit_facility_quarter` | 60 | 60 | 138 |

That is the live trace's "Read 60 field definition(s) across 11 relation(s)",
and it is the loop's fuel: the response is a partial dump that explicitly says
931 more are available. Paging on is the reasonable next move, and it is not
progress.

**Now:** a relation expansion larger than one page is **refused**, with the
relation sizes and what to do instead. No page, no cursor, no invitation.

```json
{"status": "needs_scope",
 "reason": "Expanding 11 relation(s) would be 991 field definitions. Name the
            field ids you need in field_ids, or narrow to one relation, or use
            detail=['discovery'] with a query to find the canonical ids.
            Nothing was returned and nothing was abbreviated.",
 "relation_sizes": {...}, "coverage_complete_for_request": false}
```

An **explicit list of field ids is always served**, however long, and still
pages — that is a request somebody actually made. Asking for a relation's
`relationships` or `coverage` is untouched: only field expansion is refused.

## 2. An unscoped request returned the whole relation list

`detail=[]` defaulted to `discovery` and listed all 11 relations; `detail=
["fields"]` with no scope returned an empty `fields` array with no explanation.

**Now:** a request with no relation, no field and no query is refused
compactly — under 2 KB — naming the authorized relations and what to specify.
An unscoped request is not a request for the whole catalogue.

## 3. The responses did not say whether they had succeeded

Nothing in a catalogue response distinguished "here is what you asked for"
from "you already had this". A model that cannot tell whether it made progress
asks again.

**Now** every response carries:

```
requested: {detail, relation_ids, field_ids, query, sample_rows}
returned:  {field_ids, relations, detail}
already_known: [...]        # fields this RUN already held
still_missing: [...]        # requested ids that do not exist
coverage_complete_for_request: true | false
added_new_information: true | false
known_so_far: {field_count, relations}
```

and, when nothing was added:

> No new catalogue information was added. The requested fields are already
> available in this conversation. Continue with the analysis, or request a
> different, specific metadata item.

## 4. Nothing bounded repetition

`catalog_calls = 4` bounded the *number* of calls, not the *repetition*. Four
identical calls were allowed, and each cost a generation.

**The policy, on repeating rather than on asking:**

| Consecutive calls adding nothing | What happens |
|---:|---|
| 1 | The response says so. The analyst may still have had a reason. |
| 2 | A typed `no_progress` block is added to the tool result, naming the count and warning that one more ends the run. |
| 3 | Terminal `NO_PROGRESS`. |

`catalog_calls` stays at **4**. The fix is not a lower global limit: a complex
question may legitimately need four distinct metadata reads, and
`test_two_distinct_catalogue_calls_are_not_blocked` asserts that exploring is
untouched. What it may not do is ask the same thing until the deadline
expires.

## The compact schema packet

The starting context already carried the canonical term→field mappings. It did
not carry the *type*, *unit*, *grain*, *period column* or *join key* — which is
a defensible reason to read the catalogue.

`semantics.field_packet()` now supplies, for each of the 16 mapped fields,
read straight off the catalogue (≈6.9 KB in total):

```json
{"term": "exposure at default",
 "field_id": "cockpit_facility_quarter.ead_reported",
 "relation": "cockpit_facility_quarter", "column": "ead_reported",
 "means": "Reported exposure at default.",
 "dtype": "float", "unit": "RCY", "aggregation": "additive",
 "currency": "SAR", "amount_scale": "million",
 "grain": "one row per facility per reporting quarter",
 "period_field": "reporting_quarter",
 "key": "facility_id", "borrower_key": "borrower_id"}
```

Facts only. It does not say which measure answers a question, how to
aggregate, which quarter to pick or what the answer is — a test greps the
packet for `select`, `group by`, `sum(`, `order by`, "you should" and "the
answer" and fails if any appears.

## `sample_rows`

The live message was:

> sample_rows requires 'samples' in detail.

The published schema declared `sample_rows` an independent integer 0–10 with
no dependency on `detail`, and the parser rejected the combination. The same
class of defect as the `finalize_response` null, in a different field.

**Now the two forms are the same request.** A non-zero `sample_rows` is the
request for samples; `"samples"` in `detail` without a count takes a default
of 3. Neither is refused for lacking the other, the schema says so, and six
schema-valid payloads are asserted to parse.

Samples are also narrow now. `SELECT *` on the widest relation is 198 columns
of borrower data to show shape; a sample reads the **requested columns only**
(at most 12 when none were named), at most 10 rows, at most 2 relations, and
returns its purpose and exact scope. An unscoped sample is refused.

## The truncated generation

The provider stop reason was `max_tokens` — the response reached its output
allowance. Not malformed JSON, not a refusal. The existing handling was
already correct and is unchanged: nothing from a truncated turn executes, the
partial assistant message is rolled out of history so the next request is not
malformed by an unanswered `tool_use`, one compact regeneration is allowed and
counted as a format recovery, and the failure stays in the trace.

The cause was upstream. Three catalogue responses of 60 field definitions each
inflated the conversation, and the prompt did not ask for compact tool
actions. Both are fixed here — the dumps are refused, and the instruction now
says length belongs in the final answer, not in the actions that get you
there. The output allowance was **not** increased, and neither was the
120-second deadline.

## The shape a simple question should have now

```
Request accepted
→ Opus generation 1        (the packet already carries relation, column,
                            type, unit, grain, period column, join key)
→ execute_analysis
→ bind proof · SQL executes
→ Opus generation 2
→ finalize_response
```

One targeted `inspect_catalog` is fine when a fact is genuinely missing.
Repeated ones are the failure.
