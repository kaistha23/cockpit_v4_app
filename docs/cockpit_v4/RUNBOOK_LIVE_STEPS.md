# Runbook: the steps only you can run (local oMLX machine)

Everything that could be built and tested without your local model is done and committed on the branch `feat/v4-omlx-reliability`. What is left needs **your Mac, your oMLX server and the running Cockpit app**.

Do the steps **in order**. Each one says what it is for, what to run, how long it takes, and what to send back.

All commands run from this folder:

```bash
cd ~/repos/CreditProbe_AdvancedCockpit_Round_H_SHARE/IPM_V2-CockpitLiveUAT
```

---

## Before you start: your usual settings

Use the same oMLX settings you use today. Put them in your terminal before any step, for example:

```bash
export AI_PROVIDER=omlx
export OMLX_URL=http://localhost:9906/v1
export AI_COCKPIT_REASONING_MODEL="Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-text-oQ4e-mtp"
export COCKPIT_V4_PRICE_CARD=config/cockpit_v4/price_card.omlx.local.json
export COCKPIT_AGENTIC_V4=true
```

Two notes:
- Only **one** copy of the app can run at a time. It always uses ports 8414 and 5414.
- To stop the app at any point, run `make stop`.

---

## Step 1: Does oMLX stop working when a request is abandoned? (10 minutes)

**Why:** when Cockpit gives up on a slow answer, the oMLX server may keep generating it in the background. If it does, a retry would compete with that leftover work. The answer decides whether the "give a timed-out answer more time" fix (plan step 11) is safe to switch on.

**How:** time a short request three ways: on its own; right after abandoning a long one; and again 60 seconds later.

```bash
M="$AI_COCKPIT_REASONING_MODEL"; U="$OMLX_URL/chat/completions"
short() { /usr/bin/time -p curl -s "$U" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$M\",\"max_tokens\":20,\"messages\":[{\"role\":\"user\",\"content\":\"Say OK.\"}]}" >/dev/null; }

# A. Short request alone. Note the "real" time.
short

# B. Start a LONG request, abandon it after 5 seconds, then immediately time a short one.
curl -s --max-time 5 "$U" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$M\",\"max_tokens\":3000,\"messages\":[{\"role\":\"user\",\"content\":\"Count from 1 to 3000, one number per line.\"}]}" >/dev/null
short

# C. Wait 60 seconds and time a short one again.
sleep 60; short
```

**How to read it:**
- B is about the same as A → the server **stops** abandoned work. Good.
- B is much slower than A (tens of seconds) and C is back to normal → the server **keeps generating** abandoned work.

**Send back:** the three `real` times (A, B, C).

---

## Step 2: Baseline, i.e. how the OLD code scores (about 5–10 hours, unattended)

**Why:** every improvement must be compared to the old behaviour on the same questions, on the same machine. Without this, nobody can say the changes helped.

**2a. Prepare a second copy of the repo at the old code** (one time only):

```bash
git worktree add ../v4-baseline wip/baseline-2026-10-04
ln -s "$PWD/.venv" ../v4-baseline/.venv
ln -s "$PWD/frontend/node_modules" ../v4-baseline/frontend/node_modules
mkdir -p ../v4-baseline/data && ln -s "$PWD/data/cockpit_v4_lake" ../v4-baseline/data/cockpit_v4_lake
```

**2b. Start the OLD app** (terminal 1):

```bash
cd ../v4-baseline && make app
```

**2c. Run the questions against it** (terminal 2, from THIS folder, not the baseline one):

```bash
caffeinate -i .venv/bin/python scripts/cockpit_v4/golden_eval.py \
  --provider omlx --model "$AI_COCKPIT_REASONING_MODEL" \
  --repeat 3 --out artifacts_eval/baseline.json
```

What this does:
- It asks every scored question 3 times. That is about 70 questions; the sealed set and the formula-pending questions are left out automatically.
- `caffeinate` keeps the Mac awake.
- It writes `artifacts_eval/baseline.json` plus a readable `artifacts_eval/baseline.report.md`.

**2d. Stop the old app:** `make stop` (in terminal 1, inside `../v4-baseline`).

> **Quick check only?** Add `--split holdout --repeat 1` to the command above. That runs about 22 questions in about 1 hour. It gives a direction, not proof (see `artifacts_eval/gates.json`).

**Send back:** `artifacts_eval/baseline.report.md`.

---

## Step 3: Candidate, i.e. how the NEW code scores (about 5–10 hours, unattended)

**Why:** this is the same test with the fixes switched on, compared question by question against Step 2.

**3a. Start the NEW app with the local-model time limits** (terminal 1, in THIS folder):

```bash
git checkout feat/v4-omlx-reliability
export COCKPIT_V4_LIMITS_PROFILE=local      # 300s per analysis, 45s per action, 100s per answer
make app
```

**3b. Run the same questions, paired with the baseline** (terminal 2):

```bash
caffeinate -i .venv/bin/python scripts/cockpit_v4/golden_eval.py \
  --provider omlx --model "$AI_COCKPIT_REASONING_MODEL" \
  --repeat 3 --paired artifacts_eval/baseline.json \
  --out artifacts_eval/candidate.json
```

**3c. Stop:** `make stop`.

**Send back:** `artifacts_eval/candidate.report.md`. It contains the side-by-side comparison: what got better, what got worse, by category, with confidence ranges.

---

## Step 4 (optional, after Step 3): Do the worked examples help? (about 5–10 hours)

**Why:** the few-shot examples are switched **off** by default. They stay off unless they measurably help on questions the system has never been tuned on.

Repeat Step 3 with one extra setting, comparing against the Step 3 result:

```bash
export COCKPIT_V4_LIMITS_PROFILE=local
export COCKPIT_V4_EXAMPLES=on
make app
# terminal 2:
caffeinate -i .venv/bin/python scripts/cockpit_v4/golden_eval.py \
  --provider omlx --model "$AI_COCKPIT_REASONING_MODEL" \
  --split holdout --repeat 3 --paired artifacts_eval/candidate.json \
  --out artifacts_eval/examples.json
```

**Send back:** `artifacts_eval/examples.report.md`.

---

## Step 5: Label the "shadow" SQL warnings (15–30 minutes, after Step 3)

**Why:** some new SQL checks only **log** warnings and never block. That stays the case until they are proven right at least 95% of the time. You decide which warnings were real problems.

```bash
.venv/bin/python scripts/cockpit_v4/shadow_precision.py --out artifacts_eval/shadow_labels.json
```

1. Open `artifacts_eval/shadow_labels.json`. For every item, set `"label"` to `true` (it was a real problem) or `false` (it was a false alarm).
2. Then run:

```bash
.venv/bin/python scripts/cockpit_v4/shadow_precision.py --labels artifacts_eval/shadow_labels.json
```

**Send back:** the printed precision per rule.

---

## Step 6: Fill in the bank's formulas (your own time)

**Why:** until you do, every "average PD / LGD" is a **simple, unweighted** average, and the answers say so. Weighted averages, ECL recomputation, sensitivity and migration questions are on hold.

Open `docs/cockpit_v4/TODO_FORMULAE_IFRS9.md` and complete each item:
- the formula;
- the weighting;
- the period convention;
- the edge cases.

Then change its `Status: pending` to `Status: provided`.

**Send back:** say "formulas ready".

---

## Settings you can switch (summary)

| Setting | Values | Default | What it does |
|---|---|---|---|
| `COCKPIT_V4_LIMITS_PROFILE` | (empty) / `local` | empty, which keeps today's limits | `local` gives slow local models enough time: 300s per analysis, 45s per action, 100s per answer, 90s held back to write the answer |
| `COCKPIT_V4_INTENT_CHECK` | `enforce` / `shadow` / `off` | **`enforce`** | Refuses plans that answer a different period or entity than the one asked about. `off` restores the old behaviour |
| `COCKPIT_V4_EXAMPLES` | `on` / (empty) | off | Shows the model two similar solved questions |

## If something goes wrong

- **The app won't start:** run `make stop`, then `make app` again. Check `~/.creditprobe/cockpit_v4_round_h_live_pass/logs/api.log`.
- **The eval stops with a connection error:** the app is not running on port 8414. Start it, then rerun the same command. The output file is written only at the END of a run, so an interrupted run must start again. To limit the risk, run it in two halves: `--domain corporate` then `--domain retail`, each with its own `--out` file.
- **You want to undo everything:** `git checkout wip/baseline-2026-10-04`. That is the snapshot taken before any change.
