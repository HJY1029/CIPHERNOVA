"""LLM 提供商在 Web/CLI 中的显示名与密钥就绪判断。"""
import os
from typing import Any, Callable, Dict, Optional

PROVIDER_DISPLAY_NAMES: Dict[str, str] = {
    "openai": "OpenAI (GPT-4)",
    "codex": "Codex（CloseAI / OpenAI 兼容）",
    "deepseek": "DeepSeek",
    "claude": "Claude (Anthropic)",
    "doubao": "豆包 (Doubao)",
    "qwen_coder_local": "Qwen2.5 Coder 7B (本地)",
}


def llm_provider_display_name(provider: str) -> str:
    return PROVIDER_DISPLAY_NAMES.get(provider, provider.replace("_", " ").title())


def llm_provider_key_ready(
    config_data: Dict[str, Any],
    get_key: Callable[[str], Optional[str]],
) -> bool:
    """是否视为已可调用：可选密钥提供商始终为 True；否则需环境或管理器中有密钥。"""
    if config_data.get("api_key_optional"):
        return True
    api_key_env = config_data.get("api_key_env", "") or ""
    if not api_key_env:
        return False
    v = get_key(api_key_env)
    if v:
        return True
    return bool(os.getenv(api_key_env))
