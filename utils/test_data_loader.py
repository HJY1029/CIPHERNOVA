"""
测试数据加载器
用于加载标准测试数据，验证生成的代码是否正确
"""
import yaml
from pathlib import Path
from typing import Dict, Optional, Any
from utils.logger import setup_logger

logger = setup_logger()

class TestDataLoader:
    """测试数据加载器"""
    
    def __init__(self, test_data_file: Optional[Path] = None, openssl_test_data_file: Optional[Path] = None):
        """
        初始化测试数据加载器
        
        Args:
            test_data_file: 测试数据文件路径，默认为项目根目录下的test_data.yaml
            openssl_test_data_file: OpenSSL官方测试数据文件路径，默认为项目根目录下的openssl_test_data.yaml
        """
        if test_data_file is None:
            # 默认使用项目根目录下的test_data.yaml
            project_root = Path(__file__).parent.parent
            test_data_file = project_root / "test_data.yaml"
        
        if openssl_test_data_file is None:
            # 默认使用项目根目录下的openssl_test_data.yaml
            project_root = Path(__file__).parent.parent
            openssl_test_data_file = project_root / "openssl_test_data.yaml"
        
        self.test_data_file = test_data_file
        self.openssl_test_data_file = openssl_test_data_file
        self.test_data = {}
        self.openssl_test_data = {}
        self._load_test_data()
        self._load_openssl_test_data()
    
    def _load_test_data(self):
        """加载测试数据"""
        try:
            if self.test_data_file.exists():
                with open(self.test_data_file, 'r', encoding='utf-8') as f:
                    self.test_data = yaml.safe_load(f) or {}
                logger.info(f"测试数据已加载: {self.test_data_file}")
            else:
                logger.warning(f"测试数据文件不存在: {self.test_data_file}")
                self.test_data = {}
        except Exception as e:
            logger.error(f"加载测试数据失败: {e}")
            self.test_data = {}
    
    def _load_openssl_test_data(self):
        """加载OpenSSL官方测试数据"""
        try:
            if self.openssl_test_data_file.exists():
                with open(self.openssl_test_data_file, 'r', encoding='utf-8') as f:
                    self.openssl_test_data = yaml.safe_load(f) or {}
                logger.info(f"OpenSSL测试数据已加载: {self.openssl_test_data_file}")
            else:
                logger.warning(f"OpenSSL测试数据文件不存在: {self.openssl_test_data_file}")
                self.openssl_test_data = {}
        except Exception as e:
            logger.error(f"加载OpenSSL测试数据失败: {e}")
            self.openssl_test_data = {}
    
    def get_test_data(self, algorithm: str, mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        获取指定算法和模式的测试数据
        
        Args:
            algorithm: 算法名称（如 'DES', 'AES', 'RSA'）
            mode: 模式（如 'ECB', 'CBC', 'CFB', 'OFB', 'GCM', 'CTR'），对于RSA可以为None
        
        Returns:
            测试数据字典，如果不存在则返回None
        """
        algorithm = algorithm.upper()
        
        if algorithm not in self.test_data:
            return None
        
        algo_data = self.test_data[algorithm]
        
        # 对于RSA，返回完整数据（不需要mode）
        if algorithm == 'RSA':
            return algo_data.copy() if algo_data else None
        
        # 对于其他算法，需要指定模式
        if mode is None:
            return None
        
        mode = mode.upper()
        
        # 首先检查modes字段（用于CTR、GCM等有独立测试数据的模式）
        if 'modes' in algo_data and mode in algo_data['modes']:
            mode_data = algo_data['modes'][mode]
            test_data = {
                'plaintext': mode_data.get('plaintext'),
                'key': mode_data.get('key'),
                'iv': mode_data.get('iv'),
                'expected_ciphertext': mode_data.get('ciphertext') or mode_data.get('ciphertext_with_tag'),
            }
            
            # 对于GCM模式，添加AAD和tag信息
            if mode == 'GCM':
                if 'aad' in mode_data:
                    test_data['aad'] = mode_data['aad']
                if 'tag' in mode_data:
                    test_data['tag'] = mode_data['tag']
                if 'ciphertext_with_tag' in mode_data:
                    test_data['expected_ciphertext'] = mode_data['ciphertext_with_tag']
            
            return test_data
        
        # 然后检查ciphertexts字段（用于ECB、CBC、CFB、OFB等使用默认测试数据的模式）
        if 'ciphertexts' in algo_data and mode in algo_data['ciphertexts']:
            test_data = algo_data.copy()
            test_data['expected_ciphertext'] = algo_data['ciphertexts'][mode]
            # 移除ciphertexts，只保留当前模式的密文
            del test_data['ciphertexts']
            # 移除modes字段（如果存在），因为当前模式不在modes中
            if 'modes' in test_data:
                del test_data['modes']
            
            # 对于GCM模式，如果存在iv_gcm字段，使用它作为IV（但代码仍从TEST_IV读取，只是提示使用前12字节）
            if mode == 'GCM' and 'iv_gcm' in algo_data:
                # 保留iv_gcm字段，但代码应该从TEST_IV读取完整的IV，然后使用前12字节
                # 为了兼容性，我们仍然设置iv为完整的IV，但添加iv_gcm提示
                test_data['iv_gcm'] = algo_data['iv_gcm']
                # 如果iv存在，保留它；如果不存在，使用iv_gcm作为iv（向后兼容）
                if 'iv' not in test_data or not test_data['iv']:
                    test_data['iv'] = algo_data.get('iv', algo_data['iv_gcm'])
            
            return test_data
        
        return None
    
    def has_test_data(self, algorithm: str, mode: Optional[str] = None) -> bool:
        """
        检查是否有指定算法和模式的测试数据
        
        Args:
            algorithm: 算法名称
            mode: 模式
        
        Returns:
            是否有测试数据
        """
        return self.get_test_data(algorithm, mode) is not None
    
    def get_all_algorithms(self) -> list:
        """获取所有有测试数据的算法列表"""
        return list(self.test_data.keys())
    
    def get_openssl_test_data(self, algorithm: str, mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        获取指定算法和模式的OpenSSL官方测试数据
        
        Args:
            algorithm: 算法名称（如 'DES', 'AES', 'RSA', 'SM4'）
            mode: 模式（如 'ECB', 'CBC', 'CFB', 'OFB', 'GCM', 'CTR'），对于RSA可以为None或'encrypt'/'sign'
        
        Returns:
            OpenSSL测试数据字典，如果不存在则返回None
        """
        algorithm = algorithm.upper()
        
        if algorithm not in self.openssl_test_data:
            return None
        
        algo_data = self.openssl_test_data[algorithm]
        
        # 对于RSA，需要指定操作类型（encrypt或sign）
        if algorithm == 'RSA':
            if mode is None:
                return None
            mode = mode.lower()
            if mode in ['encrypt', 'sign']:
                if mode in algo_data:
                    mode_data = algo_data[mode]
                    test_data = {
                        'plaintext': mode_data.get('plaintext'),
                        'expected_ciphertext': mode_data.get('ciphertext') or mode_data.get('signature'),
                    }
                    if mode == 'encrypt':
                        if 'public_key_n' in mode_data:
                            test_data['public_key'] = {
                                'n': mode_data['public_key_n'],
                                'e': mode_data.get('public_key_e', '10001')
                            }
                    elif mode == 'sign':
                        if 'private_key_n' in mode_data:
                            test_data['private_key'] = {
                                'n': mode_data['private_key_n'],
                                'd': mode_data.get('private_key_d')
                            }
                    return test_data
            return None
        
        # 对于其他算法，需要指定模式
        if mode is None:
            return None
        
        mode = mode.upper()
        
        # 检查是否有该模式的测试数据
        if mode in algo_data:
            mode_data = algo_data[mode]
            test_data = {
                'plaintext': mode_data.get('plaintext'),
                'key': mode_data.get('key'),
                'iv': mode_data.get('iv'),
                'expected_ciphertext': mode_data.get('ciphertext') or mode_data.get('expected_output'),
            }
            
            # 对于GCM模式，添加AAD和tag信息
            if mode == 'GCM':
                if 'aad' in mode_data:
                    test_data['aad'] = mode_data['aad']
                if 'tag' in mode_data:
                    test_data['tag'] = mode_data['tag']
                if 'expected_output' in mode_data:
                    test_data['expected_ciphertext'] = mode_data['expected_output']
            
            return test_data
        
        return None

