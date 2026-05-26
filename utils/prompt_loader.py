"""
Prompt模板加载器
支持模板组合：通用模板 + 算法特定模板 + LLM特定模板
"""
import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
from utils.logger import setup_logger

logger = setup_logger()


class PromptLoader:
    """Prompt模板加载器，支持模板组合"""

    # 默认：逐文件加载仅 DEBUG，首次 INFO 输出「YAML 已加载成功」。若需逐文件列表，设环境变量 AICRYPTO_PROMPT_LOAD_VERBOSE=1
    _quiet_summary_logged: bool = False

    def __init__(self, prompts_dir: str = "prompts"):
        """
        初始化Prompt加载器
        
        Args:
            prompts_dir: prompt模板文件目录
        """
        self.prompts_dir = Path(prompts_dir)
        self._common_templates: Dict[str, Any] = {}
        # 仅来自 prompts/common/base_prompt.yaml（不合并同目录其它 yaml），供 common_only / base_prompt_only
        self._base_prompt_yaml_isolated: Dict[str, Any] = {}
        self._algorithm_templates: Dict[str, Dict[str, Any]] = {}
        self._algorithm_common: Dict[str, str] = {}  # 存储算法共同内容（如DES/common.yaml, AES/common.yaml）
        self._language_specific: Dict[str, Dict[str, str]] = {}  # 存储语言特定内容，格式：{language: {algorithm-mode: content}}
        self._llm_templates: Dict[str, Dict[str, Any]] = {}
        self._llm_algorithm_supplements: Dict[str, Dict[str, Any]] = {}
        self._load_templates()
    
    def _load_templates(self):
        """加载所有prompt模板"""
        verbose = os.environ.get("AICRYPTO_PROMPT_LOAD_VERBOSE", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

        def _load_info(msg: str) -> None:
            if verbose:
                logger.info(msg)
            else:
                logger.debug(msg)

        if not self.prompts_dir.exists():
            logger.warning(f"Prompt目录不存在: {self.prompts_dir}，将使用默认prompt")
            return
        
        # 1. 通用层：仅 prompts/common/base_prompt.yaml（I/O、失败驱动等已迁至 llms/<provider>/io_constraints.yaml）
        common_dir = self.prompts_dir / "common"
        if common_dir.exists():
            bp_only = common_dir / "base_prompt.yaml"
            if bp_only.exists():
                try:
                    with open(bp_only, "r", encoding="utf-8") as f:
                        loaded = yaml.safe_load(f) or {}
                        self._base_prompt_yaml_isolated = loaded
                        self._common_templates = dict(loaded)
                    _load_info(f"已加载通用基座（仅此文件）：{bp_only}")
                except Exception as e:
                    logger.error(f"加载 base_prompt.yaml 失败 {bp_only}: {e}")
                    self._base_prompt_yaml_isolated = {}
                    self._common_templates = {}
            else:
                logger.warning(f"未找到 {bp_only}，通用基座为空")
        else:
            # 向后兼容：如果common目录不存在，尝试加载common.yaml
            common_file = self.prompts_dir / "common.yaml"
            if common_file.exists():
                try:
                    with open(common_file, 'r', encoding='utf-8') as f:
                        self._common_templates = yaml.safe_load(f) or {}
                    _load_info(f"成功加载通用模板: {common_file}")
                except Exception as e:
                    logger.error(f"加载通用模板失败 {common_file}: {e}")
        
        # 2. 加载算法共同内容（algorithms/DES/common.yaml, algorithms/AES/common.yaml等）
        algorithms_dir = self.prompts_dir / "algorithms"
        if algorithms_dir.exists():
            # 加载算法共同内容（如DES/common.yaml, AES/common.yaml）
            for algorithm_dir in algorithms_dir.iterdir():
                if algorithm_dir.is_dir():
                    common_file = algorithm_dir / "common.yaml"
                    if common_file.exists():
                        try:
                            with open(common_file, 'r', encoding='utf-8') as f:
                                common_data = yaml.safe_load(f) or {}
                                algorithm_name = algorithm_dir.name.upper()  # DES, AES等
                                self._algorithm_common[algorithm_name] = common_data.get('algorithm_common', '')
                            _load_info(f"成功加载算法共同内容: {common_file}")
                        except Exception as e:
                            logger.error(f"加载算法共同内容失败 {common_file}: {e}")
        
        # 3. 加载语言特定模板（algorithms/c/*.yaml, algorithms/cpp/*.yaml等）
        for lang_dir_name in ['c', 'cpp', 'python']:
            lang_dir = algorithms_dir / lang_dir_name
            if lang_dir.exists():
                if lang_dir_name not in self._language_specific:
                    self._language_specific[lang_dir_name] = {}
                for yaml_file in lang_dir.glob("*.yaml"):
                    try:
                        algorithm_mode = yaml_file.stem  # 例如：AES-CBC
                        with open(yaml_file, 'r', encoding='utf-8') as f:
                            lang_data = yaml.safe_load(f) or {}
                            # 根据文件内容提取语言特定内容
                            if 'c_specific' in lang_data:
                                self._language_specific[lang_dir_name][algorithm_mode] = lang_data.get('c_specific', '')
                            elif 'cpp_specific' in lang_data:
                                self._language_specific[lang_dir_name][algorithm_mode] = lang_data.get('cpp_specific', '')
                            elif 'python_specific' in lang_data:
                                self._language_specific[lang_dir_name][algorithm_mode] = lang_data.get('python_specific', '')
                        _load_info(f"成功加载语言特定模板: {yaml_file}")
                    except Exception as e:
                        logger.error(f"加载语言特定模板失败 {yaml_file}: {e}")
        
        # 4. 加载算法特定模板（algorithms/*.yaml，不包括子目录中的文件）
        if algorithms_dir.exists():
            for yaml_file in algorithms_dir.glob("*.yaml"):
                try:
                    algorithm_name = yaml_file.stem  # 例如：DES-ECB
                    with open(yaml_file, 'r', encoding='utf-8') as f:
                        self._algorithm_templates[algorithm_name] = yaml.safe_load(f) or {}
                    _load_info(f"成功加载算法模板: {yaml_file}")
                except Exception as e:
                    logger.error(f"加载算法模板失败 {yaml_file}: {e}")
        
        # 5. 加载LLM特定模板：llms/<provider>/llm.yaml（或 <provider>.yaml）+ algorithms/*.yaml 按任务键补充
        llms_dir = self.prompts_dir / "llms"
        if llms_dir.exists():
            for sub in sorted(llms_dir.iterdir()):
                if not sub.is_dir():
                    continue
                provider_name = sub.name
                main_yaml = None
                for candidate in (sub / "llm.yaml", sub / f"{provider_name}.yaml"):
                    if candidate.exists():
                        main_yaml = candidate
                        break
                merged_llm: Dict[str, Any] = {}
                if main_yaml:
                    try:
                        with open(main_yaml, "r", encoding="utf-8") as f:
                            merged_llm = yaml.safe_load(f) or {}
                        _load_info(f"成功加载LLM模板: {main_yaml}")
                    except Exception as e:
                        logger.error(f"加载LLM模板失败 {main_yaml}: {e}")
                        merged_llm = {}
                ioc = sub / "io_constraints.yaml"
                if ioc.exists():
                    try:
                        with open(ioc, "r", encoding="utf-8") as f:
                            ioc_data = yaml.safe_load(f) or {}
                        for k, v in ioc_data.items():
                            merged_llm[k] = v
                        _load_info(f"已合并 LLM I/O 约束: {ioc}")
                    except Exception as e:
                        logger.error(f"加载 io_constraints.yaml 失败 {ioc}: {e}")
                self._llm_templates[provider_name] = merged_llm
                algo_supp_dir = sub / "algorithms"
                if algo_supp_dir.exists():
                    if provider_name not in self._llm_algorithm_supplements:
                        self._llm_algorithm_supplements[provider_name] = {}
                    for yf in algo_supp_dir.glob("*.yaml"):
                        task_key = yf.stem
                        try:
                            with open(yf, 'r', encoding='utf-8') as f:
                                self._llm_algorithm_supplements[provider_name][task_key] = yaml.safe_load(f) or {}
                            _load_info(f"成功加载LLM算法补充: {yf}")
                        except Exception as e:
                            logger.error(f"加载LLM算法补充失败 {yf}: {e}")
            for yaml_file in llms_dir.glob("*.yaml"):
                llm_name = yaml_file.stem
                if llm_name in self._llm_templates:
                    continue
                try:
                    with open(yaml_file, 'r', encoding='utf-8') as f:
                        self._llm_templates[llm_name] = yaml.safe_load(f) or {}
                    _load_info(f"成功加载LLM模板(根目录): {yaml_file}")
                except Exception as e:
                    logger.error(f"加载LLM模板失败 {yaml_file}: {e}")

        # 5b. 未单独提供 io_constraints.yaml 的提供商：从 openai 补齐 I/O 段（浅拷贝引用）
        _io_keys = (
            "code_requirements",
            "input_requirements",
            "key_requirements",
            "output_requirements",
            "ending",
            "failure_driven_constraints",
        )
        base_openai = self._llm_templates.get("openai") or {}
        for pname, pdata in list(self._llm_templates.items()):
            if not isinstance(pdata, dict):
                continue
            for ik in _io_keys:
                if ik not in pdata or pdata.get(ik) is None:
                    if ik in base_openai:
                        pdata[ik] = base_openai[ik]

        if not verbose and not PromptLoader._quiet_summary_logged:
            logger.info("YAML 已加载成功")
            PromptLoader._quiet_summary_logged = True
    
    @staticmethod
    def _ablation_allows(prompt_ablation: Optional[str], key: str) -> bool:
        """
        论文消融：控制提示栈包含哪些层（与 Web 全量 prompt 对齐用 prompt_ablation=None/'full'）。

        与 prompts/ 目录的对应（正文可写 @prompts/...）：
        - common_only → **仅** ``prompts/common/base_prompt.yaml`` 内 ``base_prompt`` 段
        - base_prompt_only → 同上，不含算法 / LLM 层
        - common_llm → **通用 + LLM**（``base_prompt`` + ``llms/<provider>/`` 的 ``coder_bootstrap`` + ``io_constraints`` 中 code/input/key/output/ending；**不含** ``prompts/algorithms/`` 下算法根与语言子目录、不含 ``llms/.../algorithms/*.yaml``）
        - common_llm_lang → 在 ``common_llm`` 上加入 ``prompts/algorithms/<DES|AES>/`` 的 ``algorithm_common`` / ``alg_spec`` / ``mode_spec`` 与 ``algorithms/<lang>/`` 的 ``lang_spec``
        - common_algorithm → **通用 + 算法**（``base_prompt`` + 算法根模板与 ``algorithm_common`` / ``alg_spec`` / ``mode_spec`` / ``test_data`` 等，**不含** ``algorithms/c|cpp|python`` 语言子目录）
        - common_algorithm_lang → **通用 + 算法 + 语言**（在上一档基础上含 ``lang_spec``，即 ``algorithms/<lang>/*.yaml``）
        - common_algorithm_llm_main → 在 ``common_algorithm_lang`` 上再开 **coder_bootstrap** 与 I/O 段（``code_req`` 等，来自 ``llms/<p>/io_constraints.yaml``），**不含** ref_cpp、蒸馏、``llm/algorithms/*.yaml`` 等
        - llm_main_only → 极端对照（base + 任务 + 测试数据 + LLM 主模板块）
        - no_prompt → **不加载分层领域模板**；仅向模型发送**一行最小任务描述**（算法/模式/语言/操作），不含 base_prompt、算法/LLM 层、测试数据等；测试反馈改进阶段仍可按脚本配置关闭（见 ``code_generator``）
        - full → **通用+算法+语言+完整 LLM 栈（本文方法）**：与 Web generate 一致（含 failure_driven、任务级 ``llms/.../algorithms/*.yaml``、蒸馏等）
        """
        pa = (prompt_ablation or "full").strip().lower()
        if pa in ("", "none"):
            pa = "full"
        if pa == "full":
            return True
        allow = {
            "no_prompt": set(),
            "common_only": {
                "base",
            },
            "base_prompt_only": {
                "base",
            },
            "common_llm": {
                "base",
                "alg_intro",
                "test_data",
                "coder_bootstrap",
                "code_req",
                "input_req",
                "key_req",
                "output_req",
                "ending",
            },
            "common_llm_lang": {
                "base",
                "alg_intro",
                "test_data",
                "alg_common",
                "alg_spec",
                "mode_spec",
                "lang_spec",
                "coder_bootstrap",
                "code_req",
                "input_req",
                "key_req",
                "output_req",
                "ending",
            },
            "common_algorithm": {
                "base",
                "alg_intro",
                "test_data",
                "alg_common",
                "alg_spec",
                "mode_spec",
            },
            "common_algorithm_lang": {
                "base",
                "alg_intro",
                "test_data",
                "alg_common",
                "alg_spec",
                "mode_spec",
                "lang_spec",
            },
            "common_algorithm_llm_main": {
                "base",
                "alg_intro",
                "test_data",
                "alg_common",
                "alg_spec",
                "mode_spec",
                "lang_spec",
                "coder_bootstrap",
                "code_req",
                "input_req",
                "key_req",
                "output_req",
                "ending",
            },
            "llm_main_only": {
                "base",
                "alg_intro",
                "test_data",
                "coder_bootstrap",
                "ref_cpp",
            },
        }
        s = allow.get(pa)
        if s is None:
            return True
        return key in s

    @staticmethod
    def _minimal_no_prompt_user_text(
        algorithm: Optional[str],
        mode: Optional[str],
        operation: Optional[str],
        language: str,
    ) -> str:
        """论文消融 ``no_prompt``：仅一行任务描述，无领域模板栈。"""
        algo = (algorithm or "指定").strip().upper()
        mod = (mode or "").strip().upper()
        if not mod:
            mod = "默认"
        op = (operation or "加密解密").strip()
        lang_map = {"python": "Python", "c": "C", "cpp": "C++", "c++": "C++"}
        ln = lang_map.get((language or "python").lower(), language or "Python")
        return f"请生成{algo}算法{mod}模式的{ln}{op}代码。"

    def get_prompt(self, provider: str, language: str, compact: bool = False, 
                   algorithm: Optional[str] = None, mode: Optional[str] = None,
                   operation: Optional[str] = None, test_data: Optional[Dict] = None,
                   is_incomplete_retry: bool = False, last_error: Optional[str] = None,
                   openssl_available: Optional[bool] = None,
                   distillation_prefix: Optional[str] = None,
                   prompt_ablation: Optional[str] = None,
                   **kwargs) -> str:
        """
        获取渲染后的prompt（组合通用模板 + 算法模板 + LLM模板）
        
        Args:
            provider: LLM提供商（openai, deepseek, claude, doubao）
            language: 编程语言（python, c, cpp）
            compact: 是否使用简化版prompt（用于OpenAI和Claude）
            algorithm: 算法名称（如DES, AES）
            mode: 模式（如ECB, CBC）
            operation: 操作（如加密解密）
            test_data: 测试数据
            is_incomplete_retry: 是否是代码不完整重试
            last_error: 上次错误信息
            distillation_prefix: 少样本蒸馏前缀（本地教师 JSONL）
            prompt_ablation: 论文消融模式；None 或 'full' 与 Web 全量一致，见 _ablation_allows
            **kwargs: 其他参数（如额外要求）
        
        Returns:
            渲染后的prompt字符串
        """
        prompt_parts = []

        pa_norm = (prompt_ablation or "full").strip().lower()
        if pa_norm in ("", "none"):
            pa_norm = "full"
        if pa_norm == "no_prompt":
            return PromptLoader._minimal_no_prompt_user_text(
                algorithm, mode, operation, language
            )

        llm_yaml_prov = self._effective_llm_yaml_provider(provider, prompt_ablation)

        # 1. 如果是重试，添加重试警告（从LLM模板获取；与 codex→openai 等 YAML 别名一致）
        if is_incomplete_retry and self._ablation_allows(prompt_ablation, "retry"):
            retry_text = self._get_retry_text(llm_yaml_prov, compact, last_error)
            if retry_text:
                prompt_parts.append(retry_text)

        # 1.5 LLM 专用「代码模型」补充（如 Qwen2.5 Coder）；可按语言追加 coder_bootstrap_python / _c / _cpp
        if self._ablation_allows(prompt_ablation, "coder_bootstrap"):
            coder_bootstrap = self._get_llm_coder_bootstrap(llm_yaml_prov, language)
            if coder_bootstrap:
                prompt_parts.append(coder_bootstrap)

        # 1.6 本地模型：教师 JSONL 少样本蒸馏（论文 § 蒸馏首轮注入）
        if distillation_prefix and self._ablation_allows(prompt_ablation, "distill"):
            prompt_parts.append(distillation_prefix.strip())
        
        # 2. 添加参考资源（从LLM模板获取，特别是对于C/C++）
        if (
            language.lower() in ['cpp', 'c++', 'c']
            and self._ablation_allows(prompt_ablation, "ref_cpp")
        ):
            reference_resources = self._get_reference_resources(llm_yaml_prov)
            if reference_resources:
                prompt_parts.append(reference_resources)
        
        # 3. 添加语言特定的规则（从LLM模板获取）
        if (
            language.lower() in ['cpp', 'c++', 'c']
            and self._ablation_allows(prompt_ablation, "ref_cpp")
        ):
            cpp_rules = self._get_cpp_rules(llm_yaml_prov, compact)
            if cpp_rules:
                prompt_parts.append(cpp_rules)
        
        # 4. 添加基础prompt（从通用模板获取）
        if self._ablation_allows(prompt_ablation, "base"):
            base_prompt = self._get_base_prompt(language, prompt_ablation)
            if base_prompt:
                prompt_parts.append(base_prompt)
        
        # 4.5 失败驱动精炼约束（来自 llms/<p>/io_constraints.yaml）
        if self._ablation_allows(prompt_ablation, "fd"):
            fd = self._get_failure_driven_constraints(language, llm_yaml_prov)
            if fd:
                prompt_parts.append(fd)
        
        # 5. 添加算法和模式信息
        if self._ablation_allows(prompt_ablation, "alg_intro"):
            algorithm_text = self._build_algorithm_intro(algorithm, mode, operation, language)
            if algorithm_text:
                prompt_parts.append(algorithm_text)
        
        # 6. 添加算法共同内容（从DES/common.yaml或AES/common.yaml获取）
        if (
            algorithm
            and self._ablation_allows(prompt_ablation, "alg_common")
            and self._algorithm_layers_allowed_aes_des(prompt_ablation, algorithm)
        ):
            algorithm_common = self._algorithm_common.get(algorithm.upper(), '')
            if algorithm_common:
                prompt_parts.append(algorithm_common)
        
        # 7. 添加算法特定说明（从算法模板获取）
        # 对于RSA，使用operation而不是mode
        algorithm_specific = ""
        if (
            algorithm
            and self._ablation_allows(prompt_ablation, "alg_spec")
            and self._algorithm_layers_allowed_aes_des(prompt_ablation, algorithm)
        ):
            if algorithm.upper() == 'RSA' and operation:
                algorithm_specific = self._get_algorithm_specific(algorithm, operation)
            else:
                algorithm_specific = self._get_algorithm_specific(algorithm, mode)
        if algorithm_specific:
            # 如果OpenSSL不可用或DES不支持，且是C/C++语言，修改prompt要求使用纯实现
            force_pure = kwargs.get('_force_pure_implementation', False)
            if language.lower() in ['c', 'cpp', 'c++'] and (openssl_available is False or force_pure):
                # 替换OpenSSL相关要求为纯实现要求
                algorithm_specific = algorithm_specific.replace(
                    '🚨🚨🚨 必须使用OpenSSL库！🚨🚨🚨',
                    '🚨🚨🚨 OpenSSL 3.0不支持DES算法（需要legacy provider），必须使用纯实现！🚨🚨🚨'
                )
                algorithm_specific = algorithm_specific.replace(
                    '必须使用OpenSSL库',
                    '必须使用纯实现（OpenSSL 3.0不支持DES）'
                )
                algorithm_specific = algorithm_specific.replace(
                    '强烈推荐使用OpenSSL库',
                    '必须使用纯实现（OpenSSL 3.0不支持DES）'
                )
                algorithm_specific = algorithm_specific.replace(
                    '如果使用OpenSSL库（强烈推荐',
                    '如果使用纯实现（OpenSSL 3.0不支持DES'
                )
            prompt_parts.append(algorithm_specific)
        
        # 8. 添加模式特定说明（从算法模板获取）
        # 对于RSA，使用operation而不是mode
        mode_specific = ""
        if (
            algorithm
            and self._ablation_allows(prompt_ablation, "mode_spec")
            and self._algorithm_layers_allowed_aes_des(prompt_ablation, algorithm)
        ):
            if algorithm.upper() == 'RSA' and operation:
                mode_specific = self._get_mode_specific(algorithm, operation)
            else:
                mode_specific = self._get_mode_specific(algorithm, mode)
        if mode_specific:
            prompt_parts.append(mode_specific)
        
        llm_alg_supp = ""
        if self._ablation_allows(prompt_ablation, "llm_algo_supp"):
            llm_alg_supp = self._get_llm_algorithm_supplement(
                provider or "",
                llm_yaml_prov,
                language,
                algorithm,
                mode,
                operation,
            ) or ""
            if llm_alg_supp:
                prompt_parts.append(llm_alg_supp)
        
        # 8.5. 添加语言特定说明（从c/或cpp/目录加载）
        if (
            algorithm
            and mode
            and self._ablation_allows(prompt_ablation, "lang_spec")
            and self._algorithm_layers_allowed_aes_des(prompt_ablation, algorithm)
        ):
            algorithm_key = f"{algorithm.upper()}-{mode}"
            lang_key = language.lower()
            if lang_key == 'c++':
                lang_key = 'cpp'
            if lang_key in self._language_specific:
                lang_specific = self._language_specific[lang_key].get(algorithm_key, '')
                if lang_specific:
                    prompt_parts.append(lang_specific)
        
        # 8.55 豆包：任务级算法补充二次置于语言模板之后，降低被通用长模板覆盖的概率
        if (
            provider
            and str(provider).lower() == "doubao"
            and llm_alg_supp
            and self._ablation_allows(prompt_ablation, "llm_algo_supp")
        ):
            prompt_parts.append(
                "**【豆包 · 任务重申（紧接语言模板后，须与上文一并遵守）】**\n\n"
                + llm_alg_supp.strip()
            )
        
        # 9. 添加测试数据
        if test_data and self._ablation_allows(prompt_ablation, "test_data"):
            test_data_text = self._format_test_data(test_data, algorithm)
            if test_data_text:
                prompt_parts.append(test_data_text)
        
        # 10. 代码 / I/O 硬性要求（llms/<p>/io_constraints.yaml）
        if self._ablation_allows(prompt_ablation, "code_req"):
            code_requirements = self._get_code_requirements(language, llm_yaml_prov)
            if code_requirements:
                prompt_parts.append(code_requirements)

        if self._ablation_allows(prompt_ablation, "input_req"):
            input_requirements = self._get_input_requirements(language, llm_yaml_prov)
            if input_requirements:
                prompt_parts.append(input_requirements)

        if self._ablation_allows(prompt_ablation, "key_req"):
            key_requirements = self._get_key_requirements(llm_yaml_prov)
            if key_requirements:
                prompt_parts.append(key_requirements)

        if self._ablation_allows(prompt_ablation, "output_req"):
            output_requirements = self._get_output_requirements(llm_yaml_prov)
            if output_requirements:
                prompt_parts.append(output_requirements)
        
        # 14. 添加额外要求
        if kwargs and self._ablation_allows(prompt_ablation, "extra"):
            extra_requirements = self._format_extra_requirements(kwargs)
            if extra_requirements:
                prompt_parts.append(extra_requirements)
        
        # 15. 结尾说明（io_constraints.yaml）
        if self._ablation_allows(prompt_ablation, "ending"):
            ending = self._get_ending(llm_yaml_prov)
            if ending:
                prompt_parts.append(ending)
        
        # 组合前做去重：避免同类约束在多来源模板中重复注入，节省上下文
        deduped_parts = self._deduplicate_prompt_parts(prompt_parts)
        return "\n\n".join(filter(None, deduped_parts))

    def _deduplicate_prompt_parts(self, parts: list) -> list:
        """按规范化文本去重（保留首次出现顺序），减少prompt冗余。"""
        out = []
        seen = set()
        for p in parts:
            if not p:
                continue
            s = str(p).strip()
            if not s:
                continue
            # 规范化空白后去重：兼容缩进/换行差异导致的“同内容重复”
            norm = " ".join(s.split())
            if norm in seen:
                continue
            seen.add(norm)
            out.append(s)
        return out

    def _algorithm_layers_allowed_aes_des(
        self, prompt_ablation: Optional[str], algorithm: Optional[str]
    ) -> bool:
        """算法特定 / LLM-main 档：算法层材料仅来自 prompts/algorithms/AES 与 DES。"""
        pa = (prompt_ablation or "full").strip().lower()
        if pa in ("", "none"):
            pa = "full"
        if pa not in (
            "common_algorithm",
            "common_algorithm_lang",
            "common_algorithm_llm_main",
            "common_llm_lang",
        ):
            return True
        if not algorithm:
            return False
        return algorithm.upper() in ("AES", "DES")

    def _effective_llm_yaml_provider(
        self, provider: str, prompt_ablation: Optional[str]
    ) -> str:
        """始终使用当前 ``provider`` 对应的 ``prompts/llms/<provider>/``（豆包/DeepSeek 各自 llm.yaml）。

        ``codex``（CloseAI 等 OpenAI 兼容网关）无独立目录时复用 ``openai`` 提示栈。

        旧版 ``common_algorithm_llm_main`` 曾对非 claude/deepseek 回退 deepseek；消融需对齐各家 YAML，已取消回退。
        """
        pl = (provider or "deepseek").lower()
        if pl == "codex":
            return "openai"
        # qwen_coder_local* 变体复用同一提示栈
        if pl.startswith("qwen_coder_local_"):
            return "qwen_coder_local"
        return pl
    
    def _get_base_prompt(self, language: str, prompt_ablation: Optional[str] = None) -> Optional[str]:
        """从通用模板获取基础prompt；common_only / base_prompt_only 仅用 base_prompt.yaml。"""
        pa = (prompt_ablation or "full").strip().lower()
        if pa in ("", "none"):
            pa = "full"
        if pa in ("common_only", "base_prompt_only"):
            base_prompts = self._base_prompt_yaml_isolated.get("base_prompt", {})
            if isinstance(base_prompts, dict):
                return base_prompts.get(language.lower(), "") or ""
            return ""
        base_prompts = self._common_templates.get('base_prompt', {})
        return base_prompts.get(language.lower(), '')
    
    def _llm_io_root(self, io_provider: Optional[str]) -> Dict[str, Any]:
        """I/O 与失败驱动等来自 ``llms/<provider>/io_constraints.yaml``（缺省时已在加载阶段从 openai 补齐）。"""
        p = (io_provider or "openai").lower()
        root = self._llm_templates.get(p)
        if isinstance(root, dict):
            return root
        return self._llm_templates.get("openai") or {}

    def _get_failure_driven_constraints(
        self, language: str, io_provider: Optional[str] = None
    ) -> Optional[str]:
        """失败驱动精炼（``llms/<p>/io_constraints.yaml`` 中 ``failure_driven_constraints``）"""
        fd = self._llm_io_root(io_provider).get("failure_driven_constraints", {})
        if isinstance(fd, dict):
            return fd.get(language.lower(), "") or None
        return None

    def _get_code_requirements(
        self, language: str, io_provider: Optional[str] = None
    ) -> Optional[str]:
        """代码要求（``io_constraints.yaml`` → ``code_requirements``）"""
        code_reqs = self._llm_io_root(io_provider).get("code_requirements", {})
        if isinstance(code_reqs, dict):
            return code_reqs.get(language.lower(), "") or ""
        return ""

    def _get_input_requirements(
        self, language: str, io_provider: Optional[str] = None
    ) -> Optional[str]:
        input_reqs = self._llm_io_root(io_provider).get("input_requirements", {})
        if isinstance(input_reqs, dict):
            return input_reqs.get(language.lower(), "") or ""
        return ""

    def _get_key_requirements(self, io_provider: Optional[str] = None) -> Optional[str]:
        key_reqs = self._llm_io_root(io_provider).get("key_requirements", {})
        if isinstance(key_reqs, str):
            return key_reqs
        if isinstance(key_reqs, dict):
            return key_reqs.get("content", "")
        return ""

    def _get_output_requirements(self, io_provider: Optional[str] = None) -> Optional[str]:
        output_reqs = self._llm_io_root(io_provider).get("output_requirements", {})
        if isinstance(output_reqs, str):
            return output_reqs
        if isinstance(output_reqs, dict):
            return output_reqs.get("content", "")
        return ""

    def _get_ending(self, io_provider: Optional[str] = None) -> Optional[str]:
        ending = self._llm_io_root(io_provider).get("ending", {})
        if isinstance(ending, str):
            return ending
        if isinstance(ending, dict):
            return ending.get("content", "")
        return ""
    
    def _get_algorithm_specific(self, algorithm: Optional[str], mode: Optional[str]) -> Optional[str]:
        """从算法模板获取算法特定说明"""
        if not algorithm:
            return None
        
        # 对于RSA，mode可能是operation（如"密钥生成"、"加密"等）
        # 对于其他算法，mode是模式（如"ECB"、"CBC"等）
        if not mode:
            return None
        
        # 构建算法-模式/操作键（例如：DES-ECB, DES-CBC, RSA-密钥生成, RSA-加密）
        algorithm_key = f"{algorithm.upper()}-{mode}"
        template = self._algorithm_templates.get(algorithm_key)
        
        if template:
            return template.get('algorithm_specific', '')
        
        return None
    
    def _get_mode_specific(self, algorithm: Optional[str], mode: Optional[str]) -> Optional[str]:
        """从算法模板获取模式特定说明"""
        if not algorithm:
            return None
        
        # 对于RSA，mode可能是operation（如"密钥生成"、"加密"等）
        # 对于其他算法，mode是模式（如"ECB"、"CBC"等）
        if not mode:
            return None
        
        # 构建算法-模式/操作键（例如：DES-ECB, DES-CBC, RSA-密钥生成, RSA-加密）
        algorithm_key = f"{algorithm.upper()}-{mode}"
        template = self._algorithm_templates.get(algorithm_key)
        
        if template:
            return template.get('mode_specific', '')
        
        return None
    
    def _llm_supplement_task_key(
        self,
        algorithm: Optional[str],
        mode: Optional[str],
        operation: Optional[str],
    ) -> Optional[str]:
        """与算法模板 stem 一致：AES-CBC、SM4-OFB、RSA-加密 等。"""
        if not algorithm:
            return None
        if algorithm.upper() == 'RSA':
            if not operation:
                return None
            return f"RSA-{operation}"
        if not mode:
            return None
        return f"{algorithm.upper()}-{mode}"
    
    def _get_llm_algorithm_supplement(
        self,
        raw_provider: str,
        llm_yaml_prov: str,
        language: str,
        algorithm: Optional[str],
        mode: Optional[str],
        operation: Optional[str],
    ) -> Optional[str]:
        """llms/<provider>/algorithms/<任务键>.yaml：supplement + 按语言的 python/c/cpp 段。

        ``codex`` 在 ``_effective_llm_yaml_provider`` 中会映射为 ``openai``，但算法 YAML 可按真实
        provider 放在 ``llms/codex/algorithms/``。

        **合并顺序**：若 ``raw_provider=codex``、``llm_yaml_prov=openai`` 且二者均有该任务 YAML，则
        **先 codex 再 openai**（专属硬约束置顶，避免模型先读 OpenAI 长篇后仍输出手搓 DES）；其余情况仍为
        effective 在前、raw 在后。
        """
        task_key = self._llm_supplement_task_key(algorithm, mode, operation)
        if not task_key:
            return None

        lang_key = language.lower()
        if lang_key == "c++":
            lang_key = "cpp"

        def pull(p: str) -> Optional[Dict[str, Any]]:
            block = self._llm_algorithm_supplements.get(p.lower(), {})
            return block.get(task_key)

        def _assemble_block(data: Dict[str, Any], pin_provider: str) -> Optional[str]:
            parts: List[str] = []
            pl = (pin_provider or "").strip().lower()
            if pl == "qwen_coder_local" or pl.startswith("qwen_coder_local_"):
                pin = data.get(f"generate_pin_{lang_key}") or data.get("generate_pin")
                if isinstance(pin, str) and pin.strip():
                    parts.append(
                        "**【Qwen · 批量 9 槽 · 须 code_history 落库】**\n" + pin.strip()
                    )
            mand = data.get("mandatory")
            if isinstance(mand, str) and mand.strip():
                parts.append("**【强制 · 违反任一条即失败】**\n" + mand.strip())
            sup = data.get("supplement")
            if isinstance(sup, str) and sup.strip():
                parts.append(sup.strip())
            lang_blob = data.get(lang_key)
            if isinstance(lang_blob, str) and lang_blob.strip():
                parts.append(lang_blob.strip())
            if not parts:
                return None
            return "\n\n".join(parts)

        raw_l = (raw_provider or "").strip().lower()
        eff_l = (llm_yaml_prov or "").strip().lower()

        ordered: List[str] = []
        if raw_l == "codex" and eff_l == "openai" and pull("codex"):
            ordered.append("codex")
            if pull("openai"):
                ordered.append("openai")
        else:
            for p in (eff_l, raw_l):
                if p and p not in ordered:
                    ordered.append(p)

        chunks: List[str] = []
        for pname in ordered:
            data = pull(pname)
            if not data:
                continue
            text = _assemble_block(data, raw_l if pname == raw_l else pname)
            if not text:
                continue
            if pname == "codex" and raw_l == "codex":
                text = (
                    "**【Codex（CloseAI / OpenAI 兼容 · 提交硬约束）】**\n\n" + text
                )
            chunks.append(text)

        if chunks:
            return "\n\n".join(chunks)

        if raw_l == "qwen_coder_local" or raw_l.startswith("qwen_coder_local_"):
            data = pull("openai")
            if data:
                text = _assemble_block(data, raw_l)
                if text:
                    return text
        return None

    def get_test_feedback_improve(
        self,
        provider: str,
        language: str,
        algorithm: Optional[str],
        mode: Optional[str] = None,
        operation: Optional[str] = None,
    ) -> str:
        """``llms/<provider>/algorithms/<任务>.yaml`` 的 ``test_feedback_improve``（及按语言后缀）+ ``llm.yaml`` 全局段。"""
        parts: List[str] = []
        prov = (provider or "").strip().lower()
        llm_raw = self._get_llm_specific_raw(prov)
        global_tf = llm_raw.get("test_feedback_improve")
        if isinstance(global_tf, str) and global_tf.strip():
            parts.append(global_tf.strip())

        task_key = self._llm_supplement_task_key(algorithm, mode, operation)
        if not task_key:
            return "\n\n".join(parts) if parts else ""

        lang_key = language.lower()
        if lang_key == "c++":
            lang_key = "cpp"

        data = self._llm_algorithm_supplements.get(prov, {}).get(task_key)
        if not isinstance(data, dict):
            return "\n\n".join(parts) if parts else ""

        lang_tf = data.get(f"test_feedback_improve_{lang_key}")
        if isinstance(lang_tf, str) and lang_tf.strip():
            parts.append(lang_tf.strip())
        else:
            base = data.get("test_feedback_improve")
            if isinstance(base, str) and base.strip():
                parts.append(base.strip())
        return "\n\n".join(parts) if parts else ""
    
    def _get_llm_specific_raw(self, provider: str) -> Dict[str, Any]:
        """读取某提供商的 llm_specific；支持 `inherits_from: <其它 provider>` 浅合并（子项覆盖基座）。"""
        p = (provider or "").strip().lower()
        tpl_key = p
        if p.startswith("qwen_coder_local_") and p not in self._llm_templates:
            tpl_key = "qwen_coder_local"
        llm_template = self._llm_templates.get(tpl_key)
        if not llm_template:
            return {}
        raw = llm_template.get("llm_specific", {}) or {}
        if not isinstance(raw, dict):
            return {}
        inherit_name = raw.get("inherits_from")
        if not inherit_name:
            return raw
        base_tpl = self._llm_templates.get(str(inherit_name).lower(), {})
        base = base_tpl.get("llm_specific", {}) or {}
        if not isinstance(base, dict):
            logger.warning(
                f"llm_specific.inherits_from 指向的 {inherit_name!r} 无有效 llm_specific，已忽略基座"
            )
            return {k: v for k, v in raw.items() if k != "inherits_from"}
        merged: Dict[str, Any] = dict(base)
        for k, v in raw.items():
            if k == "inherits_from":
                continue
            merged[k] = v
        return merged

    def _get_llm_coder_bootstrap(self, provider: str, language: Optional[str] = None) -> Optional[str]:
        """Qwen Coder 等在 user prompt 前置的补充约束；可选 `coder_bootstrap_<python|c|cpp>` 按语言追加。
        `coder_bootstrap_prepend`（若存在）**先于** `coder_bootstrap` 拼接，便于本地小模型把硬性规则放在最前。"""
        raw = self._get_llm_specific_raw(provider)
        prepend = (raw.get("coder_bootstrap_prepend") or "").strip()
        parts = []
        if prepend:
            parts.append(prepend)
        parts.append((raw.get("coder_bootstrap") or "").strip())
        lk = (language or "python").lower()
        if lk in ("c++",):
            lk = "cpp"
        extra = (raw.get(f"coder_bootstrap_{lk}") or "").strip()
        if extra:
            parts.append(extra)
        text = "\n\n".join(p for p in parts if p)
        return text or None

    def _get_cpp_rules(self, provider: str, compact: bool) -> Optional[str]:
        """从LLM模板获取C++规则；qwen_coder_local 未配置时回退 openai。"""
        def pull(p: str) -> str:
            llm_specific = self._get_llm_specific_raw(p)
            use_compact = llm_specific.get('compact', False)
            c = compact
            if use_compact != c:
                c = use_compact
            if c:
                return llm_specific.get('cpp_rules_compact', '') or ''
            return llm_specific.get('cpp_rules_full', '') or ''

        text = pull(provider)
        if text:
            return text
        pl = provider.lower()
        if pl == "qwen_coder_local" or pl.startswith("qwen_coder_local_"):
            text = pull("openai")
        return text or None
    
    def _get_reference_resources(self, provider: str) -> Optional[str]:
        """从LLM模板获取参考资源；qwen_coder_local 未配置时回退 openai。"""
        text = (self._get_llm_specific_raw(provider).get("reference_resources") or "").strip()
        if text:
            return text
        if provider.lower() == "qwen_coder_local":
            text = (self._get_llm_specific_raw("openai").get("reference_resources") or "").strip()
        return text or None
    
    def _get_retry_text(self, provider: str, compact: bool, last_error: Optional[str]) -> Optional[str]:
        """从LLM模板获取重试提示；无配置时 qwen_coder_local 回退 openai。"""
        llm_specific = self._get_llm_specific_raw(provider)
        use_compact = llm_specific.get('compact', False)
        if use_compact != compact:
            compact = use_compact
        
        if compact:
            retry_template = llm_specific.get('incomplete_retry_compact', '')
        else:
            retry_template = llm_specific.get('incomplete_retry_full', '')
        
        pl = provider.lower()
        if not retry_template and (pl == "qwen_coder_local" or pl.startswith("qwen_coder_local_")):
            fb = self._get_llm_specific_raw("openai")
            if compact:
                retry_template = fb.get("incomplete_retry_compact", "")
            else:
                retry_template = fb.get("incomplete_retry_full", "")
        
        if retry_template:
            # 安全地替换 {last_error} 占位符
            if last_error:
                # 转义大括号，避免格式化错误
                error_text = last_error[:500] if last_error else ''
                # 替换 {last_error} 占位符
                retry_text = retry_template.replace('{last_error}', error_text)
            else:
                # 如果没有错误信息，移除 {last_error} 占位符所在的行或替换为空
                retry_text = retry_template.replace('{last_error}', '')
            return retry_text
        
        return None
    
    def _build_algorithm_intro(self, algorithm: Optional[str], mode: Optional[str], 
                               operation: Optional[str], language: str) -> str:
        """构建算法介绍文本"""
        lang_name = self._get_language_name(language)
        
        if algorithm and mode:
            intro = f"请帮我编写一个使用{algorithm}算法的{mode}模式"
        elif algorithm:
            intro = f"请帮我编写一个使用{algorithm}算法"
        else:
            intro = "请帮我编写密码学代码"
        
        if operation:
            if algorithm and algorithm.upper() == 'RSA':
                intro += self._format_rsa_operation(operation, language)
            else:
                intro += f"进行{operation}的{lang_name}代码。"
        else:
            intro += f"的完整{lang_name}代码。"
        
        return intro
    
    def _format_test_data(self, test_data: Dict, algorithm: Optional[str]) -> str:
        """格式化测试数据"""
        text = "标准测试数据（用于验证代码正确性，但不要硬编码到代码中）：\n"
        
        if 'plaintext' in test_data:
            text += f"测试明文（16进制）：{test_data['plaintext']}\n"
        if 'key' in test_data:
            text += f"测试密钥（16进制）：{test_data['key']}\n"
        if 'iv' in test_data:
            text += f"测试初始化向量IV（16进制）：{test_data['iv']}\n"
        if test_data.get('aad'):
            text += f"测试附加认证数据AAD（16进制，AES-GCM 必用）：{test_data['aad']}\n"
        if 'expected_ciphertext' in test_data:
            text += f"预期密文（16进制）：{test_data['expected_ciphertext']}\n"
        
        if algorithm and algorithm.upper() == 'RSA':
            if 'public_key' in test_data:
                text += f"测试公钥 n（16进制）：{test_data['public_key'].get('n', '')}\n"
                text += f"测试公钥 e（16进制）：{test_data['public_key'].get('e', '')}\n"
            if 'private_key' in test_data:
                text += f"测试私钥 n（16进制）：{test_data['private_key'].get('n', '')}\n"
                text += f"测试私钥 d（16进制）：{test_data['private_key'].get('d', '')}\n"
            if 'ciphertexts' in test_data:
                if 'encrypt' in test_data['ciphertexts']:
                    text += f"预期加密结果（16进制）：{test_data['ciphertexts']['encrypt']}\n"
                if 'sign' in test_data['ciphertexts']:
                    text += f"预期签名结果（16进制）：{test_data['ciphertexts']['sign']}\n"
        
        text += "\n重要说明：\n"
        text += "1. 上述测试数据仅用于验证代码正确性，不要硬编码到代码中！\n"
        text += "2. 代码必须能够接受任意输入，而不是只处理上述测试数据\n"
        text += "3. 代码应该优先从环境变量读取输入（如果存在），否则从stdin读取，或提供交互式输入\n"
        text += "4. 环境变量名称：TEST_PLAINTEXT（明文）、TEST_CIPHERTEXT（密文）、TEST_KEY（密钥）、TEST_IV（初始化向量）"
        if test_data.get('aad'):
            text += "、TEST_AAD（GCM 附加认证数据，十六进制字符串）"
        text += "\n"
        if algorithm and algorithm.upper() == 'RSA':
            text += "5. 对于RSA：TEST_PUBLIC_KEY_N、TEST_PUBLIC_KEY_E、TEST_PRIVATE_KEY_N、TEST_PRIVATE_KEY_D\n"
        text += "6. 当使用上述测试数据作为环境变量时，代码必须产生完全匹配的预期结果\n"
        text += "7. 但代码必须能够处理其他输入，不能只处理测试数据\n"
        
        return text
    
    def _format_rsa_operation(self, operation: Optional[str], language: str) -> str:
        """格式化RSA操作说明"""
        lang_name = self._get_language_name(language)
        
        if operation == '密钥生成':
            return f"进行密钥生成的{lang_name}代码。\n\n"
        elif operation == '加密':
            return f"进行加密的{lang_name}代码。\n\n"
        elif operation == '解密':
            return f"进行解密的{lang_name}代码。\n\n"
        elif operation == '签名':
            return f"进行数字签名的{lang_name}代码。\n\n"
        elif operation == '验证':
            return f"进行签名验证的{lang_name}代码。\n\n"
        else:
            return f"的完整{lang_name}代码，包括密钥生成、加密、解密、数字签名和验证功能。\n\n"
    
    def _format_extra_requirements(self, kwargs: Dict) -> str:
        """格式化额外要求"""
        lines: List[str] = []
        for key, value in kwargs.items():
            if key in ('_task_id', 'prompt_ablation'):  # 跳过内部参数
                continue
            lines.append(f"- {key}: {value}")
        if not lines:
            return ""
        return "具体要求：\n" + "\n".join(lines)
    
    def _get_language_name(self, language: str) -> str:
        """获取语言名称"""
        lang_map = {
            'python': 'Python',
            'c': 'C',
            'cpp': 'C++',
            'c++': 'C++'
        }
        return lang_map.get(language.lower(), language)