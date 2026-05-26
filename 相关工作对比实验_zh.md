# 相关工作对比实验（DES 12 格）

英文版 **[RELATED_WORK_COMPARISON.md](RELATED_WORK_COMPARISON.md)**

这份文档说明：如何复现论文表 **`tab:rw_same_protocol_des`** 里的「同协议 DES」对比。思路很简单——**每个基线都要交出同一套 12 个源文件**，再用**同一套打分脚本**算出 **GSR / VPR / FTPR**，这样才公平。

---

## 这套实验在比什么？

- **任务**：只比 **DES**，四种模式 **ECB / CBC / CFB / OFB**，三种语言 **Python / C / C++**，一共 **12 格**。
- **交付物**：每个基线一个目录，里面必须有 12 个约定文件名（见下表）。
- **打分**：在仓库根目录，用根目录的 **`test_data.yaml`** 标准向量，跑 **`rw_des_protocol_eval.py score`**。
- **重要口径**：评的是**基线自己生成的代码**，所以对外部基线打分时要加 **`--no-canonical-whole-file`**（不要用本仓库 OpenSSL 整文件替换去「帮」它们提分）。
- **禁止**：用本仓库的 **`CryptoAgent` / `prompts/`** 生成后，再贴成 SecCoder、SVEN 等标签——那会变成「本文方法」，不是相关工作复现。

### 12 个约定文件名

| 模式 | Python | C | C++ |
|------|--------|---|-----|
| ECB | `des_ecb.py` | `des_ecb.c` | `des_ecb.cpp` |
| CBC | `des_cbc.py` | `des_cbc.c` | `des_cbc.cpp` |
| CFB | `des_cfb.py` | `des_cfb.c` | `des_cfb.cpp` |
| OFB | `des_ofb.py` | `des_ofb.c` | `des_ofb.cpp` |

### 三个指标

| 指标 | 含义 |
|------|------|
| **GSR** | 生成成功 |
| **VPR** | 通过本仓库 **`CodeValidator`**（语法/结构等） |
| **FTPR** | 通过 **`CodeTester`**，密文与 **`test_data.yaml`** 里标准向量一致 |

---

## 开始前：环境设置

下面命令默认在 **仓库根目录**执行。我日常用 **WSL + Git Bash**；路径按你机器改，不要照抄占位符。

### 1）进入仓库根

```bash
# WSL 示例（D 盘挂载）
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
```

```powershell
# Windows PowerShell 示例（不用 WSL 时）
cd D:\aicrypto-helper
```

### 2）Python 依赖

```bash
pip install pyyaml
```

跑 **Self-Refine / SecCoder  / AgentCoder ** 时还需要：

```bash
pip install -U "openai>=1.0"
```

### 3）API Key（DeepSeek 类实验）

wsl:

```bash
export DEEPSEEK_API_KEY="你的key"
# 或兼容 OpenAI 时：
# export OPENAI_API_KEY="你的key"
```

PowerShell：

```powershell
$env:DEEPSEEK_API_KEY = "你的key"
```

### 4）SVEN 额外说明（可选）

- 连不上 HuggingFace：`export HF_ENDPOINT=https://hf-mirror.com`
- `ruamel.yaml` 冲突：`pip install 'ruamel.yaml>=0.17,<0.19'`
- 已有本地模型：`export SVEN_MODEL_DIR=/path/to/codegen-350m`

---

## 实验 0：准备上游仓库

**做什么**：把 SVEN、AgentCoder、self-refine 克隆到 `external/`。SecCoder 官方附件需单独下载（见「实验 8」）。

**得到什么**：`external/sven`、`external/AgentCoder`、`external/self-refine` 等目录。

**完整命令**（在仓库根）：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
bash experiments/related_work/clone_baselines.sh
```

---

## 实验 1：导出 12 格任务清单

**做什么**：根据 `test_data.yaml` 生成 **`experiments/rw_des_tasks.jsonl`**。后面各基线脚本都会读它，知道每格要什么算法、模式、语言和**期望文件名**。

**得到什么**：`experiments/rw_des_tasks.jsonl`

**完整命令**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/related_work/rw_des_protocol_eval.py export -o experiments/rw_des_tasks.jsonl
```

PowerShell 一行版：

```powershell
cd D:\aicrypto-helper
python experiments/related_work/rw_des_protocol_eval.py export -o experiments/rw_des_tasks.jsonl
```

---

## 实验 2：SVEN（官方栈 + 本地小模型）

**做什么**：在 **官方 SVEN 仓库**里跑 DES 的 HumanEval 式题目 → 从生成结果里**抽出 12 个文件** → 再打分。这是四条相关工作里**唯一不耗 API**、但要装 PyTorch / SVEN 依赖的一条。

**得到什么**：

- 生成目录：`experiments/sven_des_outputs/`
- 指标 JSON：`experiments/rw_sven.json`

**完整命令**（一条龙，推荐）：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
# 可选：国内镜像
# export HF_ENDPOINT=https://hf-mirror.com
# 可选：本地已下载模型目录
# export SVEN_MODEL_DIR=/path/to/codegen-350m
bash experiments/related_work/run_rw_sven_des.sh
```

脚本内部等价于（仅供理解）：

```bash
cd "$ROOT"
python experiments/related_work/rw_des_protocol_eval.py export -o experiments/rw_des_tasks.jsonl
python experiments/related_work/sven_des_problem_yamls.py -o external/sven/data_eval/des_rw
# … 在 external/sven/scripts 跑 human_eval_gen.py …
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

## 实验 3：Self-Refine + DeepSeek

**做什么**：用本仓库实现的 **Self-Refine 迭代 refine**（**不是** `prompts/` 里的论文提示），DeepSeek 生成 12 个文件并打分。需要 API Key。

**得到什么**：

- 生成目录：`experiments/selfrefine_deepseek_out/`
- 指标 JSON：`experiments/rw_selfrefine_deepseek.json`

**完整命令**（一条龙）：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
bash experiments/related_work/run_rw_selfrefine_des.sh
```

等价于下面这条「export → 生成 → score」管道（与脚本一致）：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
python experiments/related_work/run_rw_baseline_pipeline.py \
  --out-dir experiments/selfrefine_deepseek_out \
  --arm selfrefine_deepseek \
  --json-out experiments/rw_selfrefine_deepseek.json \
  -- \
  python experiments/related_work/selfrefine_des_deepseek.py \
    --out-dir experiments/selfrefine_deepseek_out \
    --max-iterations 3
```

可选环境变量：`SELFREFINE_MAX_ITERS`（默认 3）、`SELFREFINE_OUT`（改输出目录）。

---

## 实验 4：SecCoder + DeepSeek（胶水复现）

**做什么**：SecCoder **官方附件没有 DES 一键脚本**。本仓库提供 **「检索占位 + LM 逐格生成」胶水**（`seccoder_des_glue_generate.py`），按 JSONL 生成 12 文件后打分。**不是** SecCoder 论文里完整 CWE 流水线的一键复现，但和表里的「SecCoder+DeepSeek」口径对齐。

**得到什么**：

- 生成目录：`experiments/seccoder_des_out/`
- 指标 JSON：`experiments/rw_seccoder.json`

**完整命令**（生成 + 打分一条龙）：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
# 若用 random_file 检索占位，需先有安全代码片段目录（见实验 8 或自建）：
# export SECCODER_RETRIEVAL_MODE=random_file
# export SECCODER_SECURITY_SNIPPETS_DIR="$ROOT/external/SecCoder_acl_security_snippets"
bash experiments/related_work/run_seccoder_des_lm_pipeline.sh
```

**若目录里已有 12 个文件，只重新打分**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export SECCODER_DES_OUT="$ROOT/experiments/seccoder_des_out"
export SECCODER_ARM="seccoder_glue_repro"
bash experiments/related_work/run_rw_seccoder_des.sh
```

只跑生成、暂不 score：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
export SKIP_SCORE=1
bash experiments/related_work/run_seccoder_des_lm_pipeline.sh
```

---

## 实验 5：AgentCoder + DeepSeek（仅 Programmer 胶水）

**做什么**：AgentCoder 上游默认是 HumanEval，**没有现成 DES 12 格**。本仓库用 **「仅 Programmer」风格** 逐格调用 LM（`agentcoder_des_programmer_glue.py`），生成 12 文件后打分。

**得到什么**：

- 生成目录：`experiments/agentcoder_des_out/`
- 指标 JSON：`experiments/rw_agentcoder.json`

**完整命令**（一条龙）：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
bash experiments/related_work/run_agentcoder_des_lm_pipeline.sh
```

**若已有 12 个文件，只打分**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export AGENTCODER_DES_OUT="$ROOT/experiments/agentcoder_des_out"
export AGENTCODER_ARM="agentcoder_des"
bash experiments/related_work/run_rw_agentcoder_des.sh
```

---

## 实验 6：任意目录「只打分」

**做什么**：你已经用别的流程生成了 12 个约定文件名，只想按论文口径重算 GSR/VPR/FTPR。

**完整命令**（把路径和标签换成你的）：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/related_work/rw_des_protocol_eval.py score \
  --inputs experiments/seccoder_des_out \
  --arm seccoder_glue_repro \
  --no-canonical-whole-file \
  -o experiments/rw_seccoder.json
```

Windows 一行版（路径用反斜杠）：

```powershell
cd D:\aicrypto-helper
python experiments/related_work/rw_des_protocol_eval.py score --inputs experiments\agentcoder_des_out --arm agentcoder_des --no-canonical-whole-file -o experiments\rw_agentcoder.json
```

---

## 实验 7：汇总成论文表（Markdown / LaTeX）

**做什么**：读取已有的 `experiments/rw_*.json`，合并成一张 GSR/VPR/FTPR 表，方便贴进论文。

**完整命令**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
bash experiments/related_work/run_rw_aggregate_table.sh experiments/rw_rates_table.md
```

或直接调 Python（第二参数可选 `latex`）：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/related_work/rw_aggregate_rates.py \
  --preset related-work \
  -o experiments/rw_rates_table.md \
  --format markdown
```

---

## 实验 8（可选）：下载 SecCoder ACL 附件

**做什么**：下载 EMNLP 2024 SecCoder 的 **software.zip / data.zip**（含 `retriever_*.py` 等），用于 **CWE 检索演示**，**不是** DES 12 格主实验的必需步骤。

**完整命令**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
bash experiments/related_work/fetch_seccoder_acl.sh
```

解压示例：

```bash
cd "$ROOT/external"
unzip -q -o 2024.emnlp-main.806.software.zip -d SecCoder_acl_software
```

若 SecCoder 胶水用 `SECCODER_RETRIEVAL_MODE=random_file`，可把若干安全相关源码放进  
`external/SecCoder_acl_security_snippets/`（目录名可自定义，用环境变量指向即可）。

---

## 本文方法（CIPHERNOVA）怎么放进同一张表？

相关工作表只评 **外部基线交付的 12 个文件**。**本文方法**走主实验流水线（测试反馈 + 分层提示），不在 `run_rw_*.sh` 里。

复现本文 DES 格子的 GSR/VPR/FTPR，见 **`实验指南.md`**，例如：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
python experiments/run_paper_ablation.py --suite main --invoke \
  --provider deepseek \
  --algorithms DES \
  --languages python,c,cpp
```

汇总后把结果与 `experiments/rw_*.json` 一并交给 **`rw_aggregate_rates.py`**，或手工填入 LaTeX 表 `tab:rw_same_protocol_des`。

---

## 结果文件对照（跑完去哪找）

| 基线 | 指标 JSON | 12 个源文件目录 |
|------|-----------|-----------------|
| SVEN | `experiments/rw_sven.json` | `experiments/sven_des_outputs/` |
| Self-Refine + DeepSeek | `experiments/rw_selfrefine_deepseek.json` | `experiments/selfrefine_deepseek_out/` |
| SecCoder + DeepSeek（胶水） | `experiments/rw_seccoder.json` | `experiments/seccoder_des_out/` |
| AgentCoder + DeepSeek（胶水） | `experiments/rw_agentcoder.json` | `experiments/agentcoder_des_out/` |
| 汇总表 | `experiments/rw_rates_table.md` | — |

文档里曾记录过一组参考率（%，**以你本地 JSON 为准**）：

- Self-Refine：100 / 83.33 / 58.33  
- SecCoder：100 / 66.67 / 66.67  
- AgentCoder：100 / 41.67 / 0  
- SVEN：100 / 0 / 0  
- 本文 + DeepSeek：100 / 100 / 100  

---

## 推荐跑法（省时间）

1. `clone_baselines.sh`（要跑 SVEN 时）  
2. `rw_des_protocol_eval.py export`（只需一次）  
3. 按基线分别跑：`run_rw_sven_des.sh` → `run_rw_selfrefine_des.sh` → `run_seccoder_des_lm_pipeline.sh` → `run_agentcoder_des_lm_pipeline.sh`  
4. `run_rw_aggregate_table.sh` 出总表  

API 类实验可并行开四个终端，注意各脚本默认输出目录不同，不会互相覆盖。

---

## 常见问题

| 现象 | 处理 |
|------|------|
| `python: can't open file .../experiments/...` | 没在仓库根执行；先 `cd` 到 `ROOT` |
| `缺少 PyYAML` | `pip install pyyaml` |
| `未检测到 API Key` | `export DEEPSEEK_API_KEY=...` |
| SecCoder/AgentCoder 只找到 N/12 个文件 | 先跑对应 `run_*_lm_pipeline.sh`，或检查文件名是否完全是 `des_<mode>.py/.c/.cpp` |
| SVEN 下载模型失败 | `export HF_ENDPOINT=https://hf-mirror.com` 或设 `SVEN_MODEL_DIR` 为本地目录 |
| 打分结果「太好」不像基线原文 | 确认 score 带了 **`--no-canonical-whole-file`** |
| 用 `CryptoAgent` 生成却标成 SecCoder | 口径错误；本文方法应走 `run_paper_ablation.py` |

更细的脚本参数见：`experiments/related_work/run_rw_*.sh` 文件头注释。
