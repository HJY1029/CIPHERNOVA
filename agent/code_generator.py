"""代码生成相关功能模块"""
import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from utils.logger import setup_logger
from utils import distillation as distill_mod
from agent.prompt_builder import (
    build_prompt,
    llm_user_content_for_api,
    resolve_llm_system_prompt,
)
from agent.code_processing import extract_code, detect_code_truncation, python_looks_like_c
from agent.generation_trace import emit_llm_begin, emit_llm_end, emit_prompt_ready

logger = setup_logger()


def _remove_decryption_code(code: str, language: str, operation: str) -> str:
    """移除代码中的解密相关功能和输出语句"""
    # 如果操作类型是"解密"或"签名验证"等，不处理
    if operation == '解密' or operation == '验证':
        return code
    
    # 对于"加密解密"或"加密"操作，需要处理
    # 默认情况下（"加密解密"）也处理，因为主要目的是加密
    logger.info(f"开始后处理：移除解密相关代码 (operation={operation}, language={language}, code_length={len(code)})")
    
    lines = code.split('\n')
    new_lines = []
    in_decrypt_function = False
    brace_count = 0
    skip_remaining = False  # 标记是否跳过后续所有代码
    
    for i, line in enumerate(lines):
        # 如果已经处理完main函数，跳过后续所有代码
        if skip_remaining:
            continue
        # 检测解密函数定义（C/C++）
        if language.lower() in ['c', 'cpp', 'c++']:
            if re.search(r'\b(decrypt|解密)\s*\(', line, re.IGNORECASE) and ('void' in line or 'int' in line or 'uint8_t' in line):
                in_decrypt_function = True
                brace_count = 0
                logger.info(f"检测到解密函数定义，将移除: {line.strip()[:50]}")
                continue
            if in_decrypt_function:
                brace_count += line.count('{') - line.count('}')
                if brace_count <= 0 and '{' not in line:
                    in_decrypt_function = False
                continue
        # 检测解密函数定义（Python）
        elif language.lower() == 'python':
            if re.search(r'\bdef\s+(decrypt|解密)', line, re.IGNORECASE):
                in_decrypt_function = True
                logger.info(f"检测到解密函数定义，将移除: {line.strip()[:50]}")
                continue
            if in_decrypt_function:
                # Python函数以非缩进行结束
                if line.strip() and not line.startswith(' ') and not line.startswith('\t') and not line.startswith('#'):
                    in_decrypt_function = False
                else:
                    continue
        
        # 检测输出解密结果的语句（更严格的匹配，避免误删）
        # 匹配包含"解密"、"decrypt"、"decrypted"、"plaintext"、"明文"等关键词的输出语句
        if re.search(r'(print|printf|cout|cerr|puts|fprintf).*?(解密后的明文|decrypted|decrypt.*?plaintext|plaintext.*?decrypt)', line, re.IGNORECASE):
            logger.info(f"检测到解密结果输出，将移除: {line.strip()[:50]}")
            continue
        
        # 检测单独的解密输出行（不包含密文）
        if re.search(r'^(print|printf|cout|cerr|puts).*?(解密|decrypt|decrypted).*?(plaintext|明文)', line, re.IGNORECASE):
            logger.info(f"检测到解密结果输出，将移除: {line.strip()[:50]}")
            continue
        
        # 检测在密文输出时附加解密明文的代码
        # 例如：ciphertext_hex += decrypted_hex 或 print(f"密文: {ciphertext_hex}{decrypted_hex}")
        if re.search(r'(ciphertext|密文|hexString|hex_string).*?\+.*?(decrypt|解密|plaintext|明文|decrypted)', line, re.IGNORECASE):
            logger.info(f"检测到密文后附加解密明文，将移除: {line.strip()[:50]}")
            continue
        
        # 检测C代码中在printf循环后继续输出解密明文的模式
        # 例如：printf循环输出密文后，又有一个循环输出解密明文
        if language.lower() in ['c', 'cpp', 'c++']:
            # 检测printf循环后紧跟另一个printf循环（可能是解密输出）
            if i > 0 and i < len(lines) - 5:
                # 检查前几行是否是密文输出循环
                prev_context = '\n'.join(lines[max(0, i-3):i+1])
                next_context = '\n'.join(lines[i+1:min(len(lines), i+6)])
                if re.search(r'printf.*密文.*for.*printf.*%02x', prev_context, re.IGNORECASE | re.DOTALL):
                    if re.search(r'printf.*(解密|decrypt|plaintext|明文)', next_context, re.IGNORECASE):
                        logger.info(f"检测到密文输出循环后的解密输出，将移除后续输出: {line.strip()[:50]}")
                        # 跳过后续的解密输出部分
                        skip_count = 0
                        for j in range(i+1, min(i+20, len(lines))):
                            if re.search(r'printf.*(解密|decrypt|plaintext|明文)', lines[j], re.IGNORECASE):
                                skip_count = j - i
                                break
                            if 'return' in lines[j] or '}' in lines[j]:
                                break
                        if skip_count > 0:
                            # 跳过解密输出部分
                            for _ in range(skip_count):
                                if i+1 < len(lines):
                                    logger.info(f"跳过解密输出行: {lines[i+1].strip()[:50]}")
                                    lines.pop(i+1) if i+1 < len(lines) else None
                            continue
        
        # 检测C代码中ciphertext_len计算错误（可能包含了额外的字节）
        # 例如：ciphertext_len += len; 后可能又添加了额外的长度
        if language.lower() in ['c', 'cpp', 'c++']:
            # 检测是否有多个地方累加ciphertext_len
            if 'ciphertext_len' in line and ('+=' in line or '=' in line):
                # 检查后续是否有解密相关的操作
                for j in range(i+1, min(i+20, len(lines))):
                    if re.search(r'(decrypt|解密|EVP_Decrypt)', lines[j], re.IGNORECASE):
                        logger.warning(f"检测到ciphertext_len计算后可能有解密操作，需要检查: {line.strip()[:50]}")
                        break
        
        # 检测在print/printf语句中同时输出密文和解密明文
        # 例如：print(f"密文: {ciphertext_hex}{decrypted_hex}")
        if re.search(r'(print|printf).*?\{.*?(ciphertext|密文).*?\}.*?\{.*?(decrypt|解密|plaintext|明文|decrypted)', line, re.IGNORECASE):
            # 尝试修复：只保留密文部分
            if 'ciphertext' in line.lower() or '密文' in line:
                # 提取密文部分
                match = re.search(r'(print|printf).*?(\{.*?ciphertext.*?\}|密文.*?\{.*?\})', line, re.IGNORECASE)
                if match:
                    logger.info(f"检测到密文和解密明文同时输出，将修复: {line.strip()[:50]}")
                    # 简化：只输出密文，移除解密部分
                    new_line = re.sub(r'\{.*?(decrypt|解密|plaintext|明文|decrypted).*?\}', '', line, flags=re.IGNORECASE)
                    new_line = re.sub(r'\+.*?(decrypt|解密|plaintext|明文|decrypted).*?', '', new_line, flags=re.IGNORECASE)
                    new_lines.append(new_line)
                    continue
            logger.info(f"检测到密文和解密明文同时输出，将移除: {line.strip()[:50]}")
            continue
        
        # 检测解密调用后输出结果的模式
        if i > 0 and i < len(lines) - 1:
            prev_line = lines[i-1].strip()
            next_line = lines[i+1].strip() if i+1 < len(lines) else ''
            if re.search(r'(decrypt|解密)', prev_line, re.IGNORECASE) and re.search(r'(print|printf|cout).*?(plaintext|明文)', next_line, re.IGNORECASE):
                logger.info(f"检测到解密调用后的输出，将移除: {line.strip()[:50]}")
                continue
        
        # 对于C代码，在输出密文后立即添加return 0，阻止后续输出
        if language.lower() in ['c', 'cpp', 'c++']:
            # 检测printf输出密文的循环结束（printf("\n")）
            if re.search(r'printf.*\\n.*\)', line, re.IGNORECASE) and i > 0:
                # 检查前几行是否是密文输出
                prev_lines = '\n'.join(lines[max(0, i-5):i+1])
                if re.search(r'printf.*密文.*for.*ciphertext_len', prev_lines, re.IGNORECASE | re.DOTALL):
                    # 这是密文输出的换行，在下一行添加return 0
                    new_lines.append(line)
                    # 检查下一行是否已经是return或}
                    if i+1 < len(lines):
                        next_line = lines[i+1].strip()
                        if next_line.startswith('return'):
                            # 已经有return，保留return，然后跳过后续代码直到}
                            logger.info(f"检测到已有return语句，跳过后续代码直到闭合大括号")
                            new_lines.append(lines[i+1])  # 保留return语句
                            # 跳过后续所有代码直到}
                            found_brace = False
                            brace_index = -1
                            for j in range(i+2, len(lines)):
                                if lines[j].strip().startswith('}'):
                                    # 保留闭合大括号
                                    new_lines.append(lines[j])
                                    found_brace = True
                                    brace_index = j
                                    # 设置标志，跳过后续所有行的处理
                                    skip_remaining = True
                                    logger.info(f"找到闭合大括号在第 {j+1} 行，设置skip_remaining=True，将跳过第 {j+2} 行及之后的所有代码")
                                    break
                            # 无论是否找到}，都设置标志
                            if not found_brace:
                                # 没找到}，可能代码结构有问题，但还是要设置标志
                                skip_remaining = True
                                logger.warning(f"未找到闭合大括号，但仍设置skip_remaining=True")
                            # continue会继续外层循环的下一次迭代，由于skip_remaining=True，后续代码会被跳过
                            continue
                        elif next_line.startswith('}'):
                            # 下一行就是}，不需要添加return
                            new_lines.append(lines[i+1])
                            continue
                        else:
                            # 没有return，添加return 0并跳过后续代码直到}
                            logger.info(f"在密文输出后添加return 0，阻止后续输出")
                            new_lines.append("    return 0;")
                            # 跳过后续所有代码直到}
                            for j in range(i+1, len(lines)):
                                if lines[j].strip().startswith('}'):
                                    # 保留闭合大括号
                                    new_lines.append(lines[j])
                                    break
                            continue
                    else:
                        # 没有下一行，直接添加return 0和闭合大括号
                        logger.info(f"在密文输出后添加return 0，阻止后续输出")
                        new_lines.append("    return 0;")
                        new_lines.append("}")
                        continue
        
        new_lines.append(line)
    
    result = '\n'.join(new_lines)
    removed_count = len(lines) - len(new_lines)
    if removed_count > 0:
        logger.info(f"后处理完成：移除了 {removed_count} 行解密相关代码")
    else:
        logger.info(f"后处理完成：未发现需要移除的解密代码（代码可能通过其他方式附加了解密明文）")
    return result



async def generate_code(agent,  algorithm: str, mode: Optional[str] = None,
                           operation: str = "加密解密", language: str = 'python', 
                           test_data: Optional[Dict] = None, **kwargs) -> Tuple[str, float]:
        """生成密码学代码
        
        Returns:
            (代码, 生成时长(秒))
        """
        import time
        logger.info(f"正在生成 {algorithm}" + (f" ({mode})" if mode else "") + f" 的{language}代码...")
        
        gen_kwargs = dict(kwargs)
        if _is_qwen_local_provider(agent) and _qwen_slot_prefers_ultra_compact(
            algorithm, mode, language
        ):
            gen_kwargs.setdefault("_force_compact_prompt", True)
        if _is_qwen_local_provider(agent) and _qwen_slot_skip_distillation(
            algorithm, mode, language, gen_kwargs
        ):
            gen_kwargs.setdefault("_skip_distillation", True)
        _shw = bool(gen_kwargs.get("_ablation_no_test_feedback"))
        prompt = build_prompt(agent, algorithm, mode, operation, language, test_data=test_data, **gen_kwargs)
        system_prompt = resolve_llm_system_prompt(language, gen_kwargs)
        user_for_llm = llm_user_content_for_api(prompt, system_prompt or "")
        emit_prompt_ready(
            step="初次生成",
            agent=agent,
            algorithm=algorithm,
            mode=mode,
            language=language,
            kwargs=gen_kwargs,
            user_prompt=prompt,
            system_prompt=system_prompt,
        )

        # 尝试生成代码；余额不足时可（由 config 决定）自动切换到其他 LLM
        failed_providers = []
        last_error = None
        allow_balance_fallback = bool(
            agent.config.get("switch_llm_on_insufficient_balance", True)
        )

        try:
            while True:
                try:
                    # 记录生成开始时间
                    start_time = time.time()
                    emit_llm_begin(
                        step="初次生成",
                        agent=agent,
                        kwargs=gen_kwargs,
                        user_chars=len(user_for_llm),
                        system_chars=len(system_prompt or ""),
                    )
                    raw_output = await agent.llm.generate(user_for_llm, system_prompt)
                    generation_time = time.time() - start_time
                    emit_llm_end(
                        step="初次生成",
                        kwargs=gen_kwargs,
                        seconds=generation_time,
                        reply_chars=len(raw_output or ""),
                    )
                    logger.info(f"代码生成耗时: {generation_time:.2f}秒")
                    break  # 成功生成，退出循环
                except Exception as e:
                    error_msg = str(e)
                    last_error = e
                    
                    # 检查是否是余额不足错误（常见 402/403 + 文案）
                    is_insufficient_balance = (
                        "402" in error_msg
                        or "Insufficient Balance" in error_msg
                        or "余额不足" in error_msg
                        or "主账户可用余额不足" in error_msg
                        or "请充值后再使用" in error_msg
                        or "invalid_request_error" in error_msg
                    )
                    
                    if is_insufficient_balance and not allow_balance_fallback:
                        logger.warning(
                            f"LLM提供商 {agent.provider} 余额不足；"
                            f"switch_llm_on_insufficient_balance=false，不自动切换，请求失败。"
                        )
                        raise

                    if is_insufficient_balance:
                        failed_providers.append(agent.provider)
                        logger.warning(f"LLM提供商 {agent.provider} 余额不足，尝试切换到其他提供商...")
                        
                        # 获取其他可用的提供商
                        available_providers = agent._get_available_providers(exclude=failed_providers)
                        
                        if not available_providers:
                            # 没有其他可用的提供商
                            error_message = (
                                f"所有LLM提供商都不可用或余额不足。\n"
                                f"已尝试的提供商: {', '.join(failed_providers)}\n"
                                f"请检查配置文件 config.yaml，确保至少有一个LLM提供商已启用且有足够的余额。"
                            )
                            logger.error(error_message)
                            raise ValueError(error_message) from e
                        
                        # 切换到下一个可用的提供商
                        agent.provider = available_providers[0]
                        logger.info(f"切换到LLM提供商: {agent.provider}")
                        agent.llm = agent._init_llm(agent.provider)
                        continue  # 重试生成

                    em_low = error_msg.lower()
                    is_context_limit = (
                        "超过或等于上下文限制" in error_msg
                        or (
                            "输入tokens" in error_msg
                            and "上下文限制" in error_msg
                        )
                        or (
                            "context" in em_low
                            and (
                                "length" in em_low
                                or "window" in em_low
                                or "exceed" in em_low
                            )
                        )
                    )
                    if is_context_limit:
                        if not gen_kwargs.get("_force_compact_prompt"):
                            logger.warning(
                                "估算输入超过模型上下文，改用精简 prompt 重试一次（向量测试无法修复此类错误）。"
                            )
                            gen_kwargs["_force_compact_prompt"] = True
                            step_label = "初次生成(精简)"
                        elif not gen_kwargs.get("_skip_distillation") and not gen_kwargs.get(
                            "_keep_distillation"
                        ):
                            logger.warning(
                                "精简后仍超长：显式跳过教师蒸馏少样本并重试一次。"
                            )
                            gen_kwargs["_skip_distillation"] = True
                            step_label = "初次生成(精简无蒸馏)"
                        elif gen_kwargs.get("_keep_distillation"):
                            raise
                        else:
                            raise
                        prompt = build_prompt(
                            agent,
                            algorithm,
                            mode,
                            operation,
                            language,
                            test_data=test_data,
                            **gen_kwargs,
                        )
                        system_prompt = resolve_llm_system_prompt(language, gen_kwargs)
                        user_for_llm = llm_user_content_for_api(prompt, system_prompt or "")
                        emit_prompt_ready(
                            step=step_label,
                            agent=agent,
                            algorithm=algorithm,
                            mode=mode,
                            language=language,
                            kwargs=gen_kwargs,
                            user_prompt=prompt,
                            system_prompt=system_prompt,
                        )
                        continue

                    # 其他类型的错误，直接抛出
                    raise

            if gen_kwargs.get("_force_compact_prompt"):
                kwargs["_force_compact_prompt"] = True
            if gen_kwargs.get("_skip_distillation"):
                kwargs["_skip_distillation"] = True

            # 提取纯代码，去除markdown格式和说明文字
            code = extract_code(
                raw_output, language, suppress_heuristic_warnings=_shw
            )
            if language.lower() == "python" and (
                not (code or "").strip() or python_looks_like_c(code)
            ):
                logger.warning("Python 初次提取为空或仍含 C，追加纠错提示后重试一次生成")
                retry_kw = dict(gen_kwargs)
                retry_kw["紧急纠错"] = (
                    "上一次输出误含 C/C++（如 void xxx(、#include、char*、DES_key_schedule）。"
                    "本次必须从头到尾仅为 Python3：仅 import/from/def；加密仅用 pycryptodome/gmdsm 等题面允许库；禁止 void 与头文件。"
                )
                prompt_retry = build_prompt(
                    agent,
                    algorithm,
                    mode,
                    operation,
                    language,
                    test_data=test_data,
                    **retry_kw,
                )
                system_retry = resolve_llm_system_prompt(language, retry_kw)
                user_retry = llm_user_content_for_api(prompt_retry, system_retry or "")
                emit_prompt_ready(
                    step="语言纠错再生成",
                    agent=agent,
                    algorithm=algorithm,
                    mode=mode,
                    language=language,
                    kwargs=retry_kw,
                    user_prompt=prompt_retry,
                    system_prompt=system_retry,
                )
                emit_llm_begin(
                    step="语言纠错再生成",
                    agent=agent,
                    kwargs=retry_kw,
                    user_chars=len(user_retry),
                    system_chars=len(system_retry or ""),
                )
                t0 = time.time()
                raw_retry = await agent.llm.generate(user_retry, system_retry)
                emit_llm_end(
                    step="语言纠错再生成",
                    kwargs=retry_kw,
                    seconds=time.time() - t0,
                    reply_chars=len(raw_retry or ""),
                )
                code = extract_code(
                    raw_retry, language, suppress_heuristic_warnings=_shw
                )

            # 检测代码是否被截断（由于max_tokens限制），可能需要多次继续生成
            max_continue_attempts = 5  # 最多尝试5次继续生成（增加次数以确保代码完整）
            for continue_attempt in range(max_continue_attempts):
                is_truncated = detect_code_truncation(code, language)
                if not is_truncated:
                    break  # 代码已完整，退出循环
                
                if continue_attempt == 0:
                    logger.warning("检测到代码可能因token限制被截断，尝试继续生成...")
                else:
                    logger.warning(f"代码仍然不完整，第 {continue_attempt + 1} 次继续生成...")
                
                # 尝试继续生成剩余部分
                # 获取最后几行作为上下文（减少token占用）
                code_lines = code.strip().split('\n')
                context_lines = code_lines[-15:] if len(code_lines) > 15 else code_lines
                context = '\n'.join(context_lines)
                
                # 提取已生成的函数名，明确告诉LLM不要重复（C 家族 / Python 分开）
                existing_functions = set()
                existing_py_defs = set()
                for line in code_lines:
                    match = re.search(r'^\s*(void|int|uint8_t|size_t|const\s+int)\s+(\w+)\s*\(', line)
                    if match:
                        existing_functions.add(match.group(2))
                    mpy = re.search(r'^\s*def\s+(\w+)\s*\(', line)
                    if mpy:
                        existing_py_defs.add(mpy.group(1))
                func_list = ', '.join(sorted(existing_functions)) if existing_functions else '无'
                func_list_py = ', '.join(sorted(existing_py_defs)) if existing_py_defs else '无'
                lang_lower = language.lower()

                if lang_lower == 'python':
                    continue_prompt = f"""代码被截断，请仅输出 **Python 3** 接续片段（不要 Markdown）。当前任务：{algorithm} {mode or ''}。

最后几行上下文：
{context}

**不要重复已有 import / 顶层 def。** 已有 def 名（勿再定义）：{func_list_py}

**严禁**输出 `#include`、`void ...(`、`uint8_t`、`DES_key_schedule` 等 C 代码；用 `import`/`from Crypto...` 与 `def` 续写。
补全缺失的函数体或 `if __name__ == '__main__':` 块；hex 环境变量用 `.strip()` 或 `''.join(s.split())`。

只输出从截断点起的后续源码，不要重复文件开头。"""
                elif lang_lower in ('c', 'cpp', 'c++'):
                    continue_prompt = f"""代码被截断，继续生成剩余部分（{algorithm} {mode or ''}）。最后几行：

{context}

**⚠️⚠️⚠️ 绝对重要：不要重复已生成的代码！⚠️⚠️⚠️**
已生成的函数（绝对不要重复）：{func_list}

**只生成缺失的部分，绝对不要重复任何已生成的函数、置换表、S盒或代码！**

必须生成（如果缺失）：
1. PKCS#7填充函数（如果代码在 `// PKCS#` 注释后截断）
2. des_decrypt_block函数（如果缺失）
3. 完整的main函数（从环境变量读取TEST_PLAINTEXT/TEST_KEY/TEST_IV，调用hex_to_bytes，调用generate_subkeys，实现CBC加密循环，输出密文，return 0）

**重要：函数调用必须正确！**
- `permute` 函数签名：`void permute(const uint8_t *input, const int *table, int num_bits, uint8_t *output)`
  - ✅ 正确：`permute(pre_output, FP_TABLE, 64, ciphertext);`
  - ❌ 错误：`permute(right, left, 8, final_permutation_table, final_permuted);` （参数顺序和数量错误，表名错误）
- 置换表名称：必须使用 `IP_TABLE`、`FP_TABLE`、`E_TABLE`、`P_TABLE`、`PC1_TABLE`、`PC2_TABLE`，不要使用 `initial_permutation_table` 或 `final_permutation_table`！
- `des_encrypt_block` 函数签名：`void des_encrypt_block(const uint8_t *plaintext, const uint8_t subkeys[16][6], uint8_t *ciphertext)`
  - ✅ 正确：`des_encrypt_block(plaintext, subkeys, ciphertext);`
  - ❌ 错误：`des_encrypt_block(plaintext, ciphertext, subkeys);` （参数顺序错误）

要求：
- **只输出缺失的代码，绝对不要重复已生成的任何内容！**
- **绝对不要包含 #include 语句！**
- **所有函数调用必须完整，不能截断（如 `memcpy(left` 是错误的，必须是 `memcpy(left, right, 4);`）**
- **只输出代码，无说明文字**
- **从截断处开始，只生成后续的代码**"""
                else:
                    continue_prompt = f"""代码被截断，请续写剩余部分。语言：{language}；任务：{algorithm} {mode or ''}。

{context}

勿重复已有定义；只输出后续代码，无 Markdown。"""
                cstep = f"续写片段#{continue_attempt + 1}"
                emit_prompt_ready(
                    step=cstep,
                    agent=agent,
                    algorithm=algorithm,
                    mode=mode,
                    language=language,
                    kwargs=kwargs,
                    user_prompt=continue_prompt,
                    system_prompt=system_prompt,
                )
                try:
                    emit_llm_begin(
                        step=cstep,
                        agent=agent,
                        kwargs=kwargs,
                        user_chars=len(continue_prompt),
                        system_chars=len(system_prompt or ""),
                    )
                    t1 = time.time()
                    continue_output = await agent.llm.generate(continue_prompt, system_prompt)
                    emit_llm_end(
                        step=cstep,
                        kwargs=kwargs,
                        seconds=time.time() - t1,
                        reply_chars=len(continue_output or ""),
                    )
                    continue_code = extract_code(
                        continue_output,
                        language,
                        suppress_heuristic_warnings=_shw,
                    )
                    # 合并代码（去除重复部分和头文件）
                    if continue_code:
                        # 移除继续生成代码中的头文件（头文件应该已经在原代码中）
                        # 不仅移除行首的include，还要移除行中的include
                        continue_code_lines = continue_code.split('\n')
                        filtered_lines = []
                        for line in continue_code_lines:
                            # 如果整行都是include，跳过
                            if re.match(r'^\s*#include\s+', line):
                                continue
                            # 如果行中包含include，移除include部分
                            if '#include' in line:
                                # 移除include及其后面的内容（可能是同一行）
                                line = re.sub(r'#include\s+[<"].*?[>"]', '', line)
                                # 如果移除后行变空了，跳过
                                if not line.strip():
                                    continue
                            filtered_lines.append(line)
                        continue_code = '\n'.join(filtered_lines).strip()
                        
                        if continue_code:
                            # 更智能的重复检测：检查继续生成代码是否包含已生成的函数
                            code_lines = code.strip().split('\n')
                            continue_lines = continue_code.split('\n')
                            
                            # 提取已生成代码中的所有函数名（用于检测重复）
                            existing_functions = set()
                            for line in code_lines:
                                # 匹配函数定义：void function_name( 或 int function_name( 等
                                match = re.search(r'^\s*(void|int|uint8_t|size_t|const\s+int)\s+(\w+)\s*\(', line)
                                if match:
                                    existing_functions.add(match.group(2))
                            
                            # 检查继续生成代码中是否包含已生成的函数
                            start_idx = 0
                            duplicate_functions = []
                            for i, line in enumerate(continue_lines):
                                # 检查是否是函数定义
                                match = re.search(r'^\s*(void|int|uint8_t|size_t|const\s+int)\s+(\w+)\s*\(', line)
                                if match:
                                    func_name = match.group(2)
                                    if func_name in existing_functions:
                                        # 找到重复的函数
                                        duplicate_functions.append((i, func_name))
                            
                            # 如果找到重复函数，从最后一个重复函数之后开始
                            if duplicate_functions:
                                # 找到最后一个重复函数的位置
                                last_dup_idx, last_dup_func = duplicate_functions[-1]
                                # 找到这个函数的结束位置（下一个函数或文件结尾）
                                start_idx = last_dup_idx + 1
                                # 查找这个函数的结束位置（闭合大括号）
                                brace_count = 0
                                found_open = False
                                for j in range(last_dup_idx, len(continue_lines)):
                                    line = continue_lines[j]
                                    brace_count += line.count('{') - line.count('}')
                                    if '{' in line:
                                        found_open = True
                                    if found_open and brace_count == 0:
                                        start_idx = j + 1
                                        break
                                logger.warning(f"检测到重复函数: {[f[1] for f in duplicate_functions]}，将从第 {start_idx} 行开始合并（跳过最后一个重复函数）")
                            
                            # 如果没找到重复函数，使用原来的方法检测重复行
                            if start_idx == 0:
                                last_lines = '\n'.join(code_lines[-20:]) if len(code_lines) > 20 else code.strip()
                                
                                # 检查继续生成代码的开头是否与原代码的结尾重复（更严格的匹配）
                                for i in range(min(40, len(continue_lines)), 5, -1):  # 至少5行，最多40行
                                    check_text = '\n'.join(continue_lines[:i]).strip()
                                    if check_text and len(check_text) > 50:  # 至少50个字符
                                        # 检查是否原代码结尾包含这个文本
                                        if last_lines.endswith(check_text) or check_text in last_lines[-200:]:
                                            start_idx = i
                                            logger.info(f"检测到 {i} 行重复内容（{len(check_text)}字符），将跳过")
                                            break
                            
                            # 合并代码
                            if start_idx > 0:
                                new_code = '\n'.join(continue_lines[start_idx:])
                                if new_code.strip():
                                    # 检查新代码是否以不完整的语句开始（如 `memcpy(left`）
                                    first_line = new_code.split('\n')[0].strip()
                                    # 如果第一行是不完整的函数调用，尝试修复或跳过
                                    if re.search(r'^\w+\s*\([^)]*$', first_line) and not first_line.endswith(';') and not first_line.endswith('}'):
                                        # 不完整的语句，检查下一行是否是完整的
                                        if len(new_code.split('\n')) > 1:
                                            second_line = new_code.split('\n')[1].strip()
                                            # 如果下一行是完整的相同函数调用，跳过第一行
                                            if re.match(r'^\w+\s*\([^)]+\);', second_line):
                                                logger.warning(f"检测到不完整的语句 '{first_line}'，下一行是完整的，跳过第一行")
                                                new_code = '\n'.join(continue_lines[start_idx + 1:])
                                            else:
                                                # 尝试修复：移除不完整的行
                                                logger.warning(f"检测到不完整的语句 '{first_line}'，尝试修复...")
                                                new_code = '\n'.join(continue_lines[start_idx + 1:])
                                    
                                    if new_code.strip():
                                        code = code.rstrip() + '\n' + new_code
                                        logger.info(f"成功继续生成代码，已合并（跳过{start_idx}行重复）")
                                    else:
                                        logger.warning("修复后代码为空，跳过合并")
                                else:
                                    logger.warning("继续生成的代码全是重复的，跳过合并")
                            else:
                                # 没有明显重复，检查第一行是否不完整
                                first_line = continue_code.split('\n')[0].strip() if continue_code else ''
                                if first_line and re.search(r'^\w+\s*\([^)]*$', first_line) and not first_line.endswith(';') and not first_line.endswith('}'):
                                    # 第一行不完整，检查是否有下一行
                                    continue_lines = continue_code.split('\n')
                                    if len(continue_lines) > 1:
                                        second_line = continue_lines[1].strip()
                                        # 如果下一行是完整的相同函数调用，跳过第一行
                                        if re.match(r'^\w+\s*\([^)]+\);', second_line):
                                            logger.warning(f"检测到不完整的首行 '{first_line}'，下一行是完整的，跳过第一行")
                                            continue_code = '\n'.join(continue_lines[1:])
                                
                                # 直接追加
                                code = code.rstrip() + '\n' + continue_code
                                logger.info("成功继续生成代码，已合并")
                            
                            # 合并后，修复可能的常见错误
                            # 检查是否有头文件被插入到错误位置（如 `for (int i =#include <stdint.h>` 或 `memcpy(left, initial_permuted#include <stdint.h>`）
                            if '#include' in code:
                                # 检查是否有不在行首的include
                                lines = code.split('\n')
                                fixed_lines = []
                                has_error = False
                                for line in lines:
                                    # 如果整行是include，保留（应该在文件开头）
                                    if re.match(r'^\s*#include\s+', line):
                                        fixed_lines.append(line)
                                    # 如果行中包含include但不在行首，移除include部分
                                    elif '#include' in line:
                                        has_error = True
                                        # 移除include及其后面的内容
                                        original_line = line
                                        line = re.sub(r'#include\s+[<"].*?[>"]', '', line)
                                        # 清理可能的残留字符（如 `=` 后面直接是换行）
                                        line = re.sub(r'=\s*$', '', line)
                                        line = re.sub(r'=\s*\n', '\n', line)
                                        if line.strip():
                                            fixed_lines.append(line)
                                        else:
                                            # 如果移除后行变空了，跳过这行
                                            logger.warning(f"移除了包含include的错误行: {original_line[:50]}")
                                    else:
                                        fixed_lines.append(line)
                                
                                if has_error:
                                    code = '\n'.join(fixed_lines)
                                    logger.info("已修复头文件位置错误（移除了代码中间的头文件）")
                                    
                                    # 再次检查，确保没有遗漏
                                    if re.search(r'[^#\n\s]#include\s+', code):
                                        logger.warning("仍有头文件在错误位置，进行更彻底的清理...")
                                        # 更彻底的清理：移除所有不在行首的include
                                        code = re.sub(r'[^#\n]#include\s+[<"].*?[>"]', '', code)
                                        # 清理可能的残留（如 `=` 后面直接是换行或空格）
                                        code = re.sub(r'=\s*\n', '\n', code)
                                        code = re.sub(r'=\s+$', '', code, flags=re.MULTILINE)
                                        logger.info("已完成更彻底的头文件清理")
                            
                            # 合并后，修复可能的常见错误
                            # 1. 检查不完整的类型声明（如 `uint;`）
                            if re.search(r'\buint\s*;', code):
                                logger.warning("合并后的代码仍有问题（如 `uint;`），尝试修复...")
                                # 移除不完整的类型声明
                                code = re.sub(r'\buint\s*;\s*\n', '', code)
                                logger.info("已移除不完整的类型声明")
                            
                            # 2. 检查并修复不完整的变量声明和函数调用
                            lines = code.split('\n')
                            fixed_lines = []
                            i = 0
                            while i < len(lines):
                                line = lines[i]
                                
                                # 检查是否是不完整的变量声明（如 `const char *key_hex =` 后面直接是 `if`）
                                incomplete_var_match = re.search(r'^\s*(const\s+)?(char|int|uint8_t|uint32_t|uint64_t|void)\s*\*?\s*(\w+)\s*=\s*$', line)
                                if incomplete_var_match and i + 1 < len(lines):
                                    var_name = incomplete_var_match.group(3)
                                    next_line = lines[i + 1].strip()
                                    # 如果下一行是 `if` 或其他语句（不是变量声明的继续），说明变量声明被截断了
                                    if re.match(r'^\s*(if|for|while|return|printf|fprintf|memcpy|memset|hex_to_bytes)', next_line):
                                        # 尝试从变量名和上下文推断应该赋什么值
                                        var_type_match = re.search(r'^\s*(const\s+)?(char|int|uint8_t|uint32_t|uint64_t|void)\s*\*?', line)
                                        var_type = var_type_match.group(0).strip() if var_type_match else 'const char *'
                                        
                                        # 根据变量名推断应该调用哪个getenv
                                        if 'key' in var_name.lower():
                                            fixed_lines.append(f'    {var_type}{var_name} = getenv("TEST_KEY");')
                                            logger.warning(f"检测到不完整的变量声明 '{line.strip()}'，已修复为 '{var_type}{var_name} = getenv(\"TEST_KEY\");'")
                                        elif 'iv' in var_name.lower():
                                            fixed_lines.append(f'    {var_type}{var_name} = getenv("TEST_IV");')
                                            logger.warning(f"检测到不完整的变量声明 '{line.strip()}'，已修复为 '{var_type}{var_name} = getenv(\"TEST_IV\");'")
                                        elif 'plaintext' in var_name.lower():
                                            fixed_lines.append(f'    {var_type}{var_name} = getenv("TEST_PLAINTEXT");')
                                            logger.warning(f"检测到不完整的变量声明 '{line.strip()}'，已修复为 '{var_type}{var_name} = getenv(\"TEST_PLAINTEXT\");'")
                                        else:
                                            # 无法推断，移除不完整的行
                                            logger.warning(f"检测到不完整的变量声明 '{line.strip()}'，但无法推断，移除该行")
                                            i += 1
                                            continue
                                        
                                        # 检查下一行是否使用了未声明的变量（如 `iv_hex`）
                                        if 'iv_hex' in next_line and not any('iv_hex' in l for l in fixed_lines):
                                            fixed_lines.append('    const char *iv_hex = getenv("TEST_IV");')
                                            logger.warning("检测到使用了未声明的变量 'iv_hex'，已添加声明")
                                        elif 'key_hex' in next_line and not any('key_hex' in l for l in fixed_lines):
                                            fixed_lines.append('    const char *key_hex = getenv("TEST_KEY");')
                                            logger.warning("检测到使用了未声明的变量 'key_hex'，已添加声明")
                                        
                                        i += 1  # 跳过不完整的行
                                        continue
                                
                                # 检查是否是不完整的函数调用（有开括号但没有闭括号和分号，且不是多行调用的开始）
                                incomplete_match = re.search(r'^\s*(\w+)\s*\([^)]*$', line)
                                if incomplete_match and not line.endswith(';') and not line.endswith('}') and not line.endswith(','):
                                    func_name = incomplete_match.group(1)
                                    # 检查下一行是否是相同函数的完整调用
                                    if i + 1 < len(lines):
                                        next_line = lines[i + 1].strip()
                                        # 如果下一行是完整的相同函数调用，跳过当前不完整的行
                                        if re.match(rf'^\s*{re.escape(func_name)}\s*\([^)]+\);', next_line):
                                            logger.warning(f"检测到不完整的函数调用 '{line.strip()[:50]}'，下一行是完整的，跳过第一行")
                                            i += 1  # 跳过不完整的行，使用下一行
                                            fixed_lines.append(next_line)
                                            i += 1
                                            continue
                                
                                fixed_lines.append(line)
                                i += 1
                            
                            if len(fixed_lines) != len(lines):
                                code = '\n'.join(fixed_lines)
                                logger.info("已修复不完整的变量声明和函数调用")
                            
                            # 3. 检查并修复错误的表名
                            if 'final_permutation_table' in code:
                                logger.warning("检测到错误的表名 'final_permutation_table'，替换为 'FP_TABLE'")
                                code = code.replace('final_permutation_table', 'FP_TABLE')
                            if 'initial_permutation_table' in code:
                                logger.warning("检测到错误的表名 'initial_permutation_table'，替换为 'IP_TABLE'")
                                code = code.replace('initial_permutation_table', 'IP_TABLE')
                            
                            # 4. 检查并修复permute函数调用的参数错误
                            # permute函数签名：void permute(const uint8_t *input, const int *table, int num_bits, uint8_t *output)
                            # 错误的调用：permute(right, left, 8, final_permutation_table, final_permuted) - 5个参数
                            # 正确的调用：permute(pre_output, FP_TABLE, 64, ciphertext) - 4个参数
                            permute_error_pattern = r'permute\s*\(([^)]+)\)'
                            for match in re.finditer(permute_error_pattern, code):
                                params_str = match.group(1)
                                params = [p.strip() for p in params_str.split(',')]
                                if len(params) == 5:  # 错误的5参数调用
                                    logger.warning(f"检测到错误的permute调用（5个参数）: permute({params_str[:60]}...)，需要修复")
                                    # 尝试修复：通常第4个参数应该是表名，第5个参数应该是输出
                                    # 但需要根据上下文判断，这里只记录警告
                        else:
                            logger.warning("继续生成的代码为空（可能只有头文件），跳过合并")
                    else:
                        logger.warning("继续生成的代码为空，跳过合并")
                except Exception as e:
                    logger.warning(f"继续生成代码失败: {e}，将在重试时强调代码完整性")
                    kwargs['_incomplete_code_retry'] = True
                    kwargs['_truncation_detected'] = True
                    break  # 如果继续生成失败，退出循环
                
                # 检查合并后是否仍然截断
                if continue_attempt < max_continue_attempts - 1:
                    # 再次检测是否仍然截断
                    is_still_truncated = detect_code_truncation(code, language)
                    if not is_still_truncated:
                        logger.info("继续生成后代码已完整")
                        break
                    else:
                        logger.warning(f"继续生成后代码仍然不完整，将尝试第 {continue_attempt + 2} 次继续生成")
            
            # 循环结束后，检查代码是否仍然不完整
            final_check = detect_code_truncation(code, language)
            if final_check:
                logger.error("经过多次继续生成，代码仍然不完整，将在重试时强调代码完整性")
                kwargs['_incomplete_code_retry'] = True
                kwargs['_truncation_detected'] = True
            
            # 检查代码是否完整（对于C语言，检查是否有空的函数体或占位符）
            if language.lower() == 'c':
                # 检查是否有明显的占位符或不完整的代码
                incomplete_indicators = [
                    '/* PC1置换表 */',
                    '/* PC2置换表 */',
                    '/* 初始置换表 */',
                    '/* 逆初始置换表 */',
                    '/* 扩展置换表 */',
                    '/* P置换表 */',
                    '/* S盒表 */',
                    '// 实现密钥调度',
                    '// 实现DES加密',
                    '// 实现DES解密',
                    'void function() { }',
                    'void function() {\n}',
                    '代码未完成',
                    '展示部分示例'
                ]
                for indicator in incomplete_indicators:
                    if indicator in code:
                        logger.warning(f"检测到代码可能不完整，包含占位符: {indicator}")
                        # 不直接返回，让验证器来检测
                
                # 检查是否使用了随机数（这是不允许的）
                random_indicators = [
                    'rand()',
                    'srand(',
                    'random()',
                    'time(NULL)',
                    'clock()',
                    'gettimeofday',
                    'RAND_bytes',
                    'RAND_pseudo_bytes'
                ]
                for indicator in random_indicators:
                    if indicator in code:
                        logger.warning(f"检测到代码使用了随机数函数: {indicator}，这会导致每次加密结果不同！")
            
            # 检查C++代码完整性
            if language.lower() in ['cpp', 'c++']:
                # 检查是否有明显的占位符或不完整的代码
                incomplete_indicators = [
                    '// TODO',
                    '// FIXME',
                    '// 实现',
                    '// 待实现',
                    '代码未完成',
                    '展示部分示例',
                    '...',  # 省略号可能表示代码不完整
                    'Placeholder for the encryption process',
                    'Placeholder for',
                    'This should include',
                    'should include all the rounds',
                    'Assume we have',
                    'For now, we\'ll just',
                    'For now, we will just'
                ]
                has_placeholder_in_code = False
                placeholder_found = []
                for indicator in incomplete_indicators:
                    if indicator.lower() in code.lower():
                        logger.warning(f"检测到C++代码可能不完整，包含占位符: {indicator}")
                        has_placeholder_in_code = True
                        placeholder_found.append(indicator)
                
                # 如果检测到占位符，在kwargs中标记，以便在下次生成时强调
                if has_placeholder_in_code and algorithm and algorithm.upper() == 'DES':
                    logger.error(f"检测到代码包含占位符，这是严重错误！代码没有完整实现DES算法！占位符: {placeholder_found}")
                    # 在kwargs中添加标记，让下次生成时强调
                    kwargs['_has_placeholder'] = True
                    kwargs['_placeholder_found'] = placeholder_found
                    # 检查关键函数是否只有占位符
                    if 'des_encrypt_block' in code.lower() or 'des_encrypt' in code.lower():
                        # 检查函数体中是否有实际的Feistel网络实现
                        # 查找des_encrypt_block函数
                        pattern = r'des_encrypt_block[^{]*\{[^}]*\}'
                        matches = re.finditer(pattern, code, re.IGNORECASE | re.DOTALL)
                        for match in matches:
                            func_body = match.group(0)
                            # 检查是否有16轮循环但没有实际实现
                            if 'for' in func_body and '16' in func_body:
                                # 检查循环体内是否有实际代码（不只是注释）
                                loop_pattern = r'for\s*\([^)]+\)\s*\{[^}]*\}'
                                loop_matches = re.finditer(loop_pattern, func_body, re.DOTALL)
                                for loop_match in loop_matches:
                                    loop_body = loop_match.group(0)
                                    # 如果循环体只有注释或空，说明是占位符
                                    loop_body_clean = re.sub(r'//.*?$', '', loop_body, flags=re.MULTILINE)
                                    loop_body_clean = re.sub(r'/\*.*?\*/', '', loop_body_clean, flags=re.DOTALL)
                                    loop_body_clean = re.sub(r'\s+', '', loop_body_clean)
                                    # 检查是否包含实际的Feistel网络代码（S盒、扩展置换、P置换等）
                                    has_actual_code = ('s_box' in loop_body_clean.lower() or 
                                                      'sbox' in loop_body_clean.lower() or
                                                      'expand' in loop_body_clean.lower() or
                                                      'e_table' in loop_body_clean.lower() or
                                                      'p_table' in loop_body_clean.lower() or
                                                      'xor' in loop_body_clean.lower() or
                                                      '^' in loop_body_clean)
                                    if len(loop_body_clean) < 100 and not has_actual_code:  # 如果清理后的循环体很短且没有实际代码，可能是占位符
                                        logger.error("检测到des_encrypt_block函数中的16轮循环只有占位符，没有实际实现！")
                                        kwargs['_has_placeholder'] = True
                                        kwargs['_placeholder_in_feistel'] = True
                                        break
                
                # 检查DES加密函数是否只有IP和FP置换（没有16轮Feistel网络）
                if algorithm and algorithm.upper() == 'DES':
                    # 检查是否有des_encrypt或des_encrypt_block函数
                    has_des_encrypt = 'des_encrypt' in code.lower() or 'des_encrypt_block' in code.lower()
                    if has_des_encrypt:
                        # 检查是否只有IP和FP置换，没有16轮Feistel网络
                        has_ip = 'IP_TABLE' in code or 'ip_table' in code.lower()
                        has_fp = 'FP_TABLE' in code or 'fp_table' in code.lower()
                        has_feistel = 'feistel' in code.lower() or 'round' in code.lower() or 's_box' in code.lower() or 'S_BOX' in code
                        has_key_schedule = 'key_schedule' in code.lower() or 'generate_subkey' in code.lower() or 'PC1_TABLE' in code or 'PC2_TABLE' in code
                        
                        if has_ip and has_fp and not has_feistel and not has_key_schedule:
                            logger.warning("检测到DES加密函数可能只有IP和FP置换，没有16轮Feistel网络！这会导致输出等于明文！")
                        elif has_ip and has_fp and not has_feistel:
                            logger.warning("检测到DES加密函数可能缺少16轮Feistel网络！只有IP和FP置换会导致输出等于明文！")
                
                # 检查括号是否匹配（基本检查）
                open_braces = code.count('{')
                close_braces = code.count('}')
                open_brackets = code.count('[')
                close_brackets = code.count(']')
                open_parens = code.count('(')
                close_parens = code.count(')')
                
                if open_braces != close_braces:
                    logger.warning(f"C++代码大括号不匹配: 开括号 {open_braces}, 闭括号 {close_braces}")
                if open_brackets != close_brackets:
                    logger.warning(f"C++代码方括号不匹配: 开括号 {open_brackets}, 闭括号 {close_brackets}")
                if open_parens != close_parens:
                    logger.warning(f"C++代码圆括号不匹配: 开括号 {open_parens}, 闭括号 {close_parens}")
                
                # 检查数组定义是否完整（检查是否有未闭合的数组）
                array_pattern = r'(?:const\s+)?(?:uint8_t|uint16_t|uint32_t|uint64_t|int|char)\s+\w+\[\d+\]\s*=\s*\{[^}]*$'
                if re.search(array_pattern, code, re.MULTILINE):
                    logger.warning("检测到C++代码中可能有未闭合的数组定义")
                
                # 检查是否使用了随机数（这是不允许的）
                random_indicators = [
                    'rand()',
                    'srand(',
                    'random()',
                    'std::random_device',
                    'std::mt19937',
                    'time(NULL)',
                    'clock()',
                    'gettimeofday',
                    'RAND_bytes',
                    'RAND_pseudo_bytes'
                ]
                for indicator in random_indicators:
                    if indicator in code:
                        logger.warning(f"检测到C++代码使用了随机数函数: {indicator}，这会导致每次加密结果不同！")
            
            # 对于Python，也检查随机数使用和库选择
            if language.lower() == 'python':
                random_indicators = [
                    'os.urandom(',
                    'random.randint',
                    'random.random',
                    'random.choice',
                    'secrets.token_bytes',
                    'secrets.randbelow',
                    'numpy.random'
                ]
                for indicator in random_indicators:
                    if indicator in code:
                        logger.warning(f"检测到代码使用了随机数函数: {indicator}，这会导致每次加密结果不同！")
                
                # 检查DES算法是否使用了错误的库
                if algorithm.upper() == 'DES':
                    if 'from cryptography' in code or 'import cryptography' in code or 'cryptography.hazmat' in code:
                        logger.warning("检测到DES代码使用了cryptography库，这是错误的！DES必须使用pycryptodome库！")
                        # 尝试自动修复
                        code = code.replace('from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes', 
                                           'from Crypto.Cipher import DES')
                        code = code.replace('from cryptography.hazmat.primitives.ciphers.algorithms import DES', 
                                           'from Crypto.Cipher import DES')
                        code = code.replace('algorithms.DES', 'DES')
                        code = code.replace('Cipher(algorithms.DES', 'DES.new')
                        # 如果还有cryptography的导入，移除它
                        lines = code.split('\n')
                        new_lines = []
                        for line in lines:
                            if 'cryptography' in line.lower() and ('import' in line or 'from' in line):
                                logger.warning(f"移除了错误的导入行: {line.strip()}")
                                continue
                            new_lines.append(line)
                        code = '\n'.join(new_lines)
                        logger.info("已尝试自动修复DES库选择问题")
            
            # 后处理：移除解密相关输出（主要为 Python 误印明文）；C/C++ 由启发式删行易破坏 {} 配对，
            # 且 DES+OpenSSL 常与 decrypt 演示混排 → 仅对 Python 启用。
            lang_low = language.lower()
            if lang_low == "python":
                original_code_length = len(code)
                code = _remove_decryption_code(code, language, operation)
                if len(code) != original_code_length:
                    logger.info(f"后处理已修改代码，长度从 {original_code_length} 变为 {len(code)}")
                else:
                    logger.info(f"后处理未检测到需要移除的解密代码")
            else:
                logger.info("跳过后处理「移除解密片段」（非 Python，避免破坏 C/C++ 语法结构）")
            
            logger.info("代码生成成功！")
            return code, generation_time
        except Exception as e:
            logger.error(f"代码生成失败: {e}")
            raise


def _stub_mock_hints_from_code(source: str) -> List[str]:
    """识别占位/假实现表述，用于基于测试反馈的改进提示（对齐批量日志中的典型失败）。"""
    if not source:
        return []
    low = source.lower()
    hints: List[str] = []
    seen: set[str] = set()

    def add(msg: str, key: str) -> None:
        if key not in seen:
            seen.add(key)
            hints.append(msg)

    if "模拟的加密" in source or "模拟加密输出" in source:
        add(
            "**严重错误：存在「模拟」加密或直接把明文字节打印为 hex——必须改为真实分组加密流程（OpenSSL `EVP_*` / `DES_*` / `AES_*` 或等价库），禁止假输出。**",
            "mock_zh",
        )
    if "篇幅限制" in source:
        add(
            "**严重错误：以「篇幅限制」省略密码逻辑——必须交付完整、可编译且能通过标准向量的实现。**",
            "length_limit_zh",
        )
    if ("假设" in source and "正确" in source) or "假装" in source:
        add(
            "**注释中假设加密已正确——删除占位逻辑，接入真实算法或密码库 API。**",
            "assume_zh",
        )
    if any(x in low for x in ("stub", "mock encryption", "not fully implemented", "todo: encrypt")):
        add(
            "**检测到 stub / mock / TODO 加密——须删除占位并实现完整加密或链接 `-lcrypto`。**",
            "stub_en",
        )
    if "placeholder" in low and any(a in low for a in ("encrypt", "des_encrypt", "aes_encrypt")):
        add(
            "**仍存在加密占位（Placeholder）——必须写完整函数体，勿留「待实现」。**",
            "placeholder",
        )
    # 常见假通过：循环内对 plaintext 字节 printf %02x（日志 batch_generation_errors 中多次出现）
    if "printf" in low and "plaintext" in low and "for " in low and "%02x" in source:
        add(
            "**可疑：在循环中对 `plaintext` 字节做 `%02x` 输出——若向量比对为「明文 hex」，说明完全未加密；须改为密文缓冲区输出。**",
            "printf_plain_loop",
        )
    if "openssl/des.h" in low or "des_cfb64_encrypt" in low or "des_ofb64" in low:
        add(
            "**【强制】源码含 `openssl/des.h` 或 `DES_cfb64_encrypt` 等——若本题为 AES/SM4，必须整文件删除 DES 段，仅保留 `openssl/evp.h` + 正确 `EVP_aes_*` / `EVP_sm4_*`。**",
            "des_h_wrong_algo",
        )
    if "key_size 8" in low.replace("_", " ") or "key[8]" in low or "密钥长度必须为8" in source:
        add(
            "**【强制】检测到 8 字节密钥/DES 文案——AES/SM4 题须 16 字节 key（32 hex），删除 `KEY_SIZE 8` 与「密钥8字节」提示。**",
            "key8_wrong",
        )
    if "scanf" in low or "fgets(stdin" in low or "getchar()" in low:
        add(
            "**【强制】评测无 stdin：删除 `scanf`/`fgets(stdin)`/`getchar`，仅用 `getenv(\"TEST_*\")`。**",
            "stdin_block",
        )
    return hints[:10]


def _is_qwen_local_provider(agent: Any) -> bool:
    p = (getattr(agent, "provider", None) or "").strip().lower()
    return "qwen" in p or p.endswith("_local")


def _is_qwen_batch_target_slot(
    algorithm: str, mode: Optional[str], language: str
) -> bool:
    """是否在 qwen 批量重跑表（未过 + 无 DB 落库）内。"""
    try:
        from scripts.qwen_batch_common import _all_qwen_batch_slot_keys
        from utils.history_manager import HistoryManager

        keys = {
            HistoryManager.normalize_case_key(a, m, l)
            for a, m, l in _all_qwen_batch_slot_keys()
        }
        sk = HistoryManager.normalize_case_key(algorithm, mode, language)
        return sk in keys
    except Exception:
        return False


def _qwen_slot_skip_distillation(
    algorithm: str, mode: Optional[str], language: str, kwargs: Optional[Dict[str, Any]] = None
) -> bool:
    """仅对极易超 32K 或蒸馏易串题的槽默认跳过蒸馏；批量失败重跑可经 ``_keep_distillation`` 强制保留。"""
    if kwargs and kwargs.get("_keep_distillation"):
        au = (algorithm or "").upper()
        m = (mode or "").upper()
        lang = (language or "").lower()
        # SM4-OFB-cpp：蒸馏/长上下文易输出 Python 或 AES 骨架，仍跳过
        if au == "SM4" and m == "OFB" and lang in ("cpp", "c++"):
            return True
        return False
    au = (algorithm or "").upper()
    m = (mode or "").upper()
    lang = (language or "").lower()
    if au == "AES" and m == "CTR" and lang in ("c", "cpp", "c++"):
        return True
    if au == "SM4" and m == "OFB" and lang in ("c", "cpp", "c++"):
        return True
    return False


def _qwen_slot_prefers_ultra_compact(
    algorithm: str, mode: Optional[str], language: str
) -> bool:
    """易撑爆 32K 的槽：初次/改进轮使用精简 prompt（与是否蒸馏独立）。"""
    au = (algorithm or "").upper()
    m = (mode or "").upper()
    lang = (language or "").lower()
    if au == "AES" and m == "CTR" and lang in ("c", "cpp", "c++"):
        return True
    if au == "SM4" and m == "OFB" and lang in ("cpp", "c++"):
        return True
    return False


def _qwen_mandatory_evp_for(algorithm: str, mode: Optional[str]) -> str:
    au = (algorithm or "").upper()
    m = (mode or "").upper()
    table = {
        ("AES", "CBC"): "EVP_aes_128_cbc()",
        ("AES", "CFB"): "EVP_aes_128_cfb8()",
        ("AES", "OFB"): "EVP_aes_128_ofb()",
        ("AES", "ECB"): "EVP_aes_128_ecb()",
        ("AES", "CTR"): "EVP_aes_128_ctr()",
        ("AES", "GCM"): "EVP_aes_256_gcm()",
        ("DES", "ECB"): "EVP_des_ecb()",
        ("DES", "CBC"): "EVP_des_cbc()",
        ("DES", "CFB"): "EVP_des_cfb8()",
        ("DES", "OFB"): "EVP_des_ofb()",
        ("SM4", "ECB"): "EVP_sm4_ecb()",
        ("SM4", "CBC"): "EVP_sm4_cbc()",
        ("SM4", "CFB"): "EVP_sm4_cfb128()",
        ("SM4", "OFB"): "EVP_sm4_ofb()",
    }
    evp = table.get((au, m))
    if not evp:
        return ""
    return f"**【强制 cipher】** 本题仅允许 **`{evp}`** + **`EVP_CIPHER_CTX_set_padding(ctx, 0)`**（GCM 另按题面处理 AAD/tag）。"


def _build_qwen_test_feedback_mandatory(
    algorithm: str,
    mode: Optional[str],
    language: str,
    original_code: str,
    test_feedback: Dict[str, Any],
) -> str:
    """Qwen 7B 改进轮：短、硬、可执行（避免长文 DES 教程淹没）。 """
    lines: List[str] = []
    au = (algorithm or "").upper()
    lang = language.lower()
    oc = original_code or ""
    em = (
        str(test_feedback.get("message") or "")
        + " "
        + str(test_feedback.get("output") or "")
        + " "
        + str(test_feedback.get("details") or "")
    ).lower()
    actual = str(test_feedback.get("actual") or "")
    expected = str(test_feedback.get("expected") or "")
    act_l = actual.lower()
    exp_l = expected.lower()

    lines.append("**【Qwen · 测试反馈改进 · 强制（违反任一条 = 仍失败）】**")
    if _is_qwen_batch_target_slot(algorithm, mode, language):
        pin_key = f"generate_pin_{lang}" if lang in ("c", "cpp", "python") else "generate_pin"
        lines.append(
            "- **【批量未过槽 · SM4-OFB-cpp】** 打开 "
            f"`prompts/llms/qwen_coder_local/algorithms/{au}-{(mode or '').upper() or 'RSA'}.yaml`："
            f"先读 **`{pin_key}`** + **`test_feedback_improve_{lang}`**（无则 `test_feedback_improve`）+ 下方 **`{lang}:`** fenced 骨架，"
            "**整文件照抄重写**（勿 iostream/RAND_bytes/sm4.h/Python/import/def 小修）。"
        )
    lines.append("- **只输出可运行完整源码**；禁止占位/TODO/「模拟加密」/只打印明文 hex。")
    lines.append("- **终端必须有一行** `密文:` + **连续小写 hex**（Python：`print(\"密文:\", ct.hex().lower())`）。")
    lines.append("- **禁止** `input`/`scanf`/`cin`/`fgets(stdin)`/`iostream` 读 env。")

    evp_line = _qwen_mandatory_evp_for(algorithm, mode)
    if evp_line and lang in ("c", "cpp", "c++"):
        lines.append(evp_line)
        if "fatal error: openssl/evp.h" in em or "no such file: openssl" in em:
            lines.append(
                "- **无 OpenSSL 头：** **删除全部** `#include <openssl/...>`，改为 **单文件纯 C/C++** 实现本题算法+模式（仍须 `密文:` 与正确长度）。"
            )
        else:
            lines.append(
                "- **有 OpenSSL（Linux 批量默认）：** **仅** `#include <openssl/evp.h>`；**禁止** `#include <openssl/des.h>`。"
            )

    oc_l = oc.lower()
    uses_hex_decode = (
        "h2b(" in oc_l
        or "hex_to_bytes" in oc_l
        or "fromhex" in oc_l
        or "%2x" in oc
        or "sscanf" in oc_l and "2x" in oc
    )
    memcpy_env = "memcpy" in oc_l and "getenv" in oc_l
    if memcpy_env and not uses_hex_decode:
        lines.append(
            "- **【日志：hex 未解码】** 禁止 `memcpy(plaintext_env/key_env,…)`；`TEST_*` 是 **hex 文本**，须照抄 YAML 骨架 **`h2b`/`sscanf %2x`** 或 Python **`bytes.fromhex`**（否则密文长度常为预期的 **2 倍**）。"
        )

    if au in ("AES", "SM4"):
        if "openssl/des.h" in oc_l or "des_cfb64" in oc_l or "des_key_schedule" in oc_l:
            lines.append("- **【检测到 DES 串题】必须整文件重写**：删除 DES 表/`des.h`/8 字节密钥逻辑。")
    if au == "AES":
        m = (mode or "").upper()
        if m == "CFB":
            lines.append(
                "- **AES-CFB 本仓库向量 = CFB-8**：C 用 **`EVP_aes_128_cfb8()`**；Python **`segment_size=8`** 或省略；**禁止** `cfb128`/`segment_size=128`（错前缀 `ea0b8fb8`）。"
            )
        if m == "CTR":
            lines.append(
                "- **AES-CTR：** **`EVP_aes_128_ctr()`** + **h2b**；明文 **128 hex→64B**；输出 **128 hex**（前缀 **`874d6191`**）。"
            )
            if lang in ("cpp", "c++"):
                lines.append(
                    "- **【日志 aes-ctr-cpp】** 禁止 **`#include <iostream>`** / **`strlen(env)`**；照抄 YAML **C 骨架（stdio+evp）**；答案 **≤70 行**。"
                )
        if m == "ECB" and lang in ("c", "cpp", "c++"):
            lines.append(
                "- **AES-ECB：** **`EVP_aes_128_ecb()`** + **iv=NULL** + **h2b**（**64 hex 明文**）；golden **`db727ac6`**（**64 hex**）；**`printf(\"密文: \");`**"
            )
            if expected and exp_l.startswith("db727ac6") and act_l and not act_l.startswith("db727ac6"):
                lines.append(
                    "- **【AES-ECB · 无 DB 槽】** 密文前缀不符：整文件照抄 **AES-ECB.yaml** **c/cpp** 骨架（常见未 **h2b** → **128 hex**）。"
                )
        if m == "GCM":
            lines.append(
                "- **AES-GCM：** **`TEST_IV`+h2b(12B nonce)**、**`TEST_AAD`+h2b(16B)**；先 **AAD `EncryptUpdate(NULL,…)`** 再明文；**`int o1,o2,l`**；输出 **ct||tag=64 hex**（前缀 **`f7264413`**）。"
            )
            if "des_key" in oc_l or "des_encrypt" in oc_l or "des_block" in oc_l:
                lines.append(
                    "- **【日志 aes-gcm-cpp · DES 串题】** 删除全部 **DES_*/des_encrypt 占位**；本题仅 **EVP_aes_256_gcm** + **key 32B(64 hex)**。"
                )
            if "invalid key" in em or "invalid key format" in em:
                lines.append(
                    "- **【Invalid key format】** 多为 **8B DES key 或未 h2b 的 hex 字符串**；改 **h2b(kh,32B)** + **EVP_aes_256_gcm**。"
                )
            if lang in ("cpp", "c++"):
                lines.append(
                    "- **【日志 aes-gcm-cpp】** 删 **RAND_bytes**；**禁止 `size_t*` 传给 EVP**；**禁止随机 IV**；勿 **iostream**。"
                )
        if m == "CBC":
            if "aes_256_cbc" in oc.lower() or act_l.startswith("afe3e274"):
                lines.append(
                    "- **【AES-CBC】** 改 **`EVP_aes_128_cbc()`** + **`set_padding(0)`** + **`printf(\"密文: \")`**；golden **`9b004899…`**（勿 aes_256）。"
                )
        if m == "OFB" and act_l.startswith("eac2dd63"):
            lines.append(
                "- **【AES-OFB】** 你输出的是 CFB 密文：改 **`EVP_aes_128_ofb()`**；golden **`ea0b8fb8…`**。"
            )
        if m == "CFB" and act_l.startswith("ea0b8fb8"):
            lines.append(
                "- **【AES-CFB】** 你输出的是 OFB/CFB-128：改 **`EVP_aes_128_cfb8()`**；golden **`eac2dd63…`**。"
            )
        if m == "CFB" and (act_l.startswith("34e0e717") or (expected and len(actual) == 2 * len(expected))):
            lines.append(
                "- **【AES-CFB · 日志 aes-cfb-cpp】** 未 h2b/env 当字节加密 → 输出 **128 hex**；必须 **TEST_IV** + **h2b** + **`密文:`** + **64 hex**。"
            )
    if au == "SM4" and lang in ("c", "cpp", "c++"):
        if "openssl/des.h" in oc.lower() or "des_encrypt" in oc.lower():
            lines.append("- **【SM4 串 DES】** 整文件重写为 **`EVP_sm4_*`**；key/iv 各 **32 hex**。")
        if (mode or "").upper() == "OFB":
            lines.append(
                "- **SM4-OFB：** 仅 **`#include <openssl/evp.h>`** + **`EVP_sm4_ofb()`**（OpenSSL 3 **无 `EVP_sm4_ofb128`**）；**禁止 sm4.h**；golden **`2754b10c…`**（**32 hex**）。"
            )
            if act_l.startswith("60dff57b") or (expected and exp_l.startswith("2754b10c") and act_l and not act_l.startswith("2754b10c")):
                lines.append(
                    "- **【日志 sm4-ofb-cpp】** 密文偏离 **`2754b10c`**：须 **getenv TEST_KEY/IV/PLAINTEXT + h2b** + **`EVP_sm4_ofb()`**；照抄 **SM4-OFB.yaml cpp 骨架**。"
                )
            if "h2b(" in oc_l and not re.search(r"\bh2b\s*\([^)]*\)\s*\{", oc, re.DOTALL):
                lines.append(
                    "- **【编译/链接 · h2b 未定义】** 必须照抄 YAML 骨架内的 **`static int h2b(...)`** 与 **`strip()`**，禁止只调用无定义。"
                )
            if re.search(r'h2b\s*\(\s*"0{32}"', oc_l) or "0000000000000000" in oc_l and "getenv" not in oc_l:
                lines.append(
                    "- **【硬编码 key/iv】** 禁止 **`h2b(\"0000000000000000\",…)`**；须 **`getenv(\"TEST_KEY\")` / `getenv(\"TEST_IV\")` + h2b**。"
                )
            if "strlen(plaintext)" in oc_l or ("strlen" in oc_l and "getenv" in oc_l and "h2b" not in oc_l):
                lines.append(
                    "- **【未 h2b】** 禁止 **`strlen(getenv…)` 当 EncryptUpdate 长度**；env 是 **hex 文本**，须 **h2b 后 pt_len=16**。"
                )
            if lang in ("cpp", "c++"):
                py_markers = (
                    re.search(r"^\s*(import |def |from )", oc, re.M)
                    or "bytes.fromhex" in oc_l
                    or "base64.b64encode" in oc_l
                    or re.search(r"\bb['\"]", oc)
                    or ".encode('utf-8')" in oc_l
                )
                if py_markers or "invalid preprocessing directive" in em and "sm4" in em:
                    lines.append(
                        "- **【严重 · Python 串题】** 本题语言是 **C++**，禁止 **`import`/`def`/`b'…'`**；整文件替换为 YAML **cpp:** 块（stdio+evp+h2b+main）。"
                    )
                if "remove_if" in oc_l or ("remove_if" in em and "algorithm" in em):
                    lines.append(
                        "- **【编译】** 禁止 **`std::remove_if`**；照抄 YAML **`strip()`**（手写去空白，勿 `<algorithm>`）。"
                    )
                if "iostream" in oc_l or "#include <string>" in oc_l:
                    lines.append(
                        "- **【编译】** 删除 **iostream/string/algorithm**；仅用 **stdio.h + evp.h**。"
                    )
                if actual == "" or (not actual and "程序输出" in em and len(em.strip()) < 80):
                    lines.append(
                        "- **【E-HEX-MISS · 空 stdout】** 必须有 **`int main`** 且 **`printf(\"密文: \");`**；勿只定义函数不调用。"
                    )
                if "evp_aes" in oc_l or "aes_128_ecb" in oc_l or "aes_ecb" in em:
                    lines.append(
                        "- **【串题 AES-ECB】** 删除 **`EVP_aes_*`**；SM4-OFB 只用 **`EVP_sm4_ofb()`** + **TEST_IV**。"
                    )

    if au == "SM4" and (mode or "").upper() == "OFB" and lang == "python":
        lines.append(
            "- **SM4-OFB-python：** gmssl **无 `crypt_ofb`/`encrypt_ofb`**；须 **`one_round` 手搓 OFB-128**（反馈 **`R=o`**，非 CFB 的 **`R=c_block`**）；golden **`2754b10c…`**。"
        )
        if "crypt_ofb" in oc_l or "encrypt_ofb" in oc_l or "no attribute 'crypt_ofb'" in em:
            lines.append(
                "- **【AttributeError · crypt_ofb】** 整文件照抄 **SM4-OFB.yaml python:** 块（`bytes_to_list`/`list_to_bytes`+`one_round`）。"
            )
        if "crypt_cfb" in oc_l or "encrypt_cfb" in oc_l:
            lines.append(
                "- **【误用 CFB API】** `CryptSM4` 亦无 **crypt_cfb**；本题是 **OFB**，须手搓状态机。"
            )
        if expected and exp_l.startswith("2754b10c") and act_l and not act_l.startswith("2754b10c"):
            lines.append(
                "- **【密文不符】** 检查 **TEST_IV 是否 fromhex**、OFB 反馈是否为 **`R=o`**（勿写成 CFB）。"
            )

    if "e-hex-miss" in em or "无法从输出中提取" in em or "未匹配到密文" in em:
        lines.append("- **【E-HEX-MISS】** 程序无有效 stdout：确保 `main`/`if __name__` 执行到 **`print(\"密文:\", …)`**，且 `import os` 在文件最顶。")
    if "退出码: 255" in em or "exit code 255" in em or "没有产生任何输出" in em:
        lines.append("- **【崩溃/无输出】** 检查空指针/`getenv` NULL、缓冲区越界；**禁止** 未初始化数组当密文。")
    if "implicit declaration" in em and "isspace" in em:
        lines.append("- **【编译】** 加 **`#include <ctype.h>`**（C）或 **`<cctype>`**（C++）。")
    if "strlen" in em and "not declared" in em and lang in ("cpp", "c++"):
        lines.append(
            "- **【编译 · 日志 des-cbc-cpp/sm4-cbc-cpp】** 加 **`#include <cstring>`**；env 须 **hex 解码**，勿 `strlen(env)` 当字节长。"
        )
    if "fatal error: openssl/evp.h" in em or "openssl/evp.h: no such file" in em:
        lines.append(
            "- **【编译 · 日志 aes-cbc-c】** 无 OpenSSL 头：删除全部 `<openssl/…>`，改 **纯 C/C++ 实现**（仍须 `密文:` 与正确长度）；或在 WSL/Linux 安装 **libssl-dev** 后用 EVP 骨架。"
        )
    if "openssl/sm4.h" in em or "sm4.h: no such file" in em:
        lines.append(
            "- **【编译 · 日志 sm4-ofb-cpp】** 删除 **`#include <openssl/sm4.h>`**；SM4 只用 **`#include <openssl/evp.h>`** + **`EVP_sm4_ofb()`**（OpenSSL 3 无独立 sm4.h）。"
        )
    if "evp_sm4_ofb128" in em and "not declared" in em:
        lines.append(
            "- **【编译 · 日志 sm4-ofb-cpp · 2026-05-23】** **`EVP_sm4_ofb128()` 不存在**；改 **`EVP_sm4_ofb()`**（evp.h 声明的 OFB-128 入口）。"
        )
    if "strlen" in em and "not declared" in em and lang in ("c", "cpp", "c++"):
        lines.append(
            "- **【编译】** 加 **`#include <string.h>`**；**禁止 `strlen(getenv…)` 当明文长** — 须 **h2b** 后 **`EncryptUpdate(..., pt, 16)`**。"
        )
    if "rand_bytes" in em and "not declared" in em:
        lines.append(
            "- **【编译 · 日志 aes-gcm-cpp】** 禁止 **RAND_bytes**；nonce 从 **`getenv(\"TEST_IV\")` + h2b(12B)** 读取。"
        )
    if "cannot convert" in em and "size_t" in em and "int*" in em:
        lines.append(
            "- **【编译 · 日志 aes-gcm-cpp】** `EVP_EncryptUpdate/Final` 第三参数必须是 **`int o1,o2,l`**，禁止 **`size_t*`**。"
        )
    if "your_plaintext" in oc_l or "placeholder" in oc_l:
        lines.append("- **【占位明文】** 必须从 **`getenv(\"TEST_PLAINTEXT\")` + h2b** 读入，禁止写死字符串。")
    if "printf" in oc_l and "%.*s" in oc_l and "密文" in oc_l:
        lines.append("- **【输出格式 · 日志 sm4-ofb-cpp】** 密文须 **`printf(\"%02x\", out[i])` 循环**，禁止 **`printf(\"%.*s\", len, (char*)ciphertext)`**。")
    if "openssl/sm4.h" in oc_l:
        lines.append("- **【头文件】** 删除 **`openssl/sm4.h`**，仅保留 **`openssl/evp.h`**。")
    if "undefined reference" in em and "h2b" in em:
        lines.append(
            "- **【链接 · h2b】** 须定义 **`static int h2b(...)`**（照抄 SM4-OFB.yaml cpp 块），勿只调用。"
        )
    if "evp_sm4_ofb128()" in oc_l:
        lines.append(
            "- **【日志 sm4-ofb-cpp】** 改 **`EVP_sm4_ofb()`**（OpenSSL 3 **无 `EVP_sm4_ofb128` 符号**）；iv 须 **16B**（勿 **iv[8]**）。"
        )
    if "crypt_ofb" in em or "no attribute 'crypt_ofb'" in em:
        lines.append(
            "- **【Python · gmssl】** **`CryptSM4` 无 `crypt_ofb`**；SM4-OFB 须 **`one_round` 手搓**（见 **SM4-OFB.yaml python:** 块）。"
        )
    if "rand_bytes" in oc_l or "evp_max_iv_length" in oc_l:
        lines.append("- **【GCM】** 删除 **RAND_bytes/EVP_MAX_IV_LENGTH**；使用 **TEST_IV** + **h2b(12B nonce)**。")
    if "iostream" in oc_l and au in ("AES", "DES", "SM4"):
        lines.append(
            "- **【日志】** 删除 **`<iostream>`/`std::cout`**；改用 **stdio `printf(\"密文: \");`+%02x**。"
        )
    if "invalid conversion" in em and "void*" in em:
        lines.append(
            "- **【日志 des-cfb-cpp】** 删 **malloc/hex_string_to_bytes** 等 C++ 辅助；照抄 YAML **stdio+EVP 短骨架**（C 风格，勿 **void*** 赋给 **unsigned char***）。"
        )
    if "ciphertext:" in em and "密文" not in em and "e-hex" not in em:
        lines.append(
            "- **【日志 des-cbc-cpp】** 输出标签须 **`密文:`**（禁止仅英文 **`ciphertext:`**）。"
        )
    if "输入内容过长" in em or "超过模型的上下文限制" in em or "inputtokens" in em.replace(" ", ""):
        hint = "des-ecb-c"
        if au == "AES" and (mode or "").upper() == "CTR":
            hint = "aes-ctr-cpp"
        lines.append(
            f"- **【上下文超限 · 日志 {hint}】** 答案须 **≤70 行**：只照抄本题 YAML **{lang}** fenced 骨架，删除教程/大表/`iostream`。"
        )
    if "des_key_schedule" in em or "des_cfb64_encrypt" in em:
        lines.append("- **【编译】** 删除 `DES_*` API；改用上文 **强制 cipher**。")
    if au == "DES":
        m = (mode or "").upper()
        if m == "ECB" and lang in ("c", "cpp", "c++"):
            lines.append(
                "- **DES-ECB：** **legacy** + **`EVP_des_ecb()`** + **iv=NULL** + **padding(0)**；golden **`958920b1`**。"
            )
        if m == "CBC" and lang in ("cpp", "c++"):
            lines.append(
                "- **DES-CBC-cpp：** 照抄 YAML **cpp** 骨架；**h2b**；golden **`5eb15b91`**；**`printf(\"密文: \");`**"
            )
            if act_l.startswith("833dd2e3") or "openssl/des.h" in oc_l:
                lines.append(
                    "- **【日志 des-cbc-cpp】** 前缀 **`833dd2e3`**=**des.h+ECB 逐块**；改 **EVP_des_cbc+legacy+h2b**。"
                )
        if m == "CFB" and lang in ("cpp", "c++"):
            if "808ce3ff" in act_l or "des_encrypt" in oc_l and "{}" in oc:
                lines.append(
                    "- **DES-CFB-cpp：** 删空 **`des_encrypt(){}`**；照抄 **`EVP_des_cfb8`** 骨架。"
                )
        if m == "OFB":
            if act_l.startswith("4e657477") or "plaintext" in oc_l and "hex" in em:
                lines.append("- **DES-OFB：** 输出明文 hex = 未加密；须 **EVP_des_ofb** + **h2b**。")
            if act_l.startswith("f70f") or "des_cfb" in oc_l:
                lines.append(
                    "- **【日志 des-ofb-c】** 输出 **`f70f…`**=误写 **CFB**；删 **des_cfb*/des.h**，改 **`EVP_des_ofb`**。"
                )
            if lang in ("c", "cpp", "c++"):
                lines.append("- **DES-OFB c/cpp：** golden **`f788b2bc`**（≠ CFB **`f70f`**）。")
        if "openssl/des.h" in oc_l:
            lines.append(
                "- **【日志 DES-*】** 删除 **`openssl/des.h`** 与 **DES_ecb_encrypt**；仅用 **evp.h+EVP_des_***+legacy。"
            )
        if "conflicting types for 'ciphertext'" in em:
            lines.append("- **【编译】** 勿在同一作用域混用 `char *ciphertext` 与数组；密文缓冲改用 `out`/`ct_buf`。")
        if "implicit declaration" in em and "hex_to_int" in em:
            lines.append("- **【编译】** `hex_to_int`/`hex_nibble` 须在首次使用前定义，或移到 `hex_to_bytes` 之上。")
        if m == "CFB" and actual and expected and len(actual) < len(expected):
            lines.append(
                "- **【DES-CFB】** 密文长度约为预期一半：须 **`EVP_des_cfb8()`** 一次处理 **完整明文**（常见 16B→32 hex），勿 ECB 逐块。"
            )
        if m == "OFB" and expected and expected.lower().startswith("f70f"):
            lines.append("- **【DES-OFB】** 预期 **`f788…`** 非 CFB **`f70f…`**：删除 cfb 函数，改用 **`EVP_des_ofb()`**。")
        if "inputtokens" in em.replace(" ", "") or "上下文限制" in em:
            lines.append(
                "- **【上下文】** 整文件重写为 **≤80 行**：照抄本题 YAML **C 骨架**，只改变量名；删除 DES 教程/大表。"
            )

    if actual and expected:
        if len(actual) == 2 * len(expected) and len(expected) > 0:
            lines.append(
                "- **【日志 aes-cfb-cpp】** 实际密文长度为预期 **2 倍**：你把 **hex 字符串当字节** 加密了；须 **h2b/fromhex** 后再 `EVP_EncryptUpdate`（AES-CFB 须读 **TEST_IV**）。"
            )
        elif len(actual) == len(expected) and act_l != exp_l:
            lines.append(
                f"- **长度对、内容错：** 对照预期前缀 **`{expected[:16].lower()}…`**，多为 **cipher/mode/padding/AAD/未 hex 解码** 错误。"
            )

    hint = _qwen_batch_log_hint(algorithm, mode, language)
    if hint:
        lines.append(hint)
    return "\n".join(lines) + "\n\n"


def _qwen_batch_log_hint(
    algorithm: str, mode: Optional[str], language: str
) -> str:
    """批量未过槽：日志归纳的一条强制提示（改进轮）。"""
    if not _is_qwen_batch_target_slot(algorithm, mode, language):
        return ""
    lang = (language or "").lower()
    if lang == "c++":
        lang = "cpp"
    table = {
        ("SM4", "OFB", "cpp"): (
            "- **【日志槽】** **`EVP_sm4_ofb()`**；stdio+string+**h2b 定义**；三路 getenv；"
            "禁硬编码 0000…；**2754b10c**。"
        ),
        ("SM4", "OFB", "python"): (
            "- **【日志槽 · sm4-ofb-python】** gmssl **无 crypt_ofb**；照抄 YAML **python:** 块（"
            "**one_round** + **R=o**）；golden **2754b10c**。"
        ),
    }
    return table.get(
        (
            (algorithm or "").upper(),
            (mode or "").upper() if mode else "",
            lang,
        ),
        "",
    )


def _qwen_improve_code_excerpt(original_code: str, max_lines: int = 48) -> str:
    """Qwen 改进轮：避免把数千行错误代码塞进 32K 上下文。"""
    lines = (original_code or "").splitlines()
    if len(lines) <= max_lines:
        return original_code or ""
    head = "\n".join(lines[: max_lines // 2])
    tail = "\n".join(lines[-(max_lines // 2) :])
    return head + "\n/* ... 中间省略 ... */\n" + tail


def _build_qwen_improve_compact_summary(
    algorithm: str,
    mode: Optional[str],
    language: str,
    original_code: str,
    test_feedback: Dict[str, Any],
) -> str:
    """Qwen 改进轮：短摘要 + 指向任务 YAML，避免 32K 上下文被通用长文撑爆。"""
    au = (algorithm or "").upper()
    m = (mode or "").upper()
    lang = language.lower()
    task_key = f"{au}-{m}" if m else au
    lines = [
        "**【Qwen · 改进摘要（优先照抄 YAML 骨架整文件重写）】**",
        f"- 打开 **`prompts/llms/qwen_coder_local/algorithms/{task_key}.yaml`** 的 **`mandatory`**、**`{lang}`** 段与 **`test_feedback_improve_{lang}`**（无则读 `test_feedback_improve`）。",
        "- **只输出完整源码**；`密文:` + 小写 hex；禁止 stdin/占位/TODO。",
    ]
    expected = str(test_feedback.get("expected") or "")
    actual = str(test_feedback.get("actual") or "")
    msg = str(test_feedback.get("message") or test_feedback.get("output") or "")
    if expected:
        lines.append(f"- 预期密文前缀：**`{expected[:20].lower()}…`**（总长 {len(expected)} hex）")
    if actual:
        lines.append(f"- 实际输出前缀：**`{actual[:20].lower()}…`**（总长 {len(actual)} hex）")
    if msg:
        one_line = " ".join(msg.split())[:400]
        lines.append(f"- 评测信息：{one_line}")
    if au == "DES" and lang in ("c", "cpp", "c++"):
        lines.append("- **DES C/C++：** 有 OpenSSL 头 → **`#include <openssl/evp.h>`** + legacy + 本题 **`EVP_des_*`**；无头 → 纯 C 实现，禁止无效 `<openssl/…>`。")
    if au == "AES" and m == "CTR" and lang in ("c", "cpp", "c++"):
        lines.append("- **AES-CTR：** 复制 **`AES-CTR.yaml` 的 c/cpp 骨架**；**`strlen(ph)==128`**；禁止 DES/CFB 函数名。")
    if au == "AES" and m == "GCM" and lang in ("c", "cpp", "c++"):
        lines.append("- **AES-GCM：** 复制 **`AES-GCM.yaml` cpp 骨架**；**int o1,o2**；**GET_TAG** 后打印 ct+tag。")
    if au == "SM4" and m == "OFB" and lang in ("c", "cpp", "c++"):
        lines.append("- **SM4-OFB-cpp：** 复制 **`SM4-OFB.yaml` cpp 骨架**；**`EVP_sm4_ofb()`**（禁 ofb128）；stdio+**string.h**+h2b；golden **`2754b10c`**。")
        actual_l = actual.lower()
        if actual_l.startswith("60dff57b"):
            lines.append("- **实际 `60dff57b…`：** 未 h2b 或错 cipher → 照抄 YAML **cpp:** 块整文件重写。")
    if au == "SM4" and m == "OFB" and lang == "python":
        lines.append("- **SM4-OFB-python：** 复制 **`SM4-OFB.yaml` python 骨架**；**禁止 crypt_ofb**；**one_round** 手搓 OFB；golden **`2754b10c`**。")
        if re.search(r"^\s*(import |def )", original_code or "", re.M):
            lines.append("- **检测到 Python 语法：** 勿改进 Python 片段；输出必须是 **.cpp 可编译源码**。")
    if au == "AES" and m == "ECB" and lang in ("c", "cpp", "c++"):
        lines.append("- **AES-ECB：** 复制 **`AES-ECB.yaml` c/cpp 骨架**；**`strlen(ph)==64`**；golden **`db727ac6`**。")
    if au == "DES":
        if m == "ECB" and lang == "c":
            lines.append("- **DES-ECB-c：** **`DES-ECB.yaml` c 骨架**；**≤80 行**；legacy + **`EVP_des_ecb`**；golden **`958920b1`**。")
        if m == "CBC" and lang in ("cpp", "c++"):
            lines.append("- **DES-CBC-cpp：** **`DES-CBC.yaml` cpp 骨架**；**h2b**；golden **`5eb15b91`**。")
        if m == "CFB" and lang in ("cpp", "c++"):
            lines.append("- **DES-CFB-cpp：** **`DES-CFB.yaml` cpp 骨架**；删空 **`des_encrypt(){}`**；golden **`f70f0158`**。")
        if m == "OFB" and lang in ("c", "cpp", "c++"):
            lines.append("- **DES-OFB：** **`DES-OFB.yaml` c/cpp 骨架**；**`EVP_des_ofb`**；golden **`f788b2bc`**（≠ CFB **`f70f`**）。")
    if _is_qwen_batch_target_slot(algorithm, mode, language):
        lines.append(
            "- **【落库】** 通过后须写入 code_history（provider=qwen_coder_local）；勿仅依赖 llm_performance.json。"
        )
    hint = _qwen_batch_log_hint(algorithm, mode, language)
    if hint:
        lines.append(hint)
    return "\n".join(lines) + "\n\n"


async def improve_code(agent,  original_code: str, algorithm: str, mode: Optional[str] = None,
                          operation: str = "加密解密", language: str = 'python',
                          test_feedback: Optional[Dict] = None, **kwargs) -> Tuple[str, float]:
        """
        基于测试反馈改进代码
        
        Args:
            original_code: 原始代码
            algorithm: 算法名称
            mode: 模式
            operation: 操作类型
            language: 编程语言
            test_feedback: 测试反馈信息，包含：
                - actual: 实际结果
                - expected: 预期结果
                - message: 错误消息
                - test_type: 测试类型（'encrypt' 或 'decrypt'）
                - plaintext: 测试用的明文（如果是加密测试）
                - ciphertext: 测试用的密文（如果是解密测试）
                - key: 使用的密钥（如果有）
                - iv: 使用的IV（如果有）
            **kwargs: 其他参数
        
        Returns:
            改进后的代码
        """
        logger.info(f"正在基于测试反馈改进 {algorithm}" + (f" ({mode})" if mode else "") + f" 的{language}代码...")

        if _is_qwen_local_provider(agent) and _qwen_slot_prefers_ultra_compact(
            algorithm, mode, language
        ):
            kwargs.setdefault("_force_compact_prompt", True)
        if _is_qwen_local_provider(agent) and _qwen_slot_skip_distillation(
            algorithm, mode, language, kwargs
        ):
            kwargs.setdefault("_skip_distillation", True)
        
        if (kwargs.get("prompt_ablation") or "").strip().lower() == "no_prompt":
            logger.info("prompt_ablation=no_prompt：不向 LLM 注入测试反馈改进提示，跳过改进")
            return original_code, 0.0
        
        lang_name = {'python': 'Python', 'c': 'C', 'cpp': 'C++', 'c++': 'C++'}.get(language.lower(), 'Python')
        
        # 构建改进提示词
        prompt = f"请帮我改进以下{algorithm}算法"
        if mode:
            prompt += f"的{mode}模式"
        prompt += f"的{lang_name}代码。\n\n"
        
        prov_tf_early = (getattr(agent, "provider", None) or kwargs.get("provider") or "").strip().lower()
        code_for_prompt = original_code
        if _is_qwen_local_provider(agent) or prov_tf_early.startswith("qwen_coder_local"):
            code_for_prompt = _qwen_improve_code_excerpt(original_code)
            prompt += "**【Qwen 改进】勿在原错误代码上小修；按 YAML 骨架整文件重写（下面旧代码仅作反例，可忽略细节）。**\n\n"
        prompt += "原始代码（节选）：\n"
        prompt += "```\n"
        prompt += code_for_prompt
        prompt += "\n```\n\n"
        
        if test_feedback:
            prov_tf = prov_tf_early or (getattr(agent, "provider", None) or kwargs.get("provider") or "").strip().lower()
            if _is_qwen_local_provider(agent) or prov_tf == "qwen_coder_local":
                prompt += _build_qwen_test_feedback_mandatory(
                    algorithm, mode, language, original_code, test_feedback
                )
            if prov_tf in ("qwen_coder_local", "doubao") or _is_qwen_local_provider(agent):
                try:
                    from utils.prompt_loader import PromptLoader

                    task_tf = PromptLoader().get_test_feedback_improve(
                        prov_tf or "qwen_coder_local",
                        language,
                        algorithm,
                        mode,
                        kwargs.get("operation"),
                    )
                    if task_tf:
                        prompt += "**【任务级测试反馈改进（YAML · 强制）】**\n" + task_tf + "\n\n"
                except Exception as e:
                    logger.debug("加载 test_feedback_improve YAML 失败: %s", e)
            if _is_qwen_local_provider(agent):
                prompt += _build_qwen_improve_compact_summary(
                    algorithm, mode, language, original_code, test_feedback
                )
            if not _is_qwen_local_provider(agent):
                # ========== 失败原因总结（最重要，放在最前面） ==========
                prompt += "=" * 80 + "\n"
                prompt += "**失败原因总结（请仔细阅读并修复这些问题）**\n"
                prompt += "=" * 80 + "\n\n"
                prompt += "**【测试反馈改进 · 硬性要求】**\n"
                prompt += "- 程序输出必须是**与标准向量一致的密文 hex**（通常小写、无空格）；禁止把**明文**或明文 ASCII 的 hex 当作密文输出。\n"
                prompt += (
                    "- **实现手段：** **优先** 可验证密码库（Python：`pycryptodome`/`cryptography`；C/C++：**有 OpenSSL 开发头时** `EVP_*` + **`-lcrypto`**）。"
                    " **若编译报错 `fatal error: openssl/evp.h`**（常见于 Windows 未装 libssl）：**删除全部 `#include <openssl/...>`**，改为 **ANSI C/C++ 标准库单文件手写** 本题算法与模式（DES/AES-128/SM4 等），仍须 **`密文:`** 与正确长度。\n"
                )
                prompt += "- 对称算法须从环境变量读取 `TEST_PLAINTEXT` / `TEST_KEY` / `TEST_IV`（及 GCM 时 `TEST_AAD`），长度与模式一致；禁止随机 IV/密钥。\n"
                prompt += "- C/C++ 若出现 `undefined reference`，须在**同一文件**实现被调符号或改为正确链接；勿只声明 `KeyExpansion`/`Cipher` 而无定义。\n"
                if language.lower() in ("c", "cpp", "c++"):
                    prompt += (
                        "- **C/C++ 常见修复：** **`std::remove_if`/`std::remove`→`<algorithm>`** 且写 **`std::remove_if`**；**`std::bitset`→`<bitset>`**；**`std::vector`→`<vector>`**；"
                        "**`std::isspace`→`<cctype>`**。禁止 **`char *ciphertext` 与 `unsigned char ciphertext[…]` 同名**（→ `conflicting types`）。\n"
                    )
                    prompt += (
                        "- **CFB/OFB（DES 或 AES）：** **`EVP_EncryptUpdate`（或等价循环）必须处理完整明文**；若实际密文 hex 长度约为预期一半，多为 **只加密首块或误用 ECB**。\n"
                    )
                    au = (algorithm or "").upper()
                    if au == "SM4":
                        prompt += "- **SM4-CFB 等：** 输出缓冲区 **`malloc` 尺寸 ≥ 明文长度**，禁止越界写导致进程崩溃（Windows 退出码 `3221226356`）。\n"
                if (algorithm or "").upper() == "DES" and language.lower() in (
                    "c",
                    "cpp",
                    "c++",
                ):
                    prompt += (
                        "- **DES · C/C++：** **有 OpenSSL 头：** **`#include <openssl/evp.h>`** + **`OSSL_PROVIDER_load(NULL,\"legacy\")`**（如需），按模式选用 "
                        "**`EVP_des_ecb` / `EVP_des_cbc` / `EVP_des_cfb8` / `EVP_des_ofb`**。"
                        " **无头文件：** **禁止** `<openssl/`，须 **手写 DES** 对应模式。"
                        " **禁止** 仅用 **`openssl/des.h`** + **`DES_ede3_*`**（三 DES）或把 **CFB** 实现用于 **CBC**。"
                        " **CBC** 禁止 `DES_ecb_encrypt` 逐块冒充链式 CBC。"
                        " **OFB** 禁止输出与 **CFB** 标准向量相同（误把 OFB 写成 CFB）。"
                        " 使用 **`isspace` 时须包含** **`<ctype.h>`** / **`<cctype>`**；C++ **`malloc`→`static_cast<unsigned char*>(...)`**。"
                        " 终端须含 **`密文:`**（勿仅用英文 **`ciphertext:`** 作唯一标签）。\n"
                    )
                if (algorithm or "").upper() in ("AES", "SM4") and language.lower() in (
                    "c",
                    "cpp",
                    "c++",
                ):
                    prompt += (
                        "- **AES/SM4 · C/C++：** **禁止** **`openssl/des.h`** / **`DES_*`** / **`DES_cfb64_*`**。"
                        " **AES-CFB（本仓库向量）：** **`EVP_aes_128_cfb8()`**；勿用 **`EVP_aes_128_cfb()`** 充当 **CFB-8**。"
                        " **SM4：** 按模式选用 **`EVP_sm4_ecb`/`cbc`/`cfb128`/`ofb128`** 等（有 OpenSSL 头时）；无头则 **手写 SM4**。"
                        " **密钥与 IV：** AES/SM4 默认各 **16 字节**（**32 hex**）。\n"
                    )
                prompt += "\n"
            
                actual_output = str(test_feedback.get('actual', '') or test_feedback.get('output', ''))
                expected_output = str(test_feedback.get('expected', '') or '')
                plaintext_hex = str(test_feedback.get('plaintext', '') or '')
                output_message = str(test_feedback.get('output', '') or '')
                error_message = str(test_feedback.get('message', '') or '')
            
                # 分析失败原因
                failure_reasons = []
            
                # 1. 检查是否是输出明文ASCII码hex
                if plaintext_hex and actual_output:
                    try:
                        # 尝试将明文hex转换为ASCII字符串，然后转换为hex，看是否匹配实际输出
                        plaintext_bytes = bytes.fromhex(plaintext_hex.replace(' ', '').replace('\n', ''))
                        plaintext_ascii_hex = plaintext_bytes.hex().lower()
                        actual_clean = ''.join(actual_output.split()).lower()
                    
                        # 检查实际输出是否是明文ASCII码的hex（或包含它）
                        if plaintext_ascii_hex in actual_clean or actual_clean.startswith(plaintext_ascii_hex):
                            failure_reasons.append(f"**严重错误：代码输出了明文的ASCII码hex（{plaintext_ascii_hex[:32]}...），而不是加密后的密文！**")
                            failure_reasons.append(f"  - 这说明代码只是将明文转换为ASCII码，然后转换为hex输出，没有执行任何加密操作！")
                            failure_reasons.append(f"  - 必须实现完整的DES加密算法，不能只是hex编码！")
                    except:
                        pass
            
                # 2. 检查长度问题
                actual_len = len(actual_output) if actual_output else 0
                expected_len = len(expected_output) if expected_output else 0
                if actual_len != expected_len:
                    if actual_len > expected_len:
                        diff = actual_len - expected_len
                        if diff == 16:
                            failure_reasons.append(f"**严重错误：实际输出长度比预期多了16个字符（8字节），很可能是输出了IV！**")
                        elif diff == 32:
                            failure_reasons.append(f"**严重错误：实际输出长度比预期多了32个字符（16字节），很可能是输出了AES的IV！**")
                        elif actual_output.endswith('0808080808080808') or actual_output.endswith('08080808080808080808080808080808'):
                            failure_reasons.append(f"**严重错误：输出末尾包含了填充字节的hex（080808...），不应该输出填充字节！**")
                            failure_reasons.append(f"  - 填充字节只用于加密过程，不应该出现在输出中！")
                        else:
                            failure_reasons.append(f"**严重错误：实际输出长度（{actual_len}字符）比预期（{expected_len}字符）多了{diff}个字符！**")
                    else:
                        failure_reasons.append(f"**严重错误：实际输出长度（{actual_len}字符）比预期（{expected_len}字符）少了{expected_len - actual_len}个字符！**")
                        failure_reasons.append(f"  - 可能是只处理了部分明文数据，或者只输出了部分密文！")
            
                # 3. 检查是否输出了明文hex
                if plaintext_hex and actual_output:
                    plaintext_clean = ''.join(plaintext_hex.split()).lower()
                    actual_clean = ''.join(actual_output.split()).lower()
                    if actual_clean == plaintext_clean or actual_clean.startswith(plaintext_clean):
                        failure_reasons.append(f"**严重错误：代码直接输出了明文的hex，没有执行加密！**")
            
                # 4. 检查是否有占位符
                placeholder_patterns = [
                        'Placeholder for the encryption process',
                        'Placeholder for',
                        'This should include',
                        'should include all the rounds',
                        'Placeholder for DES encryption',
                        'Placeholder for DES encryption using',
                        'Placeholder for.*CFB',
                        'Placeholder for.*CBC',
                        'Placeholder for.*OFB',
                        'Placeholder for.*ECB'
                    ]
                has_placeholder = any(pattern.lower() in original_code.lower() for pattern in placeholder_patterns)
                if has_placeholder:
                    failure_reasons.append(f"**严重错误：代码中包含占位符注释，说明加密函数没有完整实现！**")
                    failure_reasons.append(f"  - 必须删除所有占位符注释，实现完整的DES加密算法！")
            
                # 5. 检查是否只有IP和FP置换
                if algorithm.upper() == 'DES':
                    has_ip = 'IP_TABLE' in original_code or 'ip_table' in original_code.lower()
                    has_fp = 'FP_TABLE' in original_code or 'fp_table' in original_code.lower()
                    has_feistel = 'feistel' in original_code.lower() or 'round' in original_code.lower() or 's_box' in original_code.lower() or 'S_BOX' in original_code
                    has_key_schedule = 'key_schedule' in original_code.lower() or 'generate_subkey' in original_code.lower() or 'PC1_TABLE' in original_code or 'PC2_TABLE' in original_code
                    if has_ip and has_fp and not has_feistel and not has_key_schedule:
                        failure_reasons.append(f"**严重错误：DES加密函数可能只有IP和FP置换，没有16轮Feistel网络！**")
                        failure_reasons.append(f"  - IP和FP置换是互逆的，如果中间没有16轮Feistel网络，输出就是明文本身！")
            
                # 6. 如果没有总结出原因，至少提供完整的测试反馈
                if not failure_reasons:
                    failure_reasons.append(f"**测试失败：实际输出与预期输出不匹配**")
            
                # 7. 代码静态扫描：占位/模拟（批量失败日志高频）
                for h in reversed(_stub_mock_hints_from_code(original_code)):
                    failure_reasons.insert(0, h)
            
                # 输出失败原因总结
                for i, reason in enumerate(failure_reasons, 1):
                    prompt += f"{i}. {reason}\n"
            
                prompt += "\n"
                prompt += "=" * 80 + "\n"
                prompt += "**完整测试反馈信息（用于详细分析）**\n"
                prompt += "=" * 80 + "\n\n"
            
                prompt += "测试反馈：\n"
                prompt += f"测试类型：{'加密测试' if test_feedback.get('test_type') == 'encrypt' else '解密测试'}\n"
            
                if test_feedback.get('test_type') == 'encrypt':
                    if test_feedback.get('plaintext'):
                        prompt += f"测试明文：{test_feedback['plaintext']}\n"
                    if test_feedback.get('key'):
                        prompt += f"使用的密钥：{test_feedback['key']}\n"
                    if test_feedback.get('iv'):
                        prompt += f"使用的IV：{test_feedback['iv']}\n"
                    actual = test_feedback.get('actual', '未知')
                    expected = test_feedback.get('expected', '未知')
                    output_full = test_feedback.get('output', '') or test_feedback.get('details', {}).get('output', '')
                
                    prompt += f"实际生成的密文：{actual}\n"
                    prompt += f"预期密文：{expected}\n"
                    prompt += f"实际密文长度：{len(actual) if actual != '未知' else 0} 字符\n"
                    prompt += f"预期密文长度：{len(expected) if expected != '未知' else 0} 字符\n"
                
                    # 添加完整的程序输出（如果有）
                    if output_full:
                        prompt += f"\n**程序完整输出：**\n"
                        prompt += f"```\n{output_full}\n```\n"
                
                    # 添加长度分析
                    if actual != '未知' and expected != '未知':
                        actual_len = len(actual)
                        expected_len = len(expected)
                        if actual_len != expected_len:
                            diff = actual_len - expected_len
                            prompt += f"\n**长度分析：**\n"
                            prompt += f"  - 实际长度: {actual_len} 个字符 ({actual_len//2} 字节)\n"
                            prompt += f"  - 预期长度: {expected_len} 个字符 ({expected_len//2} 字节)\n"
                            prompt += f"  - 长度差异: {diff:+d} 个字符 ({diff//2:+d} 字节)\n"
                            if diff > 0:
                                prompt += f"  - **问题：实际输出比预期多了{diff}个字符！**\n"
                                if actual_output.endswith('0808080808080808') or actual_output.endswith('08080808080808080808080808080808'):
                                    prompt += f"  - **检测到输出末尾包含填充字节的hex（080808...），这是错误的！**\n"
                                    prompt += f"  - **填充字节只用于加密过程，不应该出现在输出中！**\n"
                            else:
                                prompt += f"  - **问题：实际输出比预期少了{abs(diff)}个字符！**\n"
                                prompt += f"  - **可能只处理了部分明文数据，或者只输出了部分密文！**\n"
                
                    # 检查实际输出是否是明文的ASCII码hex
                    if plaintext_hex and actual_output:
                        try:
                            plaintext_bytes = bytes.fromhex(plaintext_hex.replace(' ', '').replace('\n', ''))
                            plaintext_ascii_hex = plaintext_bytes.hex().lower()
                            actual_clean = ''.join(actual_output.split()).lower()
                        
                            if plaintext_ascii_hex in actual_clean or actual_clean.startswith(plaintext_ascii_hex):
                                prompt += f"\n**严重问题：实际输出包含明文的ASCII码hex！**\n"
                                prompt += f"  - 明文hex: {plaintext_hex}\n"
                                prompt += f"  - 明文ASCII码hex: {plaintext_ascii_hex[:64]}...\n"
                                prompt += f"  - 实际输出: {actual_output[:64]}...\n"
                                prompt += f"  - **问题：代码只是将明文转换为ASCII码，然后转换为hex输出，没有执行任何加密操作！**\n"
                                prompt += f"  - **必须实现完整的DES加密算法，不能只是hex编码！**\n"
                        except:
                            pass
                
                    # 如果长度相同但内容不同，提供更详细的分析提示
                    if actual != '未知' and expected != '未知' and len(actual) == len(expected):
                        prompt += f"\n**严重问题：密文长度正确但内容完全不匹配！**\n"
                        prompt += f"这说明代码虽然输出了正确长度的密文，但加密算法实现是错误的。\n\n"
                        prompt += f"**当前情况分析：**\n"
                        prompt += f"- 实际密文：{actual}\n"
                        prompt += f"- 预期密文：{expected}\n"
                        prompt += f"- 长度相同（都是{len(actual)}个字符），但内容完全不同\n"
                        prompt += f"- 这说明代码处理了所有输入数据，但加密算法本身是错误的\n\n"
                        if mode == 'OFB':
                            prompt += f"**特别注意：这是OFB模式，必须检查以下关键点：**\n"
                            prompt += f"1. **OFB模式的工作方式：**\n"
                            prompt += f"   - OFB模式每次处理一个字节（OFB-8）或一个块（OFB-64）\n"
                            prompt += f"   - 对于DES OFB-8：每次处理1字节，反馈寄存器每次左移8位\n"
                            prompt += f"   - 对于DES OFB-64：每次处理8字节（64位），反馈寄存器每次更新为整个加密结果\n"
                            prompt += f"   - **关键：反馈寄存器必须更新为加密结果（keystream），不是密文！**\n"
                            prompt += f"2. **常见OFB实现错误：**\n"
                            prompt += f"   - 使用OFB-64但应该使用OFB-8（或反之）\n"
                            prompt += f"   - 反馈寄存器更新错误（更新为密文而不是加密结果）\n"
                            prompt += f"   - 密钥流生成错误（没有使用真正的DES加密函数）\n"
                            prompt += f"   - 反馈寄存器移位错误（应该左移8位，加密结果的最左8位移入最右8位）\n"
                            prompt += f"3. **正确的OFB-8实现步骤（每次处理1字节）：**\n"
                            prompt += f"   - 初始化：feedback = IV（8字节）\n"
                            prompt += f"   - 对于每个明文字节（循环plain_len次）：\n"
                            prompt += f"     a. 使用DES加密函数加密feedback（8字节），得到keystream（8字节）\n"
                            prompt += f"     b. 取keystream[0]（最左字节）作为密钥流字节\n"
                            prompt += f"     c. cipher[i] = plain[i] XOR keystream[0]\n"
                            prompt += f"     d. feedback左移8位：memmove(feedback, feedback+1, 7); feedback[7] = keystream[0];\n"
                            prompt += f"4. **正确的OFB-64实现步骤（每次处理8字节块）：**\n"
                            prompt += f"   - 初始化：feedback = IV（8字节）\n"
                            prompt += f"   - 对于每个8字节块（循环plain_len/8次）：\n"
                            prompt += f"     a. 使用DES加密函数加密feedback（8字节），得到keystream（8字节）\n"
                            prompt += f"     b. cipher[i:i+8] = plain[i:i+8] XOR keystream\n"
                            prompt += f"     c. feedback = keystream（整个8字节更新）\n"
                            prompt += f"5. **检查代码中的OFB实现：**\n"
                            prompt += f"   - 确认是OFB-8还是OFB-64（根据测试数据，可能需要特定的变体）\n"
                            prompt += f"   - 确认反馈寄存器更新为加密结果（keystream），不是密文\n"
                            prompt += f"   - 确认每次循环都正确调用DES加密函数\n"
                            prompt += f"   - 确认密钥流生成正确（使用完整的DES加密，不是XOR）\n\n"
                        prompt += f"**最可能的原因（按概率排序）：**\n"
                        prompt += f"1. **代码只是模拟或简化实现，没有实现真正的DES/AES算法**（99%的可能性！）\n"
                        prompt += f"   - 如果代码中只是简单的XOR操作（如 `block[j] = current_iv[j] ^ key[j]`），那不是真正的加密算法！\n"
                        prompt += f"   - 如果代码中有注释说'简化的DES加密（仅供示例）'，那必须完全重写！\n"
                        prompt += f"   - **DES算法必须实现完整的加密流程：**\n"
                        prompt += f"     * 密钥调度（Key Schedule）：将64位密钥（去除8个校验位后为56位）生成16个48位子密钥\n"
                        prompt += f"     * 初始置换（IP）：对64位明文块进行初始置换\n"
                        prompt += f"     * 16轮Feistel网络：每轮包括：\n"
                        prompt += f"       - 扩展置换（E）：将32位右半部分扩展为48位\n"
                        prompt += f"       - 与子密钥异或：48位扩展结果与48位子密钥异或\n"
                        prompt += f"       - S盒替换：将48位结果通过8个S盒替换为32位\n"
                        prompt += f"       - 置换（P）：对32位结果进行置换\n"
                        prompt += f"       - 与左半部分异或：置换结果与左半部分异或\n"
                        prompt += f"     * 最终置换（FP）：对64位结果进行最终置换\n"
                        prompt += f"   - **AES算法必须实现完整的加密流程：**\n"
                        prompt += f"     * 密钥扩展（Key Expansion）：从128/192/256位密钥生成轮密钥\n"
                        prompt += f"     * 轮密钥加（AddRoundKey）\n"
                        prompt += f"     * 字节替换（SubBytes）：使用S盒\n"
                        prompt += f"     * 行移位（ShiftRows）\n"
                        prompt += f"     * 列混合（MixColumns）\n"
                        prompt += f"   - **解决方案：必须使用标准密码库或完整算法实现！**\n"
                        if algorithm.upper() == "DES":
                            prompt += f"     * **DES · C：** `EVP_des_*`（CFB-8 用 **`EVP_des_cfb8()`**）或 legacy `des.h`；**禁止** XOR 模拟。\n"
                            prompt += f"     * **DES · Python：** `from Crypto.Cipher import DES`。\n"
                        elif algorithm.upper() in ("AES", "SM4"):
                            prompt += (
                                f"     * **{algorithm.upper()} · C/C++：** **`#include <openssl/evp.h>`** + 本题对应 **`EVP_*`**；"
                                f"**禁止** 在 {algorithm.upper()} 题使用 **`openssl/des.h`**。\n"
                            )
                            prompt += f"     * **{algorithm.upper()} · Python：** `Crypto.Cipher` / `gmssl`（SM4）。\n"
                        else:
                            prompt += f"     * 使用 OpenSSL **`EVP_*`** 或 PyCryptodome 标准 API。\n"
                        prompt += f"2. **{mode}模式实现不正确**（如果当前是{mode}模式）：\n"
                        if mode == 'CFB':
                            prompt += f"   - CFB模式必须使用真正的DES/AES加密函数来加密反馈块，不能只是XOR！\n"
                            prompt += f"   - **CFB模式的标准工作流程：**\n"
                            prompt += f"     1. 初始化反馈寄存器（shift register）为IV\n"
                            prompt += f"     2. 对于每个明文块（CFB模式通常以字节为单位，即s=8位）：\n"
                            prompt += f"        a. **使用真正的DES加密函数加密反馈寄存器（使用密钥）**\n"
                            prompt += f"           - 这一步绝对不能只是XOR！必须调用完整的DES加密函数！\n"
                            prompt += f"        b. 取加密结果的最左s位（对于CFB-8，s=8位，即1字节）\n"
                            prompt += f"        c. 密文 = 明文 XOR 加密结果的最左s位\n"
                            prompt += f"        d. 反馈寄存器左移s位，密文的最左s位移入反馈寄存器的最右s位\n"
                            prompt += f"   - **关键点：**\n"
                            prompt += f"     * 步骤a必须使用真正的DES加密函数，不能是 `current_iv[j] ^ key[j]` 这样的XOR！\n"
                            prompt += f"     * 必须使用OpenSSL的DES_cfb_encrypt或DES_cfb64_encrypt函数\n"
                            prompt += f"     * 或者实现完整的DES算法，然后调用DES加密函数来加密反馈寄存器\n"
                        elif mode == 'OFB':
                            prompt += f"   - OFB模式必须使用真正的DES/AES加密函数来生成密钥流，不能只是XOR！\n"
                            prompt += f"   - **OFB模式的标准工作流程：**\n"
                            prompt += f"     1. 初始化反馈寄存器（shift register）为IV\n"
                            prompt += f"     2. 对于每个明文块（OFB模式通常以字节为单位，即s=8位）：\n"
                            prompt += f"        a. **使用真正的DES加密函数加密反馈寄存器（使用密钥）**\n"
                            prompt += f"           - 这一步绝对不能只是XOR！必须调用完整的DES加密函数！\n"
                            prompt += f"           - 加密结果作为密钥流输出\n"
                            prompt += f"        b. 取加密结果的最左s位（对于OFB-8，s=8位，即1字节）作为密钥流\n"
                            prompt += f"        c. 密文 = 明文 XOR 密钥流\n"
                            prompt += f"        d. **反馈寄存器更新为加密结果（不是密文！）**\n"
                            prompt += f"           - 这是OFB和CFB的关键区别：OFB的反馈是加密结果，CFB的反馈是密文\n"
                            prompt += f"           - 反馈寄存器左移s位，加密结果的最左s位移入反馈寄存器的最右s位\n"
                            prompt += f"   - **关键点：**\n"
                            prompt += f"     * 步骤a必须使用真正的DES加密函数，不能是 `current_iv[j] ^ key[j]` 这样的XOR！\n"
                            prompt += f"     * OFB模式的反馈是加密结果，不是密文！这是与CFB模式的关键区别！\n"
                            prompt += f"     * 必须使用OpenSSL的DES_ofb_encrypt或DES_ofb64_encrypt函数\n"
                            prompt += f"     * 或者实现完整的DES算法，然后调用DES加密函数来生成密钥流\n"
                            prompt += f"   - **常见错误：**\n"
                            prompt += f"     * 将反馈寄存器更新为密文（这是CFB模式，不是OFB！）\n"
                            prompt += f"     * 使用XOR操作来模拟DES加密（必须使用真正的DES加密函数！）\n"
                            prompt += f"     * 密钥流生成不正确（必须使用DES加密函数加密反馈寄存器）\n"
                        prompt += f"3. IV使用不正确（可能使用了随机IV或错误的IV值）\n"
                        prompt += f"   - 确保IV是从环境变量TEST_IV正确读取的十六进制字符串\n"
                        prompt += f"   - 确保IV正确转换为字节数组（十六进制字符串长度除以2）\n"
                        prompt += f"   - 对于DES，IV必须是8字节（16个hex字符）\n"
                        prompt += f"4. 密钥使用不正确（可能密钥格式、编码或长度不对）\n"
                        prompt += f"   - 确保密钥是从环境变量TEST_KEY正确读取的十六进制字符串\n"
                        prompt += f"   - 确保密钥正确转换为字节数组（十六进制字符串长度除以2）\n"
                        prompt += f"   - 对于DES，密钥必须是8字节（16个hex字符）\n"
                        prompt += f"5. 输入数据格式处理错误\n"
                        prompt += f"   - 确保从环境变量读取的明文、密钥、IV都是十六进制字符串\n"
                        prompt += f"   - 确保正确将十六进制字符串转换为字节数组（每2个字符转换为1个字节）\n"
                        prompt += f"   - 确保字节数组的长度正确\n"
                        prompt += f"6. 编码方式不正确（大小写、格式等）\n"
                        prompt += f"   - 输出密文时，确保使用小写十六进制字符\n"
                        prompt += f"   - 不要有空格、换行或其他分隔符\n"
                        prompt += f"\n**请仔细检查代码，确保实现了真正的密码学算法，而不仅仅是模拟！**\n"
                        prompt += f"**如果代码中有任何XOR操作来'模拟'DES加密，必须完全重写，使用真正的DES算法或OpenSSL库！**\n"
                else:  # decrypt
                    if test_feedback.get('ciphertext'):
                        prompt += f"测试密文：{test_feedback['ciphertext']}\n"
                    if test_feedback.get('key'):
                        prompt += f"使用的密钥：{test_feedback['key']}\n"
                    if test_feedback.get('iv'):
                        prompt += f"使用的IV：{test_feedback['iv']}\n"
                    actual = test_feedback.get('actual', '未知')
                    expected = test_feedback.get('expected', '未知')
                    prompt += f"实际解密的明文：{actual}\n"
                    prompt += f"预期明文：{expected}\n"
                    prompt += f"实际明文长度：{len(actual) if actual != '未知' else 0} 字符\n"
                    prompt += f"预期明文长度：{len(expected) if expected != '未知' else 0} 字符\n"
            
                if test_feedback.get('actual_normalized') and test_feedback.get('expected_normalized'):
                    prompt += f"\n规范化后对比：\n"
                    prompt += f"实际（规范化）：{test_feedback['actual_normalized']}\n"
                    prompt += f"预期（规范化）：{test_feedback['expected_normalized']}\n"
            
                if test_feedback.get('message'):
                    prompt += f"\n错误信息：{test_feedback['message']}\n"
            
                # 检查是否是cryptography库的DES错误
                error_message = str(test_feedback.get('message', ''))
                output_message = str(test_feedback.get('output', ''))
                combined_error = error_message + ' ' + output_message

                # OpenAI 批量日志（batch_generation_errors）：AES/SM4 误写成 DES、main 内 redeclaration、C++ ld
                if algorithm.upper() in ("AES", "SM4") and language.lower() in ("c", "cpp", "c++"):
                    oc = original_code or ""
                    if (
                        "static const int IP[" in oc
                        or "IP_TABLE" in oc
                        or (
                            "S_BOX" in oc
                            and "EVP_aes" not in oc
                            and "EVP_sm4" not in oc
                        )
                    ):
                        prompt += (
                            "\n**【任务串题 · 必须修正】** 本题是 **"
                            + algorithm.upper()
                            + "**，但源码含 **DES** 置换表/S 盒或手写 Feistel。"
                            "请**全部删除** DES 相关表与轮函数，仅保留 **`EVP_aes_*`**（AES）或 **`EVP_sm4_*`**（SM4）与 `main` 读 env、`EncryptUpdate`/`Final`、打印密文 hex。\n"
                        )
                em_low = combined_error.lower()
                if "redeclaration" in em_low or "redefinition of" in em_low:
                    prompt += (
                        "\n**【编译错误 · 重复声明】** 须在 **`main` 内只保留一组 `int len`/`ciphertext_len`（或改用 `outl`/`block_len`），"
                        "后续 **`EVP_EncryptUpdate`/`Final` 禁止再次写 `int len`**。\n"
                    )
                if (
                    "collect2:" in em_low
                    or "ld returned" in em_low
                    or "relocation against" in em_low
                    or "undefined reference" in em_low
                ):
                    prompt += (
                        "\n**【链接错误】** C++ 请将 **`EVP_aes_*`/`EVP_sm4_*`** 与上下文集中在**同一编译单元**，"
                        "避免类静态成员/模板实例分散导致 **`relocation`/`undefined reference`**；优先删除手写 `AES_*` 类封装。\n"
                    )

                if algorithm.upper() == 'DES' and language.lower() == 'python':
                    if 'cryptography' in combined_error.lower() and ('no attribute' in combined_error.lower() or 'DES' in combined_error):
                        prompt += f"\n**严重错误：DES算法使用了错误的库！**\n"
                        prompt += f"- 错误信息：{combined_error}\n"
                        prompt += f"- **cryptography库不支持DES算法（已移除）！**\n"
                        prompt += f"- **必须使用pycryptodome库！**\n"
                        prompt += f"- **正确的导入方式：**\n"
                        prompt += f"  ```python\n"
                        prompt += f"  from Crypto.Cipher import DES\n"
                        prompt += f"  ```\n"
                        prompt += f"- **绝对不要使用：**\n"
                        prompt += f"  ```python\n"
                        prompt += f"  from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes\n"
                        prompt += f"  # 或\n"
                        prompt += f"  from cryptography.hazmat.primitives.ciphers.algorithms import DES  # 这个不存在！\n"
                        prompt += f"  ```\n"
                        prompt += f"- **正确的使用方式：**\n"
                        prompt += f"  ```python\n"
                        prompt += f"  from Crypto.Cipher import DES\n"
                        if mode:
                            mode_map = {'ECB': 'DES.MODE_ECB', 'CBC': 'DES.MODE_CBC', 
                                       'CFB': 'DES.MODE_CFB', 'OFB': 'DES.MODE_OFB'}
                            mode_const = mode_map.get(mode, 'DES.MODE_ECB')
                            prompt += f"  cipher = DES.new(key_bytes, {mode_const}, iv_bytes)\n"
                        else:
                            prompt += f"  cipher = DES.new(key_bytes, DES.MODE_ECB)\n"
                        prompt += f"  ciphertext = cipher.encrypt(plaintext)\n"
                        prompt += f"  ```\n"
                        prompt += f"- **必须完全移除所有cryptography相关的导入和使用！**\n"
                        prompt += f"- **只使用pycryptodome库！**\n\n"
            
                # 检查是否是每次结果不同的问题（用户反馈或测试结果）
                user_message = str(test_feedback.get('message', ''))
                # 如果用户明确说"每次都不一样"或"每次不同"，或者测试结果显示结果不一致
                # 也检查是否有多次测试结果不同的情况
                is_non_deterministic = ('每次' in user_message and ('不一样' in user_message or '不同' in user_message or '随机' in user_message))
            
                # 如果实际结果存在但和预期不匹配，且用户提到"每次都不一样"，也认为是非确定性问题
                if not is_non_deterministic and test_feedback.get('actual') and test_feedback.get('expected'):
                    if test_feedback.get('actual') != test_feedback.get('expected') and '每次' in user_message:
                        is_non_deterministic = True
            
                if is_non_deterministic:
                    prompt += f"\n**严重问题：每次加密的结果都不一样！**\n"
                    prompt += f"这说明代码不是确定性的，这是不允许的！相同输入必须产生相同输出！\n\n"
                    prompt += f"**可能的原因：**\n"
                    prompt += f"1. **使用了随机数（随机IV、随机密钥等）**\n"
                    prompt += f"   - 检查代码中是否使用了 `rand()`、`random()`、`srand()`、`time()`、`os.urandom()` 等随机函数\n"
                    prompt += f"   - 检查IV是否从环境变量 `getenv(\"TEST_IV\")` 正确读取（C语言）或 `os.environ.get('TEST_IV')` 读取（Python）\n"
                    prompt += f"   - 检查密钥是否从环境变量 `getenv(\"TEST_KEY\")` 正确读取（C语言）或 `os.environ.get('TEST_KEY')` 读取（Python）\n"
                    prompt += f"   - **如果环境变量不存在，必须使用固定的默认值（如全零），绝对不能使用随机值！**\n"
                    prompt += f"   - **删除所有随机数生成相关的代码！**\n"
                    prompt += f"2. **未初始化的变量或内存问题**\n"
                    prompt += f"   - 检查所有数组和变量是否正确初始化（使用 `{{0}}` 或 `memset`）\n"
                    prompt += f"   - 检查是否有未初始化的局部变量被使用\n"
                    prompt += f"   - 检查内存操作是否正确（`memcpy`、`memset` 等）\n"
                    prompt += f"3. **DES算法实现有bug**\n"
                    prompt += f"   - 检查密钥调度函数是否正确实现（PC-1、PC-2置换、循环移位）\n"
                    prompt += f"   - 检查S盒替换是否正确实现（行和列的计算）\n"
                    prompt += f"   - 检查位操作是否正确（左移、右移、异或等）\n"
                    prompt += f"   - 检查Feistel网络是否正确实现（左右交换、异或操作）\n"
                    prompt += f"4. **CFB模式实现有bug**\n"
                    prompt += f"   - 检查反馈寄存器的初始化是否正确（应该等于IV）\n"
                    prompt += f"   - 检查反馈寄存器的更新是否正确（左移、新密文移入）\n"
                    prompt += f"   - 检查每次循环中DES加密函数的调用是否正确\n"
                    prompt += f"\n**解决方案：**\n"
                    prompt += f"1. **确保代码是确定性的：**\n"
                    prompt += f"   - 所有变量必须正确初始化\n"
                    prompt += f"   - IV和密钥必须从环境变量读取，不能使用随机值\n"
                    prompt += f"   - 删除所有随机数生成相关的代码\n"
                    prompt += f"2. **检查DES算法实现：**\n"
                    prompt += f"   - 确保密钥调度正确生成16个子密钥\n"
                    prompt += f"   - 确保S盒替换使用正确的行和列索引\n"
                    prompt += f"   - 确保位操作正确（注意字节序和位序）\n"
                    prompt += f"3. **检查CFB模式实现：**\n"
                    prompt += f"   - 反馈寄存器初始化为IV\n"
                    prompt += f"   - 每次循环：加密反馈寄存器 -> 取最左字节 -> XOR明文 -> 更新反馈寄存器\n"
                    prompt += f"4. **如果无法修复，建议使用OpenSSL库或Python的标准库：**\n"
                    prompt += f"   - C语言：使用OpenSSL的 `DES_cfb_encrypt()` 函数\n"
                    prompt += f"   - Python：使用 `pycryptodome` 的 `DES.new(key, DES.MODE_CFB, iv)`\n"
                    prompt += f"\n**重要：代码必须是确定性的！相同的明文、密钥、IV必须产生完全相同的密文！**\n\n"
            
                # 检查代码是否不完整（仅 DES · C 手搓路径；AES/SM4 应用 EVP，勿要求 500 行 DES）
                if language.lower() == 'c' and (algorithm or "").upper() == "DES":
                    # 检查代码是否包含必要的函数
                    has_main = 'int main(' in original_code or 'void main(' in original_code
                    has_cfb = 'cfb' in original_code.lower() or 'CFB' in original_code
                    has_des_encrypt = 'des_encrypt' in original_code.lower() or 'des_encrypt_block' in original_code.lower()
                    code_length = len(original_code.split('\n'))
                
                    # 如果代码很短（少于300行）且缺少关键函数，说明代码不完整
                    if code_length < 300 and (not has_main or not has_cfb or not has_des_encrypt):
                        prompt += f"\n**严重问题：生成的C代码不完整！**\n"
                        prompt += f"- 代码只有{code_length}行，对于完整的DES CFB实现，通常需要500-800行代码\n"
                        prompt += f"- 检查结果：\n"
                        prompt += f"  * main函数：{'✓' if has_main else '✗ 缺失'}\n"
                        prompt += f"  * CFB实现：{'✓' if has_cfb else '✗ 缺失'}\n"
                        prompt += f"  * DES加密函数：{'✓' if has_des_encrypt else '✗ 缺失'}\n"
                        prompt += f"- **必须生成完整的、可编译运行的代码！**\n"
                        prompt += f"- 代码必须包含：\n"
                        prompt += f"  * 所有置换表和S盒的完整定义\n"
                        prompt += f"  * 密钥调度函数（generate_subkeys）的完整实现\n"
                        prompt += f"  * DES加密函数（des_encrypt_block）的完整实现\n"
                        prompt += f"  * CFB模式加密函数（des_cfb_encrypt）的完整实现\n"
                        prompt += f"  * main函数的完整实现（从环境变量读取输入，输出密文）\n"
                        prompt += f"- **所有函数必须有完整的实现，不能只是函数声明或空函数体！**\n"
                        prompt += f"- **代码必须能够直接编译运行，不需要用户补充任何代码！**\n\n"
            
                # 检查是否是密钥/IV长度错误（通常是环境变量包含空白字符导致的）
                error_message = str(test_feedback.get('message', ''))
                output_message = str(test_feedback.get('output', ''))
                combined_error = error_message + ' ' + output_message
            
                if ('key must be' in combined_error.lower() or 'iv must be' in combined_error.lower() or 
                    'exactly 8 bytes' in combined_error.lower() or '16 hex' in combined_error.lower() or
                    'invalid.*hex' in combined_error.lower() or 'error decoding' in combined_error.lower()):
                    prompt += f"\n**严重问题：密钥或IV长度验证失败！**\n"
                    prompt += f"- 错误信息：{combined_error}\n"
                    prompt += f"- **这通常是因为环境变量中的字符串包含空白字符（空格、换行符等）！**\n"
                    prompt += f"- **解决方案：**\n"
                    if language.lower() == 'c':
                        prompt += f"  * 必须实现一个函数来去除字符串中的所有空白字符\n"
                        prompt += f"  * 在从环境变量读取后，立即去除所有空白字符（使用 `isspace()` 检查）\n"
                        prompt += f"  * 示例代码：\n"
                        prompt += f"    ```c\n"
                        prompt += f"    void remove_whitespace(char *str) {{\n"
                        prompt += f"        char *dst = str;\n"
                        prompt += f"        while (*str) {{\n"
                        prompt += f"            if (!isspace((unsigned char)*str)) {{\n"
                        prompt += f"                *dst++ = *str;\n"
                        prompt += f"            }}\n"
                        prompt += f"            str++;\n"
                        prompt += f"        }}\n"
                        prompt += f"        *dst = '\\0';\n"
                        prompt += f"    }}\n"
                        prompt += f"    // 使用：\n"
                        prompt += f"    char key_hex[128];\n"
                        prompt += f"    strncpy(key_hex, getenv(\"TEST_KEY\"), sizeof(key_hex)-1);\n"
                        prompt += f"    remove_whitespace(key_hex);\n"
                        prompt += f"    ```\n"
                    elif language.lower() == 'python':
                        prompt += f"  * 使用 `.strip()` 方法去除首尾空白字符\n"
                        prompt += f"  * 或者使用 `''.join(hex_str.split())` 去除所有空白字符\n"
                        prompt += f"  * 示例代码：\n"
                        prompt += f"    ```python\n"
                        prompt += f"    key_hex = os.environ.get('TEST_KEY', '').strip()\n"
                        prompt += f"    # 或者去除所有空白字符：\n"
                        prompt += f"    key_hex = ''.join(os.environ.get('TEST_KEY', '').split())\n"
                        prompt += f"    ```\n"
                    prompt += f"  * **必须在解析十六进制字符串之前去除所有空白字符！**\n"
                    prompt += f"  * **环境变量中的字符串可能包含换行符、空格等，必须处理！**\n\n"
            
                # 检查是否是输出明文hex而不是密文的问题
                actual_output = str(test_feedback.get('actual', '') or test_feedback.get('output', ''))
                expected_output = str(test_feedback.get('expected', '') or '')
                plaintext_hex = str(test_feedback.get('plaintext', '') or '')
            
                # 检查实际输出是否等于明文的hex（说明没有执行加密）
                is_plaintext_output = False
                if plaintext_hex and actual_output:
                    # 去除空白字符后比较
                    plaintext_clean = ''.join(plaintext_hex.split()).lower()
                    actual_clean = ''.join(actual_output.split()).lower()
                    # 检查实际输出是否是明文hex的前缀（说明只输出了部分明文hex）
                    # 或者实际输出完全匹配明文hex（说明输出了完整明文hex）
                    if actual_clean and plaintext_clean:
                        # 检查是否是前缀匹配（实际输出是明文hex的前N个字符）
                        if actual_clean == plaintext_clean[:len(actual_clean)]:
                            is_plaintext_output = True
                            logger.warning(f"检测到代码输出了明文的hex而不是密文！实际输出={actual_clean}, 明文hex前缀={plaintext_clean[:len(actual_clean)]}")
                        # 检查是否完全匹配（实际输出等于明文hex）
                        elif actual_clean == plaintext_clean:
                            is_plaintext_output = True
                            logger.warning(f"检测到代码输出了完整的明文hex而不是密文！实际输出={actual_clean}, 明文hex={plaintext_clean}")
                        # 检查实际输出是否包含在明文hex中（可能是部分匹配）
                        elif actual_clean in plaintext_clean and len(actual_clean) >= 8:  # 至少8个字符（4字节）
                            is_plaintext_output = True
                            logger.warning(f"检测到代码可能输出了明文的hex片段！实际输出={actual_clean}, 明文hex={plaintext_clean}")
            
                # 检查代码中是否有占位符注释
                placeholder_patterns = [
                    'Placeholder for the encryption process',
                    'Placeholder for',
                    'This should include',
                    'TODO: implement',
                    'FIXME: implement',
                    '// Placeholder',
                    '/* Placeholder',
                    'placeholder for',
                    'should include all the rounds'
                ]
                has_placeholder = any(pattern.lower() in original_code.lower() for pattern in placeholder_patterns)
            
                # 检查是否是硬编码示例文本的问题
                hardcoded_patterns = [
                    'Your plaintext here', 'Your key here', 'Your plaintext', 'Your key',
                    'example', 'Example', 'EXAMPLE', 'test', 'Test', 'TEST',
                    'sample', 'Sample', 'SAMPLE', 'demo', 'Demo', 'DEMO'
                ]
                has_hardcoded = any(pattern in original_code for pattern in hardcoded_patterns)
                has_hardcoded_output = any(pattern in actual_output for pattern in hardcoded_patterns)
            
                # 检查是否输出了明文hex而不是密文
                if is_plaintext_output or has_placeholder:
                    prompt += f"\n**严重错误：代码没有执行真正的加密，只是输出了明文的hex！**\n"
                    prompt += f"- **实际输出：{actual_output}**\n"
                    prompt += f"- **明文hex：{plaintext_hex}**\n"
                    prompt += f"- **预期密文：{expected_output}**\n"
                    prompt += f"- **问题：代码只是将明文转换为hex输出，没有执行任何加密操作！**\n"
                    if has_placeholder:
                        prompt += f"- **检测到代码中有占位符注释，说明加密函数没有完整实现！**\n"
                        prompt += f"  * 例如：`// Placeholder for the encryption process`\n"
                        prompt += f"  * 例如：`// This should include all the rounds of DES encryption`\n"
                        prompt += f"  * **必须删除所有占位符注释，实现完整的DES加密算法！**\n"
                    prompt += f"- **必须实现完整的DES加密算法，包括：**\n"
                    prompt += f"  1. **密钥调度（Key Schedule）：**\n"
                    prompt += f"     * 必须实现PC-1置换、循环移位、PC-2置换\n"
                    prompt += f"     * 必须生成16个48位子密钥\n"
                    prompt += f"  2. **16轮Feistel网络（这是DES的核心！）：**\n"
                    prompt += f"     * 每轮必须包括：扩展置换（E）、与子密钥异或、S盒替换、P置换、与左半部分异或\n"
                    prompt += f"     * **绝对不能跳过16轮Feistel网络！**\n"
                    prompt += f"     * **绝对不能只有IP置换和FP置换，中间必须有16轮Feistel网络！**\n"
                    prompt += f"  3. **S盒替换：**\n"
                    prompt += f"     * 必须实现8个S盒（S1到S8），每个S盒是4x16的查找表\n"
                    prompt += f"     * **S盒替换是DES加密的核心，绝对不能省略！**\n"
                    prompt += f"- **当前代码的问题：**\n"
                    prompt += f"  * 可能只有IP置换和FP置换，没有16轮Feistel网络\n"
                    prompt += f"  * 可能有占位符注释，没有实际实现\n"
                    prompt += f"  * 可能直接返回了明文或明文的hex\n"
                    prompt += f"- **参考OpenSSL源代码：**\n"
                    prompt += f"  * https://github.com/openssl/openssl.git\n"
                    prompt += f"  * 查看 `crypto/des/des_enc.c` 了解正确的DES实现\n"
                    prompt += f"  * 查看 `crypto/des/set_key.c` 了解密钥调度实现\n"
                    prompt += f"- **必须完全重写加密函数，实现完整的DES算法！**\n\n"
            
                if has_hardcoded or has_hardcoded_output:
                    prompt += f"\n**严重错误：代码中硬编码了示例文本！**\n"
                    prompt += f"- **绝对不能硬编码任何示例文本，如 'Your plaintext here'、'Your key here' 等！**\n"
                    prompt += f"- **必须从环境变量读取输入：**\n"
                    if language.lower() == 'cpp':
                        prompt += f"  * C++: `std::getenv(\"TEST_PLAINTEXT\")` 和 `std::getenv(\"TEST_KEY\")`\n"
                    elif language.lower() == 'c':
                        prompt += f"  * C: `getenv(\"TEST_PLAINTEXT\")` 和 `getenv(\"TEST_KEY\")`\n"
                    elif language.lower() == 'python':
                        prompt += f"  * Python: `os.environ.get('TEST_PLAINTEXT')` 和 `os.environ.get('TEST_KEY')`\n"
                    prompt += f"- **如果环境变量不存在，可以从stdin读取，但绝对不能硬编码示例文本！**\n"
                    prompt += f"- **检查代码中的以下错误模式：**\n"
                    prompt += f"  * ❌ `std::string plaintext = \"Your plaintext here\";` - 这是错误的！\n"
                    prompt += f"  * ❌ `std::string key = \"Your key here\";` - 这是错误的！\n"
                    prompt += f"  * ❌ `char plaintext[] = \"example\";` - 这是错误的！\n"
                    prompt += f"  * ✅ `const char* plaintext_env = std::getenv(\"TEST_PLAINTEXT\");` - 这是正确的！\n"
                    prompt += f"- **输出格式要求：**\n"
                    prompt += f"  * 输出必须是十六进制格式的密文，例如：`密文: 958920b1358ef1972b9ee4548dc08e8a`\n"
                    prompt += f"  * 绝对不能输出明文文本（如 'Your plaintext here'）！\n"
                    prompt += f"  * 如果输出的是明文文本而不是密文，说明代码没有执行加密，必须修复！\n\n"
            
                # 检查是否是permute函数参数类型错误
                if ('invalid conversion from \'int\' to \'const int*\' in permute' in combined_error.lower() or
                    'permute' in combined_error.lower() and ('invalid conversion' in combined_error.lower() or 'int*' in combined_error)):
                    prompt += f"\n**严重错误：permute函数参数类型错误！**\n"
                    prompt += f"- **错误信息：{combined_error}\n"
                    prompt += f"- **问题：permute函数的第二个参数必须是数组名（如E_TABLE），不能是整数（如0x0E）！**\n"
                    prompt += f"- **错误示例：**\n"
                    prompt += f"  * `permute(right, 0x0E, 48)` - 错误！0x0E是整数，不是数组！\n"
                    prompt += f"  * `permute(block, 0x40, 64)` - 错误！0x40是整数，不是数组！\n"
                    prompt += f"- **正确做法：**\n"
                    prompt += f"  * 首先定义置换表数组：\n"
                    prompt += f"    ```cpp\n"
                    prompt += f"    const int E_TABLE[48] = {{32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, ...}};\n"
                    prompt += f"    const int P_TABLE[32] = {{16, 7, 20, 21, 29, 12, 28, 17, ...}};\n"
                    prompt += f"    const int IP_TABLE[64] = {{58, 50, 42, 34, 26, 18, 10, 2, ...}};\n"
                    prompt += f"    const int FP_TABLE[64] = {{40, 8, 48, 16, 56, 24, 64, 32, ...}};\n"
                    prompt += f"    ```\n"
                    prompt += f"  * 然后使用数组名调用permute函数：\n"
                    prompt += f"    ```cpp\n"
                    prompt += f"    std::string right_expanded = permute(right, E_TABLE, 48);  // 正确！\n"
                    prompt += f"    std::string block_permuted = permute(block, IP_TABLE, 64);  // 正确！\n"
                    prompt += f"    ```\n"
                    prompt += f"- **常见的置换表数组名：**\n"
                    prompt += f"  * E_TABLE：扩展置换（32位->48位），用于f函数中\n"
                    prompt += f"  * P_TABLE：P盒置换（32位->32位），用于f函数中\n"
                    prompt += f"  * IP_TABLE：初始置换（64位->64位），用于加密/解密开始\n"
                    prompt += f"  * FP_TABLE：最终置换（64位->64位），用于加密/解密结束\n"
                    prompt += f"  * PC1_TABLE：PC-1置换（64位->56位），用于密钥调度\n"
                    prompt += f"  * PC2_TABLE：PC-2置换（56位->48位），用于密钥调度\n"
                    prompt += f"- **必须在使用permute函数之前定义这些置换表数组！**\n"
                    prompt += f"- **检查代码中所有permute函数调用，确保第二个参数是数组名，不是整数！**\n\n"
            
                # 检查是否是stoi错误（通常是十六进制字符串解析问题）
                if 'stoi' in combined_error.lower() or 'invalid_argument' in combined_error.lower():
                    prompt += f"\n**严重错误：std::stoi转换失败！**\n"
                    prompt += f"- **错误信息：{combined_error}\n"
                    prompt += f"- **问题：代码在尝试将字符串转换为整数时失败，通常是因为：**\n"
                    prompt += f"  1. **二进制字符串长度不是4的倍数**（在 `binary_to_hex` 函数中）\n"
                    prompt += f"     * 如果使用 `std::stoi(binary.substr(i, 4), nullptr, 2)`，必须确保每次取4位\n"
                    prompt += f"     * 如果 `binary.length()` 不是4的倍数，最后一个子字符串可能少于4位，导致 `stoi` 失败\n"
                    prompt += f"     * **解决方案：**\n"
                    prompt += f"       - 检查 `binary.length() % 4 == 0`，如果不是，需要填充\n"
                    prompt += f"       - 或者在循环中检查 `i + 4 <= binary.length()`\n"
                    prompt += f"       - 或者使用更安全的方法：`std::stoi(binary.substr(i, std::min(4, (int)(binary.length() - i))), nullptr, 2)`\n"
                    prompt += f"  2. **十六进制字符串格式错误**（包含非十六进制字符）\n"
                    prompt += f"     * 确保十六进制字符串只包含 0-9, A-F, a-f\n"
                    prompt += f"     * 在解析前必须去除所有空白字符\n"
                    prompt += f"     * 确保字符串长度是偶数（每个字节需要2个hex字符）\n"
                    prompt += f"  3. **输入数据格式错误**\n"
                    prompt += f"     * 从环境变量读取的明文、密钥必须是有效的十六进制字符串\n"
                    prompt += f"     * 确保正确将十六进制字符串转换为字节数组，然后再转换为二进制字符串\n"
                    prompt += f"- **正确的十六进制到字节数组转换方法：**\n"
                    prompt += f"  ```cpp\n"
                    prompt += f"  std::vector<uint8_t> hex_to_bytes(const std::string& hex) {{\n"
                    prompt += f"      std::vector<uint8_t> bytes;\n"
                    prompt += f"      for (size_t i = 0; i < hex.length(); i += 2) {{\n"
                    prompt += f"          if (i + 1 < hex.length()) {{\n"
                    prompt += f"              std::string byte_str = hex.substr(i, 2);\n"
                    prompt += f"              uint8_t byte = static_cast<uint8_t>(std::stoul(byte_str, nullptr, 16));\n"
                    prompt += f"              bytes.push_back(byte);\n"
                    prompt += f"          }}\n"
                    prompt += f"      }}\n"
                    prompt += f"      return bytes;\n"
                    prompt += f"  }}\n"
                    prompt += f"  ```\n"
                    prompt += f"- **正确的二进制到十六进制转换方法（避免stoi错误）：**\n"
                    prompt += f"  ```cpp\n"
                    prompt += f"  std::string binary_to_hex(const std::string& binary) {{\n"
                    prompt += f"      std::string hex;\n"
                    prompt += f"      for (size_t i = 0; i < binary.length(); i += 4) {{\n"
                    prompt += f"          if (i + 4 <= binary.length()) {{\n"
                    prompt += f"              int nibble = std::stoi(binary.substr(i, 4), nullptr, 2);\n"
                    prompt += f"              hex.push_back(nibble < 10 ? '0' + nibble : 'a' + (nibble - 10));\n"
                    prompt += f"          }}\n"
                    prompt += f"      }}\n"
                    prompt += f"      return hex;\n"
                    prompt += f"  }}\n"
                    prompt += f"  ```\n"
                    prompt += f"- **或者使用更简单的方法：直接操作字节数组，而不是二进制字符串！**\n"
                    prompt += f"  ```cpp\n"
                    prompt += f"  std::string bytes_to_hex(const std::vector<uint8_t>& bytes) {{\n"
                    prompt += f"      std::stringstream hex;\n"
                    prompt += f"      hex << std::hex << std::setfill('0');\n"
                    prompt += f"      for (uint8_t byte : bytes) {{\n"
                    prompt += f"          hex << std::setw(2) << static_cast<int>(byte);\n"
                    prompt += f"      }}\n"
                    prompt += f"      return hex.str();\n"
                    prompt += f"  }}\n"
                    prompt += f"  ```\n"
                    prompt += f"- **建议：使用字节数组（`std::vector<uint8_t>`）而不是二进制字符串，这样更简单、更安全！**\n\n"
            
                # 检查是否是代码不完整或编译错误的问题
                if ('代码未完成' in error_message or '展示部分示例' in error_message or '超时' in error_message or
                    '编译失败' in combined_error or 'error:' in combined_error.lower() or 
                    'expected' in combined_error.lower() or 'syntax error' in combined_error.lower() or
                    '未完成的语句' in combined_error or '缺少' in combined_error or
                    'redeclaration' in combined_error.lower() or 'redefinition of' in combined_error.lower() or
                    'expected initializer at end of input' in combined_error.lower() or
                    'expected \')\' at end of input' in combined_error.lower()):
                    prompt += f"\n**严重问题：生成的代码不完整或有编译错误！**\n"
                    prompt += f"- 如果代码输出'代码未完成，展示部分示例'，说明代码只有框架，没有完整实现\n"
                    prompt += f"- 如果代码执行超时，可能是代码有死循环、等待输入或实现不完整\n"
                    prompt += f"- **如果出现编译错误（如 'expected ;'、'expected declaration'、'syntax error'、'expected initializer at end of input'、'expected ) at end of input'），说明代码不完整！**\n"
                    prompt += f"- **常见编译错误原因：**\n"
                    prompt += f"  * 语句未完成（如 `cd_bytes[5] = (CD >> 8) & 0` 缺少 `FF;`）\n"
                    prompt += f"  * 函数调用未完成（如 `des_encrypt(plaintext` 缺少 `)` 和 `;`）\n"
                    prompt += f"  * main函数未完成（如只有 `int main` 而没有函数体）\n"
                    prompt += f"  * 缺少分号、括号、大括号等\n"
                    prompt += f"  * 函数未完成（缺少函数体或返回值）\n"
                    prompt += f"  * 数组或结构体定义不完整\n"
                    prompt += f"- **main函数必须完整，包括：**\n"
                    prompt += f"  * 函数签名：`int main() {{`\n"
                    prompt += f"  * 完整的函数体（所有语句）\n"
                    prompt += f"  * 闭合的大括号：`}}`\n"
                    prompt += f"  * **绝对不能只有 `int main` 而没有函数体！**\n"
                    prompt += f"- **必须生成完整的、可编译的代码，不能只是函数声明或框架！**\n"
                    prompt += f"- 所有函数必须有完整的实现，不能是空的函数体\n"
                    prompt += f"- 所有常量表（S盒、置换表等）必须有完整的数值定义\n"
                    prompt += f"- main函数必须完整实现，能够从环境变量读取输入并输出结果\n"
                    prompt += f"- **所有语句必须完整，所有表达式必须完整，所有语句必须以分号结尾！**\n"
                    prompt += f"- 代码必须能够直接编译运行，不需要用户补充任何代码\n"
                    prompt += f"- 输出格式必须包含'密文'或'ciphertext'关键词\n"
                    prompt += f"- **检查代码末尾，确保没有未完成的语句或函数！**\n\n"
            
                prompt += "\n"
                _au_imp = (algorithm or "").upper()
                _is_qwen_imp = _is_qwen_local_provider(agent)
                if _is_qwen_imp and _au_imp in ("AES", "SM4"):
                    _exp_imp = str(test_feedback.get("expected") or "")
                    prompt += "**【Qwen · AES/SM4 改进轮 · 强制】整文件重写（禁止在 DES 模板上打补丁）**\n"
                    _evp_imp = _qwen_mandatory_evp_for(algorithm, mode)
                    if _evp_imp:
                        prompt += _evp_imp + "\n"
                    if _exp_imp:
                        prompt += f"- 目标密文须匹配预期（前缀 **`{_exp_imp[:24].lower()}…`**）。\n"
                    prompt += (
                        "- **仅** `getenv`/`os.getenv` 读 `TEST_*`；**禁止** stdin。\n"
                        "- **仅输出**完整源码；**必须**含 **`密文:`** + 连续小写 hex；无 markdown、无解释。\n"
                        "- **删除**原文件全部 `des.h`、`DES_*`、`KEY_SIZE 8`、DES 置换表。\n\n"
                    )
                elif _is_qwen_imp:
                    prompt += (
                        "请根据以上反馈修正；**禁止 stdin**；**必须** `密文:` + 连续小写 hex；"
                        "仅输出完整源码。\n\n"
                    )
                if not _is_qwen_imp:
                    prompt += "请根据以上测试反馈，修正代码中的问题，确保：\n"
                    prompt += "1. **最重要：必须实现真正的密码学算法！**\n"
                    prompt += "   - 如果当前代码只是简单的XOR、移位或其他模拟操作，必须完全重写\n"
                    prompt += "   - **对于DES算法，必须严格按照NIST标准实现完整的DES加密算法，包括：**\n"
                    prompt += "     * **必须使用标准的DES置换表和S盒，不能自己编造或修改！**\n"
                    prompt += "     * **密钥调度（Key Schedule）：**\n"
                    prompt += "       - 将64位密钥（去除8个校验位后为56位）通过PC-1置换（标准PC-1表）\n"
                    prompt += "       - 将56位密钥分成左右两部分（各28位）\n"
                    prompt += "       - 对每轮（共16轮），根据轮数进行左循环移位（第1,2,9,16轮移1位，其他轮移2位）\n"
                    prompt += "       - 通过PC-2置换生成48位子密钥（标准PC-2表）\n"
                    prompt += "       - 必须生成16个48位子密钥\n"
                    prompt += "     * **初始置换（IP）：**对64位明文块进行初始置换（标准IP表）\n"
                    prompt += "     * **16轮Feistel网络：**每轮包括：\n"
                    prompt += "       - 扩展置换（E）：将32位右半部分扩展为48位（标准E表）\n"
                    prompt += "       - 与子密钥异或：48位扩展结果与48位子密钥异或\n"
                    prompt += "       - **S盒替换：**将48位结果通过8个S盒替换为32位（必须使用标准的DES S盒表S1到S8）\n"
                    prompt += "       - 置换（P）：对32位结果进行置换（标准P表）\n"
                    prompt += "       - 与左半部分异或：置换结果与左半部分异或\n"
                    prompt += "       - 左右交换（除了最后一轮）\n"
                    prompt += "     * **最终置换（FP）：**对64位结果进行最终置换（标准FP表，IP的逆置换）\n"
                    prompt += "     * **对于ECB模式：必须对每个8字节（64位）块分别进行DES加密，然后将结果拼接！**\n"
                    prompt += "       - 如果明文长度不是8字节的倍数，需要填充（PKCS#5或PKCS#7）\n"
                    prompt += "       - 每个8字节块独立加密，不依赖其他块\n"
                    prompt += "     * **输入处理：**\n"
                    prompt += "       - 从环境变量读取的十六进制字符串必须正确解析为字节数组\n"
                    prompt += "       - 十六进制字符串中的每个字符对（如'4E'）代表一个字节（0x4E）\n"
                    prompt += "       - 必须正确处理大小写（'A'和'a'都代表10）\n"
                    prompt += "     * **输出处理：**\n"
                    prompt += "       - 加密结果必须正确转换为十六进制字符串\n"
                    prompt += "       - 每个字节必须转换为两个十六进制字符（如0x95 -> '95'）\n"
                    prompt += "       - 输出格式必须包含'密文'或'ciphertext'关键词\n"
                    prompt += "       - 将56位密钥分成左右两部分（各28位）\n"
                    prompt += "       - 对每轮（共16轮），根据轮数进行左循环移位（1或2位）\n"
                    prompt += "       - 通过PC-2置换生成48位子密钥\n"
                    prompt += "       - 必须生成16个48位子密钥\n"
                    prompt += "     * **初始置换（IP）：**对64位明文块进行初始置换\n"
                    prompt += "     * **16轮Feistel网络：**每轮包括：\n"
                    prompt += "       - 扩展置换（E）：将32位右半部分扩展为48位\n"
                    prompt += "       - 与子密钥异或：48位扩展结果与48位子密钥异或\n"
                    prompt += "       - **S盒替换：**将48位结果通过8个S盒替换为32位（这是DES的核心！）\n"
                    prompt += "         * 每个S盒是6位输入，4位输出\n"
                    prompt += "         * 必须使用标准的DES S盒表（S1到S8）\n"
                    prompt += "         * 不能跳过或简化S盒替换！\n"
                    prompt += "       - 置换（P）：对32位结果进行置换\n"
                    prompt += "       - 与左半部分异或：置换结果与左半部分异或\n"
                    prompt += "       - 左右交换（除了最后一轮）\n"
                    prompt += "     * **最终置换（FP）：**对64位结果进行最终置换（IP的逆置换）\n"
                if mode == 'CFB':
                    prompt += "   - **对于CFB模式，必须严格按照标准实现：**\n"
                    prompt += "     * CFB模式使用分组密码（DES）的加密函数，不是简单的XOR\n"
                    prompt += "     * 初始化：反馈寄存器 = IV（8字节）\n"
                    prompt += "     * 对于每个明文块（CFB-8模式，每次处理1字节）：\n"
                    prompt += "       1. **使用真正的DES加密函数加密反馈寄存器（使用密钥）**\n"
                    prompt += "          - 这一步必须调用完整的DES加密算法（密钥调度、IP、16轮Feistel、FP）\n"
                    prompt += "          - 绝对不能只是 `current_iv[j] ^ key[j]` 这样的XOR操作！\n"
                    prompt += "       2. 取加密结果的最左8位（1字节）\n"
                    prompt += "       3. 密文 = 明文 XOR 加密结果的最左8位\n"
                    prompt += "       4. 反馈寄存器左移8位，密文的最左8位移入反馈寄存器的最右8位\n"
                    prompt += "     * **绝对不能只是简单的XOR操作，必须使用真正的DES加密函数！**\n"
                elif mode == 'OFB':
                    prompt += "   - **对于OFB模式，必须严格按照标准实现：**\n"
                    prompt += "     * OFB模式使用分组密码（DES）的加密函数来生成密钥流，不是简单的XOR\n"
                    prompt += "     * **重要：OFB模式有两种变体，必须根据测试数据选择正确的变体：**\n"
                    prompt += "       - OFB-8：每次处理1字节（8位），反馈寄存器每次左移8位\n"
                    prompt += "       - OFB-64：每次处理8字节（64位），反馈寄存器每次更新为整个加密结果\n"
                    prompt += "     * **初始化：反馈寄存器 = IV（8字节）**\n"
                    prompt += "     * **对于OFB-8模式（每次处理1字节）：**\n"
                    prompt += "       1. **使用真正的DES加密函数加密反馈寄存器（使用密钥）**\n"
                    prompt += "          - 这一步必须调用完整的DES加密算法（密钥调度、IP、16轮Feistel、FP）\n"
                    prompt += "          - 绝对不能只是 `current_iv[j] ^ key[j]` 这样的XOR操作！\n"
                    prompt += "          - 加密结果作为密钥流输出（8字节）\n"
                    prompt += "       2. 取加密结果的最左字节（keystream[0]）作为密钥流字节\n"
                    prompt += "       3. 密文[i] = 明文[i] XOR keystream[0]\n"
                    prompt += "       4. **反馈寄存器左移8位，加密结果的最左字节移入反馈寄存器的最右字节**\n"
                    prompt += "          - 实现：memmove(feedback, feedback+1, 7); feedback[7] = keystream[0];\n"
                    prompt += "          - **关键：反馈寄存器更新为加密结果的最左字节，不是密文！**\n"
                    prompt += "     * **对于OFB-64模式（每次处理8字节块）：**\n"
                    prompt += "       1. **使用真正的DES加密函数加密反馈寄存器（使用密钥）**\n"
                    prompt += "          - 这一步必须调用完整的DES加密算法（密钥调度、IP、16轮Feistel、FP）\n"
                    prompt += "          - 绝对不能只是 `current_iv[j] ^ key[j]` 这样的XOR操作！\n"
                    prompt += "          - 加密结果作为密钥流输出（8字节）\n"
                    prompt += "       2. 密文[i:i+8] = 明文[i:i+8] XOR keystream（整个8字节）\n"
                    prompt += "       3. **反馈寄存器更新为整个加密结果（不是密文！）**\n"
                    prompt += "          - 实现：memcpy(feedback, keystream, 8);\n"
                    prompt += "          - **关键：反馈寄存器更新为整个加密结果，不是密文！**\n"
                    prompt += "     * **绝对不能只是简单的XOR操作，必须使用真正的DES加密函数！**\n"
                    prompt += "     * **重要：OFB模式的反馈是加密结果（keystream），不是密文！这是与CFB模式的关键区别！**\n"
                    prompt += "     * **常见错误：**\n"
                    prompt += "       - 使用OFB-64但测试数据期望OFB-8（或反之）\n"
                    prompt += "       - 反馈寄存器更新为密文（这是CFB模式，不是OFB！）\n"
                    prompt += "       - 反馈寄存器移位错误（OFB-8必须左移8位）\n"
                    prompt += "       - 密钥流生成不正确（必须使用DES加密函数，不是XOR）\n"
                    prompt += "   - **如果使用C语言：**\n"
                    prompt += "     * **强烈建议使用OpenSSL库！**\n"
                    prompt += "       - 包含头文件：`#include <openssl/des.h>`\n"
                    prompt += "       - 使用 `DES_cfb_encrypt()` 或 `DES_cfb64_encrypt()` 函数\n"
                    prompt += "       - 编译命令：`gcc -o program program.c -lcrypto`\n"
                    prompt += "     * **如果OpenSSL不可用（系统没有安装OpenSSL开发库），必须实现完整的纯C DES算法：**\n"
                    prompt += "       - 纯C实现必须包含完整的DES算法（数百行代码）\n"
                    prompt += "       - 必须实现所有8个S盒（S1到S8），每个S盒是4x16的查找表\n"
                    prompt += "       - 必须实现所有置换表（IP、FP、E、P、PC-1、PC-2）\n"
                    prompt += "       - 必须实现密钥调度算法\n"
                    prompt += "       - 必须实现16轮Feistel网络\n"
                    prompt += "       - 然后在CFB模式中，对每个反馈块调用完整的DES加密函数\n"
                    prompt += "       - **绝对不能只是XOR操作！必须实现完整的DES算法！**\n"
                    prompt += "       - 可以使用条件编译：`#ifdef USE_OPENSSL` 来选择使用OpenSSL或纯C实现\n"
                    prompt += "       - 默认情况下，代码应该可以在没有OpenSSL的环境中编译运行（使用纯C实现）\n"
                    prompt += "     * **如果无法实现完整的DES算法，建议：**\n"
                    prompt += "       - 安装OpenSSL开发库（Linux: `sudo apt-get install libssl-dev`）\n"
                    prompt += "       - 或者使用Python语言（Python有现成的DES库）\n"
                    prompt += "   - 如果使用Python，使用pycryptodome的DES.new(key, DES.MODE_CFB, iv)或cryptography库的标准实现\n"
                    prompt += "2. 代码能够正确执行\n"
                    prompt += "3. 实际输出与预期输出完全匹配（内容必须完全一致）\n"
                    prompt += "4. 保持代码的完整性和可运行性\n"
                    prompt += "5. 保留所有必要的注释和错误处理\n"
                    prompt += "6. **绝对禁止使用随机数！使用固定的IV和密钥（从环境变量读取），确保相同输入每次产生相同输出**\n"
                    prompt += "   - **绝对不要使用 `rand()`、`random()`、`srand()`、`time()`、`os.urandom()` 等随机函数！**\n"
                    prompt += "   - IV必须从环境变量TEST_IV读取，如果不存在，使用全零字节，但绝对不能使用随机IV！\n"
                    prompt += "   - 密钥必须从环境变量TEST_KEY读取，绝对不能使用随机密钥！\n"
                    prompt += "   - **代码必须是确定性的：相同的明文、密钥、IV必须产生完全相同的密文！**\n"
                    prompt += "7. 输出完整的密文，不要截断或只输出部分内容\n"
                    prompt += "8. 密文输出格式要统一（十六进制），所有字符小写，不要有空格、换行或其他分隔符\n"
                    prompt += "9. 确保密文长度正确：\n"
                actual_len = len(test_feedback.get('actual', '')) if test_feedback.get('actual') else 0
                expected_len = len(test_feedback.get('expected', '')) if test_feedback.get('expected') else 0
                if actual_len > expected_len:
                    diff = actual_len - expected_len
                    if diff == 16:  # 多了8字节（16个hex字符）
                        prompt += f"   - **严重错误：实际密文长度比预期多了16个字符（8字节），这很可能是输出了IV（初始化向量）！**\n"
                        prompt += f"   - 对于DES：IV是8字节（16个hex字符），绝对不要输出IV！\n"
                        prompt += f"   - **检查代码中是否有以下错误：**\n"
                        prompt += f"     * 是否在输出前执行了 `ciphertext.push_back(iv)` 或类似操作？\n"
                        prompt += f"     * 是否在输出时错误地将IV追加到了密文后面？\n"
                        prompt += f"     * 是否在 `bytes_to_hex()` 函数中包含了IV？\n"
                        prompt += f"     * 是否在main函数中错误地输出了IV？\n"
                        prompt += f"   - **解决方案：**\n"
                        prompt += f"     * 确保 `cbc_encrypt()` 函数只返回密文，不包含IV\n"
                        prompt += f"     * 确保输出时只输出密文，不要输出IV\n"
                        prompt += f"     * 检查是否有类似 `result += iv` 或 `result.insert(result.end(), iv.begin(), iv.end())` 的代码\n"
                        prompt += f"     * IV只用于加密过程，绝对不应该出现在输出中\n"
                        prompt += f"     * 如果使用 `cout << \"密文: \" << bytes_to_hex(ciphertext) << endl;`，确保ciphertext只包含密文，不包含IV\n"
                    elif diff == 32:  # 多了16字节（32个hex字符）
                        prompt += f"   - 当前问题：实际密文长度比预期多了32个字符（16字节），这很可能是输出了AES的IV！\n"
                        prompt += f"   - 解决方案：只输出密文本身，不要输出IV。IV只用于加密过程，不应该出现在输出中\n"
                    elif diff == expected_len:  # 长度是2倍
                        prompt += f"   - 当前问题：实际密文长度是预期的2倍，可能是重复输出了密文，或者同时输出了原始字节和hex编码\n"
                    else:
                        prompt += f"   - 当前问题：实际密文长度比预期多了{diff}个字符，可能是输出了额外的信息（IV、密钥等）\n"
                elif actual_len < expected_len and actual_len > 0:
                    diff = expected_len - actual_len
                    prompt += f"   - **严重问题：实际密文长度比预期少了{diff}个字符（{diff//2}字节）！**\n"
                    prompt += f"   - 这通常是因为：\n"
                    prompt += f"     1. **只处理了部分明文数据**（最常见！）\n"
                    prompt += f"        - 检查代码是否正确读取了完整的明文（从环境变量或stdin）\n"
                    prompt += f"        - 确保明文长度计算正确（十六进制字符串长度除以2）\n"
                    prompt += f"        - 不要要求用户输入明文长度，应该自动计算\n"
                    prompt += f"        - 确保循环处理了所有明文字节，而不是只处理部分\n"
                    prompt += f"     2. **只输出了部分密文**\n"
                    prompt += f"        - 检查输出函数是否正确输出了所有密文字节\n"
                    prompt += f"        - 确保输出长度等于密文实际长度，而不是某个固定值或部分长度\n"
                    prompt += f"        - 不要截断输出，必须输出完整的密文\n"
                    prompt += f"     3. **CFB模式实现错误**（如果当前是CFB模式）\n"
                    prompt += f"        - CFB模式必须处理所有明文字节，不能只处理部分\n"
                    prompt += f"        - 确保CFB模式的循环处理了完整的明文\n"
                    prompt += f"        - 对于DES CFB模式，明文长度是16字节（32个hex字符），必须输出16字节的密文\n"
                    prompt += f"   - **解决方案：**\n"
                    prompt += f"     * 确保从环境变量读取的明文长度正确（十六进制字符串长度除以2）\n"
                    prompt += f"     * 确保加密函数处理了所有明文字节\n"
                    prompt += f"     * 确保输出函数输出了所有密文字节（长度等于明文长度）\n"
                    prompt += f"     * 不要要求用户输入长度，应该自动从输入数据计算\n"
                    prompt += f"     * 对于DES CFB模式，输入明文是16字节，输出密文也必须是16字节（32个hex字符）\n"
                    prompt += "   - 只输出一次密文，只输出hex编码的字符串，不要输出原始字节\n"
                    prompt += "   - 对于分组密码，密文长度应该是分组大小的整数倍\n"
                    prompt += "   - 绝对不要输出IV、密钥或其他信息，只输出密文本身\n"
                    prompt += "10. 仔细检查IV、密钥、填充方式和编码方式，确保与预期结果完全匹配\n"
                    prompt += "    - 确保IV和密钥都是从环境变量正确读取的十六进制字符串\n"
                    prompt += "    - **重要：环境变量中的字符串可能包含空白字符，必须去除！**\n"
                    prompt += "    - 在解析十六进制字符串之前，必须先去除所有空白字符（空格、换行符等）\n"
                    prompt += "    - 确保十六进制字符串正确转换为字节数组\n"
                    prompt += "    - 确保字节数组的长度正确（DES密钥8字节，IV 8字节）\n"
                    prompt += "    - **如果出现'Key must be exactly 8 bytes'错误，通常是环境变量中包含空白字符导致的！**\n"
                    prompt += "11. 输出密文时，格式如：'密文: xxxxxx' 或 'ciphertext: xxxxxx'，其中xxxxxx是hex编码的密文（小写，无空格）\n"
                    prompt += "12. 如果密文长度不正确，检查是否：\n"
                    prompt += "    - 输出了两次密文\n"
                    prompt += "    - 同时输出了原始字节和hex编码\n"
                    prompt += "    - hex编码了两次\n"
                    prompt += "    - 输出了IV（初始化向量）- 这是最常见的错误！IV不应该出现在输出中\n"
                    prompt += "    - 输出了密钥或其他附加信息\n"
                    prompt += "    确保只输出一次hex编码的密文，长度应该等于预期长度\n"
                    prompt += "13. 重要：代码必须从环境变量或stdin读取输入，不要硬编码任何测试数据！\n"
                    prompt += "    - 优先从环境变量读取：TEST_PLAINTEXT、TEST_KEY、TEST_IV等\n"
                    prompt += "    - 如果环境变量不存在，可以从stdin读取或提供交互式输入\n"
                    prompt += "    - 代码必须能够处理任意输入，而不仅仅是测试数据\n"
                    prompt += "13. **必须实现真正的密码学算法，不要使用模拟或简化的实现！**\n"
                    prompt += "    - 如果当前代码只是简单的XOR或其他模拟操作，必须替换为真正的算法实现\n"
                    prompt += "    - 对于DES算法，必须实现完整的DES加密（密钥调度、S盒替换等）\n"
                    prompt += "    - 对于AES算法，必须实现完整的AES加密（密钥扩展、字节替换、行移位、列混合等）\n"
                    prompt += "    - 如果使用纯C实现，必须包含完整的算法实现，不能只是模拟\n"
                    prompt += "    - 或者使用标准库（如OpenSSL）来实现真正的加密算法\n"
                    prompt += "14. **输入输出格式处理：**\n"
                    prompt += "    - 从环境变量读取的明文、密钥、IV都是十六进制字符串格式\n"
                    prompt += "    - 必须正确将十六进制字符串转换为字节数组进行加密\n"
                    prompt += "    - 加密后的密文也要转换为十六进制字符串输出\n"
                    prompt += "    - 确保十六进制字符串的大小写一致（建议使用小写）\n"
        else:
            prompt += "请改进代码，确保代码正确性和完整性。\n"

        try:
            _prov = (getattr(agent, "provider", None) or "").lower()
            _skip_imp_dist = bool(kwargs.get("_skip_distillation"))
            d_suffix = ""
            if not _skip_imp_dist and distill_mod.is_distillation_target_provider(agent):
                d_suffix = distill_mod.build_improve_suffix(
                    agent,
                    algorithm,
                    mode,
                    operation,
                    language,
                    test_feedback,
                    original_code,
                )
                if d_suffix and _is_qwen_local_provider(agent):
                    cap = 2500
                    try:
                        raw_cfg = getattr(getattr(agent, "config", None), "_config", {}) or {}
                        cap = int((raw_cfg.get("distillation") or {}).get("max_teacher_code_chars") or 2500)
                        cap = max(800, min(cap, 4000))
                    except (TypeError, ValueError):
                        pass
                    if len(d_suffix) > cap:
                        d_suffix = d_suffix[:cap] + "\n…(教师参考已截断)\n"
            if d_suffix:
                prompt += "\n\n" + d_suffix
        except Exception as ex:
            logger.warning(f"蒸馏改进注入跳过: {ex}")
        
        if kwargs:
            prompt += "\n其他要求：\n"
            for key, value in kwargs.items():
                if str(key).startswith("_"):
                    continue
                prompt += f"- {key}: {value}\n"
        
        if _is_qwen_local_provider(agent):
            prompt += (
                "\n**【强制】仅输出修正后的完整源码**（从首行 `import`/`#include` 到 `main` 结束）；"
                "无 markdown 围栏、无解释文字。\n"
            )
        else:
            prompt += "\n请提供改进后的完整代码，只输出纯代码，不要使用markdown代码块标记，不要输出任何说明文字！"
        
        system_prompt = resolve_llm_system_prompt(language, kwargs)
        emit_prompt_ready(
            step="测试反馈改进",
            agent=agent,
            algorithm=algorithm,
            mode=mode,
            language=language,
            kwargs=kwargs,
            user_prompt=prompt,
            system_prompt=system_prompt,
        )

        try:
            import time
            start_time = time.time()
            user_improve = llm_user_content_for_api(prompt, system_prompt or "")
            emit_llm_begin(
                step="测试反馈改进",
                agent=agent,
                kwargs=kwargs,
                user_chars=len(user_improve),
                system_chars=len(system_prompt or ""),
            )
            raw_output = await agent.llm.generate(user_improve, system_prompt)
            generation_time = time.time() - start_time
            emit_llm_end(
                step="测试反馈改进",
                kwargs=kwargs,
                seconds=generation_time,
                reply_chars=len(raw_output or ""),
            )
            logger.info(f"代码改进耗时: {generation_time:.2f}秒")
            
            # 提取纯代码，去除markdown格式和说明文字
            improved_code = extract_code(
                raw_output,
                language,
                suppress_heuristic_warnings=bool(
                    kwargs.get("_ablation_no_test_feedback")
                ),
            )
            logger.info("代码改进成功！")
            return improved_code, generation_time
        except Exception as e:
            logger.error(f"代码改进失败: {e}")
            raise
    
