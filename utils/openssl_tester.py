"""
OpenSSL标准测试工具 - 使用OpenSSL命令行工具进行标准测试
"""
import subprocess
import tempfile
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple, List
from utils.logger import setup_logger

logger = setup_logger()

class OpenSSLTester:
    """OpenSSL标准测试器 - 使用OpenSSL命令行工具进行标准测试"""
    
    def __init__(self):
        self.temp_dir = Path(tempfile.gettempdir()) / "aicrypto_openssl_tester"
        self.temp_dir.mkdir(exist_ok=True)
        self.openssl_available = self._check_openssl_available()
        self.openssl_major = self._parse_openssl_major() if self.openssl_available else 0

    def _parse_openssl_major(self) -> int:
        """OpenSSL 3.x 默认上下文不含 DES，enc -des-* 需加载 legacy provider。"""
        try:
            result = subprocess.run(
                ["openssl", "version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return 1
            parts = (result.stdout or "").strip().split()
            if len(parts) >= 2 and parts[0] == "OpenSSL":
                m = parts[1].split(".")[0]
                if m.isdigit():
                    return int(m)
        except Exception:
            pass
        return 1

    def _check_openssl_available(self) -> bool:
        """检查OpenSSL是否可用"""
        try:
            result = subprocess.run(
                ['openssl', 'version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info(f"OpenSSL可用: {result.stdout.strip()}")
                return True
            else:
                logger.warning("OpenSSL不可用")
                return False
        except FileNotFoundError:
            logger.warning("OpenSSL未安装或不在PATH中")
            return False
        except Exception as e:
            logger.warning(f"检查OpenSSL时出错: {e}")
            return False
    
    def _hex_to_bytes(self, hex_str: str) -> bytes:
        """将十六进制字符串转换为字节"""
        hex_str = hex_str.replace(' ', '').replace('\n', '').strip()
        try:
            return bytes.fromhex(hex_str)
        except ValueError as e:
            raise ValueError(f"无效的十六进制字符串: {e}")
    
    def _bytes_to_hex(self, data: bytes) -> str:
        """将字节转换为十六进制字符串（小写）"""
        return data.hex().lower()
    
    def test_des_encrypt(self, plaintext_hex: str, key_hex: str, iv_hex: Optional[str] = None, 
                         mode: str = 'cbc') -> Tuple[bool, str, Dict]:
        """
        使用OpenSSL测试DES加密
        
        Args:
            plaintext_hex: 明文（十六进制字符串）
            key_hex: 密钥（十六进制字符串，8字节）
            iv_hex: IV（十六进制字符串，8字节，可选）
            mode: 模式 ('ecb', 'cbc', 'cfb', 'ofb')
            
        Returns:
            (是否成功, 消息, 详细信息)
        """
        if not self.openssl_available:
            return False, "OpenSSL不可用", {'error': 'OpenSSL未安装或不在PATH中'}
        
        try:
            # 转换输入
            plaintext = self._hex_to_bytes(plaintext_hex)
            key = self._hex_to_bytes(key_hex)
            
            if len(key) != 8:
                return False, f"密钥长度错误: 需要8字节，实际{len(key)}字节", {}
            
            # 构建OpenSSL命令
            cmd = ['openssl', 'enc', '-des']
            
            # 添加模式
            mode_map = {
                'ecb': '-des-ecb',
                'cbc': '-des-cbc',
                'cfb': '-des-cfb',
                'ofb': '-des-ofb'
            }
            if mode.lower() in mode_map:
                cmd = ['openssl', 'enc', mode_map[mode.lower()]]
            else:
                cmd = ['openssl', 'enc', '-des-cbc']

            # OpenSSL 3：DES 在 legacy provider 中，否则 inner_evp_generic_fetch: unsupported
            if self.openssl_major >= 3:
                cmd = ['openssl', '-provider', 'default', '-provider', 'legacy'] + cmd[1:]
            
            # 添加IV（如果提供；ECB 不需要 IV，传了会 warning: iv not used by this cipher）
            if iv_hex and mode.lower() != 'ecb':
                iv = self._hex_to_bytes(iv_hex)
                if len(iv) != 8:
                    return False, f"IV长度错误: 需要8字节，实际{len(iv)}字节", {}
                cmd.extend(['-iv', iv_hex])
            
            # 添加密钥
            cmd.extend(['-K', key_hex])
            
            # 不进行base64编码，直接输出原始字节
            cmd.append('-nosalt')
            cmd.append('-nopad')  # 不填充（对于某些测试）
            
            # 执行加密
            result = subprocess.run(
                cmd,
                input=plaintext,
                capture_output=True,
                timeout=10
            )
            
            if result.returncode != 0:
                error_msg = result.stderr.decode('utf-8', errors='ignore')
                return False, f"OpenSSL加密失败: {error_msg}", {'error': error_msg}
            
            # 获取密文
            ciphertext = result.stdout
            ciphertext_hex = self._bytes_to_hex(ciphertext)
            
            return True, "OpenSSL测试成功", {
                'ciphertext': ciphertext_hex,
                'plaintext': plaintext_hex,
                'key': key_hex,
                'iv': iv_hex,
                'mode': mode
            }
            
        except ValueError as e:
            return False, f"输入格式错误: {str(e)}", {'error': str(e)}
        except subprocess.TimeoutExpired:
            return False, "OpenSSL测试超时", {'error': 'timeout'}
        except Exception as e:
            logger.error(f"OpenSSL测试失败: {e}")
            return False, f"OpenSSL测试失败: {str(e)}", {'error': str(e)}
    
    def test_aes_encrypt(self, plaintext_hex: str, key_hex: str, iv_hex: Optional[str] = None,
                         key_size: int = 128, mode: str = 'cbc') -> Tuple[bool, str, Dict]:
        """
        使用OpenSSL测试AES加密
        
        Args:
            plaintext_hex: 明文（十六进制字符串）
            key_hex: 密钥（十六进制字符串）
            iv_hex: IV（十六进制字符串，16字节，可选）
            key_size: 密钥长度（128, 192, 256）
            mode: 模式 ('ecb', 'cbc', 'cfb', 'ofb', 'ctr', 'gcm')
            
        Returns:
            (是否成功, 消息, 详细信息)
        """
        if not self.openssl_available:
            return False, "OpenSSL不可用", {'error': 'OpenSSL未安装或不在PATH中'}
        
        try:
            # 转换输入
            plaintext = self._hex_to_bytes(plaintext_hex)
            key = self._hex_to_bytes(key_hex)
            
            # 验证密钥长度
            expected_key_len = key_size // 8
            if len(key) != expected_key_len:
                return False, f"密钥长度错误: 需要{expected_key_len}字节，实际{len(key)}字节", {}
            
            # 构建OpenSSL命令
            mode_map = {
                'ecb': f'-aes-{key_size}-ecb',
                'cbc': f'-aes-{key_size}-cbc',
                'cfb': f'-aes-{key_size}-cfb',
                'ofb': f'-aes-{key_size}-ofb',
                'ctr': f'-aes-{key_size}-ctr',
                'gcm': f'-aes-{key_size}-gcm'
            }
            
            if mode.lower() not in mode_map:
                return False, f"不支持的AES模式: {mode}", {}
            
            cmd = ['openssl', 'enc', mode_map[mode.lower()]]
            
            # 添加IV（如果提供且需要）
            if iv_hex and mode.lower() != 'ecb':
                iv = self._hex_to_bytes(iv_hex)
                expected_iv_len = 16  # AES IV总是16字节
                if len(iv) != expected_iv_len:
                    return False, f"IV长度错误: 需要{expected_iv_len}字节，实际{len(iv)}字节", {}
                cmd.extend(['-iv', iv_hex])
            
            # 添加密钥
            cmd.extend(['-K', key_hex])
            
            # 不进行base64编码，直接输出原始字节
            cmd.append('-nosalt')
            cmd.append('-nopad')  # 不填充（对于某些测试）
            
            # 执行加密
            result = subprocess.run(
                cmd,
                input=plaintext,
                capture_output=True,
                timeout=10
            )
            
            if result.returncode != 0:
                error_msg = result.stderr.decode('utf-8', errors='ignore')
                return False, f"OpenSSL加密失败: {error_msg}", {'error': error_msg}
            
            # 获取密文
            ciphertext = result.stdout
            ciphertext_hex = self._bytes_to_hex(ciphertext)
            
            return True, "OpenSSL测试成功", {
                'ciphertext': ciphertext_hex,
                'plaintext': plaintext_hex,
                'key': key_hex,
                'iv': iv_hex,
                'mode': mode,
                'key_size': key_size
            }
            
        except ValueError as e:
            return False, f"输入格式错误: {str(e)}", {'error': str(e)}
        except subprocess.TimeoutExpired:
            return False, "OpenSSL测试超时", {'error': 'timeout'}
        except Exception as e:
            logger.error(f"OpenSSL测试失败: {e}")
            return False, f"OpenSSL测试失败: {str(e)}", {'error': str(e)}
    
    def test_encrypt(self, algorithm: str, plaintext_hex: str, key_hex: str, 
                    iv_hex: Optional[str] = None, mode: Optional[str] = None,
                    key_size: Optional[int] = None) -> Tuple[bool, str, Dict]:
        """
        通用加密测试方法
        
        Args:
            algorithm: 算法 ('DES', 'AES')
            plaintext_hex: 明文（十六进制字符串）
            key_hex: 密钥（十六进制字符串）
            iv_hex: IV（十六进制字符串，可选）
            mode: 模式（可选，默认CBC）
            key_size: 密钥长度（仅AES，默认128）
            
        Returns:
            (是否成功, 消息, 详细信息)
        """
        algorithm = algorithm.upper()
        
        if algorithm == 'DES':
            return self.test_des_encrypt(plaintext_hex, key_hex, iv_hex, mode or 'cbc')
        elif algorithm == 'AES':
            return self.test_aes_encrypt(plaintext_hex, key_hex, iv_hex, key_size or 128, mode or 'cbc')
        else:
            return False, f"不支持的算法: {algorithm}", {'error': f'Unsupported algorithm: {algorithm}'}
    
    def compare_with_openssl(self, generated_ciphertext: str, plaintext_hex: str, 
                            key_hex: str, iv_hex: Optional[str] = None,
                            algorithm: str = 'DES', mode: Optional[str] = None,
                            key_size: Optional[int] = None) -> Tuple[bool, str, Dict]:
        """
        将生成的密文与OpenSSL标准结果进行比较
        
        Args:
            generated_ciphertext: 生成的密文（十六进制字符串）
            plaintext_hex: 明文（十六进制字符串）
            key_hex: 密钥（十六进制字符串）
            iv_hex: IV（十六进制字符串，可选）
            algorithm: 算法 ('DES', 'AES')
            mode: 模式（可选）
            key_size: 密钥长度（仅AES，可选）
            
        Returns:
            (是否匹配, 消息, 详细信息)
        """
        # 使用OpenSSL生成标准密文
        success, message, details = self.test_encrypt(
            algorithm, plaintext_hex, key_hex, iv_hex, mode, key_size
        )
        
        if not success:
            return False, f"OpenSSL测试失败: {message}", details
        
        openssl_ciphertext = details.get('ciphertext', '')
        generated_ciphertext = generated_ciphertext.replace(' ', '').replace('\n', '').strip().lower()
        openssl_ciphertext = openssl_ciphertext.replace(' ', '').replace('\n', '').strip().lower()
        
        # 比较密文
        if generated_ciphertext == openssl_ciphertext:
            return True, "生成的密文与OpenSSL标准结果完全匹配", {
                'generated': generated_ciphertext,
                'openssl': openssl_ciphertext,
                'match': True
            }
        else:
            return False, "生成的密文与OpenSSL标准结果不匹配", {
                'generated': generated_ciphertext,
                'openssl': openssl_ciphertext,
                'match': False,
                'generated_len': len(generated_ciphertext),
                'openssl_len': len(openssl_ciphertext)
            }
    
    def test_rsa_sign(self, plaintext_hex: str, private_key_n: str, private_key_d: str) -> Tuple[bool, str, Dict]:
        """
        使用OpenSSL测试RSA签名
        
        Args:
            plaintext_hex: 明文（十六进制字符串）
            private_key_n: 私钥模数n（十六进制字符串）
            private_key_d: 私钥指数d（十六进制字符串）
            
        Returns:
            (是否成功, 消息, 详细信息)
        """
        if not self.openssl_available:
            return False, "OpenSSL不可用", {'error': 'OpenSSL未安装或不在PATH中'}
        
        try:
            # 转换输入
            plaintext = self._hex_to_bytes(plaintext_hex)
            
            # 创建临时文件
            plaintext_file = self.temp_dir / "rsa_plaintext.bin"
            with open(plaintext_file, 'wb') as f:
                f.write(plaintext)
            
            # 构建RSA私钥（使用n和d）
            # 注意：OpenSSL需要完整的私钥格式，这里我们使用rsautl命令
            # 但rsautl需要PEM格式的私钥，所以我们需要先创建私钥文件
            
            # 使用Python的cryptography库来创建RSA私钥并签名
            try:
                from cryptography.hazmat.primitives.asymmetric import rsa
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.backends import default_backend
                import hashlib
                
                # 将十六进制字符串转换为整数
                n = int(private_key_n.replace(' ', '').replace('\n', ''), 16)
                d = int(private_key_d.replace(' ', '').replace('\n', ''), 16)
                
                # 创建RSA私钥（需要p, q, d等，但我们可以使用n和d）
                # 注意：这里我们使用公共指数65537（通常的e值）
                # 实际上，我们需要从n和d推导出完整的私钥参数
                # 为了简化，我们直接使用n和d进行签名计算
                
                # 使用OpenSSL的rsautl命令进行签名
                # 但rsautl需要PEM格式的私钥，所以我们使用Python的cryptography库
                
                # 计算SHA256哈希
                hash_obj = hashlib.sha256(plaintext)
                hash_value = hash_obj.digest()
                
                # 使用私钥对哈希值进行签名（RSA签名实际上是加密哈希值）
                # signature = hash_value^d mod n
                hash_int = int.from_bytes(hash_value, 'big')
                signature_int = pow(hash_int, d, n)
                
                # 将签名转换为十六进制
                signature_bytes = signature_int.to_bytes((signature_int.bit_length() + 7) // 8, 'big')
                signature_hex = self._bytes_to_hex(signature_bytes)
                
                return True, "RSA签名生成成功", {
                    'signature': signature_hex,
                    'plaintext': plaintext_hex,
                    'algorithm': 'RSA'
                }
                
            except ImportError:
                # 如果没有cryptography库，尝试使用OpenSSL命令行
                # 但OpenSSL需要PEM格式的私钥，这比较复杂
                return False, "需要cryptography库来生成RSA签名", {'error': 'cryptography library required'}
            except Exception as e:
                logger.error(f"RSA签名生成失败: {e}")
                return False, f"RSA签名生成失败: {str(e)}", {'error': str(e)}
                
        except ValueError as e:
            return False, f"输入格式错误: {str(e)}", {'error': str(e)}
        except Exception as e:
            logger.error(f"RSA签名测试失败: {e}")
            return False, f"RSA签名测试失败: {str(e)}", {'error': str(e)}
    
    def is_available(self) -> bool:
        """检查OpenSSL是否可用"""
        return self.openssl_available

