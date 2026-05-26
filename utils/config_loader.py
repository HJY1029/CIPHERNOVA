import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
from utils.logger import setup_logger

logger = setup_logger()

class ConfigLoader:
    """配置加载器"""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化配置加载器
        
        Args:
            config_path: 配置文件路径
        """
        p = Path(config_path)
        if p.is_absolute():
            self.config_path = p
        else:
            # 优先当前工作目录；否则回退仓库根（解决从 web/ 等子目录启动时读不到根目录 config.yaml）
            cwd_candidate = (Path.cwd() / p).resolve()
            repo_root = Path(__file__).resolve().parent.parent
            repo_candidate = (repo_root / p).resolve()
            if cwd_candidate.exists():
                self.config_path = cwd_candidate
            elif repo_candidate.exists():
                self.config_path = repo_candidate
            else:
                self.config_path = cwd_candidate
        self._config: Dict[str, Any] = {}
        self._load_config()
    
    def _load_config(self):
        """加载配置文件"""
        try:
            if not self.config_path.exists():
                logger.warning(f"配置文件不存在: {self.config_path}，使用默认配置")
                self._config = {}
                return
            
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
            
            logger.info(f"成功加载配置文件: {self.config_path}")
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            self._config = {}
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值
        
        Args:
            key: 配置键
            default: 默认值
            
        Returns:
            配置值或默认值
        """
        return self._config.get(key, default)
    
    def get_llm_config(self, provider: str) -> Dict[str, Any]:
        """
        获取LLM提供商配置
        
        Args:
            provider: 提供商名称（如 'openai', 'deepseek' 等）
            
        Returns:
            提供商配置字典
        """
        llm_providers = self._config.get('llm_providers', {})
        return llm_providers.get(provider, {})
    
    def get_enabled_llm_providers(self) -> List[str]:
        """
        获取所有启用的LLM提供商列表
        
        Returns:
            启用的提供商名称列表
        """
        llm_providers = self._config.get('llm_providers', {})
        enabled = []
        for provider, config in llm_providers.items():
            if config.get('enabled', False):
                enabled.append(provider)
        return enabled
    
    def __getitem__(self, key: str) -> Any:
        """支持字典式访问"""
        return self._config[key]
    
    def __contains__(self, key: str) -> bool:
        """支持 in 操作符"""
        return key in self._config

