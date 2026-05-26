import subprocess
import tempfile
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple, List
from utils.logger import setup_logger
from utils.c_code_sanitize import sanitize_c_illegal_numeric_macros
from utils.python_code_sanitize import (
    aes_ofb_sanitize_hint,
    pop_eval_crypto_task,
    push_eval_crypto_task,
    sanitize_python_crypto_code,
)

logger = setup_logger()

class CodeValidator:
    """
    代码验证器 - 用于测试生成的代码是否正确
    
    注意：当前验证器只检查代码能否编译/运行，不验证输出内容是否正确。
    要验证输出是否正确，请使用自定义测试功能。
    """
    
    def __init__(self):
        self.temp_dir = Path(tempfile.gettempdir()) / "aicrypto_validator"
        self.temp_dir.mkdir(exist_ok=True)

    @staticmethod
    def _cleanup_paths(*paths: Path) -> None:
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    
    def validate_python(self, code: str, test_data: Optional[Dict] = None) -> Tuple[bool, str]:
        """
        验证Python代码
        
        Args:
            code: Python代码字符串
            test_data: 测试数据（可选）
            
        Returns:
            (是否成功, 输出信息)
        """
        build_id = uuid.uuid4().hex
        temp_file = self.temp_dir / f"test_{build_id}.py"
        try:
            alg = (test_data or {}).get("algorithm")
            mod = (test_data or {}).get("mode")
            hint = aes_ofb_sanitize_hint(
                str(alg) if alg is not None else None,
                str(mod) if mod is not None else None,
            )
            _alg = str(alg) if alg is not None else None
            _mod = str(mod) if mod is not None else None
            tok = push_eval_crypto_task(_alg, _mod)
            try:
                code = sanitize_python_crypto_code(
                    code,
                    temp_file.name,
                    hint_aes_mode=hint,
                    algorithm=_alg,
                    mode=_mod,
                )
            finally:
                pop_eval_crypto_task(tok)
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(code)
            
            # 准备环境变量（如果提供了测试数据）
            env = os.environ.copy()
            stdin_input = None
            if test_data:
                # 设置环境变量，避免代码等待stdin输入
                if 'plaintext' in test_data:
                    env['TEST_PLAINTEXT'] = test_data['plaintext']
                if 'key' in test_data:
                    env['TEST_KEY'] = test_data['key']
                if 'iv' in test_data:
                    env['TEST_IV'] = test_data['iv']
                if test_data.get('aad'):
                    env['TEST_AAD'] = test_data['aad']
                if 'public_key' in test_data:
                    if 'n' in test_data['public_key']:
                        env['TEST_PUBLIC_KEY_N'] = test_data['public_key']['n']
                    if 'e' in test_data['public_key']:
                        env['TEST_PUBLIC_KEY_E'] = test_data['public_key']['e']
                if 'private_key' in test_data:
                    if 'n' in test_data['private_key']:
                        env['TEST_PRIVATE_KEY_N'] = test_data['private_key']['n']
                    if 'd' in test_data['private_key']:
                        env['TEST_PRIVATE_KEY_D'] = test_data['private_key']['d']
                
                # 准备stdin输入（向后兼容，如果代码从stdin读取）
                stdin_lines = []
                if 'plaintext' in test_data:
                    stdin_lines.append(test_data['plaintext'])
                if 'key' in test_data:
                    stdin_lines.append(test_data['key'])
                if 'iv' in test_data:
                    stdin_lines.append(test_data['iv'])
                if stdin_lines:
                    stdin_input = '\n'.join(stdin_lines) + '\n'
            
            # 执行代码（增加超时时间到60秒，因为某些算法需要加载库或执行复杂计算）
            result = subprocess.run(
                [sys.executable, str(temp_file)],
                capture_output=True,
                text=True,
                timeout=60,  # 从30秒增加到60秒
                cwd=str(self.temp_dir),
                env=env,
                input=stdin_input
            )
            
            if result.returncode == 0:
                output_msg = result.stdout
                if output_msg:
                    output_msg += "\n\n注意：验证通过仅表示代码可以运行，不保证输出内容正确。请使用自定义测试功能验证输出是否正确。"
                else:
                    output_msg = "代码执行成功（无输出）\n\n注意：验证通过仅表示代码可以运行，不保证输出内容正确。请使用自定义测试功能验证输出是否正确。"
                return True, output_msg
            else:
                return False, result.stderr
                
        except subprocess.TimeoutExpired:
            return False, "代码执行超时"
        except Exception as e:
            return False, f"验证失败: {str(e)}"
        finally:
            self._cleanup_paths(temp_file)
    
    def validate_c(
        self,
        code: str,
        test_data: Optional[Dict] = None,
        *,
        allow_canonical_whole_file: Optional[bool] = None,
        allow_error_auto_repair: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """
        验证C代码
        
        Args:
            code: C代码字符串
            test_data: 测试数据（可选）
            allow_canonical_whole_file: None 沿用 ContextVar；False 禁止 golden 整文件替换（论文消融）
            allow_error_auto_repair: None 沿用 ContextVar；False 关闭写盘后段错误自动修复（论文消融）
            
        Returns:
            (是否成功, 输出信息)
        """
        build_id = uuid.uuid4().hex
        temp_file = self.temp_dir / f"test_{build_id}.c"
        executable = self.temp_dir / f"test_{build_id}"
        try:
            td_alg = (test_data or {}).get("algorithm")
            td_mod = (test_data or {}).get("mode")
            code = sanitize_c_illegal_numeric_macros(
                code,
                temp_file.name,
                algorithm=str(td_alg) if td_alg is not None else None,
                mode=str(td_mod) if td_mod is not None else None,
                allow_canonical_whole_file=allow_canonical_whole_file,
                allow_error_auto_repair=allow_error_auto_repair,
            )
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(code)
            
            # 先尝试不使用OpenSSL编译（纯C实现）
            compile_result = subprocess.run(
                ['gcc', '-o', str(executable), str(temp_file), '-lm'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # 如果编译失败，尝试使用OpenSSL（如果代码使用了OpenSSL）
            if compile_result.returncode != 0:
                compile_result_openssl = subprocess.run(
                    ['gcc', '-o', str(executable), str(temp_file), '-lssl', '-lcrypto'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if compile_result_openssl.returncode != 0:
                    # 两次编译都失败
                    error_msg = compile_result.stderr
                    # 检查是否是因为OpenSSL不可用
                    if 'openssl' in error_msg.lower() or 'des.h' in error_msg.lower() or 'aes.h' in error_msg.lower() or 'cannot find -lcrypto' in error_msg.lower():
                        error_msg += "\n\n提示：代码使用了OpenSSL库，但系统未安装OpenSSL开发库。\n" + \
                                   "解决方案：生成不依赖OpenSSL的纯C实现代码（必须实现完整的DES/AES算法，不能只是XOR操作）。"
                    return False, f"编译失败: {error_msg}"
                # 使用OpenSSL版本编译成功
                compile_result = compile_result_openssl
            
            # 准备环境变量（如果提供了测试数据）
            env = os.environ.copy()
            stdin_input = None
            if test_data:
                # 设置环境变量，避免代码等待stdin输入
                if 'plaintext' in test_data:
                    env['TEST_PLAINTEXT'] = test_data['plaintext']
                if 'key' in test_data:
                    env['TEST_KEY'] = test_data['key']
                if 'iv' in test_data:
                    env['TEST_IV'] = test_data['iv']
                if test_data.get('aad'):
                    env['TEST_AAD'] = test_data['aad']
                if 'public_key' in test_data:
                    if 'n' in test_data['public_key']:
                        env['TEST_PUBLIC_KEY_N'] = test_data['public_key']['n']
                    if 'e' in test_data['public_key']:
                        env['TEST_PUBLIC_KEY_E'] = test_data['public_key']['e']
                if 'private_key' in test_data:
                    if 'n' in test_data['private_key']:
                        env['TEST_PRIVATE_KEY_N'] = test_data['private_key']['n']
                    if 'd' in test_data['private_key']:
                        env['TEST_PRIVATE_KEY_D'] = test_data['private_key']['d']
                
                # 准备stdin输入（向后兼容，如果代码从stdin读取）
                stdin_lines = []
                if 'plaintext' in test_data:
                    stdin_lines.append(test_data['plaintext'])
                if 'key' in test_data:
                    stdin_lines.append(test_data['key'])
                if 'iv' in test_data:
                    stdin_lines.append(test_data['iv'])
                if stdin_lines:
                    stdin_input = '\n'.join(stdin_lines) + '\n'
            
            # 执行编译后的程序（增加超时时间到60秒）
            run_result = subprocess.run(
                [str(executable)],
                capture_output=True,
                text=True,
                timeout=60,  # 从30秒增加到60秒
                cwd=str(self.temp_dir),
                env=env,
                input=stdin_input
            )
            
            if run_result.returncode == 0:
                return True, run_result.stdout
            else:
                return False, run_result.stderr
                
        except FileNotFoundError:
            return False, "未找到gcc编译器，请先安装gcc"
        except subprocess.TimeoutExpired:
            return False, "代码执行超时"
        except Exception as e:
            return False, f"验证失败: {str(e)}"
        finally:
            self._cleanup_paths(executable, temp_file)
    
    def validate_cpp(
        self,
        code: str,
        test_data: Optional[Dict] = None,
        *,
        allow_canonical_whole_file: Optional[bool] = None,
        allow_error_auto_repair: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """
        验证C++代码
        
        Args:
            code: C++代码字符串
            test_data: 测试数据（可选）
            allow_canonical_whole_file: None 沿用 ContextVar；False 禁止 golden 整文件替换（论文消融）
            allow_error_auto_repair: None 沿用 ContextVar；False 关闭写盘后段错误自动修复（论文消融）
            
        Returns:
            (是否成功, 输出信息)
        """
        build_id = uuid.uuid4().hex
        temp_file = self.temp_dir / f"test_{build_id}.cpp"
        executable = self.temp_dir / f"test_{build_id}"
        try:
            td_alg = (test_data or {}).get("algorithm")
            td_mod = (test_data or {}).get("mode")
            code = sanitize_c_illegal_numeric_macros(
                code,
                temp_file.name,
                algorithm=str(td_alg) if td_alg is not None else None,
                mode=str(td_mod) if td_mod is not None else None,
                allow_canonical_whole_file=allow_canonical_whole_file,
                allow_error_auto_repair=allow_error_auto_repair,
            )
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(code)
            
            # 先尝试不使用OpenSSL编译（纯C++实现）
            compile_result = subprocess.run(
                ['g++', '-o', str(executable), str(temp_file), '-lm', '-std=c++11'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # 如果编译失败，尝试使用OpenSSL（如果代码使用了OpenSSL）
            if compile_result.returncode != 0:
                compile_result_openssl = subprocess.run(
                    ['g++', '-o', str(executable), str(temp_file), '-lssl', '-lcrypto', '-std=c++11'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if compile_result_openssl.returncode != 0:
                    # 两次编译都失败
                    error_msg = compile_result.stderr
                    # 检查是否是因为OpenSSL不可用
                    if 'openssl' in error_msg.lower() or 'des.h' in error_msg.lower() or 'aes.h' in error_msg.lower() or 'cannot find -lcrypto' in error_msg.lower():
                        error_msg += "\n\n提示：代码使用了OpenSSL库，但系统未安装OpenSSL开发库。\n" + \
                                   "解决方案：生成不依赖OpenSSL的纯C++实现代码（必须实现完整的DES/AES算法，不能只是XOR操作）。"
                    return False, f"编译失败: {error_msg}"
                # 使用OpenSSL版本编译成功
                compile_result = compile_result_openssl
            
            # 准备环境变量（如果提供了测试数据）
            env = os.environ.copy()
            stdin_input = None
            if test_data:
                # 设置环境变量，避免代码等待stdin输入
                if 'plaintext' in test_data:
                    env['TEST_PLAINTEXT'] = test_data['plaintext']
                if 'key' in test_data:
                    env['TEST_KEY'] = test_data['key']
                if 'iv' in test_data:
                    env['TEST_IV'] = test_data['iv']
                if test_data.get('aad'):
                    env['TEST_AAD'] = test_data['aad']
                if 'public_key' in test_data:
                    if 'n' in test_data['public_key']:
                        env['TEST_PUBLIC_KEY_N'] = test_data['public_key']['n']
                    if 'e' in test_data['public_key']:
                        env['TEST_PUBLIC_KEY_E'] = test_data['public_key']['e']
                if 'private_key' in test_data:
                    if 'n' in test_data['private_key']:
                        env['TEST_PRIVATE_KEY_N'] = test_data['private_key']['n']
                    if 'd' in test_data['private_key']:
                        env['TEST_PRIVATE_KEY_D'] = test_data['private_key']['d']
                
                # 准备stdin输入（向后兼容，如果代码从stdin读取）
                stdin_lines = []
                if 'plaintext' in test_data:
                    stdin_lines.append(test_data['plaintext'])
                if 'key' in test_data:
                    stdin_lines.append(test_data['key'])
                if 'iv' in test_data:
                    stdin_lines.append(test_data['iv'])
                if stdin_lines:
                    stdin_input = '\n'.join(stdin_lines) + '\n'
            
            # 执行编译后的程序（增加超时时间到60秒）
            run_result = subprocess.run(
                [str(executable)],
                capture_output=True,
                text=True,
                timeout=60,  # 从30秒增加到60秒
                cwd=str(self.temp_dir),
                env=env,
                input=stdin_input
            )
            
            if run_result.returncode == 0:
                return True, run_result.stdout
            else:
                return False, run_result.stderr
                
        except FileNotFoundError:
            return False, "未找到g++编译器，请先安装g++"
        except subprocess.TimeoutExpired:
            return False, "代码执行超时"
        except Exception as e:
            return False, f"验证失败: {str(e)}"
        finally:
            self._cleanup_paths(executable, temp_file)
    
    def validate(
        self,
        code: str,
        language: str,
        test_data: Optional[Dict] = None,
        *,
        allow_canonical_whole_file: Optional[bool] = None,
        allow_error_auto_repair: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """
        通用验证方法
        
        Args:
            code: 代码字符串
            language: 编程语言 ('python', 'c', 'cpp')
            test_data: 测试数据（可选）
            allow_canonical_whole_file: 传入 generate_and_save 与消融档位一致的开关（线程池内需显式传递）
            allow_error_auto_repair: C/C++ 写盘清洗是否启用后段错误自动修复
            
        Returns:
            (是否成功, 输出信息)
        """
        language = language.lower()
        
        if language == 'python':
            return self.validate_python(code, test_data)
        elif language == 'c':
            return self.validate_c(
                code,
                test_data,
                allow_canonical_whole_file=allow_canonical_whole_file,
                allow_error_auto_repair=allow_error_auto_repair,
            )
        elif language == 'cpp' or language == 'c++':
            return self.validate_cpp(
                code,
                test_data,
                allow_canonical_whole_file=allow_canonical_whole_file,
                allow_error_auto_repair=allow_error_auto_repair,
            )
        else:
            return False, f"不支持的语言: {language}"
    
    def cleanup(self):
        """清理临时文件"""
        try:
            import shutil
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                self.temp_dir.mkdir(exist_ok=True)
        except Exception as e:
            logger.warning(f"清理临时文件失败: {e}")

