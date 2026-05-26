"""
API密钥管理器 - 管理Web界面配置的API密钥
"""
import json
from pathlib import Path
from typing import Dict, Optional
from utils.logger import setup_logger

logger = setup_logger()

class APIKeyManager:
    """API密钥管理器"""
    
    def __init__(self, keys_file: str = ".api_keys.json"):
        """
        初始化API密钥管理器
        
        Args:
            keys_file: 存储API密钥的文件路径
        """
        self.keys_file = Path(keys_file)
        self._keys: Dict[str, str] = {}
        self._load_keys()
    
    def _load_keys(self):
        """从文件加载API密钥"""
        try:
            if self.keys_file.exists():
                with open(self.keys_file, 'r', encoding='utf-8') as f:
                    self._keys = json.load(f)
                logger.info(f"成功加载API密钥文件: {self.keys_file}")
            else:
                self._keys = {}
                logger.info("API密钥文件不存在，使用空配置")
        except Exception as e:
            logger.error(f"加载API密钥文件失败: {e}")
            self._keys = {}
    
    def _save_keys(self):
        """保存API密钥到文件"""
        try:
            # 确保目录存在
            self.keys_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.keys_file, 'w', encoding='utf-8') as f:
                json.dump(self._keys, f, indent=2, ensure_ascii=False)
            logger.info(f"成功保存API密钥到: {self.keys_file}")
            return True
        except Exception as e:
            logger.error(f"保存API密钥文件失败: {e}")
            return False
    
    def get_key(self, key_name: str) -> Optional[str]:
        """
        获取API密钥
        
        Args:
            key_name: 密钥名称（如 'OPENAI_API_KEY'）
            
        Returns:
            API密钥值，如果不存在则返回None
        """
        return self._keys.get(key_name)
    
    def set_key(self, key_name: str, key_value: str) -> bool:
        """
        设置API密钥
        
        Args:
            key_name: 密钥名称
            key_value: 密钥值
            
        Returns:
            是否保存成功
        """
        if not key_value or not key_value.strip():
            # 如果值为空，删除该密钥
            if key_name in self._keys:
                del self._keys[key_name]
        else:
            self._keys[key_name] = key_value.strip()
        
        return self._save_keys()
    
    def set_keys(self, keys: Dict[str, str]) -> bool:
        """
        批量设置API密钥
        
        Args:
            keys: 密钥字典
            
        Returns:
            是否保存成功
        """
        for key_name, key_value in keys.items():
            if key_value and key_value.strip():
                self._keys[key_name] = key_value.strip()
            elif key_name in self._keys:
                del self._keys[key_name]
        
        return self._save_keys()
    
    def get_all_keys(self) -> Dict[str, str]:
        """获取所有API密钥"""
        return self._keys.copy()
    
    def has_key(self, key_name: str) -> bool:
        """检查是否存在指定的API密钥"""
        return key_name in self._keys and bool(self._keys[key_name])
    
    def clear_all(self) -> bool:
        """清空所有API密钥"""
        self._keys = {}
        return self._save_keys()

