import asyncio
import re
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from utils.config_loader import ConfigLoader
from utils.logger import setup_logger
from utils.code_validator import CodeValidator
from utils.code_tester import CodeTester
from utils.test_data_loader import TestDataLoader
from utils.openssl_tester import OpenSSLTester
from utils.history_manager import HistoryManager
from utils.prompt_loader import PromptLoader
from agent.llm_adapter import LLMAdapter
from agent.prompts import LANGUAGE_PROMPTS, LANGUAGE_EXTENSIONS
from agent.code_processing import extract_code, detect_code_truncation, fix_missing_headers
from agent.prompt_builder import build_prompt, get_system_prompt
from agent.code_generator import generate_code as _generate_code, improve_code as _improve_code
from agent.code_saver import save_code as _save_code, generate_and_save as _generate_and_save

logger = setup_logger()


def _llm_performance_json_path() -> Path:
    """运行时性能日志：统一写入 experiments/results/llm_performance.json。"""
    return Path(__file__).resolve().parent.parent / "experiments" / "results" / "llm_performance.json"


def _llm_performance_legacy_json_path() -> Path:
    """历史默认路径（仓库根）；若新路径尚无文件则读此文件以合并。"""
    return Path(__file__).resolve().parent.parent / "llm_performance.json"


class CryptoAgent:
    """密码学代码生成Agent"""
    
    # 从 prompts 模块导入
    LANGUAGE_PROMPTS = LANGUAGE_PROMPTS
    LANGUAGE_EXTENSIONS = LANGUAGE_EXTENSIONS
    
    def __init__(self, config_path: str = "config.yaml", enable_validation: bool = True,
                 prompt_loader: Optional[PromptLoader] = None, 
                 enable_testing: bool = True, provider: Optional[str] = None):
        self.config = ConfigLoader(config_path)
        # 如果指定了provider，使用指定的；否则使用配置文件中的默认值
        self.provider = provider or self.config.get('default_provider', 'deepseek')
        self.llm = self._init_llm()
        self.output_dir = Path(self.config.get('output_dir', './generated_code'))
        self.output_dir.mkdir(exist_ok=True)
        self.validator = CodeValidator() if enable_validation else None
        self.tester = CodeTester() if enable_testing else None
        self.test_data_loader = TestDataLoader() if enable_testing else None
        self.openssl_tester = OpenSSLTester() if enable_testing else None
        self.history_manager = HistoryManager()  # 历史记录管理器
        # 初始化prompt加载器
        self.prompt_loader = prompt_loader or PromptLoader()
        # 检测OpenSSL开发库是否可用（用于编译）
        self.openssl_dev_available = self._check_openssl_dev_available()
    
    def _check_openssl_dev_available(self) -> bool:
        """检查OpenSSL开发库是否可用（用于编译）"""
        import subprocess
        import tempfile
        from pathlib import Path
        
        try:
            # 创建一个测试C文件，尝试编译包含OpenSSL头文件的代码
            test_code = '''
#include <openssl/des.h>
int main() { return 0; }
'''
            temp_dir = Path(tempfile.gettempdir()) / "aicrypto_openssl_check"
            temp_dir.mkdir(exist_ok=True)
            test_file = temp_dir / "test_openssl.c"
            test_exe = temp_dir / "test_openssl"
            
            with open(test_file, 'w') as f:
                f.write(test_code)
            
            # 尝试编译（检查头文件和链接库）
            # 先尝试只编译（检查头文件）
            result = subprocess.run(
                ['gcc', '-c', str(test_file), '-o', str(test_exe) + '.o'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            # 如果编译成功，尝试链接（检查库文件）
            if result.returncode == 0:
                link_result = subprocess.run(
                    ['gcc', str(test_exe) + '.o', '-o', str(test_exe), '-lssl', '-lcrypto'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if link_result.returncode != 0:
                    result = link_result  # 使用链接结果
            
            # 清理临时文件
            try:
                if test_file.exists():
                    test_file.unlink()
                if (test_exe.parent / (test_exe.name + '.o')).exists():
                    (test_exe.parent / (test_exe.name + '.o')).unlink()
                if test_exe.exists():
                    test_exe.unlink()
            except:
                pass
            
            if result.returncode == 0:
                logger.info("[OK] OpenSSL开发库可用，将优先使用OpenSSL生成代码")
                return True
            else:
                error_msg = result.stderr[:200] if result.stderr else '编译失败'
                logger.warning(f"[X] OpenSSL开发库不可用: {error_msg}")
                logger.info("将使用纯C实现生成代码（代码会更长，但不需要OpenSSL）")
                return False
        except FileNotFoundError:
            logger.warning("gcc未找到，无法检测OpenSSL开发库")
            return False
        except Exception as e:
            logger.warning(f"检测OpenSSL开发库时出错: {e}")
            return False
    
    def _init_llm(self, provider: Optional[str] = None) -> LLMAdapter:
        """初始化LLM适配器"""
        provider = provider or self.provider
        llm_config = self.config.get_llm_config(provider)
        if not llm_config.get('enabled', False):
            raise ValueError(f"LLM提供商 {provider} 未启用")
        
        return LLMAdapter(provider, llm_config)
    
    def _get_available_providers(self, exclude: Optional[List[str]] = None) -> List[str]:
        """获取可用的LLM提供商列表（排除指定的提供商）"""
        exclude = exclude or []
        enabled_providers = self.config.get_enabled_llm_providers()
        return [p for p in enabled_providers if p not in exclude]
    
    def _get_model_name(self) -> str:
        """获取当前使用的模型名称"""
        llm_config = self.config.get_llm_config(self.provider)
        model = llm_config.get('model', 'unknown')
        return f"{self.provider}:{model}"
    
    def _record_performance(self, algorithm: str, mode: Optional[str], language: str,
                           validation_success: Optional[bool], test_success: Optional[bool],
                           attempts: int, generation_time: float = 0.0, error_message: Optional[str] = None):
        """记录模型性能数据"""
        try:
            performance_file = _llm_performance_json_path()
            performance_file.parent.mkdir(parents=True, exist_ok=True)
            legacy_file = _llm_performance_legacy_json_path()
            load_path = performance_file if performance_file.is_file() else legacy_file

            # 读取现有数据（文件可能被异常中断写入或非 UTF-8 字节污染）
            if load_path.is_file():
                try:
                    text = load_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    text = load_path.read_text(encoding="utf-8", errors="replace")
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning(
                        "llm_performance.json 无法解析 JSON，已跳过历史合并（可手动备份后删除该文件）"
                    )
                    data = {}
            else:
                data = {}
            
            # 获取模型标识
            model_key = self._get_model_name()
            
            # 初始化模型数据
            if model_key not in data:
                data[model_key] = {
                    'provider': self.provider,
                    'model': self.config.get_llm_config(self.provider).get('model', 'unknown'),
                    'total_attempts': 0,
                    'validation_success': 0,
                    'validation_failure': 0,
                    'test_success': 0,
                    'test_failure': 0,
                    'total_generation_time': 0.0,
                    'avg_generation_time': 0.0,
                    'records': []
                }
            
            # 更新统计数据
            model_data = data[model_key]
            model_data['total_attempts'] += attempts
            
            # 更新生成时长统计
            if generation_time > 0:
                model_data['total_generation_time'] += generation_time
                if model_data['total_attempts'] > 0:
                    model_data['avg_generation_time'] = model_data['total_generation_time'] / model_data['total_attempts']
            
            if validation_success is not None:
                if validation_success:
                    model_data['validation_success'] += 1
                else:
                    model_data['validation_failure'] += 1
            
            if test_success is not None:
                if test_success:
                    model_data['test_success'] += 1
                else:
                    model_data['test_failure'] += 1
            
            # 添加详细记录
            record = {
                'timestamp': datetime.now().isoformat(),
                'algorithm': algorithm,
                'mode': mode,
                'language': language,
                'validation_success': validation_success,
                'test_success': test_success,
                'attempts': attempts,
                'generation_time': generation_time,
                'error_message': error_message
            }
            model_data['records'].append(record)
            
            # 只保留最近1000条记录，避免文件过大
            if len(model_data['records']) > 1000:
                model_data['records'] = model_data['records'][-1000:]
            
            # 保存数据
            with open(performance_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"已记录性能数据: {model_key} - 验证:{validation_success}, 测试:{test_success}, 尝试次数:{attempts}, 生成时长:{generation_time:.2f}秒")
        except Exception as e:
            logger.warning(f"记录性能数据失败: {e}")
    
    async def test_connection(self) -> Tuple[bool, str]:
        """
        测试API连接
        
        Returns:
            (是否成功, 消息)
        """
        try:
            # 发送一个简单的测试请求
            test_prompt = "请回复'OK'"
            test_system_prompt = "你是一个测试助手，只需要回复'OK'即可。"
            
            response = await self.llm.generate(test_prompt, test_system_prompt)
            
            if response:
                return True, "API连接测试成功"
            else:
                return False, "API返回空响应"
        except Exception as e:
            error_msg = str(e)
            # 简化错误信息
            if "api_key" in error_msg.lower() or "key" in error_msg.lower():
                return False, "API密钥无效或未配置"
            elif "timeout" in error_msg.lower():
                return False, "API连接超时"
            elif "network" in error_msg.lower() or "connection" in error_msg.lower():
                return False, "网络连接失败"
            else:
                return False, f"API连接失败: {error_msg}"
    
    def _get_system_prompt(self, language: str = 'python') -> str:
        """获取指定语言的系统提示词"""
        return get_system_prompt(language)
    
    def _extract_code(self, text: str, language: str = 'python') -> str:
        """从LLM返回的文本中提取纯代码"""
        return extract_code(text, language)

    def _detect_code_truncation(self, code: str, language: str) -> bool:
        """检测代码是否被截断"""
        return detect_code_truncation(code, language)

    def _fix_missing_headers(self, code: str, language: str) -> str:
        """修复缺失的头文件"""
        return fix_missing_headers(code, language)

    def _build_prompt(self, algorithm: str, mode: Optional[str] = None, 
                     operation: str = "加密解密", language: str = 'python', 
                     test_data: Optional[Dict] = None, **kwargs) -> str:
        """构建提示词"""
        return build_prompt(self, algorithm, mode, operation, language, test_data, **kwargs)

    async def generate_code(self, algorithm: str, mode: Optional[str] = None,
                           operation: str = "加密解密", language: str = 'python', 
                           test_data: Optional[Dict] = None, **kwargs) -> Tuple[str, float]:
        """生成密码学代码"""
        return await _generate_code(self, algorithm, mode, operation, language, test_data, **kwargs)

    async def improve_code(self, original_code: str, algorithm: str, mode: Optional[str] = None,
                          operation: str = "加密解密", language: str = 'python',
                          test_feedback: Optional[Dict] = None, **kwargs) -> Tuple[str, float]:
        """基于测试反馈改进代码"""
        return await _improve_code(self, original_code, algorithm, mode, operation, language, test_feedback, **kwargs)

    async def save_code(
        self,
        code: str,
        filename: str,
        algorithm: Optional[str] = None,
        mode: Optional[str] = None,
        **kwargs,
    ) -> Path:
        """保存生成的代码"""
        # 仅 CodeTester.test 需要；勿传入底层 save_code（否则 TypeError 导致整表 ok=false）
        kwargs.pop("suppress_heuristic_warnings", None)
        return await _save_code(self, code, filename, algorithm, mode, **kwargs)

    async def generate_and_save(self, algorithm: str, mode: Optional[str] = None,
                               operation: str = "加密解密", language: str = 'python',
                               filename: Optional[str] = None, validate: bool = True,
                               max_retries: int = 3, **kwargs) -> Tuple[Path, Optional[Tuple[bool, str]], Optional[Tuple[bool, str, Dict]], Optional[Tuple[bool, str, Dict]]]:
        """生成并保存代码，自动测试并重试直到通过测试"""
        return await _generate_and_save(self, algorithm, mode, operation, language, filename, validate, max_retries, **kwargs)

    def list_supported_algorithms(self) -> Dict[str, List[str]]:
        """列出支持的算法和模式"""
        return {
            'DES': self.config.get('des_modes', []),
            'AES': self.config.get('aes_modes', []),
            'RSA': [],  # RSA没有模式，只有操作类型（通过operation字段指定）
            'SM4': ['ECB', 'CBC', 'CFB', 'OFB']
        }
    
    def list_supported_languages(self) -> List[str]:
        """列出支持的编程语言"""
        return list(self.LANGUAGE_PROMPTS.keys())
    
    def list_available_providers(self) -> List[str]:
        """列出可用的LLM提供商（已启用且配置了API密钥的）"""
        from agent.llm.base import get_api_key
        from utils.llm_provider_ui import llm_provider_key_ready

        available = []
        llm_providers = self.config._config.get('llm_providers', {})
        
        for provider, config in llm_providers.items():
            if config.get('enabled', False):
                if llm_provider_key_ready(config, get_api_key):
                    available.append(provider)
        
        return available
    
    def get_provider_info(self) -> Dict[str, Dict]:
        """获取所有LLM提供商的配置信息"""
        return self.config._config.get('llm_providers', {})
