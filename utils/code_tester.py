"""
代码测试器 - 用于执行生成的代码并验证用户输入的测试数据
"""
import subprocess
import tempfile
import os
import sys
import re
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

class CodeTester:
    """代码测试器 - 用于执行代码并验证用户输入的测试数据"""
    
    def __init__(self):
        self.temp_dir = Path(tempfile.gettempdir()) / "aicrypto_tester"
        self.temp_dir.mkdir(exist_ok=True)

    @staticmethod
    def _cleanup_paths(*paths: Path) -> None:
        """并行测试时不能共用固定可执行文件名；清理避免 /tmp 堆积。"""
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    _STRICT_CIPHER_LINE_KEYS = frozenset(
        {r"密文", r"ciphertext", r"加密结果", r"encrypted"}
    )
    _CIPHER_LINE_LABELS = ("密文", "ciphertext", "加密结果", "encrypted")

    def _collapse_cipher_hex_multiline(self, output: str) -> str:
        """密文: 单独一行、hex 在后续行时合并为一行，便于严格正则提取。"""
        if not output:
            return output
        lines = output.replace("\r\n", "\n").split("\n")
        out_lines = []
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            consumed = False
            for kw in self._CIPHER_LINE_LABELS:
                if re.match(rf"(?i)^{re.escape(kw)}\s*[:：]\s*$", stripped):
                    j = i + 1
                    parts = []
                    while j < len(lines):
                        ln = lines[j].strip()
                        if not ln:
                            j += 1
                            continue
                        compact = re.sub(r"\s+", "", ln)
                        if re.fullmatch(r"[0-9a-fA-F]+", compact) and len(compact) >= 8:
                            parts.append(compact)
                            j += 1
                        else:
                            break
                    if parts:
                        hx = "".join(parts)
                        if len(hx) >= 8:
                            out_lines.append(f"{kw}: {hx}")
                            i = j
                            consumed = True
                            break
            if not consumed:
                out_lines.append(lines[i])
                i += 1
        return "\n".join(out_lines)

    def _output_looks_like_cipher_instruction(self, output: str) -> bool:
        """输出是否为照抄的评测说明（非 hex），如含「请确保代码输出包含…」。"""
        if not output:
            return False
        return (
            "请确保代码输出包含" in output
            or ("关键词" in output and "加密结果" in output)
            or "冒号后为纯十六进制" in output
        )

    def _msg_extract_ciphertext_failed(self, output: str) -> str:
        # 提示须短、且勿与题面/printf 模板撞车，避免被模型原样写进下轮 printf
        if self._output_looks_like_cipher_instruction(output):
            hint = "（E-HEX-INSTR）非 hex 说明文。改源码：仅密文标签+%%02x，勿打印长句说明。"
        else:
            hint = "（E-HEX-MISS）未匹配到密文: 后纯十六进制。检查标签与 Update/Final 输出长度。"
        return f"无法从输出中提取密文。\n程序输出:\n{output}\n{hint}"

    def _strict_keyword_hex_line(self, output: str, search_patterns: List[str]) -> Optional[str]:
        """
        从最后一行向上查找「关键词: 纯十六进制」整行，避免误匹配「…包含'密文'…」等。
        """
        keys = [p for p in search_patterns if p in self._STRICT_CIPHER_LINE_KEYS]
        if not keys:
            return None
        lines = output.replace("\r\n", "\n").split("\n")
        for raw in reversed(lines):
            line = raw.strip()
            if not line:
                continue
            for k in keys:
                esc = re.escape(k)
                m = re.match(rf"(?i)^{esc}\s*[:：]\s*([0-9a-fA-F]+)\s*$", line)
                if m and len(m.group(1)) >= 8:
                    return m.group(1).lower()
        return None

    def _maybe_strip_iv_from_hex(
        self,
        result: str,
        expected_length: Optional[int],
        *,
        suppress_heuristic_warnings: bool = False,
    ) -> str:
        """若密文 hex 比预期多 16/32 字符，按原逻辑尝试去掉可能附带的 IV。"""
        # 「无测试反馈改进」消融：不做密文长度启发式修补（不剥离疑似 IV），仅做原始 hex 与标准向量比对
        if suppress_heuristic_warnings:
            return result
        if not (expected_length and len(result) > expected_length):
            return result
        diff = len(result) - expected_length
        if diff not in (16, 32):
            return result
        result_tail = result[-diff:]
        result_head = result[:expected_length]
        def _iv_warn(msg: str) -> None:
            if not suppress_heuristic_warnings:
                logger.warning(msg)

        if result_tail == "0" * diff:
            _iv_warn(f"检测到IV（全零）被附加在密文末尾，已去除: {result_tail}")
            return result_head
        if result_tail.count("0") >= diff // 2:
            _iv_warn(f"检测到可能的IV被附加在密文末尾（包含大量零），已去除: {result_tail}")
            return result_head
        result_prefix = result[:diff]
        result_suffix = result[diff:]
        if result_prefix == "0" * diff:
            _iv_warn(f"检测到IV（全零）被附加在密文开头，已去除: {result_prefix}")
            return result_suffix
        if result_prefix.count("0") >= diff // 2:
            _iv_warn(f"检测到可能的IV被附加在密文开头（包含大量零），已去除: {result_prefix}")
            return result_suffix
        _iv_warn(f"密文长度比预期多{diff}个字符（可能是IV），尝试去除末尾: {result_tail}")
        return result_head
    
    def _extract_result_from_output(
        self,
        output: str,
        search_patterns: List[str],
        expected_length: Optional[int] = None,
        *,
        suppress_heuristic_warnings: bool = False,
    ) -> Optional[str]:
        """
        从输出中提取结果
        
        Args:
            output: 程序输出
            search_patterns: 搜索模式列表（按优先级排序）
            expected_length: 预期结果长度（十六进制字符数），用于检测是否包含了IV等额外信息
            
        Returns:
            提取的结果字符串，如果未找到则返回None
        """
        output = self._collapse_cipher_hex_multiline(output)
        # 优先：整行「密文:/ciphertext: … + 纯 hex」，取自下而上第一条（兼容多行 input 提示后再打印密文）
        strict = self._strict_keyword_hex_line(output, search_patterns)
        if strict is not None:
            return self._maybe_strip_iv_from_hex(
                strict,
                expected_length,
                suppress_heuristic_warnings=suppress_heuristic_warnings,
            )

        # 尝试匹配各种可能的输出格式
        for pattern in search_patterns:
            # 尝试匹配十六进制格式（支持多行）
            hex_pattern = rf'{pattern}[:：\s]*([0-9a-fA-F\s\n]+?)(?:\n\n|\n[A-Za-z]|$)'
            match = re.search(hex_pattern, output, re.IGNORECASE | re.MULTILINE)
            if match:
                result = match.group(1).strip().replace(' ', '').replace('\n', '')
                if len(result) >= 8:  # 至少8个字符才认为是有效结果
                    return self._maybe_strip_iv_from_hex(
                        result,
                        expected_length,
                        suppress_heuristic_warnings=suppress_heuristic_warnings,
                    )
            
            # 尝试匹配Base64格式（支持多行）
            base64_pattern = rf'{pattern}[:：\s]*([A-Za-z0-9+/=\s\n]+?)(?:\n\n|\n[A-Za-z]|$)'
            match = re.search(base64_pattern, output, re.IGNORECASE | re.MULTILINE)
            if match:
                result = match.group(1).strip().replace(' ', '').replace('\n', '')
                if len(result) >= 8:  # 至少8个字符才认为是有效结果
                    return result
            
            # 尝试匹配普通文本格式（引号内的内容）
            quoted_pattern = rf'{pattern}[:：\s]*["\']([^"\']+)["\']'
            match = re.search(quoted_pattern, output, re.IGNORECASE)
            if match:
                result = match.group(1).strip()
                if result:
                    return result
            
            # 尝试匹配普通文本格式（冒号后的内容）
            text_pattern = rf'{pattern}[:：\s]+([^\n]+)'
            match = re.search(text_pattern, output, re.IGNORECASE)
            if match:
                result = match.group(1).strip()
                # 移除可能的引号
                result = result.strip('"\'')
                if result and len(result) >= 4:  # 至少4个字符
                    compact = re.sub(r"\s+", "", result)
                    is_hex = bool(
                        re.fullmatch(r"[0-9a-fA-F]+", compact, flags=re.IGNORECASE)
                    )
                    # 已知预期为 hex 时，拒绝「请确保…」等非 hex 说明被当成密文
                    if expected_length is not None and not is_hex:
                        continue
                    if is_hex and len(compact) >= 8:
                        return self._maybe_strip_iv_from_hex(
                            compact.lower(),
                            expected_length,
                            suppress_heuristic_warnings=suppress_heuristic_warnings,
                        )
                    if expected_length is None:
                        return result
        
        # 如果所有模式都失败，尝试提取所有看起来像密文/明文的字符串
        # 查找十六进制字符串（至少16个字符）
        hex_matches = re.findall(r'[0-9a-fA-F]{16,}', output, re.IGNORECASE)
        if hex_matches:
            return hex_matches[-1].lower()  # 返回最后一个匹配，转为小写
        
        # 查找Base64字符串（至少20个字符）
        base64_matches = re.findall(r'[A-Za-z0-9+/=]{20,}', output)
        if base64_matches:
            return base64_matches[-1]  # 返回最后一个匹配
        
        return None
    
    def _normalize_text(self, text: str) -> str:
        """
        规范化文本，去除空格、换行等
        """
        # 移除所有空白字符
        text = re.sub(r'\s+', '', text)
        # 转换为小写（对于十六进制）
        if re.match(r'^[0-9a-fA-F]+$', text):
            text = text.lower()
        return text
    
    def _compare_results(
        self,
        actual: str,
        expected: str,
        *,
        suppress_heuristic_warnings: bool = False,
    ) -> Tuple[bool, str, Dict]:
        """
        比较实际结果和预期结果
        
        suppress_heuristic_warnings: 为 True 时不附加长度/IV 等面向改进轮次的诊断段落（消融「无测试反馈改进」）
        
        Returns:
            (是否匹配, 消息, 详细信息字典)
        """
        details = {
            'actual': actual or '(空)',
            'expected': expected or '(空)',
            'actual_normalized': None,
            'expected_normalized': None,
            'match': False
        }
        
        if not actual or not expected:
            return False, f"测试失败：结果为空\n实际结果: {actual or '(空)'}\n预期结果: {expected or '(空)'}", details
        
        actual_normalized = self._normalize_text(actual)
        expected_normalized = self._normalize_text(expected)
        
        details['actual_normalized'] = actual_normalized
        details['expected_normalized'] = expected_normalized
        details['match'] = (actual_normalized == expected_normalized)
        
        logger.info(f"比较结果 - 实际: {actual_normalized}, 预期: {expected_normalized}")
        
        if actual_normalized == expected_normalized:
            return True, f"测试通过：结果匹配\n实际结果: {actual}\n预期结果: {expected}", details
        else:
            diagnostic_msg = (
                f"测试失败：结果不匹配\n实际结果: {actual}\n预期结果: {expected}\n"
                f"(规范化后: 实际={actual_normalized}, 预期={expected_normalized})\n"
            )
            if suppress_heuristic_warnings:
                return False, diagnostic_msg, details

            # 提供更详细的诊断信息（测试反馈改进路径）
            actual_len = len(actual_normalized)
            expected_len = len(expected_normalized)
            len_diff = actual_len - expected_len
            
            # 如果长度不匹配，提供诊断信息
            if len_diff != 0:
                diagnostic_msg += f"\n长度分析：\n"
                diagnostic_msg += f"  - 实际长度: {actual_len} 个字符 ({actual_len//2} 字节)\n"
                diagnostic_msg += f"  - 预期长度: {expected_len} 个字符 ({expected_len//2} 字节)\n"
                diagnostic_msg += f"  - 长度差异: {len_diff} 个字符 ({len_diff//2} 字节)\n"
                
                # 检查是否是IV的问题
                if len_diff == 16:
                    diagnostic_msg += f"\n可能的问题：\n"
                    diagnostic_msg += f"  - 代码可能输出了DES的IV（8字节，16个hex字符）\n"
                    diagnostic_msg += f"  - IV不应该出现在输出中，只应该输出密文本身\n"
                    diagnostic_msg += f"  - 如果已尝试去除末尾16个字符，说明代码逻辑本身可能有问题\n"
                elif len_diff == 32:
                    diagnostic_msg += f"\n可能的问题：\n"
                    diagnostic_msg += f"  - 代码可能输出了AES的IV（16字节，32个hex字符）\n"
                    diagnostic_msg += f"  - IV不应该出现在输出中，只应该输出密文本身\n"
                    diagnostic_msg += f"  - 如果已尝试去除末尾32个字符，说明代码逻辑本身可能有问题\n"
                else:
                    diagnostic_msg += f"\n可能的问题：\n"
                    diagnostic_msg += f"  - 密文长度不正确，可能是填充、IV或其他问题\n"
                    diagnostic_msg += f"  - 请检查代码是否正确处理了所有明文字节\n"
                    diagnostic_msg += f"  - 请检查代码是否正确输出了所有密文字节\n"
            
            # 检查是否有部分匹配
            if actual_len == expected_len:
                # 计算匹配的字符数
                matches = sum(1 for a, e in zip(actual_normalized, expected_normalized) if a == e)
                match_rate = matches / actual_len * 100 if actual_len > 0 else 0
                diagnostic_msg += f"\n匹配度分析：\n"
                diagnostic_msg += f"  - 匹配字符数: {matches}/{actual_len}\n"
                diagnostic_msg += f"  - 匹配率: {match_rate:.1f}%\n"
                if match_rate < 50:
                    diagnostic_msg += f"  - 匹配率很低，说明代码逻辑可能有根本性错误\n"
            
            return False, diagnostic_msg, details
    
    def test_python(self, code: str, plaintext: Optional[str] = None, 
                    expected_ciphertext: Optional[str] = None,
                    ciphertext: Optional[str] = None,
                    expected_plaintext: Optional[str] = None,
                    key: Optional[str] = None,
                    iv: Optional[str] = None,
                    aad: Optional[str] = None,
                    public_key_n: Optional[str] = None,
                    public_key_e: Optional[str] = None,
                    private_key_n: Optional[str] = None,
                    private_key_d: Optional[str] = None,
                    signature: Optional[str] = None,
                    algorithm: Optional[str] = None,
                    mode: Optional[str] = None,
                    *,
                    suppress_heuristic_warnings: bool = False,
                    ) -> Tuple[bool, str, Dict]:
        """
        测试Python代码
        
        Args:
            code: Python代码字符串
            plaintext: 明文（用于测试加密）
            expected_ciphertext: 预期密文（用于测试加密）
            ciphertext: 密文（用于测试解密）
            expected_plaintext: 预期明文（用于测试解密）
            algorithm/mode: 可选，用于 AES+OFB 等场景的源码清洗（与临时文件名解耦）
            
        Returns:
            (是否成功, 输出信息)
        """
        build_id = uuid.uuid4().hex
        temp_file = self.temp_dir / f"test_{build_id}.py"
        try:
            hint = aes_ofb_sanitize_hint(algorithm, mode)
            task_tok = push_eval_crypto_task(algorithm, mode)
            try:
                code = sanitize_python_crypto_code(
                    code,
                    temp_file.name,
                    hint_aes_mode=hint,
                    algorithm=algorithm,
                    mode=mode,
                )
            finally:
                pop_eval_crypto_task(task_tok)
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(code)
            
            # 准备环境变量
            env = os.environ.copy()
            if plaintext:
                env['TEST_PLAINTEXT'] = plaintext
            if ciphertext:
                env['TEST_CIPHERTEXT'] = ciphertext
            if key:
                env['TEST_KEY'] = key
            if iv:
                env['TEST_IV'] = iv
            if aad:
                env['TEST_AAD'] = aad
            if public_key_n:
                env['TEST_PUBLIC_KEY_N'] = public_key_n
            if public_key_e:
                env['TEST_PUBLIC_KEY_E'] = public_key_e
            if private_key_n:
                env['TEST_PRIVATE_KEY_N'] = private_key_n
            if private_key_d:
                env['TEST_PRIVATE_KEY_D'] = private_key_d
            if signature:
                env['TEST_SIGNATURE'] = signature
            
            # 准备stdin输入（向后兼容，如果代码从stdin读取）
            stdin_input = None
            if plaintext or ciphertext:
                stdin_lines = []
                if plaintext:
                    stdin_lines.append(plaintext)
                elif ciphertext:
                    stdin_lines.append(ciphertext)
                if key:
                    stdin_lines.append(key)
                if iv:
                    stdin_lines.append(iv)
                stdin_input = '\n'.join(stdin_lines) + '\n'
            
            # 执行代码
            result = subprocess.run(
                [sys.executable, str(temp_file)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.temp_dir),
                env=env,
                input=stdin_input
            )
            
            if result.returncode != 0:
                output = result.stdout + result.stderr
                # 尝试从输出中提取结果，即使执行失败
                if plaintext and expected_ciphertext:
                    search_patterns = [r'密文', r'ciphertext', r'加密结果', r'encrypted', r'输出']
                    actual = (
                        self._extract_result_from_output(
                            output,
                            search_patterns,
                            suppress_heuristic_warnings=suppress_heuristic_warnings,
                        )
                        or ''
                    )
                    return False, f"代码执行失败: {result.stderr}", {'output': output, 'actual': actual, 'expected': expected_ciphertext}
                elif ciphertext and expected_plaintext:
                    search_patterns = [r'明文', r'plaintext', r'解密结果', r'decrypted', r'输出']
                    actual = (
                        self._extract_result_from_output(
                            output,
                            search_patterns,
                            suppress_heuristic_warnings=suppress_heuristic_warnings,
                        )
                        or ''
                    )
                    return False, f"代码执行失败: {result.stderr}", {'output': output, 'actual': actual, 'expected': expected_plaintext}
                else:
                    return False, f"代码执行失败: {result.stderr}", {'output': output, 'actual': '', 'expected': ''}
            
            output = result.stdout + result.stderr
            logger.info(f"程序输出:\n{output}")
            
            # 根据测试类型提取结果
            if plaintext and expected_ciphertext:
                # 测试加密
                search_patterns = [
                    r'密文',
                    r'ciphertext',
                    r'加密结果',
                    r'encrypted',
                    r'输出'
                ]
                # 传入预期长度，用于检测是否包含了IV
                expected_len = len(expected_ciphertext) if expected_ciphertext else None
                actual_ciphertext = self._extract_result_from_output(
                    output,
                    search_patterns,
                    expected_length=expected_len,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                
                if not actual_ciphertext:
                    return False, self._msg_extract_ciphertext_failed(output), {'output': output, 'actual': '', 'expected': expected_ciphertext}
                
                logger.info(f"提取到的密文: {actual_ciphertext}")
                logger.info(f"预期的密文: {expected_ciphertext}")
                
                # 提取签名（如果存在）- 仅对RSA算法
                actual_signature = None
                if private_key_n and private_key_d:  # 如果提供了私钥，尝试提取签名
                    signature_patterns = [
                        r'签名',
                        r'signature',
                        r'数字签名',
                        r'digital.*signature'
                    ]
                    actual_signature = self._extract_result_from_output(
                    output,
                    signature_patterns,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                    if actual_signature:
                        logger.info(f"提取到的签名: {actual_signature}")
                
                match, message, details = self._compare_results(
                    actual_ciphertext,
                    expected_ciphertext,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                details['output'] = output
                if actual_signature:
                    details['signature'] = actual_signature
                if match:
                    return True, message, details
                else:
                    return False, message + f"\n\n程序完整输出:\n{output}", details
            
            elif ciphertext and expected_plaintext:
                # 测试解密
                search_patterns = [
                    r'明文',
                    r'plaintext',
                    r'解密结果',
                    r'decrypted',
                    r'输出'
                ]
                # 传入预期长度，用于检测是否包含了额外信息
                expected_len = len(expected_plaintext) if expected_plaintext else None
                actual_plaintext = self._extract_result_from_output(
                    output,
                    search_patterns,
                    expected_length=expected_len,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                
                if not actual_plaintext:
                    return False, f"无法从输出中提取明文。\n\n程序输出:\n{output}\n\n请确保代码输出包含'明文'、'plaintext'或'解密结果'等关键词。", {'output': output, 'actual': '', 'expected': expected_plaintext}
                
                logger.info(f"提取到的明文: {actual_plaintext}")
                logger.info(f"预期的明文: {expected_plaintext}")
                
                match, message, details = self._compare_results(
                    actual_plaintext,
                    expected_plaintext,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                details['output'] = output
                if match:
                    return True, message, details
                else:
                    return False, message + f"\n\n程序完整输出:\n{output}", details
            
            else:
                return False, "请提供测试数据（明文+预期密文 或 密文+预期明文）", {'actual': '', 'expected': '', 'output': ''}
                
        except subprocess.TimeoutExpired:
            return False, "代码执行超时", {'actual': '', 'expected': '', 'output': ''}
        except Exception as e:
            return False, f"测试失败: {str(e)}", {'actual': '', 'expected': '', 'output': str(e)}
        finally:
            self._cleanup_paths(temp_file)
    
    def test_c(self, code: str, plaintext: Optional[str] = None, 
               expected_ciphertext: Optional[str] = None,
               ciphertext: Optional[str] = None,
               expected_plaintext: Optional[str] = None,
               key: Optional[str] = None,
               iv: Optional[str] = None,
               aad: Optional[str] = None,
               public_key_n: Optional[str] = None,
               public_key_e: Optional[str] = None,
               private_key_n: Optional[str] = None,
               private_key_d: Optional[str] = None,
               signature: Optional[str] = None,
               algorithm: Optional[str] = None,
               mode: Optional[str] = None,
               *,
               allow_canonical_whole_file: Optional[bool] = None,
               allow_error_auto_repair: Optional[bool] = None,
               suppress_heuristic_warnings: bool = False,
               ) -> Tuple[bool, str, Dict]:
        """
        测试C代码
        """
        build_id = uuid.uuid4().hex
        temp_file = self.temp_dir / f"test_{build_id}.c"
        executable = self.temp_dir / f"test_{build_id}"
        try:
            code = sanitize_c_illegal_numeric_macros(
                code,
                temp_file.name,
                algorithm=algorithm,
                mode=mode,
                allow_canonical_whole_file=allow_canonical_whole_file,
                allow_error_auto_repair=allow_error_auto_repair,
            )
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(code)
            
            # 编译C代码 - 先尝试不链接OpenSSL（纯C实现）
            compile_result = subprocess.run(
                ['gcc', '-o', str(executable), str(temp_file), '-lm'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # 如果编译失败，尝试链接OpenSSL（如果代码使用了OpenSSL）
            if compile_result.returncode != 0:
                compile_result_openssl = subprocess.run(
                    ['gcc', '-o', str(executable), str(temp_file), '-lssl', '-lcrypto'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if compile_result_openssl.returncode != 0:
                    # 两次编译都失败，返回错误信息
                    error_msg = compile_result.stderr
                    if 'openssl' in error_msg.lower() or 'des.h' in error_msg.lower() or 'aes.h' in error_msg.lower():
                        error_msg += "\n\n提示：代码使用了OpenSSL库，但系统未安装OpenSSL开发库。\n" + \
                                   "解决方案：\n" + \
                                   "1. 安装OpenSSL开发库（Linux: sudo apt-get install libssl-dev, Mac: brew install openssl）\n" + \
                                   "2. 或者要求LLM生成不依赖OpenSSL的纯C实现代码"
                    return False, f"编译失败: {error_msg}", {'actual': '', 'expected': expected_ciphertext if plaintext else (expected_plaintext if ciphertext else ''), 'output': error_msg}
                # 使用OpenSSL版本编译成功
                compile_result = compile_result_openssl
            
            # 准备环境变量
            env = os.environ.copy()
            if plaintext:
                env['TEST_PLAINTEXT'] = plaintext
            if ciphertext:
                env['TEST_CIPHERTEXT'] = ciphertext
            if key:
                env['TEST_KEY'] = key
            if iv:
                env['TEST_IV'] = iv
            if aad:
                env['TEST_AAD'] = aad
            if public_key_n:
                env['TEST_PUBLIC_KEY_N'] = public_key_n
            if public_key_e:
                env['TEST_PUBLIC_KEY_E'] = public_key_e
            if private_key_n:
                env['TEST_PRIVATE_KEY_N'] = private_key_n
            if private_key_d:
                env['TEST_PRIVATE_KEY_D'] = private_key_d
            if signature:
                env['TEST_SIGNATURE'] = signature
            
            # 准备stdin输入（向后兼容）
            stdin_input = None
            if plaintext or ciphertext:
                stdin_lines = []
                if plaintext:
                    stdin_lines.append(plaintext)
                elif ciphertext:
                    stdin_lines.append(ciphertext)
                if key:
                    stdin_lines.append(key)
                if iv:
                    stdin_lines.append(iv)
                stdin_input = '\n'.join(stdin_lines) + '\n'
            
            # 执行编译后的程序
            run_result = subprocess.run(
                [str(executable)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.temp_dir),
                env=env,
                input=stdin_input
            )
            
            output = run_result.stdout + run_result.stderr
            logger.info(f"程序执行结果 - returncode: {run_result.returncode}")
            logger.info(f"程序stdout:\n{run_result.stdout}")
            logger.info(f"程序stderr:\n{run_result.stderr}")
            logger.info(f"程序完整输出:\n{output}")
            
            if run_result.returncode != 0:
                # 尝试从输出中提取结果，即使执行失败
                if plaintext and expected_ciphertext:
                    search_patterns = [r'密文', r'ciphertext', r'加密结果', r'encrypted', r'输出']
                    actual = (
                        self._extract_result_from_output(
                            output,
                            search_patterns,
                            suppress_heuristic_warnings=suppress_heuristic_warnings,
                        )
                        or ''
                    )
                    error_msg = run_result.stderr if run_result.stderr else f"程序异常退出，退出码: {run_result.returncode}"
                    if not output.strip():
                        error_msg += "\n\n程序没有产生任何输出，可能是程序崩溃或等待输入。"
                    return False, f"代码执行失败: {error_msg}", {'output': output if output.strip() else f"程序异常退出，退出码: {run_result.returncode}，没有输出", 'actual': actual, 'expected': expected_ciphertext}
                elif ciphertext and expected_plaintext:
                    search_patterns = [r'明文', r'plaintext', r'解密结果', r'decrypted', r'输出']
                    actual = (
                        self._extract_result_from_output(
                            output,
                            search_patterns,
                            suppress_heuristic_warnings=suppress_heuristic_warnings,
                        )
                        or ''
                    )
                    error_msg = run_result.stderr if run_result.stderr else f"程序异常退出，退出码: {run_result.returncode}"
                    if not output.strip():
                        error_msg += "\n\n程序没有产生任何输出，可能是程序崩溃或等待输入。"
                    return False, f"代码执行失败: {error_msg}", {'output': output if output.strip() else f"程序异常退出，退出码: {run_result.returncode}，没有输出", 'actual': actual, 'expected': expected_plaintext}
                else:
                    error_msg = run_result.stderr if run_result.stderr else f"程序异常退出，退出码: {run_result.returncode}"
                    if not output.strip():
                        error_msg += "\n\n程序没有产生任何输出，可能是程序崩溃或等待输入。"
                    return False, f"代码执行失败: {error_msg}", {'output': output if output.strip() else f"程序异常退出，退出码: {run_result.returncode}，没有输出", 'actual': '', 'expected': ''}
            
            # 程序执行成功，但检查是否有输出
            if not output.strip():
                error_msg = "程序执行成功，但没有产生任何输出。\n可能的原因：\n1. 程序在等待输入（但应该从环境变量读取）\n2. 程序没有输出密文或明文\n3. 程序输出格式不正确（没有包含'密文'、'ciphertext'等关键词）"
                if plaintext and expected_ciphertext:
                    return False, error_msg, {'output': '程序执行成功，但没有输出', 'actual': '', 'expected': expected_ciphertext}
                elif ciphertext and expected_plaintext:
                    return False, error_msg, {'output': '程序执行成功，但没有输出', 'actual': '', 'expected': expected_plaintext}
                else:
                    return False, error_msg, {'output': '程序执行成功，但没有输出', 'actual': '', 'expected': ''}
            
            # 根据测试类型提取结果
            if plaintext and expected_ciphertext:
                # 测试加密
                search_patterns = [
                    r'密文',
                    r'ciphertext',
                    r'加密结果',
                    r'encrypted',
                    r'输出'
                ]
                # 传入预期长度，用于检测是否包含了IV
                expected_len = len(expected_ciphertext) if expected_ciphertext else None
                actual_ciphertext = self._extract_result_from_output(
                    output,
                    search_patterns,
                    expected_length=expected_len,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                
                if not actual_ciphertext:
                    return False, self._msg_extract_ciphertext_failed(output), {'output': output, 'actual': '', 'expected': expected_ciphertext}
                
                logger.info(f"提取到的密文: {actual_ciphertext}")
                logger.info(f"预期的密文: {expected_ciphertext}")
                
                # 提取签名（如果存在）- 仅对RSA算法
                actual_signature = None
                if private_key_n and private_key_d:  # 如果提供了私钥，尝试提取签名
                    signature_patterns = [
                        r'签名',
                        r'signature',
                        r'数字签名',
                        r'digital.*signature'
                    ]
                    actual_signature = self._extract_result_from_output(
                    output,
                    signature_patterns,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                    if actual_signature:
                        logger.info(f"提取到的签名: {actual_signature}")
                
                match, message, details = self._compare_results(
                    actual_ciphertext,
                    expected_ciphertext,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                details['output'] = output
                if actual_signature:
                    details['signature'] = actual_signature
                if match:
                    return True, message, details
                else:
                    return False, message + f"\n\n程序完整输出:\n{output}", details
            
            elif ciphertext and expected_plaintext:
                # 测试解密
                search_patterns = [
                    r'明文',
                    r'plaintext',
                    r'解密结果',
                    r'decrypted',
                    r'输出'
                ]
                # 传入预期长度，用于检测是否包含了额外信息
                expected_len = len(expected_plaintext) if expected_plaintext else None
                actual_plaintext = self._extract_result_from_output(
                    output,
                    search_patterns,
                    expected_length=expected_len,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                
                if not actual_plaintext:
                    return False, f"无法从输出中提取明文。\n\n程序输出:\n{output}\n\n请确保代码输出包含'明文'、'plaintext'或'解密结果'等关键词。", {'output': output, 'actual': '', 'expected': expected_plaintext}
                
                logger.info(f"提取到的明文: {actual_plaintext}")
                logger.info(f"预期的明文: {expected_plaintext}")
                
                match, message, details = self._compare_results(
                    actual_plaintext,
                    expected_plaintext,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                details['output'] = output
                if match:
                    return True, message, details
                else:
                    return False, message + f"\n\n程序完整输出:\n{output}", details
            
            else:
                return False, "请提供测试数据（明文+预期密文 或 密文+预期明文）", {}
                
        except FileNotFoundError:
            return False, "未找到gcc编译器，请先安装gcc", {'actual': '', 'expected': '', 'output': ''}
        except subprocess.TimeoutExpired:
            return False, "代码执行超时", {'actual': '', 'expected': '', 'output': ''}
        except Exception as e:
            return False, f"测试失败: {str(e)}", {'actual': '', 'expected': '', 'output': str(e)}
        finally:
            self._cleanup_paths(executable, temp_file)
    
    def test_cpp(self, code: str, plaintext: Optional[str] = None, 
                 expected_ciphertext: Optional[str] = None,
                 ciphertext: Optional[str] = None,
                 expected_plaintext: Optional[str] = None,
                 key: Optional[str] = None,
                 iv: Optional[str] = None,
                 aad: Optional[str] = None,
                 public_key_n: Optional[str] = None,
                 public_key_e: Optional[str] = None,
                 private_key_n: Optional[str] = None,
                 private_key_d: Optional[str] = None,
                 signature: Optional[str] = None,
                 algorithm: Optional[str] = None,
                 mode: Optional[str] = None,
                 *,
                 allow_canonical_whole_file: Optional[bool] = None,
                 allow_error_auto_repair: Optional[bool] = None,
                 suppress_heuristic_warnings: bool = False,
                 ) -> Tuple[bool, str, Dict]:
        """
        测试C++代码
        """
        build_id = uuid.uuid4().hex
        temp_file = self.temp_dir / f"test_{build_id}.cpp"
        executable = self.temp_dir / f"test_{build_id}"
        try:
            code = sanitize_c_illegal_numeric_macros(
                code,
                temp_file.name,
                algorithm=algorithm,
                mode=mode,
                allow_canonical_whole_file=allow_canonical_whole_file,
                allow_error_auto_repair=allow_error_auto_repair,
            )
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(code)
            
            # 编译C++代码 - 先尝试不链接OpenSSL（纯C++实现）
            compile_result = subprocess.run(
                ['g++', '-o', str(executable), str(temp_file), '-lm', '-std=c++11'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # 如果编译失败，尝试链接OpenSSL（如果代码使用了OpenSSL）
            if compile_result.returncode != 0:
                compile_result_openssl = subprocess.run(
                    ['g++', '-o', str(executable), str(temp_file), '-lssl', '-lcrypto', '-std=c++11'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if compile_result_openssl.returncode != 0:
                    # 两次编译都失败，返回错误信息
                    error_msg = compile_result.stderr
                    if 'openssl' in error_msg.lower() or 'des.h' in error_msg.lower() or 'aes.h' in error_msg.lower():
                        error_msg += "\n\n提示：代码使用了OpenSSL库，但系统未安装OpenSSL开发库。\n" + \
                                   "解决方案：\n" + \
                                   "1. 安装OpenSSL开发库（Linux: sudo apt-get install libssl-dev, Mac: brew install openssl）\n" + \
                                   "2. 或者要求LLM生成不依赖OpenSSL的纯C++实现代码"
                    return False, f"编译失败: {error_msg}", {'actual': '', 'expected': expected_ciphertext if plaintext else (expected_plaintext if ciphertext else ''), 'output': error_msg}
                # 使用OpenSSL版本编译成功
                compile_result = compile_result_openssl
            
            # 准备环境变量
            env = os.environ.copy()
            if plaintext:
                env['TEST_PLAINTEXT'] = plaintext
            if ciphertext:
                env['TEST_CIPHERTEXT'] = ciphertext
            if key:
                env['TEST_KEY'] = key
            if iv:
                env['TEST_IV'] = iv
            if aad:
                env['TEST_AAD'] = aad
            if public_key_n:
                env['TEST_PUBLIC_KEY_N'] = public_key_n
            if public_key_e:
                env['TEST_PUBLIC_KEY_E'] = public_key_e
            if private_key_n:
                env['TEST_PRIVATE_KEY_N'] = private_key_n
            if private_key_d:
                env['TEST_PRIVATE_KEY_D'] = private_key_d
            if signature:
                env['TEST_SIGNATURE'] = signature
            
            # 准备stdin输入（向后兼容）
            stdin_input = None
            if plaintext or ciphertext:
                stdin_lines = []
                if plaintext:
                    stdin_lines.append(plaintext)
                elif ciphertext:
                    stdin_lines.append(ciphertext)
                if key:
                    stdin_lines.append(key)
                if iv:
                    stdin_lines.append(iv)
                stdin_input = '\n'.join(stdin_lines) + '\n'
            
            # 执行编译后的程序
            run_result = subprocess.run(
                [str(executable)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.temp_dir),
                env=env,
                input=stdin_input
            )
            
            if run_result.returncode != 0:
                output = run_result.stdout + run_result.stderr
                # 尝试从输出中提取结果，即使执行失败
                if plaintext and expected_ciphertext:
                    search_patterns = [r'密文', r'ciphertext', r'加密结果', r'encrypted', r'输出']
                    actual = (
                        self._extract_result_from_output(
                            output,
                            search_patterns,
                            suppress_heuristic_warnings=suppress_heuristic_warnings,
                        )
                        or ''
                    )
                    return False, f"代码执行失败: {run_result.stderr}", {'output': output, 'actual': actual, 'expected': expected_ciphertext}
                elif ciphertext and expected_plaintext:
                    search_patterns = [r'明文', r'plaintext', r'解密结果', r'decrypted', r'输出']
                    actual = (
                        self._extract_result_from_output(
                            output,
                            search_patterns,
                            suppress_heuristic_warnings=suppress_heuristic_warnings,
                        )
                        or ''
                    )
                    return False, f"代码执行失败: {run_result.stderr}", {'output': output, 'actual': actual, 'expected': expected_plaintext}
                else:
                    return False, f"代码执行失败: {run_result.stderr}", {'output': output, 'actual': '', 'expected': ''}
            
            output = run_result.stdout + run_result.stderr
            logger.info(f"程序输出:\n{output}")
            
            # 根据测试类型提取结果
            if plaintext and expected_ciphertext:
                # 测试加密
                search_patterns = [
                    r'密文',
                    r'ciphertext',
                    r'加密结果',
                    r'encrypted',
                    r'输出'
                ]
                # 传入预期长度，用于检测是否包含了IV
                expected_len = len(expected_ciphertext) if expected_ciphertext else None
                actual_ciphertext = self._extract_result_from_output(
                    output,
                    search_patterns,
                    expected_length=expected_len,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                
                if not actual_ciphertext:
                    return False, self._msg_extract_ciphertext_failed(output), {'output': output, 'actual': '', 'expected': expected_ciphertext}
                
                logger.info(f"提取到的密文: {actual_ciphertext}")
                logger.info(f"预期的密文: {expected_ciphertext}")
                
                # 提取签名（如果存在）- 仅对RSA算法
                actual_signature = None
                if private_key_n and private_key_d:  # 如果提供了私钥，尝试提取签名
                    signature_patterns = [
                        r'签名',
                        r'signature',
                        r'数字签名',
                        r'digital.*signature'
                    ]
                    actual_signature = self._extract_result_from_output(
                    output,
                    signature_patterns,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                    if actual_signature:
                        logger.info(f"提取到的签名: {actual_signature}")
                
                match, message, details = self._compare_results(
                    actual_ciphertext,
                    expected_ciphertext,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                details['output'] = output
                if actual_signature:
                    details['signature'] = actual_signature
                if match:
                    return True, message, details
                else:
                    return False, message + f"\n\n程序完整输出:\n{output}", details
            
            elif ciphertext and expected_plaintext:
                # 测试解密
                search_patterns = [
                    r'明文',
                    r'plaintext',
                    r'解密结果',
                    r'decrypted',
                    r'输出'
                ]
                # 传入预期长度，用于检测是否包含了额外信息
                expected_len = len(expected_plaintext) if expected_plaintext else None
                actual_plaintext = self._extract_result_from_output(
                    output,
                    search_patterns,
                    expected_length=expected_len,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                
                if not actual_plaintext:
                    return False, f"无法从输出中提取明文。\n\n程序输出:\n{output}\n\n请确保代码输出包含'明文'、'plaintext'或'解密结果'等关键词。", {'output': output, 'actual': '', 'expected': expected_plaintext}
                
                logger.info(f"提取到的明文: {actual_plaintext}")
                logger.info(f"预期的明文: {expected_plaintext}")
                
                match, message, details = self._compare_results(
                    actual_plaintext,
                    expected_plaintext,
                    suppress_heuristic_warnings=suppress_heuristic_warnings,
                )
                details['output'] = output
                if match:
                    return True, message, details
                else:
                    return False, message + f"\n\n程序完整输出:\n{output}", details
            
            else:
                return False, "请提供测试数据（明文+预期密文 或 密文+预期明文）", {}
                
        except FileNotFoundError:
            return False, "未找到g++编译器，请先安装g++", {}
        except subprocess.TimeoutExpired:
            return False, "代码执行超时", {}
        except Exception as e:
            return False, f"测试失败: {str(e)}", {}
        finally:
            self._cleanup_paths(executable, temp_file)
    
    def test(self, code: str, language: str, 
             plaintext: Optional[str] = None,
             expected_ciphertext: Optional[str] = None,
             ciphertext: Optional[str] = None,
             expected_plaintext: Optional[str] = None,
             key: Optional[str] = None,
             iv: Optional[str] = None,
             aad: Optional[str] = None,
             public_key_n: Optional[str] = None,
             public_key_e: Optional[str] = None,
             private_key_n: Optional[str] = None,
             private_key_d: Optional[str] = None,
             signature: Optional[str] = None,
             algorithm: Optional[str] = None,
             mode: Optional[str] = None,
             *,
             allow_canonical_whole_file: Optional[bool] = None,
             allow_error_auto_repair: Optional[bool] = None,
             suppress_heuristic_warnings: bool = False,
             ) -> Tuple[bool, str, Dict]:
        """
        通用测试方法
        
        Args:
            code: 代码字符串
            language: 编程语言 ('python', 'c', 'cpp')
            plaintext: 明文（用于测试加密）
            expected_ciphertext: 预期密文（用于测试加密）
            ciphertext: 密文（用于测试解密）
            expected_plaintext: 预期明文（用于测试解密）
            algorithm/mode: 可选（Python 对称任务等）
            allow_canonical_whole_file: 与 generate_and_save 消融档位一致；线程池内需显式传递
            allow_error_auto_repair: C/C++ 写盘清洗是否启用后段错误自动修复
            suppress_heuristic_warnings: 为 True 时表示「无测试反馈改进」消融：不做密文 hex 长度/疑似 IV 的剥离修补，
                比对失败信息不含长度·IV 辅导段落；亦不打印相关启发式 WARNING
            
        Returns:
            (是否成功, 输出信息)
        """
        language = language.lower()
        
        if language == 'python':
            return self.test_python(
                code,
                plaintext,
                expected_ciphertext,
                ciphertext,
                expected_plaintext,
                key,
                iv,
                aad,
                public_key_n,
                public_key_e,
                private_key_n,
                private_key_d,
                signature,
                algorithm,
                mode,
                suppress_heuristic_warnings=suppress_heuristic_warnings,
            )
        elif language == 'c':
            return self.test_c(
                code,
                plaintext,
                expected_ciphertext,
                ciphertext,
                expected_plaintext,
                key,
                iv,
                aad,
                public_key_n,
                public_key_e,
                private_key_n,
                private_key_d,
                signature,
                algorithm,
                mode,
                allow_canonical_whole_file=allow_canonical_whole_file,
                allow_error_auto_repair=allow_error_auto_repair,
                suppress_heuristic_warnings=suppress_heuristic_warnings,
            )
        elif language == 'cpp' or language == 'c++':
            return self.test_cpp(
                code,
                plaintext,
                expected_ciphertext,
                ciphertext,
                expected_plaintext,
                key,
                iv,
                aad,
                public_key_n,
                public_key_e,
                private_key_n,
                private_key_d,
                signature,
                algorithm,
                mode,
                allow_canonical_whole_file=allow_canonical_whole_file,
                allow_error_auto_repair=allow_error_auto_repair,
                suppress_heuristic_warnings=suppress_heuristic_warnings,
            )
        else:
            return False, f"不支持的语言: {language}", {}
    
    def cleanup(self):
        """清理临时文件"""
        try:
            import shutil
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                self.temp_dir.mkdir(exist_ok=True)
        except Exception as e:
            logger.warning(f"清理临时文件失败: {e}")

