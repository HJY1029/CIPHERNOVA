"""代码处理相关功能模块"""
import re
from typing import Optional
from utils.logger import setup_logger

logger = setup_logger()


def python_looks_like_c(s: str) -> bool:
    """判断提取片段是否像误混入的 C/C++/OpenSSL（用于 Python 任务）。"""
    if not (s and s.strip()):
        return False
    if re.search(r"^\s*#include\s", s, re.MULTILINE):
        return True
    if re.search(r"void\s+remove_whitespace\s*\(\s*char\s*\*", s):
        return True
    if re.search(r"^\s*void\s+\w+\s*\(", s, re.MULTILINE):
        return True
    if re.search(r"^\s*unsigned\s+(char|int|long)\s+\w+\s*[=;,\[]", s, re.MULTILINE):
        return True
    if re.search(r"^\s*int\s+main\s*\(", s, re.MULTILINE):
        return True
    if re.search(r"\bDES_key_schedule\b", s):
        return True
    if re.search(r"^\s*//\s*S-?boxes?\s+for\s+DES", s, re.IGNORECASE | re.MULTILINE):
        return True
    return False


def strip_markdown_code_fences(text: str) -> str:
    """
    移除 Markdown ```…``` 围栏的定界部分，保留围栏内代码正文。

    旧实现曾用空行替换整个围栏，会把代码一并删掉，导致「去围栏后再 extract_code」路径
    与全文 extract_code 结果系统性不一致。现与常见「去围栏」语义一致：只去掉成对 ```。
    """
    if not text:
        return ""
    # ``` + 可选语言/信息串 + 换行 + 正文 + 闭合 ```（非贪婪匹配正文）
    return re.sub(r"```[^\n]*\n([\s\S]*?)```", r"\1", text)


def extract_code_markdown_fence_only(text: str, language: str = "python") -> str:
    """
    仅从 Markdown 围栏提取（论文表「Markdown 代码块」路径），
    不做无围栏时的行扫描与后续启发式修复。
    """
    if not text:
        return ""
    code_block_patterns = [
        rf"```(?i:{re.escape(language)})\s*\n(.*?)```",
        rf"```{language}\s*\n(.*?)```",
        rf"```{language.lower()}\s*\n(.*?)```",
        r"```\s*\n(.*?)```",
        r"```.*?\n(.*?)```",
    ]
    candidates: list[str] = []
    seen: set[str] = set()
    for pattern in code_block_patterns:
        for block in re.findall(pattern, text, re.DOTALL):
            b = block.strip()
            if b and b not in seen:
                seen.add(b)
                candidates.append(b)

    if language.lower() == "python":
        ok_blocks = [c for c in candidates if not python_looks_like_c(c)]
        if ok_blocks:
            def _py_fence_score(b: str) -> tuple:
                has_import = bool(re.search(r"^\s*(import|from)\s", b, re.MULTILINE))
                has_def = bool(re.search(r"^\s*def\s+\w+\s*\(", b, re.MULTILINE))
                prio = 2 if has_import else (1 if has_def else 0)
                return (prio, len(b))

            return max(ok_blocks, key=_py_fence_score)
    elif candidates:
        return candidates[0]
    return ""


def extract_code_plain_text_recognition(
    text: str, language: str = "python", *, suppress_heuristic_warnings: bool = False
) -> str:
    """
    「纯文本代码识别」评测口径：去掉围栏后调用完整 extract_code（此时通常走行扫描等路径）。

    若原文含 Markdown 围栏，与 ``extract_code`` 一致：优先取围栏内正文（不做行扫描阶段的
    ``fix_missing_headers`` 等仅无围栏路径才触发的补全），避免与全文 ``extract_code`` _fence 分支_ 结果不一致。
    """
    stripped = strip_markdown_code_fences(text)
    if stripped != text:
        fenced = extract_code_markdown_fence_only(text, language)
        if fenced.strip():
            return fenced
    return extract_code(
        stripped,
        language,
        suppress_heuristic_warnings=suppress_heuristic_warnings,
    )


def extract_code(
    text: str, language: str = "python", *, suppress_heuristic_warnings: bool = False
) -> str:
        """
        从LLM返回的文本中提取纯代码
        去除markdown代码块标记和说明文字
        """
        if not text:
            return ""

        def _diag_warn(msg: str) -> None:
            if not suppress_heuristic_warnings:
                logger.warning(msg)
        
        # 尝试匹配markdown代码块
        # 匹配 ```language 或 ``` 开头的代码块（语言标签大小写不敏感）
        code_block_patterns = [
            rf"```(?i:{re.escape(language)})\s*\n(.*?)```",
            rf'```{language}\s*\n(.*?)```',
            rf'```{language.lower()}\s*\n(.*?)```',
            r'```\s*\n(.*?)```',
            r'```.*?\n(.*?)```',
        ]
        candidates: list[str] = []
        seen: set[str] = set()
        for pattern in code_block_patterns:
            for block in re.findall(pattern, text, re.DOTALL):
                b = block.strip()
                if b and b not in seen:
                    seen.add(b)
                    candidates.append(b)

        # Python：多围栏时不能仅按长度选（短 import 块会输给长 ```c 块）
        if language.lower() == "python":
            ok_blocks = [c for c in candidates if not python_looks_like_c(c)]
            if ok_blocks:
                def _py_fence_score(b: str) -> tuple:
                    has_import = bool(re.search(r"^\s*(import|from)\s", b, re.MULTILINE))
                    has_def = bool(re.search(r"^\s*def\s+\w+\s*\(", b, re.MULTILINE))
                    prio = 2 if has_import else (1 if has_def else 0)
                    return (prio, len(b))

                return max(ok_blocks, key=_py_fence_score)
        elif candidates:
            return candidates[0]

        # 如果没有找到代码块，尝试查找代码特征
        # 移除常见的说明文字模式
        lines = text.split('\n')
        code_lines = []
        skip_until_code = True
        
        # 常见的说明文字开头
        skip_patterns = [
            r'^以下是',
            r'^这是一个',
            r'^下面是',
            r'^代码如下',
            r'^以下是代码',
            r'^代码实现',
            r'^实现代码',
            r'^```',
            r'^# 说明',
            r'^# 介绍',
            r'^# 以下是',
        ]
        
        # 需要过滤的中文说明文字（出现在代码行中）
        chinese_comment_patterns = [
            r'将以下内容补全',
            r'确保数组完整',
            r'补全',
            r'以下内容',
            r'请补全',
            r'需要补全',
        ]
        
        for line in lines:
            # 如果遇到代码块标记，跳过
            if line.strip().startswith('```'):
                if skip_until_code:
                    continue
                else:
                    # 如果已经开始收集代码，遇到结束标记时停止
                    break
            
            # 检查是否是说明文字
            is_skip = False
            for pattern in skip_patterns:
                if re.match(pattern, line.strip(), re.IGNORECASE):
                    is_skip = True
                    break
            
            if is_skip and skip_until_code:
                continue
            
            # 如果遇到看起来像代码的行，开始收集
            if skip_until_code:
                # 检查是否是代码行（包含import、#include、def、class、函数定义等）
                code_indicators = [
                    r'^\s*(import|from|#include|#define|def |class |int |void |#)',
                    r'^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*[=\(]',  # 变量赋值或函数调用
                ]
                for indicator in code_indicators:
                    if re.match(indicator, line):
                        skip_until_code = False
                        break
            
            if not skip_until_code:
                stripped_ln = line.strip()
                # 围栏已去掉时，模型常跟在代码后的英文结语——不应与 fenced 路径结果拼接在一起
                if re.match(
                    r"^(Let me know|Compile with|Hope this|Thanks!?\s*$|Done\.?\s*$|End of answer\.?\s*$|"
                    r"Best regards|See also|The above code|GCC\b|g\+\+\b)",
                    stripped_ln,
                    re.I,
                ):
                    break
                if (
                    len(stripped_ln) < 120
                    and re.match(r"^[A-Za-z][^.!?]*[.!?]\s*$", stripped_ln)
                    and not re.search(r"[;{}()\[\]=]|def\s+|import\s|#include", stripped_ln)
                ):
                    break
                # 检查行中是否包含中文说明文字（不应该出现在代码中）
                has_chinese_comment = False
                for pattern in chinese_comment_patterns:
                    if re.search(pattern, line):
                        has_chinese_comment = True
                        _diag_warn(f"检测到代码行中包含中文说明文字，将过滤: {line[:100]}")
                        # 移除中文说明文字，保留代码部分
                        for chinese_pattern in chinese_comment_patterns:
                            line = re.sub(chinese_pattern + r'.*', '', line)
                        break
                
                # 如果行不为空（过滤后），添加到代码行
                if line.strip() or not has_chinese_comment:
                    code_lines.append(line)
        
        result = '\n'.join(code_lines).strip()
        
        # 再次清理：移除代码中残留的中文说明文字
        for pattern in chinese_comment_patterns:
            result = re.sub(pattern + r'[^\n]*', '', result)
        
        # 对于C/C++代码，检查是否有明显的截断（未闭合的数组、函数等）
        if language.lower() in ['cpp', 'c++', 'c']:
            # 修复连在一起的 include 语句
            # 例如：#include <iomanip>#include <sstream> -> #include <iomanip>\n#include <sstream>
            old_result = result
            result = re.sub(r'(#include\s+<[^>]+>)(#include\s+<[^>]+>)', r'\1\n\2', result)
            if result != old_result:
                logger.info("已修复连在一起的 include 语句")
            
            # 修复常见的LLM生成错误：?数字 -> 数字
            # 例如：?13 -> 13, ?8 -> 8
            result = re.sub(r'\?(\d+)', r'\1', result)
            logger.info("已修复代码中的 ?数字 格式错误（如 ?13 -> 13）")
            
            # 修复类型错误：uint3?2_t -> uint32_t
            result = re.sub(r'uint3\?2_t', 'uint32_t', result)
            result = re.sub(r'uint(\d+)\?(\d+)_t', r'uint\1\2_t', result)
            logger.info("已修复代码中的类型错误（如 uint3?2_t -> uint32_t）")
            
            # 修复数组元素后面单独的 ?（在逗号前或行尾）
            # 例如：{4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14}, ? -> {4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14},
            result = re.sub(r',\s*\?\s*([,\}])', r'\1', result)  # 逗号后的 ?
            result = re.sub(r'\?\s*([,\}])', r'\1', result)  # 直接跟逗号或右括号的 ?
            result = re.sub(r'\?\s*$', '', result, flags=re.MULTILINE)  # 行尾的 ?
            logger.info("已修复数组元素后面单独的 ? 符号")
            
            # 修复OpenSSL相关的常见错误
            # DES_BLOCK_SIZE 不存在，应该使用 8（DES块大小是8字节）
            if 'DES_BLOCK_SIZE' in result:
                result = re.sub(r'DES_BLOCK_SIZE', '8', result)
                logger.info("已修复 DES_BLOCK_SIZE 错误（替换为 8）")
            
            # 修复数组定义中的字母错误（在数组元素位置，单独的字母应该是十六进制值）
            # 例如：{8, 9, 10, 11, A, 13} -> {8, 9, 10, 11, 0x0A, 13}
            # 例如：{9, 14, 15, 5, 2, 8, 12, a, 1, 10} -> {9, 14, 15, 5, 2, 8, 12, 0x0A, 1, 10}
            # 匹配模式：在数组定义中（大括号内），单独的字母（A-F, a-f）前后是逗号、空格或大括号
            hex_letter_map = {
                'A': '0x0A', 'a': '0x0A',
                'B': '0x0B', 'b': '0x0B',
                'C': '0x0C', 'c': '0x0C',
                'D': '0x0D', 'd': '0x0D',
                'E': '0x0E', 'e': '0x0E',
                'F': '0x0F', 'f': '0x0F',
            }
            
            # 修复数组元素中的单独字母（在逗号之间或行尾）
            # 匹配：逗号后的字母（前后可能有空格），或行首的字母（在数组定义中）
            # 重要：只在数组定义的大括号内进行替换，避免误替换函数参数、变量名等
            for letter, hex_value in hex_letter_map.items():
                # 使用 lambda 函数作为替换参数，避免 \10 被误解析为第10个捕获组
                # 匹配模式：在数组定义中，单独的字母（前后是逗号、空格或大括号）
                # 关键：确保不在函数参数、变量名等位置替换
                # 特别注意：不能替换函数调用中的参数（如 memcpy(cd, c, 3) 中的 c）
                
                # 模式1：逗号后的字母，后面必须是逗号或右大括号（确保在数组定义中）
                # 但必须确保不在函数调用中（不在括号内）
                pattern1 = rf',\s*{re.escape(letter)}\s*([,\}}])'
                # 模式2：左大括号后的字母（数组定义开始）
                pattern2 = rf'{{\s*{re.escape(letter)}\s*([,\}}])'
                
                old_result = result
                try:
                    # 更严格的检查：确保替换的字母不在函数调用中
                    # 检查模式1：逗号后的字母，但必须确保前面不是函数调用的左括号
                    def safe_replace1(match):
                        # 检查匹配位置之前是否有未闭合的左括号（可能是函数调用）
                        pos = match.start()
                        before = result[:pos]
                        # 计算未闭合的左括号数量
                        open_parens = before.count('(') - before.count(')')
                        # 如果前面有未闭合的左括号，说明可能在函数调用中，不替换
                        if open_parens > 0:
                            return match.group(0)  # 返回原样
                        return f', {hex_value}{match.group(1)}'
                    
                    def safe_replace2(match):
                        # 模式2更安全，因为左大括号后通常是数组定义
                        return f'{{ {hex_value}{match.group(1)}'
                    
                    result = re.sub(pattern1, safe_replace1, result)
                    result = re.sub(pattern2, safe_replace2, result)
                    if result != old_result:
                        logger.info(f"已修复数组定义中的字母错误：{letter} -> {hex_value}")
                except Exception as e:
                    logger.error(f"修复字母错误时出错（letter={letter}）: {e}")
                    # 如果出错，回退到原始结果
                    result = old_result
            
            # 修复新的错误模式：A后跟数字（如 A30, A48, A11, A14, A0）
            # 例如：A30 -> 30, A48 -> 48, A0 -> 0
            old_result = result
            result = re.sub(r'\bA(\d+)\b', r'\1', result)
            if result != old_result:
                logger.info("已修复 A后跟数字 的错误（如 A30 -> 30, A0 -> 0）")
            
            # 自动检测并添加缺失的头文件
            result = fix_missing_headers(result, language)
            
            # 修复数字后跟字母O（如 1O -> 10）
            old_result = result
            result = re.sub(r'\b(\d+)O\b', r'\g<1>0', result)
            if result != old_result:
                logger.info("已修复数字后跟字母O的错误（如 1O -> 10）")
            
            # 修复 A后跟十六进制（如 A0xFFFFFFFF -> 0xFFFFFFFF）
            old_result = result
            result = re.sub(r'\bA(0x[0-9a-fA-F]+)\b', r'\1', result)
            if result != old_result:
                logger.info("已修复 A后跟十六进制 的错误（如 A0xFFFFFFFF -> 0xFFFFFFFF）")
            
            # 修复字符串前的A（如 A"字符串" -> "字符串"）
            old_result = result
            result = re.sub(r'\bA(".*?")', r'\1', result)
            if result != old_result:
                logger.info("已修复字符串前的A错误（如 A\"字符串\" -> \"字符串\"）")
            
            # 修复中文字符误写为数字的错误
            # 例如：统领6 -> 6, 统考1 -> 1, 统考 -> 空
            old_result = result
            # 修复"统领"后跟数字（如 统领6 -> 6, 统领0 -> 0, 统领14 -> 14）
            result = re.sub(r'统领(\d+)', r'\1', result)
            # 修复"统考"后跟数字（如 统考1 -> 1, 统考5 -> 5, 统考7 -> 7）
            result = re.sub(r'统考(\d+)', r'\1', result)
            # 修复单独的"统考"（在函数体开始处，应该是空）
            result = re.sub(r'统考\s*$', '', result, flags=re.MULTILINE)
            # 修复"统考"后跟其他字符（如 统考{ -> {, 统考; -> ;）
            result = re.sub(r'统考\s*([{,;])', r'\1', result)
            # 修复其他可能的中文字符误写（如 统、领、考等后跟数字）
            result = re.sub(r'[统领考](\d+)', r'\1', result)
            if result != old_result:
                logger.info("已修复中文字符误写错误（如 统领6 -> 6, 统考1 -> 1）")
            
            # 修复 namespace 和 class 冲突（如果同时定义了 namespace DES 和 class DES，移除 namespace）
            # 检查是否有 namespace DES { } 和 class DES 同时存在
            if re.search(r'namespace\s+DES\s*\{', result) and re.search(r'class\s+DES\s*\{', result):
                _diag_warn("检测到 namespace DES 和 class DES 冲突，将移除 namespace DES")
                # 移除 namespace DES { } 包装，保留内容
                result = re.sub(r'namespace\s+DES\s*\{', '', result)
                result = re.sub(r'}\s*//\s*namespace\s+DES', '', result)
                result = re.sub(r'}\s*;\s*//\s*namespace\s+DES', '', result)
                logger.info("已移除 namespace DES，解决命名冲突")
            
            # 检测并移除重复的函数定义
            # 查找所有函数定义（包括返回类型、函数名、参数列表）
            function_pattern = r'(?:^|\n)(\w+(?:\s*::\s*\w+)?\s+\w+\s*\([^)]*\)\s*(?:const\s*)?\{)'
            functions = {}
            lines = result.split('\n')
            seen_functions = set()
            new_lines = []
            skip_until_brace = False
            brace_count = 0
            
            for i, line in enumerate(lines):
                # 检查是否是函数定义
                if re.search(r'^\s*\w+(?:\s*::\s*\w+)?\s+\w+\s*\([^)]*\)\s*(?:const\s*)?\{', line):
                    # 提取函数签名（返回类型 + 函数名 + 参数）
                    match = re.search(r'(\w+(?:\s*::\s*\w+)?\s+)(\w+)\s*\(([^)]*)\)', line)
                    if match:
                        return_type = match.group(1).strip()
                        func_name = match.group(2)
                        params = match.group(3)
                        func_signature = f"{return_type} {func_name}({params})"
                        
                        if func_signature in seen_functions:
                            _diag_warn(f"检测到重复的函数定义：{func_signature}，将移除重复定义")
                            skip_until_brace = True
                            brace_count = line.count('{') - line.count('}')
                            continue
                        else:
                            seen_functions.add(func_signature)
                            skip_until_brace = False
                            brace_count = 0
                
                if skip_until_brace:
                    brace_count += line.count('{') - line.count('}')
                    if brace_count <= 0:
                        skip_until_brace = False
                    continue
                
                new_lines.append(line)
            
            if len(new_lines) < len(lines):
                result = '\n'.join(new_lines)
                logger.info("已移除重复的函数定义")
            
            # 检测并修复错误的函数调用（如 permute(R, 0x0E, 32, 48) 其中第二个参数应该是数组指针）
            # 常见的置换表映射：根据输入输出位数推断应该使用哪个表
            permute_table_map = {
                # (input_bits, output_bits) -> table_name
                (32, 48): 'E_TABLE',  # 扩展置换E：32位->48位（最常见，用于f函数）
                (32, 32): 'P_TABLE',  # P盒置换：32位->32位（用于f函数）
                (56, 48): 'PC2_TABLE',  # PC-2置换：56位->48位（用于密钥调度）
                (64, 56): 'PC1_TABLE',  # PC-1置换：64位->56位（用于密钥调度）
                # 注意：(64, 64) 可能是 IP_TABLE 或 FP_TABLE，需要根据上下文判断
                # 但通常 IP_TABLE 在加密/解密开始，FP_TABLE 在结束
            }
            
            # 修复 permute 函数调用中的整数参数错误
            # 匹配模式：permute(..., 0x0E, 32, 48) 或 permute(..., 0x0E, 32, 48)
            permute_error_pattern = r'permute\s*\(([^,]+),\s*(0x[0-9a-fA-F]+)\s*,\s*(\d+)\s*,\s*(\d+)\)'
            
            def fix_permute_call(match):
                input_var = match.group(1).strip()
                hex_value = match.group(2)
                input_bits = int(match.group(3))
                output_bits = int(match.group(4))
                
                # 根据输入输出位数推断应该使用哪个表
                key = (input_bits, output_bits)
                table_name = permute_table_map.get(key)
                
                if table_name:
                    logger.info(f"修复permute函数调用：将 {hex_value} 替换为 {table_name} (输入{input_bits}位->输出{output_bits}位)")
                    return f'permute({input_var}, {table_name}, {input_bits}, {output_bits})'
                else:
                    # 如果无法从映射表推断，尝试根据常见的值推断
                    hex_int = int(hex_value, 16)
                    # 最常见的错误：0x0E 用于扩展置换E（32->48）
                    if hex_int == 0x0E and input_bits == 32 and output_bits == 48:
                        logger.info(f"修复permute函数调用：将 {hex_value} 替换为 E_TABLE (扩展置换E: 32位->48位)")
                        return f'permute({input_var}, E_TABLE, {input_bits}, {output_bits})'
                    # 0x0F 用于P盒置换（32->32）
                    elif hex_int == 0x0F and input_bits == 32 and output_bits == 32:
                        logger.info(f"修复permute函数调用：将 {hex_value} 替换为 P_TABLE (P盒置换: 32位->32位)")
                        return f'permute({input_var}, P_TABLE, {input_bits}, {output_bits})'
                    # 对于 (64, 64)，需要根据上下文判断是IP还是FP
                    # 如果是在函数开始处（如encrypt_block开始），可能是IP_TABLE
                    # 如果是在函数结束处（如encrypt_block结束），可能是FP_TABLE
                    # 这里默认使用IP_TABLE，因为更常见
                    elif input_bits == 64 and output_bits == 64:
                        # 检查上下文：如果是在函数开始附近，使用IP_TABLE；否则使用FP_TABLE
                        # 简单策略：默认使用IP_TABLE
                        logger.info(f"修复permute函数调用：将 {hex_value} 替换为 IP_TABLE (初始置换IP: 64位->64位)")
                        return f'permute({input_var}, IP_TABLE, {input_bits}, {output_bits})'
                    else:
                        _diag_warn(f"无法自动修复permute函数调用：permute({input_var}, {hex_value}, {input_bits}, {output_bits})，需要手动修复")
                        return match.group(0)  # 返回原始内容
            
            old_result = result
            result = re.sub(permute_error_pattern, fix_permute_call, result)
            if result != old_result:
                logger.info("已修复permute函数调用中的整数参数错误")
            
            # 修复数组末尾缺少逗号的情况（如果下一行是数组元素）
            lines_list = result.split('\n')
            fixed_lines = []
            for i, line in enumerate(lines_list):
                # 检查是否是数组元素行（以数字结尾，没有逗号，下一行也是数组元素）
                if i < len(lines_list) - 1:
                    next_line = lines_list[i + 1].strip()
                    # 当前行看起来像数组元素（数字结尾），下一行也是数组元素
                    if (re.search(r'^\s*\d+\s*$', line.strip()) and 
                        (re.search(r'^\s*\d+', next_line) or next_line.startswith('{'))):
                        # 如果当前行没有逗号，添加逗号
                        if not line.rstrip().endswith(','):
                            line = line.rstrip() + ','
                fixed_lines.append(line)
            result = '\n'.join(fixed_lines)
            # 检查是否有未闭合的数组定义（以 { 开头但没有对应的 }）
            lines_list = result.split('\n')
            brace_count = 0
            bracket_count = 0
            in_array_def = False
            array_start_line = -1
            
            for i, line in enumerate(lines_list):
                # 检查数组定义
                if re.search(r'\w+\s*\[\s*\d+\s*\]\s*=\s*\{', line):
                    in_array_def = True
                    array_start_line = i
                    brace_count = line.count('{') - line.count('}')
                    bracket_count = line.count('[') - line.count(']')
                elif in_array_def:
                    brace_count += line.count('{') - line.count('}')
                    bracket_count += line.count('[') - line.count(']')
                    # 如果数组定义闭合了
                    if brace_count == 0 and bracket_count == 0:
                        in_array_def = False
                        array_start_line = -1
                else:
                    brace_count += line.count('{') - line.count('}')
                    bracket_count += line.count('[') - line.count(']')
            
            # 如果检测到未闭合的数组定义，记录警告
            if in_array_def and array_start_line >= 0:
                lang_name = 'C++' if language.lower() in ['cpp', 'c++'] else 'C'
                _diag_warn(f"检测到{lang_name}代码中可能有未闭合的数组定义（从第 {array_start_line + 1} 行开始）")
                # 尝试修复：如果最后一行看起来像数组元素，添加闭合括号
                last_line = lines_list[-1].strip() if lines_list else ""
                # 检查最后一行是否是数组元素（十六进制或十进制数字，可能有逗号）
                is_array_element = (
                    re.match(r'^\s*0x[0-9a-fA-F]+(?:,\s*)?$', last_line) or 
                    re.match(r'^\s*\d+(?:,\s*)?$', last_line) or
                    re.match(r'^\s*0x[0-9a-fA-F]+(?:,\s*0x[0-9a-fA-F]+)*,?\s*$', last_line)  # 多个十六进制数
                )
                
                # 检查是否有语法错误（数组元素和数组定义混在一起）
                has_syntax_error = False
                for i, line in enumerate(lines_list[array_start_line:], start=array_start_line):
                    # 检查是否有类似 "0x..., Aes::inv_sbox[256] = {" 这样的错误
                    if re.search(r'0x[0-9a-fA-F]+.*::\w+\[', line):
                        logger.error(f"检测到数组定义语法错误（第 {i + 1} 行）：数组元素和数组定义混在一起")
                        has_syntax_error = True
                        # 尝试修复：移除错误的数组定义部分
                        line_fixed = re.sub(r',\s*\w+::\w+\[\d+\]\s*=\s*\{.*$', '', line)
                        if line_fixed != line:
                            lines_list[i] = line_fixed
                            result = '\n'.join(lines_list).strip()
                            logger.info(f"已尝试修复语法错误（第 {i + 1} 行）")
                
                if is_array_element and not has_syntax_error:
                    _diag_warn("检测到代码可能在数组定义处截断，尝试添加闭合括号")
                    # 如果最后一行没有逗号，先添加逗号（如果数组还没结束）
                    if not last_line.endswith(','):
                        result = result.rstrip() + ',\n'
                    result += '};\n'
                    logger.info("已尝试修复：添加了数组闭合括号")
                    # 更新括号计数
                    brace_count = 0
                    bracket_count = 0
                    in_array_def = False
                elif has_syntax_error:
                    logger.error("检测到数组定义语法错误，代码可能不完整，无法自动修复")
            
            # 检查是否有不完整的语句（以操作符或函数名结尾但没有完成）
            incomplete_statements = [
                r'=\s*gf\s*$',  # 不完整的函数调用，如 `temp[0] = gf`
                r'=\s*\w+\s*$',  # 赋值语句不完整（赋值后只有变量名，没有操作符或分号）
                r'\.\s*$',  # 成员访问不完整
                r'->\s*$',  # 指针访问不完整
                r'\(\s*$',  # 函数调用开始但没有参数和闭合括号
            ]
            
            for pattern in incomplete_statements:
                matches = re.finditer(pattern, result, re.MULTILINE)
                for match in matches:
                    line_num = result[:match.start()].count('\n') + 1
                    _diag_warn(f"检测到C++代码中可能有未完成的语句（第 {line_num} 行，匹配模式: {pattern}）")
                    # 如果最后一行匹配不完整模式，说明代码可能被截断
                    if lines_list and re.search(pattern, lines_list[-1]):
                        logger.error("检测到代码可能在语句中间截断，代码不完整！")
                        # 不自动修复，因为无法确定正确的补全方式，让验证器报告错误
            
            # 检查是否有不完整的函数调用或语句（以操作符结尾但没有完成）
            incomplete_patterns = [
                r'=\s*gf\s*$',  # 不完整的函数调用，如 `temp[0] = gf`
                r'=\s*\w+\s*$',  # 赋值语句不完整
                r'\.\s*$',  # 成员访问不完整
                r'->\s*$',  # 指针访问不完整
                r'\(\s*$',  # 函数调用开始但没有参数
            ]
            
            for pattern in incomplete_patterns:
                if re.search(pattern, result, re.MULTILINE):
                    _diag_warn(f"检测到{'C++' if language.lower() in ['cpp', 'c++'] else 'C'}代码中可能有未完成的语句（匹配模式: {pattern}）")
                    # 如果最后一行匹配不完整模式，说明代码可能被截断
                    if lines_list and re.search(pattern, lines_list[-1]):
                        _diag_warn("检测到代码可能在语句中间截断，代码可能不完整")
                        # 不自动修复，因为无法确定正确的补全方式，让验证器报告错误
            
            # 对于C代码，检查是否有不完整的语句（如 `bin` 后面缺少分号）
            if language.lower() == 'c':
                # 检查最后一行是否是不完整的语句
                if lines_list:
                    last_line = lines_list[-1].strip()
                    # 检查是否是单独的标识符（可能是被截断的语句）
                    if re.match(r'^\s*\w+\s*$', last_line) and not last_line.endswith(';') and not last_line.endswith('}'):
                        # 检查是否是函数名、变量名等（不是关键字）
                        keywords = ['return', 'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default', 'break', 'continue', 'goto']
                        if last_line not in keywords:
                            logger.error(
                                f"检测到C代码可能在语句中间截断：最后一行是单独的标识符 '{last_line}'。"
                                "不在此处自动补分号（易把 `final`/`write_bit` 等半截标识符变成非法语句）。"
                            )
                
                # 检查是否有未闭合的括号
                open_parens = result.count('(')
                close_parens = result.count(')')
                open_braces = result.count('{')
                close_braces = result.count('}')
                open_brackets = result.count('[')
                close_brackets = result.count(']')
                
                if open_parens != close_parens:
                    _diag_warn(f"C代码圆括号不匹配: 开括号 {open_parens}, 闭括号 {close_parens}")
                if open_braces != close_braces:
                    _diag_warn(f"C代码大括号不匹配: 开括号 {open_braces}, 闭括号 {close_braces}")
                if open_brackets != close_brackets:
                    _diag_warn(f"C代码方括号不匹配: 开括号 {open_brackets}, 闭括号 {close_brackets}")
        
        # 如果提取的结果为空或太短，返回原始文本（去除明显的说明文字）
        if not result or len(result) < 50:
            # 移除开头的说明文字
            result = text
            for pattern in skip_patterns:
                result = re.sub(rf'^{pattern}.*?\n', '', result, flags=re.IGNORECASE | re.MULTILINE)
            result = result.strip()

        if language.lower() == "python" and result.strip():
            if python_looks_like_c(result):
                _diag_warn("extract_code: Python 目标片段仍判定为 C/C++ 混入，丢弃")
                return ""

        return result
    


def detect_code_truncation( code: str, language: str) -> bool:
        """
        检测代码是否被截断（由于max_tokens限制）
        
        Args:
            code: 生成的代码
            language: 编程语言
            
        Returns:
            如果代码可能被截断，返回True
        """
        if not code:
            return False
        
        # 对于C语言，检查常见的截断模式
        if language.lower() == 'c':
            # 检查最后一行是否是单独的标识符（如 hex_to）
            lines = code.strip().split('\n')
            if lines:
                last_line = lines[-1].strip()
                
                # 检查是否在注释后截断（如 // PKCS#）
                if last_line.startswith('//') and len(last_line) < 20:
                    # 如果最后一行是短注释，可能是截断
                    if 'PKCS' in last_line or '填充' in last_line or '填充' in last_line:
                        return True
                
                # 检查是否是单独的标识符（不是关键字，不是完整的语句）
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', last_line):
                    keywords = ['return', 'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default', 'break', 'continue', 'goto', 'int', 'void', 'char', 'float', 'double']
                    if last_line not in keywords and not last_line.endswith(';') and not last_line.endswith('}'):
                        # 检查是否是函数名的一部分（如 hex_to 应该是 hex_to_bytes）
                        if '_' in last_line and len(last_line) < 20:
                            return True
                
                # 检查括号是否匹配
                open_braces = code.count('{')
                close_braces = code.count('}')
                if open_braces != close_braces:
                    # 如果缺少闭合大括号，可能是截断
                    if open_braces > close_braces:
                        return True
                
                # 检查是否有未完成的函数调用（如 hex_to_bytes(plaintext_hex）
                if re.search(r'\b\w+\([^)]*$', last_line):
                    return True
                
                # 检查main函数是否存在和完整
                has_main = 'int main' in code.lower() or 'void main' in code.lower()
                if not has_main:
                    # 如果没有main函数，肯定是截断了
                    return True
                elif has_main and 'return 0' not in code and 'return 0;' not in code:
                    # 如果有main函数但没有return 0，可能是截断了
                    return True
                
                # 检查是否在函数定义后截断（如 void pkcs7_pad 后没有函数体）
                if re.search(r'void\s+\w+\s*\([^)]*\)\s*$', code, re.MULTILINE):
                    # 如果最后匹配到函数定义但没有函数体，可能是截断
                    return True
        
        return False
    


def fix_missing_headers( code: str, language: str) -> str:
        """
        自动检测并添加缺失的头文件（根据语言选择正确的头文件格式）
        
        Args:
            code: C/C++代码
            language: 编程语言 ('c', 'cpp', 'c++')
        
        Returns:
            修复后的代码
        """
        if language.lower() not in ['cpp', 'c++', 'c']:
            return code
        
        is_c_language = language.lower() == 'c'
        
        # 如果是C语言，先移除所有C++头文件（避免编译错误）
        if is_c_language:
            # C++头文件列表（C语言不能使用）
            cpp_headers = [
                r'#include\s+<cstdint>',
                r'#include\s+<cstdlib>',
                r'#include\s+<cctype>',
                r'#include\s+<iostream>',
                r'#include\s+<string>',
                r'#include\s+<vector>',
                r'#include\s+<sstream>',
                r'#include\s+<iomanip>',
                r'#include\s+<algorithm>',
                r'#include\s+<fstream>',
                r'#include\s+<bitset>',
            ]
            for pattern in cpp_headers:
                code = re.sub(pattern, '', code, flags=re.IGNORECASE)
            # 清理多余的空行（连续3个或更多空行变成2个）
            code = re.sub(r'\n{3,}', '\n\n', code)
        
        # 检测代码中使用的类型和函数，确定需要的头文件
        required_headers = set()
        existing_headers = set()
        
        # 提取已有的头文件
        include_pattern = r'#include\s+[<"]([^>"]+)[>"]'
        existing_includes = re.findall(include_pattern, code)
        for inc in existing_includes:
            existing_headers.add(inc.strip())
        
        # 检测需要的头文件
        # uint8_t, uint16_t, uint32_t, uint64_t
        if re.search(r'\buint\d+_t\b', code):
            if is_c_language:
                # C语言使用 <stdint.h>
                # 检查是否已有stdint.h或cstdint（如果已有cstdint，需要移除并添加stdint.h）
                if 'stdint.h' not in existing_headers:
                    required_headers.add('#include <stdint.h>')
            else:
                # C++语言使用 <cstdint>
                # 检查是否已有cstdint或stdint.h（如果已有stdint.h，可以保留，但优先使用cstdint）
                if 'cstdint' not in existing_headers:
                    required_headers.add('#include <cstdint>')
        
        # C++特有的头文件（C语言不需要）
        if not is_c_language:
            # std::cout, std::cin, std::cerr -> <iostream>
            if re.search(r'std::(cout|cin|cerr|endl)', code) and 'iostream' not in existing_headers:
                required_headers.add('#include <iostream>')
            
            # std::string -> <string>
            if re.search(r'std::string\b', code) and 'string' not in existing_headers:
                required_headers.add('#include <string>')
            
            # std::vector -> <vector>
            if re.search(r'std::vector\b', code) and 'vector' not in existing_headers:
                required_headers.add('#include <vector>')
            
            # std::stringstream, std::ostringstream -> <sstream>
            if re.search(r'std::(stringstream|ostringstream|istringstream)', code) and 'sstream' not in existing_headers:
                required_headers.add('#include <sstream>')
            
            # std::hex, std::setw, std::setfill -> <iomanip>
            if re.search(r'std::(hex|setw|setfill|dec|oct)', code) and 'iomanip' not in existing_headers:
                required_headers.add('#include <iomanip>')
            
            # std::remove_if, std::remove, std::find, std::sort -> <algorithm>
            if re.search(r'std::(remove_if|remove|find|sort|transform|for_each)', code) and 'algorithm' not in existing_headers:
                required_headers.add('#include <algorithm>')
            
            # std::ifstream, std::ofstream -> <fstream>
            if re.search(r'std::(ifstream|ofstream|fstream)', code) and 'fstream' not in existing_headers:
                required_headers.add('#include <fstream>')
            
            # std::bitset -> <bitset>
            if re.search(r'std::bitset\b', code) and 'bitset' not in existing_headers:
                required_headers.add('#include <bitset>')
        
        # 字符处理函数（C和C++都需要，但头文件不同）
        if re.search(r'\b(isxdigit|toupper|tolower|isdigit|isalpha|isspace)\s*\(', code):
            if is_c_language:
                # C语言使用 <ctype.h>
                if 'ctype.h' not in existing_headers:
                    required_headers.add('#include <ctype.h>')
            else:
                # C++语言使用 <cctype>
                if 'cctype' not in existing_headers:
                    required_headers.add('#include <cctype>')
        
        # getenv, malloc, free等（C和C++都需要，但头文件不同）
        if re.search(r'\b(getenv|malloc|free|atoi|atol|atof)\s*\(', code):
            if is_c_language:
                # C语言使用 <stdlib.h>
                if 'stdlib.h' not in existing_headers:
                    required_headers.add('#include <stdlib.h>')
            else:
                # C++语言使用 <cstdlib>
                if 'cstdlib' not in existing_headers:
                    required_headers.add('#include <cstdlib>')
        
        # 如果没有需要添加的头文件，直接返回
        if not required_headers:
            return code
        
        # 找到第一个#include的位置，在它之前插入缺失的头文件
        lines = code.split('\n')
        first_include_idx = -1
        
        for i, line in enumerate(lines):
            if re.match(r'\s*#include\s+', line):
                first_include_idx = i
                break
        
        # 如果找到了#include，在它之前插入；否则在文件开头插入
        if first_include_idx >= 0:
            # 在第一个#include之前插入缺失的头文件
            header_lines = sorted(list(required_headers))
            lines = lines[:first_include_idx] + header_lines + lines[first_include_idx:]
            logger.info(f"已自动添加缺失的头文件: {', '.join([h.replace('#include ', '') for h in header_lines])}")
        else:
            # 在文件开头插入
            header_lines = sorted(list(required_headers))
            lines = header_lines + lines
            logger.info(f"已自动添加缺失的头文件到文件开头: {', '.join([h.replace('#include ', '') for h in header_lines])}")
        
        return '\n'.join(lines)
    
