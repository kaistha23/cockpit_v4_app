# Cockpit V4 — runtime readiness and Saudi release commissioning

No paid provider call was made. Every result below is measured in this
container against the real release, the real FastAPI application, the real
runtime constructor and the real provisioning command.

---

## 1. Starting HEAD

`ce588b0` — verified equal to `origin/claude/cockpit-single-agent-v4-h8fsbq`
with a clean working tree before anything was changed.

## 2. Final HEAD

The last commit of this round; the pushed SHA is named in the handover below.

## 3. Why `v4-saudi-20q-v1` was missing on the Mac

It had never been published into that machine's V4 runtime namespace.

The release is not in the repository — it is *built* from the shared
generator and then localized. This container has it because the container ran
the seeder; the Mac worktree did not. Nothing was broken; nothing had provided
it.

What made that a crisis rather than a one-line fix was the **instruction** the
runtime gave: the message came from V3's `store.py` and said

    Run scripts/build_cockpit_agentic_v3.py to build it.

which is the wrong tool. V4 publishes into its own namespace, localizes to
Saudi Arabia after the shared generator has run, and never touches a V3
release. V4 now writes its own message — and V3's file was not modified.

## 4. Correct provisioning command

```bash
python3 scripts/cockpit_v4/seed_release.py --release v4-saudi-20q-v1
```

Named once, in `service.provision_command()`, so the preflight failure, the
launcher's terminal output and the diagnostics document all hand the operator
the same sentence. An operator given three different instructions tries all
three.

What it guarantees, asserted by tests: V4 namespace only (it refuses the V3
namespace by name and refuses any target outside its own); Saudi-native data
localized *after* the shared build, so no V3 release can be affected; SAR at
million scale; 20 quarters ending 2026Q2; deterministic (borrower names are a
pure function of the borrower id); no FX conversion; and immutable — a second
run prints "already exists … immutable" and leaves the fingerprint identical.

**A defect found while testing this:** seeding *any* release rewrote the
evidence documents under `docs/cockpit_v4/evidence/`, which describe *the*
demonstration release. A twenty-borrower probe would have been published in
the docs as the book. Evidence is now written only for the release the docs
are about.

## 5. Why `start.py` continued after DATA_UNAVAILABLE

It printed the failure and then ran on to launch uvicorn. The release gate
existed, but it sat *after* the process launch, so a failure it had already
detected changed nothing.

## 6. Startup fail-closed fix

The gate now sits before the launch, and a regression asserts that ordering by
character offset in the file rather than by reading the code charitably. On
failure it prints the code, the reason, an `Action:` line carrying the
provisioning command, and `Nothing else started.`, then returns 1. A second
gate refuses when `ready_for_sql_analysis` is false for any other reason.

Measured in this container against an absent release:

```
$ python3 scripts/cockpit_v4/start.py --release v4-saudi-20q-v1-absent
...
start.py exit=1
uvicorn processes started: 0
```

(It stops at the credential gate here, which is correct ordering — this
container has no API key and none was requested. The *release* gate is
covered by the positional regression and by direct tests of the diagnostics it
reads.)

## 7. Exact `scope_for` root cause

`_Runtime` was not a class that lost a method. It was a **placeholder
invented by `create_app`**:

```python
class _Runtime:
    cfg = None

holder = runtime or _Runtime()
holder.cfg = cfg
routes.install(store=store, runtime=holder, ...)
```

When preflight failed, that object was installed *as the runtime*. The real
`service.Runtime` has `scope_for` and always did; the placeholder had `cfg`
and nothing else. `routes._attention_session` called `runtime.scope_for(who)`
and got an AttributeError, which reached the browser as a raw 500.

That one decision produced all four live symptoms:

| symptom | why |
|---|---|
| attention 500 with a traceback | the placeholder lacks `scope_for` |
| `POST /runs` → 202 | run creation had a runtime, so nothing objected |
| run stuck at "Request accepted" | the worker check read the **real** runtime, correctly saw none, and started nothing — so the API promised work to a worker that had refused to exist |
| `/health` → 200, looking fine | health reported the process, not the capability |

A stand-in that is present but cannot work is worse than an absence, because
an absence is checkable.

## 8. Runtime-interface fix

There is no stand-in. The runtime is the real one or it is `None`.

`backend/cockpit_v4/readiness.py` is the single contract: given a runtime and
a config, it answers six capability questions. `routes.install()` now takes
`cfg` and `preflight_error` separately, precisely because `runtime` may be
`None` — a runtime that could not be built still has a configuration worth
reporting and a reason worth naming.

There is **one** canonical way to derive the authorized scope:
`Runtime.scope_for(principal)`, which wraps V3's `for_principal` with the
server-pinned release. Attention, analysis, saved work and investigations all
reach it through the same runtime object. A test asserts a real runtime
exposes it and that calling it returns a scope.

**Why the suite never caught this (§14):** the API test fixture installed its
own `Holder` carrying only `cfg` — *the same shape* production substituted. The
suite and the broken runtime were fake in the same way, so the suite could not
see it. Every fixture now installs the real runtime, and a test reads the
suite's own source to keep it that way:

> `test_no_test_installs_a_runtime_production_would_not_recognise`

## 9. Health and readiness fix

Six flags, named identically in `/api/v1/health` and `/api/v1/cockpit-v4/diagnostics`:

`process_alive`, `release_ready`, `product_help_ready`, `sql_analysis_ready`,
`attention_ready`, `python_analysis_ready`.

Measured, with the release absent:

```json
{"process_alive": true, "release_ready": false, "product_help_ready": false,
 "sql_analysis_ready": false, "attention_ready": false,
 "python_analysis_ready": false}
```

and present:

```json
{"release_id": "v4-saudi-20q-v1",
 "release_fingerprint": "24df0d375e72d1cd23a8a92f1410745426d35cd756858b266a0e801e64acca88",
 "country": "Saudi Arabia", "reporting_currency": "SAR",
 "amount_scale": "million", "latest_populated_quarter": "2026Q2",
 "not_client_data": true}
```

The health *document* still returns 200 — the process is answering, and that
is what an HTTP status means. What it *says* is now true.

One correction this forced: `/api/v1/health` used to re-derive readiness from
the environment, which answers a different question ("could a runtime be built
from this config") from the one a caller is asking ("what can the one serving
you do"). It now reports the runtime that is **installed**. Two health
documents disagreeing about one process is worse than either being wrong alone.

Diagnostics additionally carry `remedy` and `substituted: false` — a runtime
configured for the Saudi release never serves `v4-uat-20q-v1` instead.

## 10. Run-acceptance fix

`POST /runs` consults readiness before anything else. With no analysis
available it returns **503** with `error_code: DATA_UNAVAILABLE`, the
capability that is missing, the full readiness map, and the remedy. It
persists nothing.

202 is a promise to do the work. Nothing that cannot keep it may send one.

Product Help is refused separately and for a different reason, preserving the
capability separation: it needs a model, not a portfolio.

## 11. Worker-startup regression

`test_an_accepted_run_advances_beyond_accepted` posts a real run through the
real application, asserts it is `ACCEPTED`, then does exactly what
`serve_forever` does for one iteration — `claim_next`, then `execute` — and
asserts the run is no longer `ACCEPTED` and has reached a terminal state. A
run that stays `ACCEPTED` fails the test.

The complementary half is asserted too: the worker is started only for a real
runtime, and there is no longer a placeholder that could make that check true
when it should be false.

## 12. Saudi attention test

Two, at different depths.

`test_attention_works_against_the_real_runtime` drives `GET /attention` through
the real FastAPI application against the real runtime object — the regression
that would have caught `scope_for` going missing.

`test_the_whole_sequence` goes further: its own release id in its own
namespace, so the absence is a fact about the filesystem rather than a fixture
pretending. Before provisioning, attention answers a typed 503 with no
`AttributeError` and no `scope_for` anywhere in the response. It then runs the
documented command as a subprocess and asserts attention answers 200 for the
release that was just published.

## 13. Test counts

| suite | result |
|---|---|
| V4 backend (`tests/cockpit_v4`) | **1068 passed, 2 skipped, 0 failed** |
| Frontend (`frontend`, node:test) | **476 passed, 0 failed** |
| Browser (real Chromium, real UI, real API, stub analyst) | **46/46 passed** |
| V3 regression (`tests/cockpit_agentic`) | **564 passed, 26 skipped, 0 failed** |
| M01–M15 and numeric rendering, re-run unchanged | **191 passed, 2 skipped** |

New this round: `test_runtime_readiness.py` (14),
`test_saudi_release_smoke.py` (12), `test_provision_sequence.py` (2, marked
`slow`), plus 3 launcher regressions.

## 14. V3 comparison

564 passed, 26 skipped, 0 failed — unchanged. No file under
`backend/cockpit_agentic/` or `tests/cockpit_agentic/` was modified. V3's
misleading error message was corrected *in V4*, by V4 writing its own rather
than forwarding V3's.

The demonstration release fingerprint is byte-identical before and after the
whole round:

```
24df0d375e72d1cd23a8a92f1410745426d35cd756858b266a0e801e64acca88
```

## 15. Verdict

**SAUDI V4 RUNTIME: READY FOR MAC UAT**

Against the hard acceptance criteria:

| | criterion | |
|---|---|---|
| 1 | provisionable with a V4-native command | ✅ |
| 2 | missing release prevents startup | ✅ exit 1, nothing launched |
| 3 | missing release cannot create a stuck 202 | ✅ 503, nothing persisted |
| 4 | attention no longer throws AttributeError | ✅ typed 503 |
| 5 | health truthfully reports capability | ✅ six flags |
| 6 | diagnostics truthfully report release status | ✅ incl. `substituted: false` |
| 7 | published release starts cleanly | ✅ |
| 8 | attention loads | ✅ 200 through the real app |
| 9 | a run progresses beyond request accepted | ✅ |
| 10 | mathematical/numeric regressions green | ✅ 191 passed |

§15 was honoured: the numeric claim and rendering architecture was not
touched. `display.py`, `precision.py`, `derivation.py` and `finalization.py`'s
claim path are unchanged this round.

**One thing named rather than claimed:** the live launcher demonstration in
this container stops at the credential gate, because there is no API key here
and none was requested. The release gate's position before the process launch
is asserted by regression, and the diagnostics it reads are tested directly —
but the Mac run in §16 is the first time that specific gate will be exercised
end to end with a credential present.

## 16. Mac commands

```bash
cd ~/IPM_V2

# 1. stop whatever is running
python3 scripts/cockpit_v4/stop.py

# 2. pull this branch
git fetch origin claude/cockpit-single-agent-v4-h8fsbq
git checkout claude/cockpit-single-agent-v4-h8fsbq
git pull origin claude/cockpit-single-agent-v4-h8fsbq

# 3. provision the Saudi release ONCE, if it is not already there.
#    Safe to run either way: if it exists it prints "already exists …
#    immutable" and changes nothing.
python3 scripts/cockpit_v4/seed_release.py --release v4-saudi-20q-v1

# 4. start. It now REFUSES rather than half-starting, and the release
#    block should read:
#       release        v4-saudi-20q-v1
#       fingerprint    24df0d375e72d1cd
#       country        Saudi Arabia
#       currency       SAR
#       amount scale   million
#       latest quarter 2026Q2
python3 scripts/cockpit_v4/start.py

# 5. verify before opening the UI
python3 scripts/cockpit_v4/status.py
curl -s http://127.0.0.1:8414/api/v1/cockpit-v4/diagnostics | python3 -m json.tool | head -40
curl -s http://127.0.0.1:8414/api/v1/health | python3 -c \
  "import json,sys; print(json.load(sys.stdin)['capabilities'])"
```

Expect from that last command:

```
{'process_alive': True, 'release_ready': True, 'product_help_ready': True,
 'sql_analysis_ready': True, 'attention_ready': True,
 'python_analysis_ready': True}
```

Then open http://127.0.0.1:5414. If any flag reads `False`, the diagnostics
document names the capability, the reason and the command that fixes it —
and nothing will have silently half-started behind it.
