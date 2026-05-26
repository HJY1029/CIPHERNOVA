import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from abc import ABC, abstractmethod
from utils.logger import setup_logger

logger = setup_logger()

# 全局API密钥管理器实例（可选）
_api_key_manager = None

# Web 将密钥持久化到 `.api_keys.json`；CLI 未调用 set_api_key_manager 时由此懒加载
_cli_file_keys_cache: Optional[Dict[str, str]] = None


def set_api_key_manager(manager):
    """设置全局API密钥管理器"""
    global _api_key_manager
    _api_key_manager = manager


def _dot_api_keys_merged() -> Dict[str, str]:
    """合并可读到的 `.api_keys.json`（仓库根与当前工作目录；后者同名键覆盖前者）。"""
    global _cli_file_keys_cache
    if _cli_file_keys_cache is not None:
        return _cli_file_keys_cache
    merged: Dict[str, str] = {}
    seen_resolved: Set[str] = set()
    paths: List[Path] = []
    extra = (os.environ.get("AICRYPTO_API_KEYS_FILE") or "").strip()
    if extra:
        paths.append(Path(extra))
    # agent/llm/base.py -> parents[2] = 项目根
    paths.append(Path(__file__).resolve().parents[2] / ".api_keys.json")
    paths.append(Path.cwd() / ".api_keys.json")

    for p in paths:
        try:
            rp = p.resolve()
            sk = str(rp)
            if sk in seen_resolved:
                continue
            seen_resolved.add(sk)
            if not p.is_file():
                continue
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            for k, v in data.items():
                if isinstance(v, str) and v.strip():
                    merged[str(k)] = v.strip()
        except Exception as e:
            logger.debug("读取 API 密钥文件 %s 跳过: %s", p, e)

    _cli_file_keys_cache = merged
    return merged


def get_api_key(env_name: str) -> Optional[str]:
    """
    获取API密钥：APIKeyManager（Web 进程内）> 环境变量 > 项目根/cwd 下 `.api_keys.json`

    Args:
        env_name: 环境变量名称（与 config 中 api_key_env 一致）

    Returns:
        API密钥值，如果不存在则返回None
    """
    if _api_key_manager:
        key = _api_key_manager.get_key(env_name)
        if key:
            return key

    env_v = os.getenv(env_name)
    if env_v:
        return env_v

    return _dot_api_keys_merged().get(env_name)


def is_qwen_coder_local_provider(provider: str) -> bool:
    """本地 Ollama Qwen Coder 系列（provider 名以 qwen_coder_local 开头）。"""
    p = (provider or "").strip().lower()
    return p == "qwen_coder_local" or p.startswith("qwen_coder_local_")


class BaseLLMAdapter(ABC):
    """LLM适配器基类"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        api_key_env = config.get('api_key_env', '') or ''
        self.api_key = get_api_key(api_key_env) if api_key_env else None
        if not self.api_key and config.get('api_key_optional'):
            self.api_key = config.get('api_key_placeholder', 'ollama')
        if not self.api_key:
            raise ValueError(
                f"未找到API密钥: {api_key_env or '(未配置)'}。"
                "请在 Web 保存密钥（写入项目根 `.api_keys.json`）、设置对应环境变量，"
                "或设置 `AICRYPTO_API_KEYS_FILE` 指向密钥 JSON。"
            )
    
    @abstractmethod
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """生成响应"""
        pass

class LLMAdapter:
    """LLM适配器工厂"""
    
    ADAPTERS = {}
    
    def __init__(self, provider: str, config: Dict[str, Any]):
        # 延迟导入适配器，避免循环依赖
        if not self.ADAPTERS:
            from agent.llm.adapters.openai_adapter import OpenAIAdapter
            from agent.llm.adapters.deepseek_adapter import DeepSeekAdapter
            from agent.llm.adapters.claude_adapter import ClaudeAdapter
            from agent.llm.adapters.doubao_adapter import DoubaoAdapter
            
            self.ADAPTERS = {
                'openai': OpenAIAdapter,
                'deepseek': DeepSeekAdapter,
                'claude': ClaudeAdapter,
                'doubao': DoubaoAdapter,
                'qwen_coder_local': OpenAIAdapter,
                # Codex / GPT-5-Codex 等：经 CloseAI 或其它网关的 OpenAI 兼容 /v1
                'codex': OpenAIAdapter,
            }
        
        if provider not in self.ADAPTERS:
            if is_qwen_coder_local_provider(provider):
                adapter_class = self.ADAPTERS["qwen_coder_local"]
            else:
                raise ValueError(f"不支持的LLM提供商: {provider}")
        else:
            adapter_class = self.ADAPTERS[provider]
        # 供 OpenAI 兼容类适配器打日志（避免 qwen_coder_local 误显示为 OpenAI）
        merged_config = {**config, "_llm_provider_id": provider}
        self.adapter = adapter_class(merged_config)
        self.provider = provider
    
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """生成响应"""
        return await self.adapter.generate(prompt, system_prompt)

