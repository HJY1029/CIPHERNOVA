# 向后兼容：从新模块导入所有内容
# 所有实现已移动到 agent/llm/ 目录下的各个模块
from agent.llm.base import (
    BaseLLMAdapter,
    LLMAdapter,
    set_api_key_manager,
    get_api_key
)
from agent.llm.adapters import (
    OpenAIAdapter,
    DeepSeekAdapter,
    ClaudeAdapter,
    DoubaoAdapter
)

# 为了向后兼容，保留logger
from utils.logger import setup_logger
logger = setup_logger()

# 导出所有内容，保持向后兼容
__all__ = [
    'BaseLLMAdapter',
    'LLMAdapter',
    'OpenAIAdapter',
    'DeepSeekAdapter',
    'ClaudeAdapter',
    'DoubaoAdapter',
    'set_api_key_manager',
    'get_api_key',
    'logger'
]
