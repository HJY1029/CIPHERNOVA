# Related-Work Comparison (DES 12 Cells)

This guide reproduces paper table **`tab:rw_same_protocol_des`**: a **same-protocol DES** comparison. Each baseline must deliver the **same 12 source files**, then the **same scoring script** computes **GSR / VPR / FTPR**—that is what makes the comparison fair.

---

## What is being compared?

- **Task:** **DES** only, four modes **ECB / CBC / CFB / OFB**, three languages **Python / C / C++** → **12 cells**.
- **Deliverable:** One directory per baseline with exactly 12 files (see table below).
- **Scoring:** From repo root, standard vectors in **`test_data.yaml`**, via **`rw_des_protocol_eval.py score`**.
- **Important:** Score **baseline-generated code as-is**. For external baselines always pass **`--no-canonical-whole-file`** (do not substitute this repo’s OpenSSL whole-file templates to inflate scores).
- **Forbidden:** Generate with this repo’s **`CryptoAgent` / `prompts/`**, then label results as SecCoder, SVEN, etc.—that is **our method**, not related-work reproduction.

### Required filenames (12 files)

| Mode | Python | C | C++ |
|------|--------|---|-----|
| ECB | `des_ecb.py` | `des_ecb.c` | `des_ecb.cpp` |
| CBC | `des_cbc.py` | `des_cbc.c` | `des_cbc.cpp` |
| CFB | `des_cfb.py` | `des_cfb.c` | `des_cfb.cpp` |
| OFB | `des_ofb.py` | `des_ofb.c` | `des_ofb.cpp` |

### Metrics

| Metric | Meaning |
|--------|---------|
| **GSR** | Generation success |
| **VPR** | Passes this repo’s **`CodeValidator`** (syntax/structure, etc.) |
| **FTPR** | Passes **`CodeTester`**; ciphertext matches **`test_data.yaml`** |

---

## Before you start

Commands assume **repository root**. I use **WSL + Git Bash** daily; adjust paths for your machine.

### 1) Repository root

```bash
# WSL example (D: drive mounted)
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
```

```powershell
# Windows PowerShell (no WSL)
cd D:\aicrypto-helper
```

### 2) Python dependencies

```bash
pip install pyyaml
```

For **Self-Refine / SecCoder / AgentCoder** glue scripts:

```bash
pip install -U "openai>=1.0"
```

### 3) API keys (DeepSeek-style runs)

WSL:

```bash
export DEEPSEEK_API_KEY="your-api-key"
# Or OpenAI-compatible:
# export OPENAI_API_KEY="your-api-key"
```

PowerShell:

```powershell
$env:DEEPSEEK_API_KEY = "your-api-key"
```

### 4) SVEN notes (optional)

- Cannot reach Hugging Face: `export HF_ENDPOINT=https://hf-mirror.com`
- `ruamel.yaml` conflict: `pip install 'ruamel.yaml>=0.17,<0.19'`
- Local model already downloaded: `export SVEN_MODEL_DIR=/path/to/codegen-350m`

---

## Experiment 0 — Prepare upstream repositories

**What it does:** Clone SVEN, AgentCoder, and self-refine into `external/`. SecCoder official artifacts are downloaded separately (Experiment 8).

**Outputs:** `external/sven`, `external/AgentCoder`, `external/self-refine`, etc.

**Full command (from repo root):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
bash experiments/related_work/clone_baselines.sh
```

---

## Experiment 1 — Export the 12-cell task list

**What it does:** Build **`experiments/rw_des_tasks.jsonl`** from `test_data.yaml`. Later baseline scripts read algorithm, mode, language, and **expected filename** per row.

**Output:** `experiments/rw_des_tasks.jsonl`

**Full command:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/related_work/rw_des_protocol_eval.py export -o experiments/rw_des_tasks.jsonl
```

PowerShell one-liner:

```powershell
cd D:\aicrypto-helper
python experiments/related_work/rw_des_protocol_eval.py export -o experiments/rw_des_tasks.jsonl
```

---

## Experiment 2 — SVEN (official stack + local small model)

**What it does:** Run DES HumanEval-style tasks in the **official SVEN repo** → extract **12 files** → score. The only related-work track here that **does not use an API**, but needs PyTorch / SVEN dependencies.

**Outputs:**

- Generated code: `experiments/sven_des_outputs/`
- Metrics JSON: `experiments/rw_sven.json`

**Full command (recommended one-shot):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
# Optional: mirror for Hugging Face
# export HF_ENDPOINT=https://hf-mirror.com
# Optional: local model directory
# export SVEN_MODEL_DIR=/path/to/codegen-350m
bash experiments/related_work/run_rw_sven_des.sh
```

Equivalent steps (for understanding only):

```bash
cd "$ROOT"
python experiments/related_work/rw_des_protocol_eval.py export -o experiments/rw_des_tasks.jsonl
python experiments/related_work/sven_des_problem_yamls.py -o external/sven/data_eval/des_rw
# … run human_eval_gen.py under external/sven/scripts …
python experiments/related_work/sven_des_extract_completions.py \
  --tasks experiments/rw_des_tasks.jsonl \
  --yaml-dir external/sven/experiments/des_rw/sven-des-lm-350m \
  --out-dir experiments/sven_des_outputs
python experiments/related_work/rw_des_protocol_eval.py score \
  --inputs experiments/sven_des_outputs \
  --arm sven_lm_350m \
  --no-canonical-whole-file \
  -o experiments/rw_sven.json
```

---

## Experiment 3 — Self-Refine + DeepSeek

**What it does:** This repo’s **Self-Refine iterative refine** implementation (**not** paper prompts under `prompts/`). DeepSeek generates 12 files and scores them. Requires an API key.

**Outputs:**

- `experiments/selfrefine_deepseek_out/`
- `experiments/rw_selfrefine_deepseek.json`

**Full command (one-shot):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
bash experiments/related_work/run_rw_selfrefine_des.sh
```

Equivalent pipeline (matches the shell script):

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
python experiments/related_work/run_rw_baseline_pipeline.py \
  --out-dir experiments/selfrefine_deepseek_out \
  --arm selfrefine_deepseek \
  --json-out experiments/rw_selfrefine_deepseek.json \
  -- \
  python experiments/related_work/selfrefine_des_deepseek.py \
    --out-dir experiments/selfrefine_deepseek_out \
    --max-iterations 3
```

Optional env: `SELFREFINE_MAX_ITERS` (default 3), `SELFREFINE_OUT`.

---

## Experiment 4 — SecCoder + DeepSeek (glue reproduction)

**What it does:** SecCoder’s **official artifact has no one-click DES script**. This repo provides **retrieval placeholder + per-cell LM glue** (`seccoder_des_glue_generate.py`), then scoring. Not a full CWE pipeline replay, but aligned with the paper’s “SecCoder + DeepSeek” row.

**Outputs:**

- `experiments/seccoder_des_out/`
- `experiments/rw_seccoder.json`

**Full command (generate + score):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
# For random_file retrieval placeholder, need a snippets dir (Experiment 8 or your own):
# export SECCODER_RETRIEVAL_MODE=random_file
# export SECCODER_SECURITY_SNIPPETS_DIR="$ROOT/external/SecCoder_acl_security_snippets"
bash experiments/related_work/run_seccoder_des_lm_pipeline.sh
```

**Re-score only** (12 files already present):

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export SECCODER_DES_OUT="$ROOT/experiments/seccoder_des_out"
export SECCODER_ARM="seccoder_glue_repro"
bash experiments/related_work/run_rw_seccoder_des.sh
```

**Generate only, skip score:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
export SKIP_SCORE=1
bash experiments/related_work/run_seccoder_des_lm_pipeline.sh
```

---

## Experiment 5 — AgentCoder + DeepSeek (Programmer-only glue)

**What it does:** AgentCoder upstream targets HumanEval—**no ready-made DES 12-cell pipeline**. This repo uses **Programmer-style** per-cell LM calls (`agentcoder_des_programmer_glue.py`), then scoring.

**Outputs:**

- `experiments/agentcoder_des_out/`
- `experiments/rw_agentcoder.json`

**Full command (one-shot):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
bash experiments/related_work/run_agentcoder_des_lm_pipeline.sh
```

**Re-score only:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export AGENTCODER_DES_OUT="$ROOT/experiments/agentcoder_des_out"
export AGENTCODER_ARM="agentcoder_des"
bash experiments/related_work/run_rw_agentcoder_des.sh
```

---

## Experiment 6 — Score an existing directory only

**What it does:** You already have 12 files with the required names; recompute GSR/VPR/FTPR under paper conventions.

**Full command (adjust path and arm label):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/related_work/rw_des_protocol_eval.py score \
  --inputs experiments/seccoder_des_out \
  --arm seccoder_glue_repro \
  --no-canonical-whole-file \
  -o experiments/rw_seccoder.json
```

Windows one-liner:

```powershell
cd D:\aicrypto-helper
python experiments/related_work/rw_des_protocol_eval.py score --inputs experiments\agentcoder_des_out --arm agentcoder_des --no-canonical-whole-file -o experiments\rw_agentcoder.json
```

---

## Experiment 7 — Aggregate into a paper table (Markdown / LaTeX)

**What it does:** Merge existing `experiments/rw_*.json` into one GSR/VPR/FTPR table.

**Full command:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
bash experiments/related_work/run_rw_aggregate_table.sh experiments/rw_rates_table.md
```

Or Python directly (second arg optional `latex`):

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/related_work/rw_aggregate_rates.py \
  --preset related-work \
  -o experiments/rw_rates_table.md \
  --format markdown
```

---

## Experiment 8 (optional) — Download SecCoder ACL artifacts

**What it does:** Download EMNLP 2024 SecCoder **software.zip / data.zip** (`retriever_*.py`, etc.) for **CWE retrieval demos**—**not** required for the DES 12-cell main comparison.

**Full command:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
bash experiments/related_work/fetch_seccoder_acl.sh
```

Unzip example:

```bash
cd "$ROOT/external"
unzip -q -o 2024.emnlp-main.806.software.zip -d SecCoder_acl_software
```

For SecCoder glue with `SECCODER_RETRIEVAL_MODE=random_file`, place security-related source snippets under  
`external/SecCoder_acl_security_snippets/` (or set `SECCODER_SECURITY_SNIPPETS_DIR`).

---

## How to include our method (CIPHERNOVA) in the same table?

The related-work table scores **12 files delivered by external baselines only**. **Our method** uses the main experiment pipeline (test feedback + hierarchical prompts), not `run_rw_*.sh`.

To reproduce our DES GSR/VPR/FTPR, see **[EXPERIMENT_GUIDE.md](EXPERIMENT_GUIDE.md)**, e.g.:

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_paper_ablation.py --suite main --invoke \
  --provider deepseek \
  --algorithms DES \
  --languages python,c,cpp
```

Merge with `experiments/rw_*.json` via **`rw_aggregate_rates.py`**, or fill LaTeX table `tab:rw_same_protocol_des` manually.

---

## Output file map

| Baseline | Metrics JSON | 12 source files |
|----------|--------------|-----------------|
| SVEN | `experiments/rw_sven.json` | `experiments/sven_des_outputs/` |
| Self-Refine + DeepSeek | `experiments/rw_selfrefine_deepseek.json` | `experiments/selfrefine_deepseek_out/` |
| SecCoder + DeepSeek (glue) | `experiments/rw_seccoder.json` | `experiments/seccoder_des_out/` |
| AgentCoder + DeepSeek (glue) | `experiments/rw_agentcoder.json` | `experiments/agentcoder_des_out/` |
| Summary table | `experiments/rw_rates_table.md` | — |

Reference rates once recorded in docs (%, **re-run locally for authoritative numbers**):

- Self-Refine: 100 / 83.33 / 58.33  
- SecCoder: 100 / 66.67 / 66.67  
- AgentCoder: 100 / 41.67 / 0  
- SVEN: 100 / 0 / 0  
- Ours + DeepSeek: 100 / 100 / 100  

---

## Recommended run order (save time)

1. `clone_baselines.sh` (if running SVEN)  
2. `rw_des_protocol_eval.py export` (once)  
3. Per baseline: `run_rw_sven_des.sh` → `run_rw_selfrefine_des.sh` → `run_seccoder_des_lm_pipeline.sh` → `run_agentcoder_des_lm_pipeline.sh`  
4. `run_rw_aggregate_table.sh` for the combined table  

API baselines can run in parallel terminals; default output directories do not clash.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `python: can't open file .../experiments/...` | Not at repo root; `cd` to `ROOT` |
| Missing PyYAML | `pip install pyyaml` |
| No API key detected | `export DEEPSEEK_API_KEY=...` |
| SecCoder/AgentCoder found N/12 files | Run `run_*_lm_pipeline.sh`; check `des_<mode>.py/.c/.cpp` names |
| SVEN model download fails | `export HF_ENDPOINT=https://hf-mirror.com` or set `SVEN_MODEL_DIR` |
| Scores “too good” for baseline raw code | Confirm **`--no-canonical-whole-file`** on score |
| Used `CryptoAgent` but labeled SecCoder | Wrong convention; our method → `run_paper_ablation.py` |

More flags: see headers in `experiments/related_work/run_rw_*.sh`.
