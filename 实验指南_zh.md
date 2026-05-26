# 实验指南

英文版 **[EXPERIMENT_GUIDE.md](EXPERIMENT_GUIDE.md)**

这份文档说明：如何用本仓库脚本复现论文（`paperzh.tex` / `paperEn.tex` / `ICICS模版/`）里的各类表格与指标。所有「真跑模型」的实验都走与 **Web 页面相同的 `CryptoAgent.generate_and_save` 链路**；只读历史、只算表的脚本**不会**消耗 API。

**相关工作对比**（SecCoder、SVEN、DES 12 格等）在另一份文档：**[相关工作对比实验.md](相关工作对比实验.md)**。

---

## 先搞懂三件事

### 1）在哪儿跑命令？

始终在**仓库根目录** `aicrypto-helper` 下执行（脚本会自动把根目录加入 `sys.path`）。

```bash
# 我的路径下用的 WSL 写法
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
```

```powershell
# Windows PowerShell（不经过 WSL 时）
cd D:\aicrypto-helper
```

### 2）`--invoke` 和「没加 invoke」差在哪？

| 情况 | 会不会调 LLM | 典型用途 |
|------|----------------|----------|
| **不加** `--invoke` / `--live` | **不会** | 看任务规模、统计 prompt 长度（dry-run） |
| **加了** `--invoke` 或 `--live` | **会**，走 `generate_and_save` | 真实验，消耗 API 额度 |

几秒就结束、GSR/VPR/FTPR 全是 0：多半是 **没加 invoke**、**Key 没配好**、或 **C/C++ 编译环境缺失**。用 `--json-output` 看 `error`、`validation_hint`。

### 3）三个通过率指标

| 指标 | 人话 |
|------|------|
| **GSR** | 生成出来了（非空、长度够） |
| **VPR** | 验证通过（`CodeValidator`） |
| **FTPR** | 功能测试通过（与 `test_data.yaml` 标准向量一致） |

LLM 性能表还有 **FGPR（首次生成即通过率）**：`attempts==1` 且 `test_success==true` 的占比，见下文「实验 4」。

---

## 环境与配置文件

| 项 | 路径 / 说明 |
|----|-------------|
| **主配置** | `config.yaml`（LLM 提供商、算法模式、蒸馏开关等） |
| **标准向量** | `test_data.yaml`、`openssl_test_data.yaml` |
| **历史库** | 默认 `code_history.db`（与 Web 写入一致） |
| **性能日志** | 默认 `experiments/results/llm_performance.json`（失败/多轮明细） |
| **API Key** | 环境变量，或项目根 `.api_keys.json`（与 Web「保存密钥」同一文件） |

**C/C++ 实验**建议在 **WSL2 Ubuntu** 上跑，并安装编译依赖：

```bash
sudo apt-get update
sudo apt-get install -y build-essential gcc g++ libssl-dev pkg-config
cd "$ROOT"
pip install -r requirements.txt
pip install pyyaml
```

---

## 实验一览（按论文用途）

| 编号 | 对应论文内容 | 脚本 | 是否调 LLM |
|------|--------------|------|------------|
| **1** | 主消融 `tab:main_ablation` 等 | `run_paper_ablation.py --suite main` | 加 `--invoke` 才调 |
| **2** | 提示策略消融 | `run_paper_ablation.py --suite prompt` | 同上 |
| **3** | 五档 prompt 长度 / 批量 invoke | `run_prompt_ablation.py` | 同上 |
| **4** | 各 LLM 整体性能 `tab:llm_performance` | `extract_llm_performance_from_history.py` | **不调**，只看历史记录 |
| **5** | 代码提取 `tab:extraction_accuracy` | `run_code_extraction_eval.py` | **不调**，只看历史记录 |
| **6** | 错误分类与修复 `tab:error_repair` | `run_error_repair_table.py` | **不调**，只看历史记录 |
| **7** | 本地 Qwen 蒸馏前后 | `run_qwen_distillation_ablation.py` 等 | 加 `--invoke` 才调用 |
| **8** | 相关工作 DES 12 格 | 见 [相关工作对比实验.md](相关工作对比实验.md) | 视基线而定 |

---

## 实验 1：论文主消融（系统配置 × GSR/VPR/FTPR）

**做什么**：对比「完整所提方法 / 无测试反馈 / 无分层提示 / 无任何提示」等行，指标与 Web 一致。

**脚本**：`experiments/run_paper_ablation.py --suite main`

| 表行含义 | 实现方式 |
|----------|----------|
| 完整所提方法 | 默认 kwargs |
| 无测试反馈改进 | `_ablation_no_test_feedback: true` |
| 无分层提示架构 | `prompt_ablation: common_only` |
| 无任何提示 | `prompt_ablation: no_prompt` |

**先看规模（不调 LLM）**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_paper_ablation.py --suite main --dry-run
```

**完整真跑（默认四家云端，耗时长、耗额度）**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
python experiments/run_paper_ablation.py --suite main --invoke \
  --max-retries 3 \
  -o experiments/results/ablation_main.md \
  --json-output experiments/results/ablation_main.json
```

**试跑（单家 + 少量格子 + 仅 Python）**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
python experiments/run_paper_ablation.py --suite main --invoke \
  --provider deepseek \
  --max-cases 4 \
  --languages python \
  --max-retries 3 \
  -o experiments/results/ablation_main_try.md \
  --json-output experiments/results/ablation_main_try.json
```

JSON 里 `performance_drop_vs_full` 是相对「完整所提方法」的 VPR/FTPR 百分点差；失败原因看 `variant_run_summary` 和 Markdown 末尾「运行诊断」。

---

## 实验 2：论文提示策略消融（GSR/VPR/FTPR）

**做什么**：对比五档 `prompt_ablation`（与 `utils/prompt_loader.py` 一致），填论文提示消融表。

| 表行（论文） | `prompt_ablation` |
|--------------|-------------------|
| 单一通用提示 | `common_only` |
| 算法特定提示 | `common_algorithm` |
| LLM 特定提示 | `common_algorithm_llm_main` |
| 四层可组合（所提方法） | `full`（默认，与 Web 未指定消融时一致） |

另有极端档 `llm_main_only` 等，见 `run_prompt_ablation.py` 文档。

**完整命令（真跑 + 写结果）**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
python experiments/run_paper_ablation.py --suite prompt --invoke \
  --max-retries 3 \
  -o experiments/results/ablation_prompt.md \
  --json-output experiments/results/ablation_prompt.json
```

**小规模试跑**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
python experiments/run_paper_ablation.py --suite prompt --invoke \
  --provider deepseek \
  --modes ECB \
  --languages python \
  --max-cases 1 \
  --max-retries 3
```

---

## 实验 3：提示消融脚本（长度统计或批量 invoke）

**做什么**：`run_prompt_ablation.py` 可单独用——不传 `--algorithm` 时默认 **DES × 云端 provider × des_modes × languages**；传 `--algorithm` 则进入单任务 dry-run。

**五档语义**（与实现对齐）：

| 档位 | 含义概要 |
|------|----------|
| `full` | 与 Web 全量 prompt 一致 |
| `common_only` | 仅 `prompts/common` |
| `common_algorithm` | common + `prompts/algorithms` |
| `common_algorithm_llm_main` | 上列 + `llms/<provider>/llm.yaml`，无 OpenSSL 长参考 |
| `llm_main_only` | 极端对照，几乎只有 llm 块 + 任务行 |

**只统计 prompt 长度（不调 LLM）**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_prompt_ablation.py \
  -o experiments/results/prompt_lens.md \
  --json-output experiments/results/prompt_lens.json
```

**缩小 dry-run**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_prompt_ablation.py \
  --modes ECB \
  --languages python \
  --provider deepseek
```

**批量真调 LLM（默认云端）**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
python experiments/run_prompt_ablation.py --invoke \
  --max-retries 3 \
  -o experiments/results/prompt_invoke.md \
  --json-output experiments/results/prompt_invoke.json
```

**单格单任务 invoke**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
export DEEPSEEK_API_KEY="你的key"
python experiments/run_prompt_ablation.py --invoke \
  --provider deepseek \
  --algorithm DES \
  --mode ECB \
  --language python \
  --max-retries 3
```

WSL 下续行用 `\`；Windows cmd 用 `^`；PowerShell 建议写成一行。

---

## 实验 4：从 Web 历史汇总各 LLM 性能（`tab:llm_performance`）

**做什么**：读 `code_history.db`（并可合并 `llm_performance.json`），按 provider 聚合成论文 **8 列表**（GSR / VPR / FTPR / **FGPR** / 平均生成时间等），可选出 2×2 图 PDF。

**重要口径**：`generate_and_save` 成功路径里，多数情况下**只有测试通过才写入 history**，所以库内偏成功样本；表默认按 **config 全网格 45 槽** 为分母，未覆盖槽计 0%。

**完整命令（默认：终端出表 + 写 PDF 图）**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py
```

**只出表、不出图**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py --no-figure
```

**表前加指标说明 + 指定输出路径**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py \
  --with-legend \
  -o experiments/results/llm_paper_table.md
```

**写出 JSON + 同名 Markdown**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py \
  --format json \
  -o experiments/results/history_stats.json
```

**指定数据库或性能日志**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/extract_llm_performance_from_history.py \
  --db "$ROOT/code_history.db" \
  --performance-json experiments/results/llm_performance.json
```

---

## 实验 5：代码提取准确率（`tab:extraction_accuracy`）

**做什么**：在本地评测三种从 LLM 回复里抽代码的策略，从历史库批量读 `code` 字段。

| 策略 | 实现 |
|------|------|
| Markdown 围栏 | `extract_code_markdown_fence_only` |
| 纯文本识别 | `extract_code_plain_text_recognition` |
| 全文多级 | `extract_code` |

**内置样例试跑**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_code_extraction_eval.py
```

**从 history 评测（论文主口径，推荐）**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_code_extraction_eval.py --from-history \
  -o experiments/results/experiments/extraction_from_history.md \
  --json-output experiments/results/experiments/extraction_from_history.json
```

## 实验 6：错误分类与修复效果（`tab:error_repair`）

**做什么**：聚合四类错误（算法理解 / 实现细节 / 环境配置 / 代码结构）的数量、占比与修复后通过率。**不调 LLM**（通常几秒内跑完）。

| 数据来源 | 何时用 |
|----------|--------|
| **`--from-history`（推荐填论文）** | 合并 `code_history.db` + `llm_performance.json`，并推断多轮修复 |
| **`--from-performance`** | 只读性能 JSON |
| **默认无参数** | 静态占位 `experiments/data/error_repair_table.json` |

**完整命令（与论文 DeepSeek 表注一致）**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_error_repair_table.py --from-history --provider deepseek \
  -o experiments/results/experiments/error_repair_from_history.md \
  --write-json experiments/results/experiments/error_repair_from_history.json
```

**仅 performance 日志**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_error_repair_table.py --from-performance \
  -o experiments/results/error_repair_from_performance.md
```

**若本地 C/C++ 长期编译失败**：修复后通过率会偏低；要在稿件里说明环境，或先按 §6.1 重跑 invoke 实验刷新 `llm_performance.json`。

### 6.1 刷新错误修复表之前：先积累可编译环境下的失败/修复记录

```bash
# 可选：备份旧日志，只统计本轮
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
mv llm_performance.json llm_performance.json.bak 2>/dev/null || true

export DEEPSEEK_API_KEY="你的key"
python experiments/run_paper_ablation.py --suite prompt --invoke \
  --provider deepseek \
  --languages python \
  --max-cases 2 \
  --max-retries 3 \
  -o experiments/results/ablation_try.md \
  --json-output experiments/results/ablation_try.json

# 再聚合
python experiments/run_error_repair_table.py --from-history --provider deepseek \
  -o experiments/results/experiments/error_repair_from_history.md \
  --write-json experiments/results/experiments/error_repair_payload.json
```

分类规则见 `experiments/error_repair_aggregate.py`（关键词启发式），与人工标注子集不完全等价，正文请注明来源。

---

## 实验 7：本地 Qwen 蒸馏前后对比

**做什么**：在同一 **config 网格**（默认 45 格）上串行跑两轮——① 关闭蒸馏 ② 按配置开启蒸馏（教师 JSONL）。与 **`prompt_ablation` 不是同一维度**；与「相关工作对比」无关。

**前置**：`data/distillation_teacher.jsonl` 里最好已有教师样本；可先用云端 provider 跑通任务，依赖 `auto_collect_cloud_teachers` 自动追加。

**脚本**：

| 脚本 | 默认格数 |
|------|----------|
| `run_qwen_distillation_ablation.py` | 全网格 |
| `run_qwen_distill_des.py` | 12 |
| `run_qwen_distill_aes.py` | 18 |
| `run_qwen_distill_rsa.py` | 3 |
| `run_qwen_distill_sm4.py` | 12 |

**规模预览**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_qwen_distillation_ablation.py --dry-run
python experiments/run_qwen_distill_des.py --dry-run
```

**仅 DES，两轮对比 + 断点续跑**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_qwen_distill_des.py --invoke \
  --checkpoint experiments/results/distill_des_ckpt.json
```

**全网格（可先 `--limit 5` 试跑）**：

```bash
export ROOT=/mnt/d/aicrypto-helper
cd "$ROOT"
python experiments/run_qwen_distillation_ablation.py --invoke \
  -o experiments/results/qwen_distill_compare.md \
  --json-output experiments/results/qwen_distill_compare.json
```

`--invoke` 时与 Web 批量同源（`web.server._batch_generate_single`，历史已跑通格会跳过 LLM）。常用：`--skip-baseline` / `--skip-distill`、`--resume`、`--algorithm DES`。

蒸馏开关在 `config.yaml` 的 `distillation` 与 `utils/distillation.py` 的 `ABLATION_FLAGS`。

---

## 实验 8：相关工作对比（DES 12 格）

与本文主实验**分开**：各基线须交付 12 个 `des_<mode>.{py,c,cpp}`，用 `rw_des_protocol_eval.py score` 且加 `--no-canonical-whole-file`。

完整步骤与命令见：**[相关工作对比实验.md](相关工作对比实验.md)**。

---

## 9. 两类口径不要混用

论文里常同时出现：

- **历史聚合**（如界面使用后写入 `code_history` 的统计，或 FGPR 来自合并明细）
- **固定批次主实验**（`run_paper_ablation` / 指定子集 invoke 跑出来的表）

两者**子集、分母、是否仅成功入库**都可能不同，**不可直接横比**。写稿时写明：时间范围、provider、算法/模式/语言子集、是否 DES 全模式等。

---

## 10. 生成代码落在哪里？

实验脚本**不会在终端打印模型全文**。invoke 时你会看到进度行；生成代码在 **`config.yaml` 的 `output_dir`**（默认 `generated_code/`）；结构化结果在 **`--json-output`** 的 `results` / `variant_run_summary`。

---

## 11. 常见问题

| 现象 | 处理 |
|------|------|
| 几秒结束、指标全 0 | 加 `--invoke`；查 Key / `.api_keys.json`；C/C++ 装 `libssl-dev` |
| `run_prompt_ablation --invoke` 全部 skipped | Agent 初始化失败，看终端「跳过 … 初始化失败」 |
| `extract` 找不到库 | 检查 `--db`；或先在 Web 生成几次 |
| 历史表 FTPR 虚高 | 注明「仅成功入库」；用 `extract` 默认 45 槽分母 |
| 错误修复率长期很低 | 本地编译不过；换 WSL 或重跑 §6.1 |
| dry-run 的 `chars` 不对 | 核对 `--provider`、`--language`、RSA 是否传 `--operation` |

需要完整 INFO 日志时，对相关脚本加 **`--verbose`**。

---

## 12. 脚本索引

| 路径 | 作用 |
|------|------|
| `experiments/run_paper_ablation.py` | 论文主消融 / 提示策略消融 |
| `experiments/run_prompt_ablation.py` | 五档 prompt dry-run / invoke |
| `experiments/extract_llm_performance_from_history.py` | LLM 性能表 + 可选图 |
| `experiments/run_code_extraction_eval.py` | 代码提取评测 |
| `experiments/run_error_repair_table.py` | 错误修复表 |
| `experiments/run_qwen_distillation_ablation.py` | Qwen 蒸馏关/开 |
| `experiments/run_qwen_distill_{des,aes,rsa,sm4}.py` | 按算法拆分蒸馏 |
| `utils/prompt_loader.py` | `_ablation_allows`、`get_prompt` |
| `agent/prompt_builder.py` | 传入 `prompt_ablation` |
| `utils/history_manager.py` | `code_history` 读写 |

若将来改为「失败也入库」，需改 `agent/code_saver.py` 并同步更新「实验 4」口径说明。
