"""
消融实验脚本（run_paper_ablation / run_prompt_ablation）共用的默认云端提供商列表。

省略 `--provider` 时，仅在下列供应商中、且 config 中已 enable 的非本地 provider 上跑实验。

指定 `--provider` 时**只允许**下列集合之一（见 ``ABLATION_EXPERIMENT_ALLOWED_PROVIDERS``）。

论文主消融默认五家云端：**deepseek、豆包（doubao）、Claude、OpenAI、Codex**；可显式 ``--provider <名称>`` 只跑其中一家（见 ``ABLATION_EXPERIMENT_ALLOWED_PROVIDERS``）。
"""
from __future__ import annotations

from typing import List, Tuple

# 论文主消融 / 提示消融默认：五家云端（config 中 enabled 且非本地的子集）；省额度可传 --provider 只跑一家
DEFAULT_ABLATION_CLOUD_PROVIDERS: Tuple[str, ...] = (
    "deepseek",
    "doubao",
    "claude",
    "openai",
    "codex",
)

# 消融脚本允许的云端标识（小写，与 config llm_providers 键一致）；含 openai / codex 供显式 --provider 使用
ABLATION_EXPERIMENT_ALLOWED_PROVIDERS = frozenset(
    {"openai", "claude", "deepseek", "doubao", "codex"}
)


def ablation_cloud_providers_subset(all_enabled_cloud: List[str]) -> List[str]:
    """从 `_cloud_provider_names` 结果中按默认顺序取出论文主消融默认云端子集（仅保留 config 里实际存在且已入选的）。"""
    by_lower = {p.lower(): p for p in all_enabled_cloud}
    out: List[str] = []
    for key in DEFAULT_ABLATION_CLOUD_PROVIDERS:
        if key in by_lower:
            out.append(by_lower[key])
    return out
