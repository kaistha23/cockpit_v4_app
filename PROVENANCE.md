# Provenance

This repo is a **V4-only copy** of CreditProbe Cockpit V4. It is part of the modular split into:
- `cockpit_v4_app`: the analyst app and UI (this repo)
- `creditprobe_ingest`: data import, releases and governance; owns the lake
- `creditprobe_rag`: embeddings, retrieval and evaluation

## Source

| | |
|---|---|
| Repository | `~/repos/CreditProbe_AdvancedCockpit_Round_H_SHARE` (frozen; never modified) |
| Sub-folder | `IPM_V2-CockpitLiveUAT/` |
| Commit | `a90e08220168f89fbcfd71a174e4f50925f29346` |
| Method | `git archive` of the committed state. Uncommitted edits to four evidence JSONs in the source were deliberately excluded. |

## Copied paths
- **Python packages:** `backend/__init__.py`, `backend/config.py`, `backend/llm/`, `backend/cockpit_agentic/` (V3; V4 imports it), `backend/cockpit_v4/`
- **Scripts:** `scripts/cockpit_v4/`, `scripts/sql_console/`
- **Tests:** `tests/__init__.py`, `tests/cockpit_v4/`, `tests/evals/cockpit_v4_clarify/`
- **Config and docs:** `config/cockpit_v4/price_card*.json`, `docs/cockpit_v4/`, `docs/product_knowledge/`
- **Frontend:** `frontend/src/components/cockpit-v4/`, `frontend/src/app/cockpit/`, `frontend/src/components/layout/content-width.tsx`, `frontend/src/lib/{api,runtime}.ts`. A new minimal Next.js shell was written around these.

## Data (gitignored, copied with rsync)
- `data/cockpit_v4/v4-saudi-20q-v1/`: the legacy test release used by `tests/cockpit_v4`.
- `data/cockpit_v4_lake`: a **symlink** to the shared lake `~/creditprobe_data/lake/`. `creditprobe_ingest` owns that lake. Phase 8 replaces the symlink with configuration.

## Not copied
- The rest of the product: V2/V3 UIs, Early Warning, auth, Postgres models, Dash apps, regulatory and teaching modules.
