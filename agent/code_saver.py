"""代码保存相关功能模块"""
import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from utils.logger import setup_logger
from utils import distillation as distill_mod
from utils.c_code_sanitize import (
    allow_canonical_openssl_whole_file,
    reset_generation_allow_canonical_replace,
    reset_generation_allow_error_auto_repair,
    sanitize_c_illegal_numeric_macros,
    set_generation_allow_canonical_replace,
    set_generation_allow_error_auto_repair,
)
from utils.llm_code_quickfix import apply_common_quickfixes
from utils.python_code_sanitize import (
    aes_ofb_sanitize_hint,
    pop_eval_crypto_task,
    push_eval_crypto_task,
    sanitize_python_crypto_code,
)
from agent.code_generator import generate_code, improve_code
from agent.prompts import LANGUAGE_EXTENSIONS

logger = setup_logger()


def _vector_retry_plaintext_echo_hint(details: Dict[str, Any], plaintext: Optional[str]) -> str:
    """向量失败并重试 generate 时：若实际 hex 与明文 hex 相同，追加一针见血提示。"""
    if not plaintext or not details:
        return ""
    act = str(details.get("actual_normalized") or details.get("actual", "") or "")
    act = "".join(act.split()).lower()
    pt = str(plaintext).strip()
    pt = "".join(pt.split()).lower()
    if len(act) >= 8 and len(pt) >= 8 and act == pt:
        return (
            "\n【诊断】当前「实际」与「明文」hex 完全一致 → 代码很可能未执行分组加密。"
            "下一轮禁止循环打印 `plaintext[i]`；请使用 OpenSSL EVP 或库函数产出密文缓冲区后再 hex 输出。\n"
        )
    return ""


def _link_retry_undefined_reference_hint(error_msg: str) -> str:
    if "undefined reference" not in error_msg.lower():
        return ""
    return (
        "[LINK_RETRY] 链接器 undefined reference：请在同一编译单元实现被调用符号，或使用 OpenSSL 官方 API 并确保链接 "
        "`-lcrypto`（必要时 `-lssl -lcrypto`）；禁止仅声明 `KeyExpansion`/`Cipher`/`AES_encrypt` 等却无定义。\n"
    )


def _compile_retry_redeclaration_hint(error_msg: str) -> str:
    """OpenAI 批量常见：main 内两处 `int len`/`ciphertext_len` → redeclaration。"""
    m = (error_msg or "").lower()
    if "redeclaration" not in m and "redefinition of" not in m:
        return ""
    return (
        "[COMPILE_RETRY] C/C++ **`redeclaration`/`redefinition`**：`main` 内 **`int len`/`ciphertext_len` 只保留一处声明**，"
        "后续 `EVP_EncryptUpdate`/`Final` 用 **`int outl`** 承接长度并累加，勿再次写 `int len`。\n"
    )


def _link_retry_reloc_hint(error_msg: str) -> str:
    m = (error_msg or "").lower()
    if "relocation against" not in m and "ld returned" not in m and "collect2:" not in m:
        return ""
    return (
        "[LINK_RETRY] C++ 链接 relocation / collect2：删除手写 `AES_*` 类拆分，改为**单文件 `EVP_aes_*`/`EVP_sm4_*`**；"
        "静态成员须在 `.cpp` 内唯一定义。\n"
    )


def _config_allows_canonical_openssl_whole_file(agent) -> bool:
    """读取 config 中 generation.use_canonical_openssl_whole_file；缺省 True 以兼容未写该项的旧配置。"""
    cfg = getattr(agent, "config", None)
    if cfg is None:
        return True
    gen = cfg.get("generation") or {}
    if not isinstance(gen, dict):
        return True
    return bool(gen.get("use_canonical_openssl_whole_file", True))


def _openssl_crosscheck_enabled(agent) -> bool:
    """OpenSSL 官方向量二次测试与 CLI 对照；默认关闭，仅以仓库标准向量为准。
    配置：`testing.enable_openssl_crosscheck: true` 时开启。"""
    cfg = getattr(agent, "config", None)
    if cfg is None:
        return False
    testing = cfg.get("testing")
    if isinstance(testing, dict):
        return bool(testing.get("enable_openssl_crosscheck", False))
    return False


async def save_code(
        agent,
        code: str,
        filename: str,
        algorithm: Optional[str] = None,
        mode: Optional[str] = None,
        *,
        allow_canonical_whole_file: Optional[bool] = None,
        allow_error_auto_repair: Optional[bool] = None,
        **_: Any,
    ) -> Path:
        """保存生成的代码。
        allow_canonical_whole_file：由 generate_and_save 传入时与消融档位一致；None 则 sanitize 内回退 ContextVar。
        allow_error_auto_repair：C/C++ 写盘前是否启用后段错误自动修复；None 则回退 ContextVar。
        额外关键字（如历史误传的 suppress_heuristic_warnings）一律忽略，避免批量/消融脚本整表失败。"""
        if filename.lower().endswith(".py"):
            hint = aes_ofb_sanitize_hint(algorithm, mode)
            tok = push_eval_crypto_task(algorithm, mode)
            try:
                code = sanitize_python_crypto_code(
                    code, filename, hint_aes_mode=hint, algorithm=algorithm, mode=mode
                )
            finally:
                pop_eval_crypto_task(tok)
        else:
            code = sanitize_c_illegal_numeric_macros(
                code,
                filename,
                algorithm=algorithm,
                mode=mode,
                allow_canonical_whole_file=allow_canonical_whole_file,
                allow_error_auto_repair=allow_error_auto_repair,
            )
        filepath = agent.output_dir / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(code)
        logger.info(f"代码已保存到: {filepath}")
        return filepath


async def rerun_vector_tests_on_code(
    agent,
    code: str,
    algorithm: str,
    mode: Optional[str],
    language: str,
    operation: str = "加密解密",
    **policy_kw: Any,
) -> Tuple[Optional[Tuple[bool, str, Dict[str, Any]]], Optional[Tuple[bool, str, Dict[str, Any]]]]:
    """
    对已有源码执行标准向量测试（复测）。OpenSSL 官方向量与 CLI 对照仅在
    `testing.enable_openssl_crosscheck` 为真时执行。

    Returns:
        (test_result, openssl_compare_result) — 与 generate_and_save 返回的后两段语义对齐；
        无测试数据或 tester 不可用时返回 (None, None)，调用方应走重新生成。
    """
    _allow_canonical = allow_canonical_openssl_whole_file(
        policy_kw
    ) and _config_allows_canonical_openssl_whole_file(agent)
    # 测试反馈消融：同时关闭写盘前 C/C++ 启发式修补（原独立开关 _ablation_no_error_auto_repair，现并入）
    _allow_error_auto_repair = not (
        bool(policy_kw.get("_ablation_no_error_auto_repair"))
        or bool(policy_kw.get("_ablation_no_test_feedback"))
    )
    _suppress_hw = bool(policy_kw.get("_ablation_no_test_feedback"))

    if not agent.tester or not agent.test_data_loader:
        return None, None

    if algorithm.upper() == "RSA":
        test_data = agent.test_data_loader.get_test_data(algorithm, None)
    else:
        test_data = agent.test_data_loader.get_test_data(algorithm, mode)
    if not test_data:
        return None, None

    if language.lower() == "python":
        code = apply_common_quickfixes(code, language)

    iv_value = test_data.get("iv")
    test_prep_data: Dict[str, Any] = {
        "plaintext": test_data.get("plaintext"),
        "expected_ciphertext": test_data.get("expected_ciphertext"),
        "key": test_data.get("key"),
        "iv": iv_value,
        "aad": test_data.get("aad"),
        "algorithm": algorithm,
        "mode": mode,
        "operation": operation,
        "public_key_n": test_data.get("public_key", {}).get("n"),
        "public_key_e": test_data.get("public_key", {}).get("e"),
        "private_key_n": test_data.get("private_key", {}).get("n"),
        "private_key_d": test_data.get("private_key", {}).get("d"),
        "ciphertexts": test_data.get("ciphertexts", {}),
    }

    logger.info("正在对历史代码执行标准向量测试（复测）…")
    plaintext = test_prep_data.get("plaintext")
    expected_ciphertext = test_prep_data.get("expected_ciphertext")
    key = test_prep_data.get("key")
    iv = test_prep_data.get("iv")
    aad = test_prep_data.get("aad")
    algorithm_test = test_prep_data.get("algorithm")
    operation_test = test_prep_data.get("operation")
    public_key_n = test_prep_data.get("public_key_n")
    public_key_e = test_prep_data.get("public_key_e")
    private_key_n = test_prep_data.get("private_key_n")
    private_key_d = test_prep_data.get("private_key_d")
    ciphertexts = test_prep_data.get("ciphertexts", {})

    async def run_test_in_executor() -> Tuple[bool, str, Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        ctx = contextvars.copy_context()
        with ThreadPoolExecutor() as executor:
            if algorithm_test and algorithm_test.upper() == "RSA":
                if operation_test == "加密" and "encrypt" in ciphertexts:
                    expected_ciphertext_rsa = ciphertexts["encrypt"]
                    return await loop.run_in_executor(
                        executor,
                        lambda: ctx.run(
                            agent.tester.test,
                            code,
                            language,
                            plaintext=plaintext,
                            expected_ciphertext=expected_ciphertext_rsa,
                            public_key_n=public_key_n,
                            public_key_e=public_key_e,
                            algorithm=algorithm_test,
                            mode=test_prep_data.get("mode"),
                            allow_canonical_whole_file=_allow_canonical,
                            allow_error_auto_repair=_allow_error_auto_repair,
                            suppress_heuristic_warnings=_suppress_hw,
                        ),
                    )
                if operation_test == "签名" and "sign" in ciphertexts:
                    expected_signature = ciphertexts["sign"]
                    return await loop.run_in_executor(
                        executor,
                        lambda: ctx.run(
                            agent.tester.test,
                            code,
                            language,
                            plaintext=plaintext,
                            expected_ciphertext=expected_signature,
                            private_key_n=private_key_n,
                            private_key_d=private_key_d,
                            algorithm=algorithm_test,
                            mode=test_prep_data.get("mode"),
                            allow_canonical_whole_file=_allow_canonical,
                            allow_error_auto_repair=_allow_error_auto_repair,
                            suppress_heuristic_warnings=_suppress_hw,
                        ),
                    )
                return True, "RSA测试跳过（无测试数据）", {}
            return await loop.run_in_executor(
                executor,
                lambda: ctx.run(
                    agent.tester.test,
                    code,
                    language,
                    plaintext=plaintext,
                    expected_ciphertext=expected_ciphertext,
                    key=key,
                    iv=iv,
                    aad=aad,
                    algorithm=algorithm_test,
                    mode=test_prep_data.get("mode"),
                    allow_canonical_whole_file=_allow_canonical,
                    allow_error_auto_repair=_allow_error_auto_repair,
                    suppress_heuristic_warnings=_suppress_hw,
                ),
            )

    success, message, details = await run_test_in_executor()
    test_result: Tuple[bool, str, Dict[str, Any]] = (success, message, details)
    if not success:
        return test_result, None

    if not _openssl_crosscheck_enabled(agent):
        return test_result, None

    # OpenSSL 官方向量（与 generate_and_save 一致；仅 enable_openssl_crosscheck 时）
    openssl_test_success = True
    openssl_test_message = "未进行OpenSSL测试"
    if agent.test_data_loader:
        openssl_test_data = None
        if algorithm.upper() == "RSA":
            if operation == "加密":
                openssl_test_data = agent.test_data_loader.get_openssl_test_data(algorithm, "encrypt")
            elif operation == "签名":
                openssl_test_data = agent.test_data_loader.get_openssl_test_data(algorithm, "sign")
        else:
            openssl_test_data = agent.test_data_loader.get_openssl_test_data(algorithm, mode)

        if openssl_test_data:
            logger.info("历史代码复测：OpenSSL 官方向量…")
            try:
                openssl_plaintext = openssl_test_data.get("plaintext")
                openssl_expected = openssl_test_data.get("expected_ciphertext")
                openssl_key = openssl_test_data.get("key")
                openssl_iv = openssl_test_data.get("iv")
                openssl_aad = openssl_test_data.get("aad")
                openssl_public_key_n = openssl_test_data.get("public_key", {}).get("n")
                openssl_public_key_e = openssl_test_data.get("public_key", {}).get("e")
                openssl_private_key_n = openssl_test_data.get("private_key", {}).get("n")
                openssl_private_key_d = openssl_test_data.get("private_key", {}).get("d")

                async def run_openssl_test() -> Tuple[bool, str, Dict[str, Any]]:
                    loop = asyncio.get_event_loop()
                    ctx = contextvars.copy_context()
                    with ThreadPoolExecutor() as executor:
                        if algorithm.upper() == "RSA":
                            if operation == "加密" and openssl_public_key_n:
                                return await loop.run_in_executor(
                                    executor,
                                    lambda: ctx.run(
                                        agent.tester.test,
                                        code,
                                        language,
                                        plaintext=openssl_plaintext,
                                        expected_ciphertext=openssl_expected,
                                        public_key_n=openssl_public_key_n,
                                        public_key_e=openssl_public_key_e,
                                        algorithm=algorithm,
                                        mode=mode,
                                        allow_canonical_whole_file=_allow_canonical,
                                        allow_error_auto_repair=_allow_error_auto_repair,
                                        suppress_heuristic_warnings=_suppress_hw,
                                    ),
                                )
                            if operation == "签名" and openssl_private_key_n:
                                return await loop.run_in_executor(
                                    executor,
                                    lambda: ctx.run(
                                        agent.tester.test,
                                        code,
                                        language,
                                        plaintext=openssl_plaintext,
                                        expected_ciphertext=openssl_expected,
                                        private_key_n=openssl_private_key_n,
                                        private_key_d=openssl_private_key_d,
                                        algorithm=algorithm,
                                        mode=mode,
                                        allow_canonical_whole_file=_allow_canonical,
                                        allow_error_auto_repair=_allow_error_auto_repair,
                                        suppress_heuristic_warnings=_suppress_hw,
                                    ),
                                )
                            return True, "RSA OpenSSL测试跳过（无测试数据）", {}
                        return await loop.run_in_executor(
                            executor,
                            lambda: ctx.run(
                                agent.tester.test,
                                code,
                                language,
                                plaintext=openssl_plaintext,
                                expected_ciphertext=openssl_expected,
                                key=openssl_key,
                                iv=openssl_iv,
                                aad=openssl_aad,
                                algorithm=algorithm,
                                mode=mode,
                                allow_canonical_whole_file=_allow_canonical,
                                allow_error_auto_repair=_allow_error_auto_repair,
                                suppress_heuristic_warnings=_suppress_hw,
                            ),
                        )

                _os, openssl_msg, _od = await run_openssl_test()
                openssl_test_success = _os
                openssl_test_message = openssl_msg
                if not openssl_test_success:
                    logger.warning(f"历史代码复测未通过 OpenSSL 官方向量: {openssl_msg}")
            except Exception as e:
                logger.warning(f"历史代码 OpenSSL 官方向量执行失败: {e}")
                openssl_test_success = False
                openssl_test_message = f"OpenSSL测试执行失败: {str(e)}"

    if not openssl_test_success:
        return (False, openssl_test_message, {}), None

    # OpenSSL 对照（仅 DES/AES 加密解密）
    openssl_test_result: Optional[Tuple[bool, str, Dict[str, Any]]] = None
    if agent.openssl_tester and agent.openssl_tester.is_available():
        algorithm_upper = algorithm.upper()
        if algorithm_upper in ("DES", "AES") and operation == "加密解密":
            logger.info("历史代码复测：OpenSSL 对照…")
            try:
                key_size = None
                if algorithm_upper == "AES" and key:
                    key_bytes = len(bytes.fromhex(key.replace(" ", "").replace("\n", "")))
                    if key_bytes == 16:
                        key_size = 128
                    elif key_bytes == 24:
                        key_size = 192
                    elif key_bytes == 32:
                        key_size = 256
                    else:
                        key_size = 128
                else:
                    key_size = 128
                loop = asyncio.get_event_loop()
                with ThreadPoolExecutor() as executor:
                    openssl_success, openssl_message, openssl_details = await loop.run_in_executor(
                        executor,
                        lambda: agent.openssl_tester.compare_with_openssl(
                            generated_ciphertext=details.get("actual_normalized", "")
                            or details.get("actual", ""),
                            plaintext_hex=plaintext,
                            key_hex=key,
                            iv_hex=iv,
                            algorithm=algorithm_upper,
                            mode=mode,
                            key_size=key_size,
                        ),
                    )
                openssl_test_result = (openssl_success, openssl_message, openssl_details)
                if not openssl_success:
                    logger.warning(f"历史代码复测未通过 OpenSSL 对照: {openssl_message}")
            except Exception as e:
                logger.warning(f"历史代码 OpenSSL 对照失败: {e}")
                openssl_test_result = (False, f"OpenSSL测试失败: {str(e)}", {})

    return test_result, openssl_test_result


async def generate_and_save(agent,  algorithm: str, mode: Optional[str] = None,
                               operation: str = "加密解密", language: str = 'python',
                               filename: Optional[str] = None, validate: bool = True,
                               max_retries: int = 3, **kwargs) -> Tuple[Path, Optional[Tuple[bool, str]], Optional[Tuple[bool, str, Dict]], Optional[Tuple[bool, str, Dict]]]:
        """
        生成并保存代码，自动测试并重试直到通过测试
        
        Args:
            algorithm: 算法名称
            mode: 模式
            operation: 操作类型
            language: 编程语言
            filename: 文件名
            validate: 是否启用代码验证
            max_retries: 最大重试次数（如果测试失败）
            **kwargs: 其他参数
        
        Returns:
            (文件路径, 验证结果(是否成功, 输出信息), 测试结果(是否成功, 消息, 详细信息), OpenSSL测试结果(是否成功, 消息, 详细信息))
        """
        # build_prompt 会在首次 generate 时 pop 掉 prompt_ablation；此处先快照策略 kwargs，避免后续误判 golden
        _policy_kw = dict(kwargs)
        _allow_canonical = allow_canonical_openssl_whole_file(
            _policy_kw
        ) and _config_allows_canonical_openssl_whole_file(agent)
        _allow_error_auto_repair = not (
            bool(_policy_kw.get("_ablation_no_error_auto_repair"))
            or bool(_policy_kw.get("_ablation_no_test_feedback"))
        )
        _suppress_hw = bool(_policy_kw.get("_ablation_no_test_feedback"))
        _canon_replace_tok = set_generation_allow_canonical_replace(_allow_canonical)
        _error_repair_tok = set_generation_allow_error_auto_repair(
            _allow_error_auto_repair
        )
        try:
            # 获取测试数据
            test_data = None
            test_result = None  # 初始化test_result，避免未定义错误
            if agent.test_data_loader:
                # 对于RSA，不需要mode参数
                if algorithm.upper() == 'RSA':
                    test_data = agent.test_data_loader.get_test_data(algorithm, None)
                else:
                    test_data = agent.test_data_loader.get_test_data(algorithm, mode)
                if test_data:
                    logger.info(f"找到标准测试数据，将自动验证生成的代码")
            
            # Self-Refine（与 github.com/madaan/self-refine 同构：Critic + Refine；另与测试反馈 improve 协同）
            # 论文主消融「无测试反馈改进」：关闭测试驱动的 refine/improve 循环（写盘前 C/C++ 规则修补亦随 _allow_error_auto_repair 一并关闭）
            if kwargs.get('_ablation_no_test_feedback'):
                sr_enabled = False
                max_refine_rounds = 0
                critic_before_test = False
            else:
                sr_raw = agent.config.get('self_refine') or {}
                sr_enabled = sr_raw.get('enabled', True)
                max_refine_rounds = max(1, int(sr_raw.get('max_refine_rounds', 3))) if sr_enabled else 0
                critic_before_test = bool(sr_raw.get('critic_before_test', False)) and sr_enabled
            
            # 重试循环
            total_generation_time = 0.0  # 累计所有尝试的生成时间
            for attempt in range(max_retries):
                # 检查是否被取消（如果提供了任务ID）
                task_id = kwargs.get('_task_id')
                if task_id:
                    # 从web.server模块导入running_tasks和task_lock
                    try:
                        import sys
                        if 'web.server' in sys.modules:
                            from web.server import running_tasks, task_lock
                            with task_lock:
                                if task_id in running_tasks and running_tasks[task_id].get('cancelled', False):
                                    raise asyncio.CancelledError("任务已被用户取消")
                    except (ImportError, KeyError):
                        pass  # 如果无法导入，继续执行
                
                if attempt > 0:
                    logger.info(f"第 {attempt + 1} 次尝试生成代码...")
                
                # 生成代码
                code, generation_time = await generate_code(agent, algorithm, mode, operation, language, test_data=test_data, **kwargs)
                total_generation_time += generation_time
                
                if critic_before_test:
                    from agent.self_refine import format_test_data_critic_summary, run_critic_feedback, run_refine_from_critique
                    try:
                        summary = format_test_data_critic_summary(test_data, algorithm, mode)
                        critique = await run_critic_feedback(agent, code, algorithm, mode, language, operation, summary)
                        code, cr_t = await run_refine_from_critique(agent, code, critique, algorithm, mode, language, operation)
                        total_generation_time += cr_t
                        logger.info("Self-Refine：已完成测试前自我批判与一轮修正")
                    except Exception as e:
                        logger.warning(f"Self-Refine 测试前批判跳过: {e}")
                
                # 确定文件扩展名
                ext = LANGUAGE_EXTENSIONS.get(language.lower(), '.py')
                
                if not filename:
                    filename = f"{algorithm.lower()}"
                    if mode:
                        filename += f"_{mode.lower()}"
                    filename += ext

                code = apply_common_quickfixes(code, language)
                
                filepath = await save_code(
                    agent,
                    code,
                    filename,
                    algorithm,
                    mode,
                    allow_canonical_whole_file=_allow_canonical,
                    allow_error_auto_repair=_allow_error_auto_repair,
                )
                
                # 不立即添加历史记录，只在测试通过后才添加
                history_id = None
                logger.info(f"[OK] 代码已保存到文件: {filepath.name}")
                
                # 使用异步并发加速：验证和测试可以并行执行
                import asyncio
                from concurrent.futures import ThreadPoolExecutor
                
                # 并行执行验证和测试准备（如果验证通过，可以立即开始测试）
                validation_result = None
                test_result = None
                
                async def run_validation():
                    """异步执行代码验证"""
                    if validate and agent.validator:
                        logger.info(f"正在验证生成的{language}代码...")
                        loop = asyncio.get_event_loop()
                        # 线程池默认不继承 ContextVar（消融下关闭 canonical 整文件替换须传到 validator）
                        ctx = contextvars.copy_context()
                        with ThreadPoolExecutor() as executor:
                            vd = dict(test_data) if test_data else {}
                            vd["algorithm"] = algorithm
                            if mode is not None:
                                vd["mode"] = mode
                            success, output = await loop.run_in_executor(
                                executor,
                                lambda: ctx.run(
                                    agent.validator.validate,
                                    code,
                                    language,
                                    vd or None,
                                    allow_canonical_whole_file=_allow_canonical,
                                    allow_error_auto_repair=_allow_error_auto_repair,
                                ),
                            )
                        return (success, output)
                    return None
                
                async def prepare_test_data():
                    """准备测试数据（不执行测试，只准备）"""
                    if test_data and agent.tester:
                        # 对于GCM模式，使用完整的IV（代码会自己取前12字节作为nonce）
                        # 对于其他模式，使用标准IV
                        iv_value = test_data.get('iv')
                        if mode and mode.upper() == 'GCM':
                            # GCM模式：如果存在iv_gcm，代码应该从TEST_IV读取完整IV，然后使用前12字节
                            # 但为了兼容性，我们仍然设置完整的IV到环境变量
                            # 代码会根据prompt使用IV的前12字节作为nonce
                            if 'iv_gcm' in test_data:
                                # 保留iv_gcm信息，但环境变量仍然使用完整IV
                                # 代码会从TEST_IV读取完整IV，然后使用iv[:12]作为nonce
                                pass
                        
                        return {
                            'plaintext': test_data.get('plaintext'),
                            'expected_ciphertext': test_data.get('expected_ciphertext'),
                            'key': test_data.get('key'),
                            'iv': iv_value,  # 使用完整的IV，代码会根据模式处理
                            'aad': test_data.get('aad'),
                            'algorithm': algorithm,
                            'mode': mode,
                            'operation': operation,
                            'public_key_n': test_data.get('public_key', {}).get('n'),
                            'public_key_e': test_data.get('public_key', {}).get('e'),
                            'private_key_n': test_data.get('private_key', {}).get('n'),
                            'private_key_d': test_data.get('private_key', {}).get('d'),
                            'ciphertexts': test_data.get('ciphertexts', {})
                        }
                    return None
                
                # 并行执行验证和测试数据准备
                validation_task = run_validation()
                test_prep_task = prepare_test_data()
                
                validation_result, test_prep_data = await asyncio.gather(validation_task, test_prep_task)
                
                if validation_result and not validation_result[0]:
                    error_msg = validation_result[1] if isinstance(validation_result[1], str) else str(validation_result[1])
                    logger.warning(f"代码验证失败: {error_msg}")
                    
                    # 检查是否是OpenSSL 3.0 DES不支持的错误
                    is_openssl_des_unsupported = (
                        '0308010C' in error_msg or
                        'unsupported' in error_msg.lower() and 'des' in error_msg.lower() and 'algorithm' in error_msg.lower() or
                        'digital envelope routines' in error_msg.lower() and 'unsupported' in error_msg.lower()
                    )
                    
                    if is_openssl_des_unsupported and attempt < max_retries - 1:
                        logger.warning("检测到OpenSSL 3.0不支持DES算法（需要legacy provider），将在下次生成时添加legacy provider加载代码")
                        kwargs['_openssl_des_unsupported'] = True
                        kwargs['_last_error'] = error_msg
                        # 不立即切换到纯实现，而是尝试添加legacy provider加载代码
                        # 只有在多次尝试后仍然失败，才考虑切换到纯实现
                        if attempt >= max_retries - 2:  # 最后一次尝试时才考虑纯实现
                            kwargs['_force_pure_implementation'] = True
                    
                    # 检查是否是permute函数参数类型错误
                    is_permute_error = (
                        'invalid conversion from \'int\' to \'const int*\' in permute' in error_msg.lower() or
                        'invalid conversion from \'int\' to \'int*\' in permute' in error_msg.lower() or
                        ('permute' in error_msg.lower() and 'invalid conversion' in error_msg.lower() and 'int*' in error_msg.lower())
                    )
                    
                    if is_permute_error and attempt < max_retries - 1:
                        logger.warning("检测到permute函数参数类型错误，将在下次生成时强调参数类型")
                        kwargs['_permute_param_error'] = True
                        kwargs['_last_error'] = error_msg
                    
                    # 检查是否是代码不完整导致的失败
                    incomplete_errors = [
                        'expected \'}\' at end of input',
                        'expected \';\' at end of input',
                        'expected primary-expression at end of input',
                        'expected unqualified-id at end of input',
                        'expected \',\' or \'...\' at end of input',
                        'expected initializer at end of input',
                        'expected \')\' at end of input',
                        'redeclared as different kind',
                        'at end of input',
                        'was not declared in this scope',  # 未定义的标识符（如A, B, C, D, E, F）
                        'no return statement in function',  # 函数缺少return语句
                        'extra tokens at end of #include',  # include语句格式错误
                    ]
                    is_incomplete = any(err in error_msg for err in incomplete_errors)
                    
                    if is_incomplete and attempt < max_retries - 1:
                        logger.warning("检测到代码不完整错误，将在下次生成时强调代码完整性")
                        # 在kwargs中添加特殊标记，让_build_prompt知道需要强调完整性
                        kwargs['_incomplete_code_retry'] = True
                        kwargs['_last_error'] = error_msg
                    
                    if attempt < max_retries - 1:
                        hint_parts: List[str] = []
                        for fn in (
                            _link_retry_undefined_reference_hint,
                            _compile_retry_redeclaration_hint,
                            _link_retry_reloc_hint,
                        ):
                            h = fn(error_msg)
                            if h:
                                hint_parts.append(h.strip())
                        if hint_parts:
                            prev = kwargs.get("_last_error") or ""
                            kwargs["_last_error"] = (
                                "\n".join(hint_parts)
                                + ("\n" + prev if prev else "")
                                + "\n"
                                + error_msg[:2800]
                            )
                    
                    # 如果达到最大重试次数，不添加历史记录（因为验证失败）
                    if attempt >= max_retries - 1:
                        # 验证失败，不添加历史记录
                        pass
                        
                        agent._record_performance(
                            algorithm, mode, language,
                            validation_success=False,
                            test_success=None,
                            attempts=attempt + 1,
                            generation_time=total_generation_time,
                            error_message=error_msg[:500] if error_msg else None
                        )
                    
                    continue  # 重新生成
                
                # 测试代码（使用标准测试数据）
                if test_prep_data and agent.tester:
                    logger.info(f"正在使用标准测试数据测试生成的代码...")
                    
                    # 使用准备好的测试数据
                    plaintext = test_prep_data.get('plaintext')
                    expected_ciphertext = test_prep_data.get('expected_ciphertext')
                    key = test_prep_data.get('key')
                    iv = test_prep_data.get('iv')
                    aad = test_prep_data.get('aad')
                    algorithm_test = test_prep_data.get('algorithm')
                    operation_test = test_prep_data.get('operation')
                    public_key_n = test_prep_data.get('public_key_n')
                    public_key_e = test_prep_data.get('public_key_e')
                    private_key_n = test_prep_data.get('private_key_n')
                    private_key_d = test_prep_data.get('private_key_d')
                    ciphertexts = test_prep_data.get('ciphertexts', {})
                    
                    # 异步执行测试（在线程池中执行，避免阻塞）
                    async def run_test_in_executor():
                        loop = asyncio.get_event_loop()
                        ctx = contextvars.copy_context()
                        with ThreadPoolExecutor() as executor:
                            # RSA特殊处理
                            if algorithm_test.upper() == 'RSA':
                                if operation_test == '加密' and 'encrypt' in ciphertexts:
                                    expected_ciphertext_rsa = ciphertexts['encrypt']
                                    return await loop.run_in_executor(
                                        executor,
                                        lambda: ctx.run(
                                            agent.tester.test,
                                            code,
                                            language,
                                            plaintext=plaintext,
                                            expected_ciphertext=expected_ciphertext_rsa,
                                            public_key_n=public_key_n,
                                            public_key_e=public_key_e,
                                            algorithm=algorithm_test,
                                            mode=test_prep_data.get("mode"),
                                            allow_canonical_whole_file=_allow_canonical,
                                            allow_error_auto_repair=_allow_error_auto_repair,
                                            suppress_heuristic_warnings=_suppress_hw,
                                        ),
                                    )
                                elif operation_test == '签名' and 'sign' in ciphertexts:
                                    expected_signature = ciphertexts['sign']
                                    return await loop.run_in_executor(
                                        executor,
                                        lambda: ctx.run(
                                            agent.tester.test,
                                            code,
                                            language,
                                            plaintext=plaintext,
                                            expected_ciphertext=expected_signature,
                                            private_key_n=private_key_n,
                                            private_key_d=private_key_d,
                                            algorithm=algorithm_test,
                                            mode=test_prep_data.get("mode"),
                                            allow_canonical_whole_file=_allow_canonical,
                                            allow_error_auto_repair=_allow_error_auto_repair,
                                            suppress_heuristic_warnings=_suppress_hw,
                                        ),
                                    )
                                else:
                                    return (True, "RSA测试跳过（无测试数据）", {})
                            else:
                                # 对称加密算法测试
                                return await loop.run_in_executor(
                                    executor,
                                    lambda: ctx.run(
                                        agent.tester.test,
                                        code,
                                        language,
                                        plaintext=plaintext,
                                        expected_ciphertext=expected_ciphertext,
                                        key=key,
                                        iv=iv,
                                        aad=aad,
                                        algorithm=algorithm_test,
                                        mode=test_prep_data.get("mode"),
                                        allow_canonical_whole_file=_allow_canonical,
                                        allow_error_auto_repair=_allow_error_auto_repair,
                                        suppress_heuristic_warnings=_suppress_hw,
                                    ),
                                )
                    
                    success, message, details = await run_test_in_executor()
                    test_result = (success, message, details)

                    if (
                        not success
                        and language.lower() == "python"
                        and test_prep_data
                        and agent.tester
                    ):
                        qf = apply_common_quickfixes(code, language)
                        if qf != code:
                            logger.info("向量测试未通过：已应用确定性 import 补全，重保存并重测一次…")
                            code = qf
                            filepath = await save_code(
                                agent,
                                code,
                                filename,
                                algorithm,
                                mode,
                                allow_canonical_whole_file=_allow_canonical,
                                allow_error_auto_repair=_allow_error_auto_repair,
                            )
                            vr_q = await run_validation()
                            if vr_q is None or vr_q[0]:
                                success, message, details = await run_test_in_executor()
                                test_result = (success, message, details)

                    refine_used = 0
                    while (not success) and max_refine_rounds > 0 and refine_used < max_refine_rounds:
                        logger.info(f"Self-Refine：根据标准测试反馈第 {refine_used + 1}/{max_refine_rounds} 轮修正…")
                        test_feedback = {
                            'test_type': 'encrypt',
                            'actual': details.get('actual', ''),
                            'expected': details.get('expected', ''),
                            'message': message,
                            'plaintext': plaintext,
                            'expected_ciphertext': expected_ciphertext,
                            'key': key,
                            'iv': iv,
                            'actual_normalized': details.get('actual_normalized', ''),
                            'expected_normalized': details.get('expected_normalized', ''),
                            'output': details.get('output', ''),
                            'details': details,
                        }
                        code, improve_time = await improve_code(agent, code, algorithm, mode, operation, language, test_feedback, **kwargs)
                        total_generation_time += improve_time
                        refine_used += 1
                        code = apply_common_quickfixes(code, language)
                        filepath = await save_code(
                            agent,
                            code,
                            filename,
                            algorithm,
                            mode,
                            allow_canonical_whole_file=_allow_canonical,
                            allow_error_auto_repair=_allow_error_auto_repair,
                        )
                        validation_result, test_prep_data = await asyncio.gather(run_validation(), prepare_test_data())
                        if validation_result and not validation_result[0]:
                            err = validation_result[1] if isinstance(validation_result[1], str) else str(validation_result[1])
                            logger.warning(f"Self-Refine 修正后未通过验证，停止本轮微调: {err[:300]}")
                            break
                        success, message, details = await run_test_in_executor()
                        test_result = (success, message, details)
                    
                    if success:
                        logger.info("[OK] 代码通过标准测试！")

                        openssl_test_success = True
                        openssl_test_message = "未进行OpenSSL交叉对照（默认关闭）"
                        openssl_test_result = None

                        if _openssl_crosscheck_enabled(agent):
                            openssl_test_message = "未进行OpenSSL测试"
                            # OpenSSL 官方向量（可选）
                            if agent.test_data_loader:
                                openssl_test_data = None
                                if algorithm.upper() == 'RSA':
                                    if operation == '加密':
                                        openssl_test_data = agent.test_data_loader.get_openssl_test_data(algorithm, 'encrypt')
                                    elif operation == '签名':
                                        openssl_test_data = agent.test_data_loader.get_openssl_test_data(algorithm, 'sign')
                                else:
                                    openssl_test_data = agent.test_data_loader.get_openssl_test_data(algorithm, mode)

                                if openssl_test_data:
                                    logger.info("找到OpenSSL官方测试数据，进行额外测试...")
                                    try:
                                        openssl_plaintext = openssl_test_data.get('plaintext')
                                        openssl_expected = openssl_test_data.get('expected_ciphertext')
                                        openssl_key = openssl_test_data.get('key')
                                        openssl_iv = openssl_test_data.get('iv')
                                        openssl_aad = openssl_test_data.get('aad')
                                        openssl_public_key_n = openssl_test_data.get('public_key', {}).get('n')
                                        openssl_public_key_e = openssl_test_data.get('public_key', {}).get('e')
                                        openssl_private_key_n = openssl_test_data.get('private_key', {}).get('n')
                                        openssl_private_key_d = openssl_test_data.get('private_key', {}).get('d')

                                        async def run_openssl_test():
                                            loop = asyncio.get_event_loop()
                                            ctx = contextvars.copy_context()
                                            with ThreadPoolExecutor() as executor:
                                                if algorithm.upper() == 'RSA':
                                                    if operation == '加密' and openssl_public_key_n:
                                                        return await loop.run_in_executor(
                                                            executor,
                                                            lambda: ctx.run(
                                                                agent.tester.test,
                                                                code,
                                                                language,
                                                                plaintext=openssl_plaintext,
                                                                expected_ciphertext=openssl_expected,
                                                                public_key_n=openssl_public_key_n,
                                                                public_key_e=openssl_public_key_e,
                                                                algorithm=algorithm,
                                                                mode=mode,
                                                                allow_canonical_whole_file=_allow_canonical,
                                                                allow_error_auto_repair=_allow_error_auto_repair,
                                                                suppress_heuristic_warnings=_suppress_hw,
                                                            ),
                                                        )
                                                    elif operation == '签名' and openssl_private_key_n:
                                                        return await loop.run_in_executor(
                                                            executor,
                                                            lambda: ctx.run(
                                                                agent.tester.test,
                                                                code,
                                                                language,
                                                                plaintext=openssl_plaintext,
                                                                expected_ciphertext=openssl_expected,
                                                                private_key_n=openssl_private_key_n,
                                                                private_key_d=openssl_private_key_d,
                                                                algorithm=algorithm,
                                                                mode=mode,
                                                                allow_canonical_whole_file=_allow_canonical,
                                                                allow_error_auto_repair=_allow_error_auto_repair,
                                                                suppress_heuristic_warnings=_suppress_hw,
                                                            ),
                                                        )
                                                    else:
                                                        return (True, "RSA OpenSSL测试跳过（无测试数据）", {})
                                                return await loop.run_in_executor(
                                                    executor,
                                                    lambda: ctx.run(
                                                        agent.tester.test,
                                                        code,
                                                        language,
                                                        plaintext=openssl_plaintext,
                                                        expected_ciphertext=openssl_expected,
                                                        key=openssl_key,
                                                        iv=openssl_iv,
                                                        aad=openssl_aad,
                                                        algorithm=algorithm,
                                                        mode=mode,
                                                        allow_canonical_whole_file=_allow_canonical,
                                                        allow_error_auto_repair=_allow_error_auto_repair,
                                                        suppress_heuristic_warnings=_suppress_hw,
                                                    ),
                                                )

                                        openssl_success, openssl_msg, openssl_details = await run_openssl_test()
                                        openssl_test_success = openssl_success
                                        openssl_test_message = openssl_msg

                                        if openssl_success:
                                            logger.info("[OK] 代码通过OpenSSL官方测试！")
                                        else:
                                            logger.warning(f"[FAIL] 代码未通过OpenSSL官方测试: {openssl_msg}")
                                    except Exception as e:
                                        logger.warning(f"OpenSSL测试执行失败: {e}")
                                        openssl_test_success = False
                                        openssl_test_message = f"OpenSSL测试执行失败: {str(e)}"

                            # CLI 对照（DES/AES 加密）
                            if agent.openssl_tester and agent.openssl_tester.is_available():
                                algorithm_upper = algorithm.upper()
                                if algorithm_upper in ('DES', 'AES') and operation == '加密':
                                    logger.info("正在进行OpenSSL CLI 对照...")
                                    try:
                                        key_size = None
                                        if algorithm_upper == 'AES' and key:
                                            key_bytes = len(bytes.fromhex(key.replace(' ', '').replace('\n', '')))
                                            if key_bytes == 16:
                                                key_size = 128
                                            elif key_bytes == 24:
                                                key_size = 192
                                            elif key_bytes == 32:
                                                key_size = 256
                                            else:
                                                key_size = 128
                                        else:
                                            key_size = 128

                                        loop = asyncio.get_event_loop()
                                        with ThreadPoolExecutor() as executor:
                                            openssl_success, openssl_message, openssl_details = await loop.run_in_executor(
                                                executor,
                                                lambda: agent.openssl_tester.compare_with_openssl(
                                                    generated_ciphertext=details.get('actual_normalized', '')
                                                    or details.get('actual', ''),
                                                    plaintext_hex=plaintext,
                                                    key_hex=key,
                                                    iv_hex=iv,
                                                    algorithm=algorithm_upper,
                                                    mode=mode,
                                                    key_size=key_size,
                                                ),
                                            )
                                        openssl_test_result = (openssl_success, openssl_message, openssl_details)

                                        if openssl_success:
                                            logger.info("[OK] 代码通过OpenSSL CLI 对照！")
                                        else:
                                            logger.warning(f"[FAIL] 代码未通过OpenSSL CLI 对照: {openssl_message}")
                                    except Exception as e:
                                        logger.warning(f"OpenSSL CLI 对照失败: {e}")
                                        openssl_test_result = (False, f"OpenSSL测试失败: {str(e)}", {})

                        final_test_success = success and openssl_test_success

                        agent._record_performance(
                            algorithm, mode, language,
                            validation_success=True,
                            test_success=final_test_success,
                            attempts=attempt + 1,
                            generation_time=total_generation_time,
                        )

                        if _openssl_crosscheck_enabled(agent) and not openssl_test_success:
                            logger.warning("OpenSSL官方向量测试失败，将重试生成代码...")
                            continue

                        write_history = final_test_success
                        if (
                            _openssl_crosscheck_enabled(agent)
                            and openssl_test_result is not None
                            and not openssl_test_result[0]
                        ):
                            write_history = False
                            logger.warning("OpenSSL CLI 对照未通过，不写入历史记录")
                        
                        if write_history:
                            try:
                                combined_details = details.copy() if details else {}
                                combined_details['openssl_official_test'] = {
                                    'success': openssl_test_success,
                                    'message': openssl_test_message,
                                }
                                if openssl_test_result is not None:
                                    combined_details['openssl_compare'] = {
                                        'success': openssl_test_result[0],
                                        'message': openssl_test_result[1] if len(openssl_test_result) > 1 else '',
                                    }
                                distillation_active = distill_mod.is_distillation_target_provider(agent)
                                history_record = agent.history_manager.add_history(
                                    algorithm=algorithm,
                                    mode=mode,
                                    language=language,
                                    code=code,
                                    provider=agent.provider,
                                    operation=operation,
                                    validation_success=validation_result[0] if validation_result else True,
                                    test_success=True,
                                    generation_time=total_generation_time,
                                    attempts=attempt + 1,
                                    filename=filepath.name,
                                    test_details=combined_details,
                                    distillation_active=distillation_active,
                                )
                                history_id = history_record.get('id')
                                logger.info(f"[OK] 代码已保存到文件和历史记录: {filepath.name} (历史ID: {history_id})")
                                logger.info(f"[OK] 已添加历史记录: {algorithm} {mode or ''} - {language} (ID: {history_id})")
                                try:
                                    distill_mod.append_cloud_teacher_from_successful_run(
                                        agent,
                                        algorithm=algorithm,
                                        mode=mode,
                                        operation=operation,
                                        language=language,
                                        code=code,
                                    )
                                except Exception as te:
                                    logger.warning(f"蒸馏教师池追加跳过: {te}")
                            except Exception as e:
                                logger.warning(f"保存历史记录失败: {e}")
                        
                        return filepath, validation_result, test_result, openssl_test_result
                    else:
                        logger.warning(f"[FAIL] 代码未通过标准测试: {message}")
                        
                        # 测试失败，不添加历史记录
                        # 如果之前有历史记录（理论上不应该有），删除它
                        if history_id:
                            try:
                                agent.history_manager.delete_history(history_id)
                                logger.info(f"已删除未通过测试的历史记录: {history_id}")
                            except Exception as e:
                                logger.warning(f"删除历史记录失败: {e}")
                        
                        if attempt < max_retries - 1:
                            # 将标准向量失败注入下一轮 generate 的 last_error（经 build_prompt → 重试模板），
                            # 区别于「无信息重采样」。论文消融「无测试反馈」在 prompt_builder 中剥离含
                            # [VECTOR_TEST_RETRY] 的片段，使该档不包含此类测试驱动提示。
                            act = (
                                details.get("actual_normalized")
                                or details.get("actual", "")
                                or ""
                            )
                            exp = (
                                details.get("expected_normalized")
                                or details.get("expected", "")
                                or expected_ciphertext
                                or ""
                            )
                            act_s = str(act).replace("\n", " ").strip()
                            exp_s = str(exp).replace("\n", " ").strip()
                            if len(act_s) > 160:
                                act_s = act_s[:160] + "…"
                            if len(exp_s) > 160:
                                exp_s = exp_s[:160] + "…"
                            echo_hint = _vector_retry_plaintext_echo_hint(details or {}, plaintext)
                            kwargs["_last_error"] = (
                                "[VECTOR_TEST_RETRY] 标准向量功能测试未通过，请对照期望输出修正实现（IV/填充/字节序/链式模式；"
                                "有 OpenSSL 开发头时用 EVP，与题面模式一致）。"
                                " **无 `openssl/evp.h`（如 Windows 未装 libssl）：整文件禁止 `#include <openssl/...>`，须单文件标准库手写本题算法。**"
                                " DES：有头时 `EVP_des_cbc`/`cfb8`/`ofb`/`ecb`+legacy；禁止 `DES_ede3_*`、CBC 题里写 CFB；"
                                " **DES-CFB8/OFB：`EVP_EncryptUpdate` 须处理完整明文长度**，禁止 stdout 只有半长密文（如 `4e6574776f726b20`）。"
                                " DES-OFB 禁止误写为 CFB（勿碰 CFB golden F70F…）。"
                                " AES/SM4：禁止 des.h；CFB-8 须 `EVP_aes_128_cfb8` / 等价；**`main` 内禁止 `char *ciphertext` 与密文缓冲区同名**。"
                                " C++：`std::remove_if`→`<algorithm>`+`std::` 前缀；`std::bitset`→`<bitset>`；`std::vector`→`<vector>`。"
                                " SM4-CFB：防越界崩溃，输出密文 hex 长度须为 `2×明文字节`。"
                                " stdout 须含「密文:」前缀；禁止明文 hex / 模拟加密占位。\n"
                                f"测试器说明: {message}\n"
                                f"期望(片段): {exp_s}\n"
                                f"实际(片段): {act_s}"
                                + echo_hint
                            )
                            logger.info(
                                "将在下一次尝试中重新生成：已附加向量测试摘要供提示词重试段使用（消融无测试反馈时由 prompt_builder 剥离）。"
                            )
                            continue
                        else:
                            logger.error(f"已达到最大重试次数 ({max_retries})，代码未通过测试")
                            # 测试失败，不添加历史记录
                            # 记录性能：验证成功，测试失败
                            agent._record_performance(
                                algorithm, mode, language,
                                validation_success=True,
                                test_success=False,
                                attempts=attempt + 1,
                                generation_time=total_generation_time,
                                error_message=message[:500] if message else None
                            )
                            return filepath, validation_result, test_result, None
                else:
                    # 没有测试数据，无法做标准向量校验 — 不写入历史记录（避免未验证结果进入历史）
                    if validation_result and validation_result[0]:
                        logger.info(f"[OK] 代码已保存到文件: {filepath.name}（无标准测试数据，未写入历史）")
                    
                    # 记录性能：验证成功，无测试数据
                    if validation_result:
                        agent._record_performance(
                            algorithm, mode, language,
                            validation_success=validation_result[0],
                            test_success=None,
                            attempts=attempt + 1,
                            generation_time=total_generation_time,
                            error_message=None if validation_result[0] else (validation_result[1][:500] if isinstance(validation_result[1], str) else str(validation_result[1])[:500])
                        )
                    return filepath, validation_result, None, None
            
            # 如果所有重试都失败（理论上不应该到达这里，因为循环内已经返回）
            # 但为了安全，返回最后一次的结果
            return filepath, validation_result, test_result if test_result else None, None
    
        finally:
            reset_generation_allow_error_auto_repair(_error_repair_tok)
            reset_generation_allow_canonical_replace(_canon_replace_tok)
