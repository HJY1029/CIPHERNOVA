# Experiment Guide

This guide explains how to reproduce paper tables and metrics (`paperzh.tex` / `paperEn.tex` / `ICICS模版/`) using scripts in this repo. Any experiment that **actually calls a model** uses the same pipeline as the **Web UI**: `CryptoAgent.generate_and_save`. Scripts that only read history or aggregate tables **do not** consume API quota.

**Related-work comparison** (SecCoder, SVEN, DES 12-cell grid, etc.) is documented separately: **[RELATED_WORK_COMPARISON.md](RELATED_WORK_COMPARISON.md)**.

---

## Three things to know first

### 1) Where to run commands

Always from the **repository root** `aicrypto-helper` (scripts add the root to `sys.path` automatically).

```bash
# WSL (my usual setup)
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
```

```powershell
# Windows PowerShell (without WSL)
cd D:\aicrypto-helper
```

### 2) `--invoke` vs. no invoke

| Case | Calls LLM? | Typical use |
|------|------------|-------------|
| **Without** `--invoke` / `--live` | **No** | Task scale, prompt length stats (dry-run) |
| **With** `--invoke` or `--live` | **Yes**, via `generate_and_save` | Real experiments; uses API quota |

If a run finishes in seconds and GSR/VPR/FTPR are all 0: you likely forgot **`--invoke`**, API keys are missing, or **C/C++ build tools** are not installed. Inspect `--json-output` for `error` and `validation_hint`.

### 3) Pass-rate metrics

| Metric | Meaning |
|--------|---------|
| **GSR** | Generation success (non-empty, sufficient length) |
| **VPR** | Validation pass (`CodeValidator`) |
| **FTPR** | Functional test pass (matches `test_data.yaml` standard vectors) |

The LLM performance table also reports **FGPR (first-generation pass rate)**: share of sessions with `attempts==1` and `test_success==true`. See **Experiment 4** below.

---

## Environment and config files

| Item | Path / notes |
|------|----------------|
| **Main config** | `config.yaml` (LLM providers, modes, distillation, etc.) |
| **Standard vectors** | `test_data.yaml`, `openssl_test_data.yaml` |
| **History DB** | Default `code_history.db` (same as Web UI) |
| **Performance log** | Default `experiments/results/llm_performance.json` (failures / multi-round detail) |
| **API keys** | Environment variables or `.api_keys.json` at repo root (shared with Web “save keys”) |

**C/C++ experiments** are best run on **WSL2 Ubuntu** with:

```bash
sudo apt-get update
sudo apt-get install -y build-essential gcc g++ libssl-dev pkg-config
cd "$ROOT"
pip install -r requirements.txt
pip install pyyaml
```

---

## Experiment overview (by paper use)

| # | Paper content | Script | Calls LLM? |
|---|---------------|--------|------------|
| **1** | Main ablation (`tab:main_ablation`, etc.) | `run_paper_ablation.py --suite main` | Only with `--invoke` |
| **2** | Prompt strategy ablation | `run_paper_ablation.py --suite prompt` | Same |
| **3** | Five prompt tiers / batch invoke | `run_prompt_ablation.py` | Same |
| **4** | Per-LLM performance `tab:llm_performance` | `extract_llm_performance_from_history.py` | **No** |
| **5** | Code extraction `tab:extraction_accuracy` | `run_code_extraction_eval.py` | **No** |
| **6** | Error taxonomy & repair `tab:error_repair` | `run_error_repair_table.py` | **No** |
| **7** | Local Qwen distillation off vs. on | `run_qwen_distillation_ablation.py`, etc. | With `--invoke` |
| **8** | Related work DES 12 cells | [RELATED_WORK_COMPARISON.md](RELATED_WORK_COMPARISON.md) | Depends on baseline |

---

## Experiment 1 — Main ablation (system configuration × GSR/VPR/FTPR)

**What it does:** Compare full method / no test feedback / no hierarchical prompts / minimal prompt; metrics match the Web UI.

**Script:** `experiments/run_paper_ablation.py --suite main`

| Table row | Implementation |
|-----------|----------------|
| Full proposed method | Default kwargs |
| Without test-feedback improvement | `_ablation_no_test_feedback: true` |
| Without hierarchical prompts | `prompt_ablation: common_only` |
| Without structured prompts | `prompt_ablation: no_prompt` |

**Preview scale (no LLM):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_paper_ablation.py --suite main --dry-run
```

**Full run (default: all enabled cloud providers; long and costly):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_paper_ablation.py --suite main --invoke \
  --max-retries 3 \
  -o experiments/results/ablation_main.md \
  --json-output experiments/results/ablation_main.json
```

**Smoke test (one provider, few cells, Python only):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_paper_ablation.py --suite main --invoke \
  --provider deepseek \
  --max-cases 4 \
  --languages python \
  --max-retries 3 \
  -o experiments/results/ablation_main_try.md \
  --json-output experiments/results/ablation_main_try.json
```

In JSON, `performance_drop_vs_full` is the VPR/FTPR gap vs. the full method. Check `variant_run_summary` and the “run diagnostics” section at the end of the Markdown output.

---

## Experiment 2 — Prompt strategy ablation (GSR/VPR/FTPR)

**What it does:** Compare five `prompt_ablation` tiers (aligned with `utils/prompt_loader.py`) for the paper prompt-ablation table.

| Paper row | `prompt_ablation` |
|-----------|-------------------|
| Generic prompt only | `common_only` |
| Algorithm-specific prompt | `common_algorithm` |
| LLM-specific prompt | `common_algorithm_llm_main` |
| Four-layer composable (our method) | `full` (default; same as Web when ablation is unset) |

Extreme tier `llm_main_only` and others: see `run_prompt_ablation.py` help.

**Full command (invoke + save results):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_paper_ablation.py --suite prompt --invoke \
  --max-retries 3 \
  -o experiments/results/ablation_prompt.md \
  --json-output experiments/results/ablation_prompt.json
```

**Small smoke test:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_paper_ablation.py --suite prompt --invoke \
  --provider deepseek \
  --modes ECB \
  --languages python \
  --max-cases 1 \
  --max-retries 3
```

---

## Experiment 3 — Prompt ablation script (length stats or batch invoke)

**What it does:** `run_prompt_ablation.py` alone. Without `--algorithm`, default grid is **DES × cloud providers × des_modes × languages**. With `--algorithm`, single-task dry-run mode.

**Five tiers (aligned with code):**

| Tier | Summary |
|------|---------|
| `full` | Same as Web full prompt |
| `common_only` | `prompts/common` only |
| `common_algorithm` | common + `prompts/algorithms` |
| `common_algorithm_llm_main` | Above + `llms/<provider>/llm.yaml`, no long OpenSSL reference |
| `llm_main_only` | Extreme baseline: mostly LLM block + task line |

**Prompt length only (no LLM):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_prompt_ablation.py \
  -o experiments/results/prompt_lens.md \
  --json-output experiments/results/prompt_lens.json
```

**Smaller dry-run:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_prompt_ablation.py \
  --modes ECB \
  --languages python \
  --provider deepseek
```

**Batch invoke (default cloud providers):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_prompt_ablation.py --invoke \
  --max-retries 3 \
  -o experiments/results/prompt_invoke.md \
  --json-output experiments/results/prompt_invoke.json
```

**Single cell invoke:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_prompt_ablation.py --invoke \
  --provider deepseek \
  --algorithm DES \
  --mode ECB \
  --language python \
  --max-retries 3
```

On WSL use `\` for line continuation; on Windows cmd use `^`; PowerShell: prefer one line.

---

## Experiment 4 — LLM performance from Web history (`tab:llm_performance`)

**What it does:** Read `code_history.db` (optionally merge `llm_performance.json`), aggregate by provider into the paper **8-column table** (GSR / VPR / FTPR / **FGPR** / avg. generation time, etc.), optional 2×2 PDF figure.

**Important:** On the success path, `generate_and_save` often **only writes history after tests pass**, so the DB skews toward successes. The default table uses the **45-slot config grid** as denominator; uncovered slots count as 0%.

**Default (table to terminal + PDF figure):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py
```

**Table only, no figure:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py --no-figure
```

**Legend + output path:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py \
  --with-legend \
  -o experiments/results/llm_paper_table.md
```

**JSON + companion Markdown:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py \
  --format json \
  -o experiments/results/history_stats.json
```

**Custom DB or performance log:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py \
  --db "$ROOT/code_history.db" \
  --performance-json experiments/results/llm_performance.json
```

---

## Experiment 5 — Code extraction accuracy (`tab:extraction_accuracy`)

**What it does:** Locally evaluate three strategies for extracting code from LLM replies; can batch-read the `code` field from history.

| Strategy | Implementation |
|----------|----------------|
| Markdown fences | `extract_code_markdown_fence_only` |
| Plain-text recognition | `extract_code_plain_text_recognition` |
| Full multi-stage | `extract_code` |

**Built-in samples:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_code_extraction_eval.py
```

**From history (recommended for the paper):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_code_extraction_eval.py --from-history \
  -o experiments/results/experiments/extraction_from_history.md \
  --json-output experiments/results/experiments/extraction_from_history.json
```

---

## Experiment 6 — Error taxonomy and repair (`tab:error_repair`)

**What it does:** Aggregate four error types (algorithm understanding / implementation detail / environment / code structure): counts, shares, and post-repair pass rate. **No LLM** (usually seconds).

| Data source | When to use |
|-------------|-------------|
| **`--from-history` (recommended for paper)** | Merge `code_history.db` + `llm_performance.json`; infer multi-round repair |
| **`--from-performance`** | Performance JSON only |
| **Default (no flags)** | Static placeholder `experiments/data/error_repair_table.json` |

**Full command (aligned with DeepSeek table note in paper):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_error_repair_table.py --from-history --provider deepseek \
  -o experiments/results/experiments/error_repair_from_history.md \
  --write-json experiments/results/experiments/error_repair_from_history.json
```

**Performance log only:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_error_repair_table.py --from-performance \
  -o experiments/results/error_repair_from_performance.md
```

If local C/C++ **never compiles**, post-repair rates stay low—document the environment in the paper, or refresh logs per **§6.1** before re-aggregating.

### 6.1 Refreshing the error-repair table: accumulate repair records first

```bash
# Optional: backup old log so only this round counts
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
mv llm_performance.json llm_performance.json.bak 2>/dev/null || true

export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_paper_ablation.py --suite prompt --invoke \
  --provider deepseek \
  --languages python \
  --max-cases 2 \
  --max-retries 3 \
  -o experiments/results/ablation_try.md \
  --json-output experiments/results/ablation_try.json

# Re-aggregate
python experiments/run_error_repair_table.py --from-history --provider deepseek \
  -o experiments/results/experiments/error_repair_from_history.md \
  --write-json experiments/results/experiments/error_repair_payload.json
```

Taxonomy rules: `experiments/error_repair_aggregate.py` (keyword heuristics). Not equivalent to a fully hand-labeled subset—state the data source in the paper.

---

## Experiment 7 — Local Qwen distillation (off vs. on)

**What it does:** On the same **config grid** (default 45 cells), run two phases in series: (1) distillation disabled (2) distillation per config (teacher JSONL). **Different axis** from `prompt_ablation`; unrelated to related-work comparison.

**Prerequisite:** Entries in `data/distillation_teacher.jsonl`. Run cloud providers first; `auto_collect_cloud_teachers` can append teachers from successful runs.

| Script | Default cells |
|--------|----------------|
| `run_qwen_distillation_ablation.py` | Full grid |
| `run_qwen_distill_des.py` | 12 |
| `run_qwen_distill_aes.py` | 18 |
| `run_qwen_distill_rsa.py` | 3 |
| `run_qwen_distill_sm4.py` | 12 |

**Scale preview:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_qwen_distillation_ablation.py --dry-run
python experiments/run_qwen_distill_des.py --dry-run
```

**DES only, two phases + checkpoint:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_qwen_distill_des.py --invoke \
  --checkpoint experiments/results/distill_des_ckpt.json
```

**Full grid (try `--limit 5` first):**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_qwen_distillation_ablation.py --invoke \
  -o experiments/results/qwen_distill_compare.md \
  --json-output experiments/results/qwen_distill_compare.json
```

With `--invoke`, behavior matches Web batch (`web.server._batch_generate_single`; cells already passing in history skip the LLM). Useful flags: `--skip-baseline`, `--skip-distill`, `--resume`, `--algorithm DES`.

Distillation switches: `config.yaml` → `distillation` and `utils/distillation.py` → `ABLATION_FLAGS`.

---

## Experiment 8 — Related-work comparison (DES 12 cells)

Separate from our main experiments: each baseline delivers 12 files `des_<mode>.{py,c,cpp}`; score with `rw_des_protocol_eval.py score` and **`--no-canonical-whole-file`**.

Full steps: **[RELATED_WORK_COMPARISON.md](RELATED_WORK_COMPARISON.md)**.

---

## 9. Do not mix two reporting conventions

The paper often cites both:

- **History aggregation** (stats after Web use → `code_history`, or FGPR from merged detail)
- **Fixed-batch main experiments** (tables from `run_paper_ablation` / invoke on a defined subset)

Subset, denominator, and “success-only in DB” may differ—**do not compare them directly**. In the manuscript, state time range, provider, algorithm/mode/language subset, whether full DES modes, etc.

---

## 10. Where generated code is saved

Experiment scripts **do not print full model replies** to the terminal. During invoke you see progress lines; code is under **`config.yaml` → `output_dir`** (default `generated_code/`); structured output is in **`--json-output`** → `results` / `variant_run_summary`.

---

## 11. Troubleshooting

| Symptom | What to do |
|---------|------------|
| Finishes in seconds; all metrics 0 | Add `--invoke`; check keys / `.api_keys.json`; install `libssl-dev` for C/C++ |
| `run_prompt_ablation --invoke` all skipped | Agent init failed—read “skipped … init failed” in terminal |
| `extract` cannot find DB | Fix `--db`; or generate a few runs in the Web UI first |
| History FTPR looks too high | Note “success-only in DB”; default 45-slot denominator in `extract` |
| Error-repair rate stays low | Local compile failures; use WSL or re-run §6.1 |
| dry-run `chars` unexpected | Check `--provider`, `--language`, RSA `--operation` |

Add **`--verbose`** for full INFO logs.

---

## 12. Script index

| Path | Role |
|------|------|
| `experiments/run_paper_ablation.py` | Main / prompt strategy ablation |
| `experiments/run_prompt_ablation.py` | Five-tier prompt dry-run / invoke |
| `experiments/extract_llm_performance_from_history.py` | LLM performance table + optional figure |
| `experiments/run_code_extraction_eval.py` | Code extraction evaluation |
| `experiments/run_error_repair_table.py` | Error-repair table |
| `experiments/run_qwen_distillation_ablation.py` | Qwen distillation off/on |
| `experiments/run_qwen_distill_{des,aes,rsa,sm4}.py` | Per-algorithm distillation |
| `utils/prompt_loader.py` | `_ablation_allows`, `get_prompt` |
| `agent/prompt_builder.py` | Passes `prompt_ablation` |
| `utils/history_manager.py` | `code_history` read/write |

If failures are later stored in history too, update `agent/code_saver.py` and the reporting notes for **Experiment 4**.
