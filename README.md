# CIPHERNOVA (织密新星)

Hierarchical prompts plus test feedback to help users without a cryptography background generate runnable **DES / AES / RSA / SM4** code in **Python, C, and C++**. Supports cloud models (DeepSeek, OpenAI, Claude, Doubao) and local Qwen, with a **Web UI**, CLI, and paper reproduction scripts.

Paper sources: `ICICS模版/ciphernova_icics.tex` (Chinese), `ICICS模版/ciphernova_icics_en.tex` (English).

---

## Quick start

Run commands from the **repository root**. I usually use **WSL**; change paths to match your machine.

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"

pip install -r requirements.txt
pip install pyyaml

# API key: env var or save via Web to .api_keys.json at repo root
export DEEPSEEK_API_KEY="your-api-key"
```

```powershell
cd D:\aicrypto-helper
pip install -r requirements.txt
$env:DEEPSEEK_API_KEY = "your-api-key"
```

For **C/C++ validation**, install the toolchain on WSL Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y build-essential gcc g++ libssl-dev pkg-config
```

---

## Part 1 — Daily use (Web / CLI)

### Web UI (recommended)

**What it does:** Pick algorithm, mode, and language in the browser; generate code and see validation/test results. History is written to `code_history.db`.

**Full commands:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python web/run_server.py
```

Or:

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python -m uvicorn web.server:app --host 127.0.0.1 --port 8000 --reload
```

Open in the browser: **http://127.0.0.1:8000**  
On first use, configure API keys in the UI; experiment scripts share **`.api_keys.json`** at the repo root.

### Interactive CLI

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python main.py
```

Generated code goes to **`generated_code/`** by default. See **`config.yaml`** for settings and **`test_data.yaml`** for standard test vectors.

---

## Part 2 — How to run paper experiments

Two tracks—do not mix their reporting conventions:

| Doc | Content |
|-----|---------|
| **[EXPERIMENT_GUIDE.md](EXPERIMENT_GUIDE.md)** | Our method: main ablation, prompt ablation, LLM performance table, code extraction, error repair, Qwen distillation |
| **[RELATED_WORK_COMPARISON.md](RELATED_WORK_COMPARISON.md)** | External baselines: SVEN, Self-Refine, SecCoder, AgentCoder (DES 12-cell same-protocol comparison) |

### Two rules to remember

1. **Real LLM calls:** Pass **`--invoke`** or **`--live`**; otherwise scripts only dry-run (no API usage).
2. **Metrics:** **GSR** (generation success) → **VPR** (validation pass) → **FTPR** (functional test vs. standard vectors). The LLM summary table also includes **FGPR** (first-generation pass rate; see Experiment 4 in the experiment guide).

---

## Part 3 — Our method (cheat sheet + full commands)

Details: **[EXPERIMENT_GUIDE.md](EXPERIMENT_GUIDE.md)**. Commands below assume you are under `ROOT`.

### Experiment 1 — Main ablation (system configuration)

Compares full method / no test feedback / no hierarchical prompts / minimal prompt, etc.

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"

# Preview scale (no LLM calls)
python experiments/run_paper_ablation.py --suite main --dry-run

# Full run
python experiments/run_paper_ablation.py --suite main --invoke \
  --max-retries 3 \
  -o experiments/results/ablation_main.md \
  --json-output experiments/results/ablation_main.json
```

Smoke test (one provider, few cells, Python only):

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_paper_ablation.py --suite main --invoke \
  --provider deepseek --max-cases 4 --languages python --max-retries 3
```

### Experiment 2 — Prompt strategy ablation

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_paper_ablation.py --suite prompt --invoke \
  --max-retries 3 \
  -o experiments/results/ablation_prompt.md \
  --json-output experiments/results/ablation_prompt.json
```

### Experiment 3 — Five prompt tiers (length / batch invoke)

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"

# Prompt length only (no LLM)
python experiments/run_prompt_ablation.py \
  -o experiments/results/prompt_lens.md \
  --json-output experiments/results/prompt_lens.json

# Batch with real LLM calls
export DEEPSEEK_API_KEY="your-api-key"
python experiments/run_prompt_ablation.py --invoke --max-retries 3 \
  -o experiments/results/prompt_invoke.md \
  --json-output experiments/results/prompt_invoke.json
```

### Experiment 4 — Per-LLM performance table (history only, no LLM)

Merges `code_history.db` and `llm_performance.json`; outputs the paper-style 8-column table (including FGPR) and optional PDF figure.

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py --with-legend \
  -o experiments/results/llm_paper_table.md
```

Table only, no figure:

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py --no-figure
```

### Experiment 5 — Code extraction accuracy

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_code_extraction_eval.py --from-history \
  -o experiments/results/experiments/extraction_from_history.md \
  --json-output experiments/results/experiments/extraction_from_history.json
```

### Experiment 6 — Error taxonomy and repair table

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_error_repair_table.py --from-history --provider deepseek \
  -o experiments/results/experiments/error_repair_from_history.md \
  --write-json experiments/results/experiments/error_repair_from_history.json
```

### Experiment 7 — Local Qwen distillation (off vs. on)

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"

python experiments/run_qwen_distill_des.py --dry-run

python experiments/run_qwen_distill_des.py --invoke \
  --checkpoint experiments/results/distill_des_ckpt.json

python experiments/run_qwen_distillation_ablation.py --invoke \
  -o experiments/results/qwen_distill_compare.md \
  --json-output experiments/results/qwen_distill_compare.json
```

---

## Part 4 — Related-work comparison (DES 12 cells)

Separate from our main experiments: each baseline must deliver 12 files `des_<mode>.{py,c,cpp}`, scored with the same script, and **`--no-canonical-whole-file`** must be set.

Full steps: **[RELATED_WORK_COMPARISON.md](RELATED_WORK_COMPARISON.md)**.

**Common commands:**

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"

# Clone upstream repos
bash experiments/related_work/clone_baselines.sh

# Export task list
python experiments/related_work/rw_des_protocol_eval.py export -o experiments/rw_des_tasks.jsonl

# Baselines (API-based runs need DEEPSEEK_API_KEY)
export DEEPSEEK_API_KEY="your-api-key"
bash experiments/related_work/run_rw_sven_des.sh
bash experiments/related_work/run_rw_selfrefine_des.sh
bash experiments/related_work/run_seccoder_des_lm_pipeline.sh
bash experiments/related_work/run_agentcoder_des_lm_pipeline.sh

# Aggregate GSR / VPR / FTPR table
bash experiments/related_work/run_rw_aggregate_table.sh experiments/rw_rates_table.md
```

---

## Project layout (experiment-relevant)

```
aicrypto-helper/
├── agent/                 # CryptoAgent: generate, validate, save
├── prompts/               # Hierarchical prompts (common / algorithms / llms)
├── utils/                 # Config, test data, history DB, distillation
├── web/                   # FastAPI + frontend
├── experiments/           # Paper and related-work reproduction
│   ├── related_work/      # DES 12-cell baseline comparison
│   └── results/           # Tables, JSON, figures (recommended output dir)
├── ICICS模版/             # Paper LaTeX
├── config.yaml            # LLMs, algorithm grid, distillation
├── test_data.yaml         # Standard test vectors
├── code_history.db        # Web/Agent success records (default path)
├── generated_code/        # Generated code output
├── EXPERIMENT_GUIDE.md        # Full experiment guide (English)
└── RELATED_WORK_COMPARISON.md # Related-work guide (English)
```

---

## Configuration and API keys

| Method | Notes |
|--------|--------|
| **Web UI** | Save in UI → `.api_keys.json` (read automatically by experiment scripts) |
| **Environment** | `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DOUBAO_API_KEY`, etc. |
| **config.yaml** | Enable providers, default models, output dir, distillation paths |

For Doubao and similar providers, set the correct **endpoint ID** in `config.yaml` (e.g. `ep-xxx`).

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Run finishes in seconds; all metrics 0 | Missing `--invoke`; API key not set; C/C++ missing `libssl-dev` |
| History table pass rates look too high | Mostly successful runs are stored; document data source and 45-slot denominator in the paper |
| Error-repair rates very low | Local compile failures; see experiment guide §6.1—re-run with `--invoke` then re-aggregate |
| Related-work scores too high | Forgot `--no-canonical-whole-file` |
| `pdflatex` not found | Install a TeX distribution; compile under `ICICS模版/` |

More detail: **[EXPERIMENT_GUIDE.md](EXPERIMENT_GUIDE.md)** and **[RELATED_WORK_COMPARISON.md](RELATED_WORK_COMPARISON.md)**.

---

# CIPHERNOVA（织密新星）

用分层提示 + 测试反馈，帮非密码学背景的人生成 **DES / AES / RSA / SM4** 的可运行代码（Python、C、C++）。支持 DeepSeek、OpenAI、Claude、Doubao 等云端模型，以及本地 Qwen；提供 **Web 界面**、命令行和论文复现脚本。

---

## 快速开始

下面命令都在**仓库根目录**执行。我日常用 **WSL**；路径请改成你自己的。

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"

pip install -r requirements.txt
pip install pyyaml

# API Key：环境变量或 Web 保存到项目根 .api_keys.json
export DEEPSEEK_API_KEY="你的key"
```

```powershell
cd D:\aicrypto-helper
pip install -r requirements.txt
$env:DEEPSEEK_API_KEY = "你的key"
```

**C/C++ 验证**建议在 WSL Ubuntu 上装编译链：

```bash
sudo apt-get update
sudo apt-get install -y build-essential gcc g++ libssl-dev pkg-config
```

---

## 板块一：日常使用（Web / 命令行）

### Web 界面（推荐）

**做什么**：在浏览器里选算法、模式、语言，一键生成并看验证/测试结果；历史写入 `code_history.db`。

**完整命令**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python web/run_server.py
```

或：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python -m uvicorn web.server:app --host 127.0.0.1 --port 8000 --reload
```

浏览器打开：**http://127.0.0.1:8000**  
首次可在页面「配置 API 密钥」，与实验脚本共用的文件为 **`.api_keys.json`**。

### 命令行交互

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python main.py
```

生成代码默认在 **`generated_code/`**；配置见 **`config.yaml`**，标准测试向量见 **`test_data.yaml`**。

---

## 板块二：论文实验怎么跑？

实验分两条线，不要混用口径：

| 文档                                           | 内容                                                         |
| ---------------------------------------------- | ------------------------------------------------------------ |
| **[实验指南.md](实验指南.md)**                 | 本文方法：主消融、提示消融、LLM 性能表、代码提取、错误修复、Qwen 蒸馏等 |
| **[相关工作对比实验.md](相关工作对比实验.md)** | 外部基线：SVEN、Self-Refine、SecCoder、AgentCoder（DES 12 格同协议对比） |

### 先记住两件事

1. **真调 LLM**：脚本必须加 **`--invoke`** 或 **`--live`**，否则只是 dry-run（不调 API）。
2. **指标**：**GSR**（生成成功）→ **VPR**（验证通过）→ **FTPR**（标准向量功能测试通过）；LLM 总表还有 **FGPR**（首次生成即通过，见实验指南「实验 4」）。

---

## 板块三：本文方法实验（速查 + 完整命令）

详细说明见 **[实验指南.md](实验指南.md)**。下面是常用一条龙命令（路径均为 `ROOT` 下）。

### 实验 1 — 主消融（系统配置）

对比完整方法 / 无测试反馈 / 无分层提示 / 无提示等。

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"

# 先看规模（不调 LLM）
python experiments/run_paper_ablation.py --suite main --dry-run

# 真跑
python experiments/run_paper_ablation.py --suite main --invoke \
  --max-retries 3 \
  -o experiments/results/ablation_main.md \
  --json-output experiments/results/ablation_main.json
```

试跑（单家、少量格、仅 Python）：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
python experiments/run_paper_ablation.py --suite main --invoke \
  --provider deepseek --max-cases 4 --languages python --max-retries 3
```

### 实验 2 — 提示策略消融

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
python experiments/run_paper_ablation.py --suite prompt --invoke \
  --max-retries 3 \
  -o experiments/results/ablation_prompt.md \
  --json-output experiments/results/ablation_prompt.json
```

### 实验 3 — 五档 Prompt 长度 / 批量 invoke

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"

# 只统计 prompt 长度
python experiments/run_prompt_ablation.py \
  -o experiments/results/prompt_lens.md \
  --json-output experiments/results/prompt_lens.json

# 批量真调 LLM
export DEEPSEEK_API_KEY="你的key"
python experiments/run_prompt_ablation.py --invoke --max-retries 3 \
  -o experiments/results/prompt_invoke.md \
  --json-output experiments/results/prompt_invoke.json
```

### 实验 4 — 各 LLM 性能表（读历史，不调 LLM）

合并 `code_history.db` 与 `llm_performance.json`，出论文 8 列表（含 FGPR），可选 PDF 图。

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py --with-legend \
  -o experiments/results/llm_paper_table.md
```

只要表、不要图：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py --no-figure
```

### 实验 5 — 代码提取准确率

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_code_extraction_eval.py --from-history \
  -o experiments/results/experiments/extraction_from_history.md \
  --json-output experiments/results/experiments/extraction_from_history.json
```

### 实验 6 — 错误分类与修复表

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_error_repair_table.py --from-history --provider deepseek \
  -o experiments/results/experiments/error_repair_from_history.md \
  --write-json experiments/results/experiments/error_repair_from_history.json
```

### 实验 7 — 本地 Qwen 蒸馏前后

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"

python experiments/run_qwen_distill_des.py --dry-run

python experiments/run_qwen_distill_des.py --invoke \
  --checkpoint experiments/results/distill_des_ckpt.json

python experiments/run_qwen_distillation_ablation.py --invoke \
  -o experiments/results/qwen_distill_compare.md \
  --json-output experiments/results/qwen_distill_compare.json
```

---

## 板块四：相关工作对比（DES 12 格）

与本文主实验**分开**：每个基线交付 12 个 `des_<mode>.{py,c,cpp}`，用同一打分脚本，且须 **`--no-canonical-whole-file`**。

完整步骤见 **[相关工作对比实验.md](相关工作对比实验.md)**。

**常用命令**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"

# 准备上游仓库
bash experiments/related_work/clone_baselines.sh

# 导出任务清单
python experiments/related_work/rw_des_protocol_eval.py export -o experiments/rw_des_tasks.jsonl

# 各基线（API 类需 DEEPSEEK_API_KEY）
export DEEPSEEK_API_KEY="你的key"
bash experiments/related_work/run_rw_sven_des.sh
bash experiments/related_work/run_rw_selfrefine_des.sh
bash experiments/related_work/run_seccoder_des_lm_pipeline.sh
bash experiments/related_work/run_agentcoder_des_lm_pipeline.sh

# 汇总 GSR/VPR/FTPR 表
bash experiments/related_work/run_rw_aggregate_table.sh experiments/rw_rates_table.md
```

---

## 项目结构（和实验相关的部分）

```
aicrypto-helper/
├── agent/                 # CryptoAgent、生成、验证、保存
├── prompts/               # 分层提示（common / algorithms / llms）
├── utils/                 # 配置、测试数据、历史库、蒸馏
├── web/                   # FastAPI + 前端
├── experiments/           # 论文与相关工作复现脚本
│   ├── related_work/      # DES 12 格基线对比
│   └── results/           # 表、JSON、图等输出（建议放这里）
├── ICICS模版/             # 论文 LaTeX
├── config.yaml            # LLM、算法网格、蒸馏
├── test_data.yaml         # 标准测试向量
├── code_history.db        # Web/Agent 成功记录（默认路径）
├── generated_code/        # 生成代码落盘目录
├── 实验指南.md            # 本文方法实验全文
└── 相关工作对比实验.md    # 外部基线实验全文
```

---

## 配置与密钥

| 方式            | 说明                                                         |
| --------------- | ------------------------------------------------------------ |
| **Web**         | 页面保存 → `.api_keys.json`（实验脚本会自动读）              |
| **环境变量**    | `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`DOUBAO_API_KEY` 等 |
| **config.yaml** | 启用/禁用 provider、默认模型、输出目录、蒸馏路径             |

豆包等需在 `config.yaml` 里填写正确的 **endpoint ID**（如 `ep-xxx`）。

---

## 常见问题

| 现象                   | 可能原因                                             |
| ---------------------- | ---------------------------------------------------- |
| 实验几秒结束、指标全 0 | 没加 `--invoke`；Key 未配置；C/C++ 未装 `libssl-dev` |
| 历史表通过率「太高」   | 成功才入库居多；写论文时注明数据来源与 45 槽分母     |
| 错误修复率很低         | 本地编译失败；见实验指南 §6.1 先重跑 invoke 再聚合   |
| 相关工作打分偏高       | 忘记 `--no-canonical-whole-file`                     |
| `pdflatex` 找不到      | 需本机安装 TeX；论文在 `ICICS模版/` 下编译           |

更细的排错见 **[实验指南.md](实验指南.md)** 与 **[相关工作对比实验.md](相关工作对比实验.md)**。

