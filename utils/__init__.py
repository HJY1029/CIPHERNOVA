"""
utils 包：避免在 ``import utils.*`` 时立即加载依赖 PyYAML 的 ``config_loader``，
便于仅需 ``test_data_loader`` / ``code_tester`` 等子模块的脚本在轻量环境中运行。

仍支持 ``from utils import ConfigLoader`` 等写法（PEP 562 惰性导出）。
"""
from __future__ import annotations

__all__ = ["ConfigLoader", "setup_logger", "CodeValidator", "APIKeyManager"]


def __getattr__(name: str):
    if name == "ConfigLoader":
        from .config_loader import ConfigLoader

        return ConfigLoader
    if name == "setup_logger":
        from .logger import setup_logger

        return setup_logger
    if name == "CodeValidator":
        from .code_validator import CodeValidator

        return CodeValidator
    if name == "APIKeyManager":
        from .api_key_manager import APIKeyManager

        return APIKeyManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
