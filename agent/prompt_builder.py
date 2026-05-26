"""提示词构建相关功能模块"""
from typing import Any, Dict, Optional
from utils.logger import setup_logger
from utils.prompt_loader import PromptLoader
from utils import distillation as distill_mod
from agent.prompts import LANGUAGE_PROMPTS

logger = setup_logger()


def _skip_distillation_prefix(agent, kwargs: Dict[str, Any], use_compact_prompt: bool) -> bool:
    """精简/本地线路下跳过教师 JSONL 注入，否则易与长模板叠加超过 Ollama 32K（见 batch 日志 context limit）。"""
    if kwargs.get("_skip_distillation"):
        return True
    if kwargs.get("_keep_distillation") or kwargs.get("_force_distillation"):
        return False
    if kwargs.get("_force_compact_prompt"):
        return True
    if use_compact_prompt and distill_mod.is_distillation_target_provider(agent):
        return True
    return False


def _resolve_use_compact_prompt(agent, kwargs: Dict[str, Any]) -> bool:
    """是否使用精简 prompt：小上下文/本地线路默认开启，避免输入估算超过 max_context。
    豆包仍用完整 prompt（与 prompts/llms/doubao/llm.yaml 一致）。
    """
    if kwargs.get("_force_compact_prompt"):
        return True
    prov = (getattr(agent, "provider", None) or "").strip().lower()
    if prov == "doubao":
        return False
    if prov in ("openai", "claude", "codex"):
        return True
    cfg: Dict[str, Any] = {}
    if getattr(agent, "config", None):
        cfg = agent.config.get_llm_config(prov) or {}
    base = (cfg.get("base_url") or "").lower()
    if "127.0.0.1" in base or "localhost" in base:
        return True
    if any(x in prov for x in ("local", "ollama")):
        return True
    mc = cfg.get("max_context")
    if mc is not None:
        try:
            if int(mc) <= 65536:
                return True
        except (TypeError, ValueError):
            pass
    return False


def get_system_prompt(language: str = 'python') -> str:
    """获取指定语言的系统提示词"""
    return LANGUAGE_PROMPTS.get(language.lower(), LANGUAGE_PROMPTS['python'])


def no_prompt_ablation(kwargs: Optional[Dict[str, Any]] = None) -> bool:
    """论文消融 ``prompt_ablation=no_prompt``：不加载分层领域模板，仅单行任务描述（见 ``PromptLoader._minimal_no_prompt_user_text``）。"""
    if not kwargs:
        return False
    return (kwargs.get("prompt_ablation") or "").strip().lower() == "no_prompt"


def resolve_llm_system_prompt(language: str, kwargs: Optional[Dict[str, Any]] = None) -> str:
    """生成/重试调用 LLM 时的 system 消息；``no_prompt`` 下为空串（任务描述仅在 user 侧）。"""
    if no_prompt_ablation(kwargs):
        return ""
    return get_system_prompt(language)


def llm_user_content_for_api(user_prompt: str, system_prompt: str) -> str:
    """OpenAI 兼容接口通常要求 user 消息非空；领域提示长度为 0 时用单空格占位。"""
    if (user_prompt or "").strip() or (system_prompt or "").strip():
        return user_prompt
    return " "
    


def build_prompt(agent, algorithm: str, mode: Optional[str] = None, 
                     operation: str = "加密解密", language: str = 'python', 
                     test_data: Optional[Dict] = None, **kwargs) -> str:
        """构建提示词"""
        lang_name = {'python': 'Python', 'c': 'C', 'cpp': 'C++', 'c++': 'C++'}.get(language.lower(), 'Python')
        
        # 检查是否是重试（因为代码不完整或permute参数错误或占位符）
        is_incomplete_retry = kwargs.get('_incomplete_code_retry', False)
        is_permute_error = kwargs.get('_permute_param_error', False)
        has_placeholder = kwargs.get('_has_placeholder', False)
        openssl_des_unsupported = kwargs.get('_openssl_des_unsupported', False)
        last_error = kwargs.get('_last_error', '')
        # 主消融「无测试反馈」：禁止把向量失败驱动的重试摘要并入提示（仍保留编译/验证类 _last_error）
        if kwargs.get('_ablation_no_test_feedback') and isinstance(last_error, str):
            if '[VECTOR_TEST_RETRY]' in last_error:
                last_error = ''
        
        # 根据 provider / 线路 / max_context 决定是否精简（本地与小上下文默认精简，避免超过 32K 等限制）
        use_compact_prompt = _resolve_use_compact_prompt(agent, kwargs)
        
        # 尝试使用prompt加载器获取prompt
        try:
            # 准备kwargs（排除内部参数）；prompt_ablation 交给 get_prompt，勿落入「具体要求」
            kw = dict(kwargs)
            prompt_ablation = kw.pop('prompt_ablation', None)
            prompt_kwargs = {k: v for k, v in kw.items() if not k.startswith('_')}

            distill_prefix = ""
            if _skip_distillation_prefix(agent, kwargs, use_compact_prompt):
                if distill_mod.is_distillation_target_provider(agent):
                    logger.debug(
                        "蒸馏少样本注入已跳过（精简 prompt / 本地线路或 _skip_distillation）"
                    )
            else:
                try:
                    distill_prefix = distill_mod.build_few_shot_prefix(
                        agent, algorithm, mode, operation, language
                    )
                except Exception as ex:
                    logger.warning(f"蒸馏少样本注入跳过: {ex}")
            
            # 如果强制使用纯实现（OpenSSL 3.0不支持DES），标记OpenSSL不可用
            force_pure = kwargs.get('_force_pure_implementation', False)
            openssl_available_for_prompt = None
            if language.lower() in ['c', 'cpp', 'c++']:
                if force_pure:
                    openssl_available_for_prompt = False  # 强制使用纯实现
                else:
                    openssl_available_for_prompt = agent.openssl_dev_available
            
            prompt = agent.prompt_loader.get_prompt(
                provider=agent.provider,
                language=language,
                compact=use_compact_prompt,
                algorithm=algorithm,
                mode=mode,
                operation=operation,
                test_data=test_data,
                is_incomplete_retry=is_incomplete_retry,
                last_error=last_error,
                openssl_available=openssl_available_for_prompt,
                distillation_prefix=distill_prefix or None,
                prompt_ablation=prompt_ablation,
                **prompt_kwargs
            )

            # Codex + DES-OFB + C：链接缺 main 时在全文最前再钉一条（避免合并长提示后模型仍不交入口）
            if prompt and isinstance(last_error, str):
                le = last_error.lower()
                if (
                    (agent.provider or "").strip().lower() == "codex"
                    and (algorithm or "").strip().upper() == "DES"
                    and (mode or "").strip().upper() == "OFB"
                    and language.strip().lower() == "c"
                    and "undefined reference" in le
                    and "main" in le
                ):
                    prompt = (
                        "**【编译错误 · 必须改正】链接器报告找不到 `main`。下一版输出必须是单个 `.c` 文件，且包含完整 "
                        "`int main(void){ ... return 0; }`，只用 `EVP_des_ofb` + legacy，禁止手写 S 盒/IP/"
                        "Feistel。**\n\n"
                        + prompt
                    )

            # 如果prompt加载器返回了prompt，直接使用
            if prompt:
                return prompt
        except Exception as e:
            logger.warning(f"使用prompt加载器失败: {e}，将使用默认prompt构建方式")
        
        # 如果prompt加载器失败或没有找到模板，使用原来的方式（向后兼容）
        prompt = ""
        
        # 对于C/C++代码，一开始就强调代码完整性
        if language.lower() in ['cpp', 'c++', 'c']:
            if use_compact_prompt:
                # 简化版C++规则（用于OpenAI和Claude）
                prompt += "**C++代码规则（严格遵循）：**\n"
                prompt += "**1. 头文件（最重要！代码必须从正确的头文件开始！）：**\n"
                prompt += "  - **必须包含所有使用的类型和函数对应的头文件！**\n"
                prompt += "  - **如果使用uint8_t/uint16_t/uint32_t/uint64_t，必须包含 `#include <cstdint>`！**\n"
                prompt += "  - **如果使用std::cout/cin/cerr，必须包含 `#include <iostream>`！**\n"
                prompt += "  - **如果使用std::string，必须包含 `#include <string>`！**\n"
                prompt += "  - **如果使用std::vector，必须包含 `#include <vector>`！**\n"
                prompt += "  - **如果使用std::remove_if/remove/find，必须包含 `#include <algorithm>`！**\n"
                prompt += "  - **如果使用std::hex/setw/setfill，必须包含 `#include <iomanip>`！**\n"
                prompt += "  - **如果使用isxdigit/toupper/tolower，必须包含 `#include <cctype>`！**\n"
                prompt += "  - **每个include必须单独一行，不能连在一起！**\n"
                prompt += "  - **代码必须从#include开始，不能缺少任何必要的头文件！**\n"
                prompt += "**2. 代码完整性：**\n"
                prompt += "- 代码必须完整，不能截断\n"
                prompt += "- 禁止在数组中使用单独字母（A-F），使用0x前缀表示十六进制\n"
                prompt += "- 禁止?数字格式，直接使用数字\n"
                prompt += "- 禁止类型名包含问号（如uint3?2_t）\n"
                prompt += "- 不要重复定义函数\n"
                prompt += "- **函数参数名必须是有效的标识符（如a, b, key等），不能是十六进制值（如0x0A）或数字！**\n"
                prompt += "  * **错误：`std::string xor_(const std::string& 0x0A, ...)` - 参数名不能是0x0A！**\n"
                prompt += "  * **正确：`std::string xor_(const std::string& a, const std::string& b)` - 参数名应该是a和b！**\n"
                prompt += "- **函数参数类型必须正确（数组指针vs整数）！**\n"
                prompt += "  - **严重错误：`permute(right, 0x0E, 48)` - 第二个参数不能是整数0x0E！**\n"
                prompt += "  - **正确做法：`permute(right, E_TABLE, 48)` - 第二个参数必须是数组名（如E_TABLE）！**\n"
                prompt += "  - **permute函数的第二个参数必须是已定义的置换表数组名，不能是整数或十六进制值！**\n"
                prompt += "- 禁止字母A后跟数字（如A30→30）\n"
                prompt += "- 禁止中文字符替代数字\n"
                prompt += "- 数组定义必须完整（以};结尾）\n"
                prompt += "- 所有函数必须有return语句（非void）\n\n"
            else:
                # 完整版C++规则（用于DeepSeek和Doubao）
                prompt += "**绝对重要：代码必须完整，不能在语句中间截断！**\n"
                prompt += "**1. 头文件（最重要！代码必须从正确的头文件开始！）：**\n"
                prompt += "  - **必须包含所有使用的类型和函数对应的头文件！**\n"
                prompt += "  - **如果使用uint8_t/uint16_t/uint32_t/uint64_t，必须包含 `#include <cstdint>`！**\n"
                prompt += "  - **如果使用std::cout/cin/cerr，必须包含 `#include <iostream>`！**\n"
                prompt += "  - **如果使用std::string，必须包含 `#include <string>`！**\n"
                prompt += "  - **如果使用std::vector，必须包含 `#include <vector>`！**\n"
                prompt += "  - **如果使用std::remove_if/remove/find，必须包含 `#include <algorithm>`！**\n"
                prompt += "  - **如果使用std::hex/setw/setfill，必须包含 `#include <iomanip>`！**\n"
                prompt += "  - **如果使用isxdigit/toupper/tolower，必须包含 `#include <cctype>`！**\n"
                prompt += "  - **每个include必须单独一行，不能连在一起！**\n"
                prompt += "  - **代码必须从#include开始，不能缺少任何必要的头文件！**\n"
                prompt += "**2. 代码完整性：**\n"
                prompt += "- **所有语句必须完整，包括：**\n"
                prompt += "  * 函数调用必须完整：`hexToBin(key)` 不能只有 `hexToBin`\n"
                prompt += "  * 模板参数必须完整：`std::bitset<32>` 不能只有 `std::bitset<32`\n"
                prompt += "  * 条件表达式必须完整：`if (ch >= 'a' && ch <= 'f')` 不能只有 `if (ch >= 'a' && ch`\n"
                prompt += "  * 赋值语句必须完整：`key_bin = hexToBin(key);` 不能只有 `key_bin = hexToBin`\n"
                prompt += "- **绝对禁止：在数组定义中使用单独的字母（A, B, C, D, E, F, a, b, c, d, e, f）！**\n"
                prompt += "  * **这些字母会被编译器识别为未定义的标识符，导致编译错误！**\n"
                prompt += "  * **错误示例：`{8, 9, 10, 11, A, 13}` 或 `{9, 14, 15, 5, 2, 8, 12, a, 1, 10}`**\n"
                prompt += "  * **正确做法：如果值确实是十六进制，必须使用 `0x` 前缀，如 `{8, 9, 10, 11, 0x0A, 13}` 或 `{9, 14, 15, 5, 2, 8, 12, 0x0A, 1, 10}`**\n"
                prompt += "- **置换表中的所有值都必须是数字（1-64），不能有任何字母！**\n"
                prompt += "  * 绝对不要使用字母A, B, C, D, E, F作为数字！\n"
                prompt += "  * 绝对不要将数字1写成字母l（小写L）！例如：51不能写成5l，13不能写成l3！\n"
                prompt += "  * **绝对不要使用问号?加数字的格式！例如：?13, ?8, ?46 都是错误的！应该直接写成 13, 8, 46！**\n"
                prompt += "  * **绝对不要使用 ? 符号在数组元素中！例如：`{4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14}, ?` 是错误的！**\n"
                prompt += "  * 置换表中的值应该直接写成十进制数字，例如：57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18, 10, 2\n"
                prompt += "- **类型名称必须完整正确！**\n"
                prompt += "  * `uint32_t` 不能写成 `uint3?2_t` 或任何包含问号的格式！\n"
                prompt += "  * 所有类型名称必须是完整的，不能有任何问号或特殊字符！\n"
                prompt += "- **每个include语句必须单独一行，绝对不能连在一起！**\n"
                prompt += "  * **错误示例：`#include <iomanip>#include <sstream>`**\n"
                prompt += "  * **正确做法：每个include单独一行：`#include <iomanip>` 和 `#include <sstream>`**\n"
                prompt += "- **不要重复定义函数！每个函数只能定义一次！**\n"
                prompt += "  * **如果函数已经在前面定义过，不要再次定义！**\n"
                prompt += "  * **检查代码中是否有重复的 `hex_to_bytes`、`bytes_to_hex` 等函数定义！**\n"
                prompt += "- **函数调用时参数类型必须正确！**\n"
                prompt += "  * **如果函数参数是数组指针（如 `const int* table`），必须传入数组名，不能传入整数！**\n"
                prompt += "  * **严重错误：`permute(R, 0x0E, 32, 48)` 其中 `0x0E` 是整数，但函数需要数组指针！**\n"
                prompt += "  * **正确做法：`permute(R, E_TABLE, 32, 48)` 其中 `E_TABLE` 是已定义的数组！**\n"
                prompt += "  * **permute函数的第二个参数必须是数组名（如E_TABLE、P_TABLE、IP_TABLE等），绝对不能是整数！**\n"
                prompt += "  * **常见的置换表：**\n"
                prompt += "    - E_TABLE：扩展置换（32位->48位），用于f函数中\n"
                prompt += "    - P_TABLE：P盒置换（32位->32位），用于f函数中\n"
                prompt += "    - IP_TABLE：初始置换（64位->64位），用于加密/解密开始\n"
                prompt += "    - FP_TABLE：最终置换（64位->64位），用于加密/解密结束\n"
                prompt += "    - PC1_TABLE：PC-1置换（64位->56位），用于密钥调度\n"
                prompt += "    - PC2_TABLE：PC-2置换（56位->48位），用于密钥调度\n"
                prompt += "- **如果S盒或常量表中使用十六进制值，必须使用0x格式（如0x0A），不能直接使用字母A, B, C, D, E, F！**\n"
                prompt += "- **所有数组元素必须是数字或已定义的常量，不能是未定义的标识符！**\n"
                prompt += "- **绝对禁止：字母A后跟数字（如 A30, A48, A11, A14, A0）！**\n"
                prompt += "  * **错误示例：`{38, 6, 46, 14, 54, 22, 62, A30}` 或 `{51, 45, 33, A48, 44, 49}` 或 `if (plaintext.size() % 8 != A0)`**\n"
                prompt += "  * **正确做法：直接使用数字，如 `{38, 6, 46, 14, 54, 22, 62, 30}` 或 `{51, 45, 33, 48, 44, 49}` 或 `if (plaintext.size() % 8 != 0)`**\n"
                prompt += "- **绝对禁止：数字后跟字母O（如 1O）！**\n"
                prompt += "  * **错误示例：`{0, 15, 7, 4, 14, 2, 13, 1, 1O, 6, 12, 11}`**\n"
                prompt += "  * **正确做法：使用数字10，如 `{0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11}`**\n"
                prompt += "- **绝对禁止：字母A后跟十六进制（如 A0xFFFFFFFF）！**\n"
                prompt += "  * **错误示例：`uint32_t right = ip & A0xFFFFFFFF;`**\n"
                prompt += "  * **正确做法：直接使用十六进制，如 `uint32_t right = ip & 0xFFFFFFFF;`**\n"
                prompt += "- **绝对禁止：字符串前的字母A（如 A\"字符串\"）！**\n"
                prompt += "  * **错误示例：`std::cerr << A\"密钥长度必须为8字节\" << std::endl;`**\n"
                prompt += "  * **正确做法：直接使用字符串，如 `std::cerr << \"密钥长度必须为8字节\" << std::endl;`**\n"
                prompt += "- **⚠️ 绝对禁止：在代码中使用中文字符替代数字！**\n"
                prompt += "  * **错误示例：`{0, 15, 7, 4, 14, 2, 13, 1, 10, 统领6, 12, 11}` 或 `{13, 2, 8, 4, 6, 15, 11, 统考1, 10, 9}` 或 `for (int j = 统考7; j >= 0; --j)`**\n"
                prompt += "  * **正确做法：直接使用数字，如 `{0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11}` 或 `{13, 2, 8, 4, 6, 15, 11, 1, 10, 9}` 或 `for (int j = 7; j >= 0; --j)`**\n"
                prompt += "  * **绝对不要使用\"统领\"、\"统考\"等中文字符！所有数字必须直接写成阿拉伯数字！**\n"
                prompt += "- **数组定义必须完整：每个数组元素后面必须有逗号（最后一个元素除外），数组定义必须以 `};` 结尾！**\n"
                prompt += "- **所有函数必须有return语句（如果函数返回非void类型）！**\n"
                prompt += "- **生成代码后，请逐行检查代码，确保：**\n"
                prompt += "  * 没有任何 `?数字` 格式（如 ?13, ?8）\n"
                prompt += "  * 没有任何类型名称包含问号（如 uint3?2_t）\n"
                prompt += "  * 数组元素中没有单独的 `?` 符号\n"
                prompt += "  * 代码完整且可以编译！\n\n"
        
        # 如果是重试且上次失败是因为permute参数错误，在开头强调
        if is_permute_error:
            if use_compact_prompt:
                prompt = "**严重错误：permute函数参数类型错误！**\n"
                if last_error:
                    prompt += f"错误：{last_error[:300]}...\n\n"
                prompt += "**permute函数的第二个参数必须是数组名（如E_TABLE），不能是整数（如0x0E）！**\n"
                prompt += "- **错误：`permute(right, 0x0E, 48)` - 0x0E是整数，不是数组！**\n"
                prompt += "- **正确：`permute(right, E_TABLE, 48)` - E_TABLE是已定义的数组！**\n"
                prompt += "- **必须在使用permute之前定义置换表数组（如E_TABLE、P_TABLE等）！**\n\n"
            else:
                prompt = "**严重错误：permute函数参数类型错误！**\n"
                if last_error:
                    prompt += f"**错误信息：{last_error[:500]}**\n\n"
                prompt += "**permute函数的第二个参数必须是数组名（如E_TABLE），不能是整数（如0x0E）！**\n"
                prompt += "- **错误示例：**\n"
                prompt += "  * `permute(right, 0x0E, 48)` - 错误！0x0E是整数，不是数组！\n"
                prompt += "  * `permute(block, 0x40, 64)` - 错误！0x40是整数，不是数组！\n"
                prompt += "- **正确做法：**\n"
                prompt += "  * 首先定义置换表数组：\n"
                prompt += "    ```cpp\n"
                prompt += "    const int E_TABLE[48] = {32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, ...};\n"
                prompt += "    const int P_TABLE[32] = {16, 7, 20, 21, 29, 12, 28, 17, ...};\n"
                prompt += "    const int IP_TABLE[64] = {58, 50, 42, 34, 26, 18, 10, 2, ...};\n"
                prompt += "    const int FP_TABLE[64] = {40, 8, 48, 16, 56, 24, 64, 32, ...};\n"
                prompt += "    ```\n"
                prompt += "  * 然后使用数组名调用permute函数：\n"
                prompt += "    ```cpp\n"
                prompt += "    std::string right_expanded = permute(right, E_TABLE, 48);  // 正确！\n"
                prompt += "    std::string block_permuted = permute(block, IP_TABLE, 64);  // 正确！\n"
                prompt += "    ```\n"
                prompt += "- **常见的置换表数组名：**\n"
                prompt += "  * E_TABLE：扩展置换（32位->48位），用于f函数中\n"
                prompt += "  * P_TABLE：P盒置换（32位->32位），用于f函数中\n"
                prompt += "  * IP_TABLE：初始置换（64位->64位），用于加密/解密开始\n"
                prompt += "  * FP_TABLE：最终置换（64位->64位），用于加密/解密结束\n"
                prompt += "  * PC1_TABLE：PC-1置换（64位->56位），用于密钥调度\n"
                prompt += "  * PC2_TABLE：PC-2置换（56位->48位），用于密钥调度\n"
                prompt += "- **必须在使用permute函数之前定义这些置换表数组！**\n"
                prompt += "- **检查代码中所有permute函数调用，确保第二个参数是数组名，不是整数！**\n\n"
        
        # 如果是重试且上次失败是因为占位符，在开头强调
        if has_placeholder:
            placeholder_found_list = kwargs.get('_placeholder_found', [])
            placeholder_found_str = ', '.join(placeholder_found_list) if placeholder_found_list else '未知'
            placeholder_in_feistel = kwargs.get('_placeholder_in_feistel', False)
            
            if use_compact_prompt:
                prompt = "**严重错误：代码包含占位符，没有完整实现DES算法！**\n"
                if last_error:
                    prompt += f"错误：{last_error[:300]}...\n\n"
                prompt += f"**检测到的占位符：{placeholder_found_str}**\n\n"
                if placeholder_in_feistel:
                    prompt += "**最严重错误：16轮Feistel循环中只有占位符注释，没有实际代码！**\n\n"
                prompt += "**绝对不能有任何占位符注释或未实现的函数！**\n"
                prompt += "- **错误示例（绝对禁止）：**\n"
                prompt += "  * `// Placeholder for DES encryption logic`\n"
                prompt += "  * `// Placeholder for Feistel function (f)`\n"
                prompt += "  * `// Placeholder for key schedule and subkeys generation`\n"
                prompt += "  * `std::vector<uint64_t> subkeys(16, 0); // Placeholder subkeys`\n"
                prompt += "  * `// right = left XOR f(right, subkeys[i]);` (被注释掉的代码)\n"
                prompt += "- **必须删除所有占位符注释，实现完整的DES加密算法！**\n"
                prompt += "- **必须实现：密钥调度、16轮Feistel网络、S盒替换等！**\n"
                prompt += "- **16轮循环中必须有实际代码，不能只有注释！**\n\n"
            else:
                prompt = "**严重错误：代码包含占位符，没有完整实现DES算法！**\n"
                if last_error:
                    prompt += f"**错误信息：{last_error[:500]}**\n\n"
                prompt += f"**检测到的占位符：{placeholder_found_str}**\n\n"
                if placeholder_in_feistel:
                    prompt += "**最严重错误：16轮Feistel循环中只有占位符注释，没有实际代码！**\n\n"
                prompt += "**绝对不能有任何占位符注释或未实现的函数！**\n"
                prompt += "- **检测到的占位符示例（必须全部删除并实现）：**\n"
                prompt += "  * `// Placeholder for DES encryption logic`\n"
                prompt += "  * `// Placeholder for the encryption process`\n"
                prompt += "  * `// Placeholder for the 16 rounds of DES encryption`\n"
                prompt += "  * `// Placeholder for Feistel function (f)`\n"
                prompt += "  * `// Placeholder for key schedule and subkeys generation`\n"
                prompt += "  * `// This should include all the rounds of DES encryption`\n"
                prompt += "  * `// Placeholder function for DES encryption of a single block`\n"
                prompt += "  * `std::vector<uint64_t> subkeys(16, 0); // Placeholder subkeys`\n"
                prompt += "  * `// right = left XOR f(right, subkeys[i]);` (被注释掉的代码)\n"
                prompt += "- **必须删除所有占位符注释，实现完整的DES加密算法！**\n"
                prompt += "- **必须实现完整的DES算法，包括：**\n"
                prompt += "  1. **密钥调度（Key Schedule）- 绝对不能是占位符！**\n"
                prompt += "     * 必须实现PC-1置换表（64位->56位）\n"
                prompt += "     * 必须实现循环移位（根据SHIFT_TABLE，第1,2,9,16轮移1位，其他轮移2位）\n"
                prompt += "     * 必须实现PC-2置换表（56位->48位）\n"
                prompt += "     * 必须生成16个48位子密钥（不能全是0！）\n"
                prompt += "     * **错误示例：`std::vector<uint64_t> subkeys(16, 0); // Placeholder subkeys`**\n"
                prompt += "     * **正确做法：必须实现完整的密钥调度函数，生成真正的子密钥！**\n"
                prompt += "  2. **16轮Feistel网络（这是DES的核心！）- 绝对不能只有注释！**\n"
                prompt += "     * 每轮必须包括：\n"
                prompt += "       - 扩展置换（E）：将32位右半部分扩展为48位（必须实现E_TABLE）\n"
                prompt += "       - 与子密钥异或：48位扩展结果与48位子密钥异或\n"
                prompt += "       - **S盒替换：将48位结果通过8个S盒替换为32位（必须实现S1到S8）**\n"
                prompt += "       - 置换（P）：对32位结果进行置换（必须实现P_TABLE）\n"
                prompt += "       - 与左半部分异或：置换结果与左半部分异或\n"
                prompt += "       - 交换左右部分（除了最后一轮）\n"
                prompt += "     * **绝对不能跳过16轮Feistel网络！**\n"
                prompt += "     * **绝对不能只有IP置换和FP置换，中间必须有16轮Feistel网络！**\n"
                prompt += "     * **错误示例：**\n"
                prompt += "       ```cpp\n"
                prompt += "       for (int i = 0; i < 16; ++i) {\n"
                prompt += "           // Placeholder for Feistel function (f)\n"
                prompt += "           // right = left XOR f(right, subkeys[i]);\n"
                prompt += "       }\n"
                prompt += "       ```\n"
                prompt += "     * **正确做法：必须在循环中实现完整的Feistel网络逻辑！**\n"
                prompt += "  3. **S盒替换：**\n"
                prompt += "     * 必须实现8个S盒（S1到S8），每个S盒是4x16的查找表\n"
                prompt += "     * **S盒替换是DES加密的核心，绝对不能省略！**\n"
                prompt += "     * 必须使用标准的DES S盒表（不能自己编造！）\n"
                prompt += "- **参考OpenSSL源代码：**\n"
                prompt += "  * https://github.com/openssl/openssl.git\n"
                prompt += "  * 查看 `crypto/des/des_enc.c` 了解正确的DES实现\n"
                prompt += "  * 查看 `crypto/des/set_key.c` 了解密钥调度实现\n"
                prompt += "  * 查看 `crypto/des/des_locl.h` 了解所有置换表和S盒的定义\n"
                prompt += "- **必须完全重写加密函数，实现完整的DES算法！**\n"
                prompt += "- **绝对不能有任何占位符、注释掉的代码或未实现的函数！**\n\n"
        
        # 如果是重试且上次失败是因为占位符，在开头强调
        if has_placeholder:
            if use_compact_prompt:
                prompt = "**严重错误：代码包含占位符，没有完整实现DES算法！**\n"
                if last_error:
                    prompt += f"错误：{last_error[:300]}...\n\n"
                prompt += "**绝对不能有任何占位符注释或未实现的函数！**\n"
                prompt += "- **错误示例：**\n"
                prompt += "  * `// Placeholder for the encryption process`\n"
                prompt += "  * `// This should include all the rounds of DES encryption`\n"
                prompt += "  * `// Placeholder function for DES encryption`\n"
                prompt += "- **必须删除所有占位符注释，实现完整的DES加密算法！**\n"
                prompt += "- **必须实现：密钥调度、16轮Feistel网络、S盒替换等！**\n\n"
            else:
                prompt = "**严重错误：代码包含占位符，没有完整实现DES算法！**\n"
                if last_error:
                    prompt += f"**错误信息：{last_error[:500]}**\n\n"
                prompt += "**绝对不能有任何占位符注释或未实现的函数！**\n"
                prompt += "- **检测到的占位符示例：**\n"
                prompt += "  * `// Placeholder for the encryption process`\n"
                prompt += "  * `// Placeholder for the 16 rounds of DES encryption`\n"
                prompt += "  * `// This should include all the rounds of DES encryption`\n"
                prompt += "  * `// Placeholder function for DES encryption of a single block`\n"
                prompt += "- **必须删除所有占位符注释，实现完整的DES加密算法！**\n"
                prompt += "- **必须实现完整的DES算法，包括：**\n"
                prompt += "  1. **密钥调度（Key Schedule）：**\n"
                prompt += "     * PC-1置换、循环移位、PC-2置换\n"
                prompt += "     * 生成16个48位子密钥\n"
                prompt += "  2. **16轮Feistel网络（这是DES的核心！）：**\n"
                prompt += "     * 每轮包括：扩展置换（E）、与子密钥异或、S盒替换、P置换、与左半部分异或\n"
                prompt += "     * **绝对不能跳过16轮Feistel网络！**\n"
                prompt += "     * **绝对不能只有IP置换和FP置换，中间必须有16轮Feistel网络！**\n"
                prompt += "  3. **S盒替换：**\n"
                prompt += "     * 必须实现8个S盒（S1到S8），每个S盒是4x16的查找表\n"
                prompt += "     * **S盒替换是DES加密的核心，绝对不能省略！**\n"
                prompt += "- **参考OpenSSL源代码：**\n"
                prompt += "  * https://github.com/openssl/openssl.git\n"
                prompt += "  * 查看 `crypto/des/des_enc.c` 了解正确的DES实现\n"
                prompt += "  * 查看 `crypto/des/set_key.c` 了解密钥调度实现\n"
                prompt += "- **必须完全重写加密函数，实现完整的DES算法！**\n\n"
        
        # 如果是重试且上次失败是因为代码不完整，在开头强调
        if is_incomplete_retry:
            if use_compact_prompt:
                prompt = "**警告：上次代码不完整被截断！**\n"
                if last_error:
                    prompt += f"错误：{last_error[:200]}...\n\n"
                prompt += "**请生成完整的、可编译的代码！**\n"
            else:
                prompt = "**严重警告：上次生成的代码不完整，在语句中间被截断！**\n"
                if last_error:
                    prompt += f"**上次错误：{last_error[:500]}**\n\n"
                prompt += "**请务必生成完整的、可编译的代码！代码绝对不能在任何语句中间截断！**\n"
                prompt += "- **所有语句必须完整，包括：**\n"
                prompt += "  * 函数调用必须完整：`hexToBin(key)` 不能只有 `hexToBin`\n"
                prompt += "  * 模板参数必须完整：`std::bitset<32>` 不能只有 `std::bitset<32`\n"
                prompt += "  * 条件表达式必须完整：`if (ch >= 'a' && ch <= 'f')` 不能只有 `if (ch >= 'a' && ch`\n"
                prompt += "  * 赋值语句必须完整：`key_bin = hexToBin(key);` 不能只有 `key_bin = hexToBin`\n"
                prompt += "- **所有数组定义必须完整，包括所有元素和闭合的大括号！**\n"
                prompt += "- **所有函数必须完整实现，不能在中途截断！**\n"
                prompt += "- **所有函数必须有return语句（如果函数返回非void类型）！**\n"
                prompt += "- **代码必须从 `#include` 开始，到 `main` 函数结束，中间不能有任何截断！**\n"
            if language.lower() in ['cpp', 'c++']:
                if use_compact_prompt:
                    prompt += "- 代码必须从#include开始，到main函数结束\n"
                    prompt += "- 所有语句、函数、数组定义必须完整\n"
                    prompt += "- 所有括号、大括号必须正确匹配\n\n"
                else:
                    prompt += "- **每个include语句必须单独一行，绝对不能连在一起！**\n"
                    prompt += "  * **错误示例：`#include <iomanip>#include <sstream>`**\n"
                    prompt += "  * **正确做法：每个include单独一行：`#include <iomanip>` 和 `#include <sstream>`**\n"
                    prompt += "- **不要重复定义函数！每个函数只能定义一次！**\n"
                    prompt += "  * **如果函数已经在前面定义过，不要再次定义！**\n"
                    prompt += "  * **检查代码中是否有重复的 `hex_to_bytes`、`bytes_to_hex` 等函数定义！**\n"
                    prompt += "- **函数参数名必须是有效的标识符，不能是十六进制值或数字！**\n"
                    prompt += "  * **严重错误：`std::string xor_(const std::string& 0x0A, const std::string& b)` - 参数名不能是 `0x0A`！**\n"
                    prompt += "  * **正确做法：`std::string xor_(const std::string& a, const std::string& b)` - 参数名应该是 `a` 和 `b`！**\n"
                    prompt += "  * **函数参数名、变量名必须是字母、数字、下划线的组合，绝对不能是十六进制值（如0x0A、0x0E）！**\n"
                    prompt += "- **函数调用时参数类型必须正确！**\n"
                    prompt += "  * **如果函数参数是数组指针（如 `const int* table`），必须传入数组名，不能传入整数！**\n"
                    prompt += "  * **严重错误：`permute(R, 0x0E, 32, 48)` 其中 `0x0E` 是整数，但函数需要数组指针！**\n"
                    prompt += "  * **正确做法：`permute(R, E_TABLE, 32, 48)` 其中 `E_TABLE` 是已定义的数组！**\n"
                    prompt += "  * **permute函数的第二个参数必须是数组名（如E_TABLE、P_TABLE、IP_TABLE等），绝对不能是整数！**\n"
                    prompt += "  * **常见的置换表：**\n"
                    prompt += "    - E_TABLE：扩展置换（32位->48位），用于f函数中\n"
                    prompt += "    - P_TABLE：P盒置换（32位->32位），用于f函数中\n"
                    prompt += "    - IP_TABLE：初始置换（64位->64位），用于加密/解密开始\n"
                    prompt += "    - FP_TABLE：最终置换（64位->64位），用于加密/解密结束\n"
                    prompt += "    - PC1_TABLE：PC-1置换（64位->56位），用于密钥调度\n"
                    prompt += "    - PC2_TABLE：PC-2置换（56位->48位），用于密钥调度\n"
                    prompt += "- **绝对禁止：在数组定义中使用单独的字母（A, B, C, D, E, F, a, b, c, d, e, f）！**\n"
                    prompt += "  * **这些字母会被编译器识别为未定义的标识符，导致编译错误！**\n"
                    prompt += "  * **错误示例：`{8, 9, 10, 11, A, 13}` 或 `{9, 14, 15, 5, 2, 8, 12, a, 1, 10}`**\n"
                    prompt += "  * **正确做法：如果值确实是十六进制，必须使用 `0x` 前缀，如 `{8, 9, 10, 11, 0x0A, 13}` 或 `{9, 14, 15, 5, 2, 8, 12, 0x0A, 1, 10}`**\n"
                    prompt += "- **置换表中的所有值都必须是数字（1-64），不能有任何字母！绝对不要使用字母A, B, C, D, E, F作为数字！绝对不要将数字1写成字母l（小写L）！例如：51不能写成5l，13不能写成l3！**\n"
                    prompt += "- **如果S盒或常量表中使用十六进制值，必须使用0x格式（如0x0A），不能直接使用字母A, B, C, D, E, F！**\n"
                    prompt += "- **所有数组元素必须是数字或已定义的常量，不能是未定义的标识符！**\n"
                    prompt += "- **绝对禁止：字母A后跟数字（如 A30, A48, A11, A14, A0）！**\n"
                    prompt += "  * **错误示例：`{38, 6, 46, 14, 54, 22, 62, A30}` 或 `{51, 45, 33, A48, 44, 49}` 或 `if (plaintext.size() % 8 != A0)`**\n"
                    prompt += "  * **正确做法：直接使用数字，如 `{38, 6, 46, 14, 54, 22, 62, 30}` 或 `{51, 45, 33, 48, 44, 49}` 或 `if (plaintext.size() % 8 != 0)`**\n"
                    prompt += "- **绝对禁止：数字后跟字母O（如 1O）！**\n"
                    prompt += "  * **错误示例：`{0, 15, 7, 4, 14, 2, 13, 1, 1O, 6, 12, 11}`**\n"
                    prompt += "  * **正确做法：使用数字10，如 `{0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11}`**\n"
                    prompt += "- **绝对禁止：字母A后跟十六进制（如 A0xFFFFFFFF）！**\n"
                    prompt += "  * **错误示例：`uint32_t right = ip & A0xFFFFFFFF;`**\n"
                    prompt += "  * **正确做法：直接使用十六进制，如 `uint32_t right = ip & 0xFFFFFFFF;`**\n"
                    prompt += "- **绝对禁止：字符串前的字母A（如 A\"字符串\"）！**\n"
                    prompt += "  * **错误示例：`std::cerr << A\"密钥长度必须为8字节\" << std::endl;`**\n"
                    prompt += "  * **正确做法：直接使用字符串，如 `std::cerr << \"密钥长度必须为8字节\" << std::endl;`**\n"
                    prompt += "- **⚠️ 绝对禁止：在代码中使用中文字符替代数字！**\n"
                    prompt += "  * **错误示例：`{0, 15, 7, 4, 14, 2, 13, 1, 10, 统领6, 12, 11}` 或 `{13, 2, 8, 4, 6, 15, 11, 统考1, 10, 9}` 或 `for (int j = 统考7; j >= 0; --j)`**\n"
                    prompt += "  * **正确做法：直接使用数字，如 `{0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11}` 或 `{13, 2, 8, 4, 6, 15, 11, 1, 10, 9}` 或 `for (int j = 7; j >= 0; --j)`**\n"
                    prompt += "  * **绝对不要使用\"统领\"、\"统考\"等中文字符！所有数字必须直接写成阿拉伯数字！**\n"
                    prompt += "- **命名空间冲突：不要同时定义 `namespace DES` 和 `class DES`，这会导致命名冲突！如果使用类，直接定义 `class DES`，不要用 `namespace DES` 包装！**\n"
            if not use_compact_prompt:
                prompt += "- **生成代码后，请逐行检查代码末尾，确保：**\n"
                prompt += "  * 最后一行是完整的语句（以分号结尾）\n"
                prompt += "  * 所有函数都有完整的函数体（包括return语句）\n"
                prompt += "  * 所有括号、大括号、方括号都正确匹配和闭合\n"
                prompt += "  * 代码可以编译通过，没有任何语法错误\n\n"
        
        prompt += f"请帮我编写一个使用{algorithm}算法"
        
        if mode:
            prompt += f"的{mode}模式"
        
        # 如果有测试数据，添加到提示中（仅用于说明，不要硬编码）
        if test_data:
            prompt += "\n\n标准测试数据（用于验证代码正确性，但不要硬编码到代码中）：\n"
            if 'plaintext' in test_data:
                prompt += f"测试明文（16进制）：{test_data['plaintext']}\n"
            if 'key' in test_data:
                prompt += f"测试密钥（16进制）：{test_data['key']}\n"
            if 'iv' in test_data:
                prompt += f"测试初始化向量IV（16进制）：{test_data['iv']}\n"
            if 'expected_ciphertext' in test_data:
                prompt += f"预期密文（16进制）：{test_data['expected_ciphertext']}\n"
            if algorithm.upper() == 'RSA':
                if 'public_key' in test_data:
                    prompt += f"测试公钥 n（16进制）：{test_data['public_key'].get('n', '')}\n"
                    prompt += f"测试公钥 e（16进制）：{test_data['public_key'].get('e', '')}\n"
                if 'private_key' in test_data:
                    prompt += f"测试私钥 n（16进制）：{test_data['private_key'].get('n', '')}\n"
                    prompt += f"测试私钥 d（16进制）：{test_data['private_key'].get('d', '')}\n"
                if 'ciphertexts' in test_data:
                    if 'encrypt' in test_data['ciphertexts']:
                        prompt += f"预期加密结果（16进制）：{test_data['ciphertexts']['encrypt']}\n"
                    if 'sign' in test_data['ciphertexts']:
                        prompt += f"预期签名结果（16进制）：{test_data['ciphertexts']['sign']}\n"
            prompt += "\n重要说明：\n"
            prompt += "1. 上述测试数据仅用于验证代码正确性，不要硬编码到代码中！\n"
            prompt += "2. 代码必须能够接受任意输入，而不是只处理上述测试数据\n"
            prompt += "3. 代码应该优先从环境变量读取输入（如果存在），否则从stdin读取，或提供交互式输入\n"
            prompt += "4. 环境变量名称：TEST_PLAINTEXT（明文）、TEST_CIPHERTEXT（密文）、TEST_KEY（密钥）、TEST_IV（初始化向量）\n"
            prompt += "5. 对于RSA：TEST_PUBLIC_KEY_N、TEST_PUBLIC_KEY_E、TEST_PRIVATE_KEY_N、TEST_PRIVATE_KEY_D\n"
            prompt += "6. 当使用上述测试数据作为环境变量时，代码必须产生完全匹配的预期结果\n"
            prompt += "7. 但代码必须能够处理其他输入，不能只处理测试数据\n\n"
        
        # 对于RSA，根据operation参数生成对应的操作代码
        if algorithm.upper() == 'RSA':
            if operation == '密钥生成':
                prompt += f"进行密钥生成的{lang_name}代码。\n\n"
            elif operation == '加密':
                prompt += f"进行加密的{lang_name}代码。\n\n"
            elif operation == '解密':
                prompt += f"进行解密的{lang_name}代码。\n\n"
            elif operation == '签名':
                prompt += f"进行数字签名的{lang_name}代码。\n\n"
            elif operation == '验证':
                prompt += f"进行签名验证的{lang_name}代码。\n\n"
            else:
                # 默认生成包含所有RSA操作的完整代码
                prompt += f"的完整{lang_name}代码，包括密钥生成、加密、解密、数字签名和验证功能。\n\n"
        else:
            prompt += f"进行{operation}的{lang_name}代码。\n\n"
        
        if kwargs:
            prompt += "具体要求：\n"
            for key, value in kwargs.items():
                prompt += f"- {key}: {value}\n"
        
        prompt += "\n请提供完整的代码，包括：\n"
        if language.lower() in ['c', 'cpp', 'c++']:
            prompt += "1. 必要的头文件包含\n"
        else:
            prompt += "1. 必要的导入语句\n"
        
        # 添加输入读取要求
        prompt += "\n输入读取要求（非常重要）：\n"
        if language.lower() == 'python':
            prompt += "- 代码必须优先从环境变量读取输入（os.environ.get('TEST_PLAINTEXT')等）\n"
            prompt += "- **重要：从环境变量读取的字符串可能包含换行符、空格等空白字符，必须去除！**\n"
            prompt += "  - 使用 `.strip()` 方法去除首尾空白字符\n"
            prompt += "  - 或者使用 `''.join(hex_str.split())` 去除所有空白字符\n"
            prompt += "- **关键：如果环境变量不存在或为空字符串（去除空白后），必须从stdin读取或使用默认值！**\n"
            prompt += "  - 检查方式：`value = os.environ.get('TEST_PLAINTEXT'); value = clean_hex_string(value) if value else None`\n"
            prompt += "  - 如果`value`为`None`或空字符串，则从stdin读取\n"
            prompt += "- 如果环境变量不存在或为空，可以从stdin读取（input()或sys.stdin）\n"
            prompt += "- 也可以提供交互式输入功能\n"
            prompt += "- 不要硬编码任何测试数据到代码中\n"
            prompt += "- **绝对不要对空字符串调用bytes.fromhex()，这会抛出ValueError！**\n"
        elif language.lower() in ['c', 'cpp', 'c++']:
            prompt += "- 代码必须优先从环境变量读取输入（getenv('TEST_PLAINTEXT')等）\n"
            prompt += "- **重要：从环境变量读取的字符串可能包含换行符、空格等空白字符，必须去除！**\n"
            prompt += "  - 必须实现一个函数来去除字符串中的所有空白字符（包括空格、制表符、换行符等）\n"
            prompt += "  - 可以使用 `isspace()` 函数检查字符是否为空白字符\n"
            prompt += "  - 在解析十六进制字符串之前，必须先去除所有空白字符\n"
            prompt += "  - 示例：遍历字符串，只保留非空白字符\n"
            prompt += "- **关键：如果环境变量不存在或为空字符串（去除空白后），必须从stdin读取或使用默认值！**\n"
            prompt += "  - 检查方式：`char *value = getenv(\"TEST_PLAINTEXT\"); if (value) { remove_whitespace(value); if (strlen(value) == 0) value = NULL; }`\n"
            prompt += "  - 如果`value`为`NULL`或空字符串，则从stdin读取\n"
            prompt += "- 如果环境变量不存在或为空，可以从stdin读取（scanf或getchar等）\n"
            prompt += "- 也可以提供交互式输入功能\n"
            prompt += "- 不要硬编码任何测试数据到代码中\n"
        prompt += "- 代码必须能够处理任意输入，而不仅仅是测试数据\n\n"
        
        # 对于RSA，根据operation调整代码要求
        if algorithm.upper() == 'RSA':
            if operation == '密钥生成':
                prompt += "2. RSA密钥生成函数\n"
                prompt += "3. 使用示例和测试代码（包含main函数）\n"
                prompt += "4. 详细的注释说明\n"
                prompt += "5. 确保代码可以直接编译/运行并测试\n\n"
            elif operation == '加密':
                prompt += "2. RSA加密函数\n"
                prompt += "3. 使用示例和测试代码（包含main函数）\n"
                prompt += "4. 详细的注释说明\n"
                prompt += "5. 确保代码可以直接编译/运行并测试\n\n"
            elif operation == '解密':
                prompt += "2. RSA解密函数\n"
                prompt += "3. 使用示例和测试代码（包含main函数）\n"
                prompt += "4. 详细的注释说明\n"
                prompt += "5. 确保代码可以直接编译/运行并测试\n\n"
            elif operation == '签名':
                prompt += "2. RSA数字签名函数\n"
                prompt += "3. 使用示例和测试代码（包含main函数）\n"
                prompt += "4. 详细的注释说明\n"
                prompt += "5. 确保代码可以直接编译/运行并测试\n\n"
            elif operation == '验证':
                prompt += "2. RSA签名验证函数\n"
                prompt += "3. 使用示例和测试代码（包含main函数）\n"
                prompt += "4. 详细的注释说明\n"
                prompt += "5. 确保代码可以直接编译/运行并测试\n\n"
            else:
                prompt += "2. RSA密钥生成函数\n"
                prompt += "3. RSA加密函数\n"
                prompt += "4. RSA解密函数\n"
                prompt += "5. RSA数字签名函数\n"
                prompt += "6. RSA签名验证函数\n"
                prompt += "7. 使用示例和测试代码（包含main函数）\n"
                prompt += "8. 详细的注释说明\n"
                prompt += "9. 确保代码可以直接编译/运行并测试\n"
                prompt += "10. **重要：对于RSA加密操作，如果提供了私钥（n和d），除了输出密文外，还必须输出数字签名！**\n"
                prompt += "    - 签名格式：'签名: xxxxxx' 或 'signature: xxxxxx'（十六进制格式）\n"
                prompt += "    - 签名应该使用私钥对明文（或明文的哈希值）进行签名\n"
                prompt += "    - 输出示例：'密文: abc123...' 和 '签名: def456...'（两行输出）\n\n"
        else:
            prompt += "2. 加密函数\n"
            prompt += "3. 解密函数\n"
            prompt += "4. 使用示例和测试代码（包含main函数）\n"
            prompt += "5. 详细的注释说明\n"
            prompt += "6. 确保代码可以直接编译/运行并测试\n\n"
        
        # 添加关键要求：确保输出一致性和完整性
        prompt += "\n关键要求（非常重要）：\n"
        prompt += "1. **绝对禁止使用随机数！**\n"
        prompt += "   - **绝对不要使用 `rand()`、`random()`、`srand()`、`time()` 等函数生成随机IV或随机密钥！**\n"
        prompt += "   - **绝对不要使用 `os.urandom()`、`random.randint()`、`secrets.token_bytes()` 等Python随机函数！**\n"
        prompt += "   - 对于需要IV的模式（CBC、CFB、OFB等），**必须从环境变量TEST_IV读取固定的IV**，确保相同输入每次产生相同输出\n"
        prompt += "   - 如果环境变量TEST_IV不存在，可以使用全零字节作为默认IV，但**绝对不能使用随机IV！**\n"
        prompt += "   - 密钥必须从环境变量TEST_KEY读取，**绝对不能使用随机密钥！**\n"
        prompt += "   - **代码必须是确定性的：相同的明文、密钥、IV必须产生完全相同的密文！**\n"
        prompt += "2. 对于GCM模式，虽然IV可以是随机的，但必须输出完整的密文（包括认证标签），不要截断\n"
        prompt += "3. 密文输出必须完整，不要截断或只输出部分内容\n"
        prompt += "4. 密文输出格式要统一，建议使用十六进制（hex）格式，所有字符小写，不要有空格、换行或其他分隔符\n"
        prompt += "5. 输出密文时，要明确标注'密文'或'ciphertext'等关键词，格式如：'密文: xxxxxx' 或 'ciphertext: xxxxxx'\n"
        prompt += "6. 确保输出的密文长度正确：\n"
        prompt += "   - 对于分组密码（如AES、DES），密文长度 = (明文长度 + 填充) 的整数倍\n"
        prompt += "   - 对于ECB模式：密文长度 = 分组大小的整数倍（如AES-128是16字节的倍数）\n"
        prompt += "   - 对于CBC/CFB/OFB模式：密文长度 = 分组大小的整数倍（不包括IV，IV单独处理）\n"
        prompt += "   - **重要：必须输出完整的密文，不能截断或只输出部分内容！**\n"
        prompt += "   - **必须处理所有明文字节，不能只处理部分明文！**\n"
        prompt += "   - 从环境变量读取的明文是十六进制字符串，必须正确计算长度（字符串长度除以2）\n"
        prompt += "   - 不要要求用户输入明文长度，应该自动从输入数据计算\n"
        prompt += "   - 确保加密循环处理了所有明文字节，而不是只处理部分\n"
        prompt += "   - 确保输出函数输出了所有密文字节，长度必须等于处理后的密文实际长度\n"
        prompt += "   - **对于C代码使用OpenSSL EVP：ciphertext_len应该只包含EVP_EncryptUpdate和EVP_EncryptFinal_ex返回的长度，不要添加额外的长度！**\n"
        prompt += "   - **绝对不要在输出密文时附加解密后的明文或其他任何内容！**\n"
        prompt += "   - **输出密文后立即退出程序（return 0），不要执行解密操作！**\n"
        prompt += "   - **不要重复输出密文，只输出一次！**\n"
        prompt += "   - **不要同时输出原始字节和hex编码，只输出hex编码的字符串！**\n"
        prompt += "   - **绝对不要在密文后面附加解密后的明文或其他任何内容！**\n"
        prompt += "   - **确保输出的密文长度等于预期长度，不要多出任何字符！**\n"
        prompt += "7. 如果使用填充（padding），确保正确实现，不要导致输出不完整或长度错误\n"
        prompt += "8. 输出示例：如果密文是 '0123456789abcdef'，应该输出 '密文: 0123456789abcdef'（16个hex字符，不要输出32个字符或更多）\n"
        prompt += "9. **绝对禁止输出解密后的明文或其他内容！**\n"
        prompt += "   - **只输出密文，绝对不要输出解密后的明文！**\n"
        prompt += "   - **不要输出任何调试信息、中间结果或其他无关内容！**\n"
        prompt += "   - **绝对不要执行解密操作！代码只需要加密，不需要解密！**\n"
        prompt += "   - **不要在代码中包含解密功能，即使是为了验证！**\n"
        prompt += "   - **绝对不要在代码中调用解密函数或执行解密操作！**\n"
        prompt += "   - **绝对不要在输出密文时附加任何其他内容（如解密后的明文、IV、密钥等）！**\n"
        prompt += "   - **确保输出的密文长度等于预期长度，不要多出任何字符！**\n"
        prompt += "   - **输出格式必须严格：只输出一行，格式为 '密文: xxxxxx' 或 'ciphertext: xxxxxx'，其中xxxxxx是hex编码的密文，长度必须等于预期长度，不要有任何其他输出！**\n"
        prompt += "   - **Python示例：`print(f\"密文: {ciphertext_hex}\"); exit(0)` - 输出密文后立即退出，不要执行解密！**\n"
        prompt += "   - **C/C++示例：`printf(\"密文: %s\\n\", hexString); return 0;` - 输出密文后立即返回，不要执行解密！**\n"
        prompt += "   - **错误示例：`print(f\"密文: {ciphertext_hex}{decrypted_hex}\")` - 绝对不要这样做！**\n"
        prompt += "   - **错误示例：`print(f\"密文: {ciphertext_hex}\"); print(f\"解密后的明文: {decrypted_hex}\")` - 绝对不要这样做！**\n"
        prompt += "   - **错误示例：`ciphertext_hex += decrypted_hex` 或 `ciphertext_hex = ciphertext_hex + decrypted_hex` - 绝对不要这样做！**\n"
        prompt += "10. **绝对不要输出IV（初始化向量）！**\n"
        prompt += "   - IV只用于加密过程，不应该出现在输出中\n"
        prompt += "   - 对于CBC/CFB/OFB模式，IV用于初始化，但输出时只输出密文，不输出IV\n"
        prompt += "   - 如果输出格式是 '密文: xxxxxx'，xxxxxx 应该只包含密文本身，不包含IV\n"
        prompt += "   - 示例：如果密文是 '0123456789abcdef'，IV是 '0000000000000000'，应该只输出 '密文: 0123456789abcdef'，不要输出 '密文: 0123456789abcdef0000000000000000'\n"
        prompt += "11. **绝对不要输出密钥！**密钥只用于加密过程，不应该出现在输出中\n"
        prompt += "12. **只输出密文本身**，不要输出IV、密钥、明文或其他任何附加信息\n"
        prompt += "13. 对于CBC模式，确保：\n"
        prompt += "   - IV只用于第一块明文的加密\n"
        prompt += "   - 后续块使用前一块的密文作为IV（CBC链式加密）\n"
        prompt += "   - **输出时只输出最终的密文，绝对不要输出IV！**\n"
        prompt += "   - **不要将IV追加到密文后面！**\n"
        prompt += "   - **不要将IV放在密文前面！**\n"
        prompt += "   - **只输出加密后的密文字节，不输出任何其他内容！**\n"
        prompt += "14. 输出格式检查：\n"
        prompt += "   - 如果使用 `cout << \"密文: \" << bytes_to_hex(ciphertext) << endl;`，确保ciphertext只包含密文，不包含IV\n"
        prompt += "   - 如果使用 `printf(\"密文: %s\\n\", hex_string);`，确保hex_string只包含密文，不包含IV\n"
        prompt += "   - 绝对不要执行类似 `ciphertext.push_back(iv)` 或 `ciphertext.insert(ciphertext.begin(), iv)` 的操作\n"
        
        # 对于特定模式，添加详细说明
        if mode == 'OFB':
            if use_compact_prompt:
                # 简化版OFB说明
                prompt += "\n13. **OFB模式要点：**\n"
                if algorithm.upper() == 'DES':
                    prompt += "    * 使用DES加密函数生成密钥流（不是简单XOR）\n"
                    prompt += "    * 通常使用OFB-8（每次1字节）或OFB-64（每次8字节）\n"
                    prompt += "    * 反馈寄存器=IV，反馈更新为加密结果（不是密文）\n"
                elif algorithm.upper() == 'AES':
                    prompt += "    * 使用AES加密函数生成密钥流（不是简单XOR）\n"
                    prompt += "    * OFB-8（每次1字节）需手动实现，OFB-128（每次16字节）可用pycryptodome\n"
                    prompt += "    * 反馈寄存器=IV，反馈更新为加密结果（不是密文）\n"
            else:
                # 完整版OFB说明
                prompt += "\n13. **对于OFB模式，必须严格按照标准实现：**\n"
                if algorithm.upper() == 'DES':
                    prompt += "    * OFB模式使用分组密码（DES）的加密函数来生成密钥流，不是简单的XOR\n"
                    prompt += "    * **重要：OFB模式有两种变体，必须根据测试数据选择正确的变体：**\n"
                    prompt += "      - OFB-8：每次处理1字节（8位），反馈寄存器每次左移8位（最常用）\n"
                    prompt += "      - OFB-64：每次处理8字节（64位），反馈寄存器每次更新为整个加密结果\n"
                    prompt += "      - **如果明文长度不是8的倍数，通常使用OFB-8模式**\n"
                    prompt += "    * **初始化：反馈寄存器 = IV（8字节，64位）**\n"
                elif algorithm.upper() == 'AES':
                    prompt += "    * OFB模式使用分组密码（AES）的加密函数来生成密钥流，不是简单的XOR\n"
                    prompt += "    * **重要：AES OFB模式有两种变体，必须根据测试数据选择正确的变体：**\n"
                    prompt += "      - OFB-8：每次处理1字节（8位），反馈寄存器每次左移8位\n"
                    prompt += "      - OFB-128：每次处理16字节（128位），反馈寄存器每次更新为整个加密结果\n"
                    prompt += "      - **pycryptodome的AES.MODE_OFB默认使用OFB-128模式，不支持segment_size参数！**\n"
                    prompt += "      - **如果需要OFB-8模式，必须手动实现！**\n"
                    prompt += "    * **初始化：反馈寄存器 = IV（16字节，128位）**\n"
                # 如果提供了测试数据，尝试确定应该使用哪个变体
                if test_data and 'expected_ciphertext' in test_data:
                    try:
                        from Crypto.Cipher import AES
                        plaintext_hex = test_data.get('plaintext', '')
                        key_hex = test_data.get('key', '')
                        iv_hex = test_data.get('iv', '')
                        expected = test_data.get('expected_ciphertext', '').lower().replace(' ', '')
                        
                        if plaintext_hex and key_hex and iv_hex:
                            plaintext = bytes.fromhex(plaintext_hex.replace(' ', ''))
                            key = bytes.fromhex(key_hex.replace(' ', ''))
                            iv = bytes.fromhex(iv_hex.replace(' ', ''))
                            
                            # 测试OFB-8（手动实现）
                            feedback = list(iv)
                            ciphertext_ofb8 = []
                            aes_ecb = AES.new(key, AES.MODE_ECB)
                            for byte in plaintext:
                                keystream_block = aes_ecb.encrypt(bytes(feedback))
                                keystream_byte = keystream_block[0]
                                cipher_byte = byte ^ keystream_byte
                                ciphertext_ofb8.append(cipher_byte)
                                feedback = feedback[1:] + [keystream_byte]
                            result_ofb8 = bytes(ciphertext_ofb8).hex().lower()
                            
                            # 测试OFB-128（默认）
                            cipher_ofb128 = AES.new(key, AES.MODE_OFB, iv=iv)
                            result_ofb128 = cipher_ofb128.encrypt(plaintext).hex().lower()
                            
                            if result_ofb8 == expected:
                                prompt += f"      - **根据测试数据，必须使用OFB-8模式（每次处理1字节）！**\n"
                                prompt += f"      - 测试数据验证：OFB-8模式的结果与预期密文完全匹配\n"
                                prompt += f"      - **重要：pycryptodome的AES.MODE_OFB不支持segment_size参数，必须手动实现OFB-8模式！**\n"
                                prompt += f"      - **实现方式：**\n"
                                prompt += f"        * 使用AES.MODE_ECB模式创建加密器\n"
                                prompt += f"        * 初始化反馈寄存器为IV（16字节）\n"
                                prompt += f"        * 对于每个明文字节：\n"
                                prompt += f"          a. 使用AES加密反馈寄存器，得到16字节密钥流块\n"
                                prompt += f"          b. 取密钥流块的第一个字节（最左字节）作为密钥流字节\n"
                                prompt += f"          c. 密文字节 = 明文字节 XOR 密钥流字节\n"
                                prompt += f"          d. 反馈寄存器左移8位：feedback = feedback[1:] + [keystream_byte]\n"
                            elif result_ofb128 == expected:
                                prompt += f"      - **根据测试数据，必须使用OFB-128模式（每次处理16字节块）！**\n"
                                prompt += f"      - 测试数据验证：OFB-128模式的结果与预期密文完全匹配\n"
                                prompt += f"      - 可以使用pycryptodome的AES.MODE_OFB（默认就是OFB-128）\n"
                            else:
                                prompt += f"      - **警告：测试数据与OFB-8和OFB-128的结果都不匹配，请仔细检查！**\n"
                                prompt += f"      - OFB-8结果：{result_ofb8[:32]}...\n"
                                prompt += f"      - OFB-128结果：{result_ofb128[:32]}...\n"
                                prompt += f"      - 预期结果：{expected[:32]}...\n"
                    except Exception as e:
                        # 如果测试失败，继续使用通用提示
                        pass
                # 为AES添加OFB-8和OFB-128的详细说明（仅在完整模式下）
                if not use_compact_prompt and algorithm.upper() == 'AES':
                    prompt += "    * **对于AES OFB-8模式（每次处理1字节）- 如果测试数据需要此模式：**\n"
                    prompt += "      1. **使用AES.MODE_ECB模式创建加密器（因为pycryptodome的OFB不支持segment_size）**\n"
                    prompt += "          - `aes_ecb = AES.new(key, AES.MODE_ECB)`\n"
                    prompt += "      2. 初始化反馈寄存器为IV（16字节列表）：`feedback = list(iv)`\n"
                    prompt += "      3. 对于每个明文字节（循环）：\n"
                    prompt += "         a. 使用AES加密反馈寄存器：`keystream_block = aes_ecb.encrypt(bytes(feedback))`\n"
                    prompt += "         b. 取密钥流块的第一个字节：`keystream_byte = keystream_block[0]`\n"
                    prompt += "         c. 密文字节 = 明文字节 XOR 密钥流字节：`cipher_byte = plain_byte ^ keystream_byte`\n"
                    prompt += "         d. 反馈寄存器左移8位：`feedback = feedback[1:] + [keystream_byte]`\n"
                    prompt += "      4. **关键：反馈寄存器更新为密钥流字节（keystream_byte），不是密文！**\n"
                    prompt += "      5. **重要：OFB模式不填充，密文长度等于明文长度**\n"
                    prompt += "    * **对于AES OFB-128模式（每次处理16字节块）- 默认模式：**\n"
                    prompt += "      1. 可以使用pycryptodome的AES.MODE_OFB（默认就是OFB-128）\n"
                    prompt += "          - `cipher = AES.new(key, AES.MODE_OFB, iv=iv)`\n"
                    prompt += "      2. 初始化反馈寄存器为IV（16字节）\n"
                    prompt += "      3. 对于每个16字节块：\n"
                    prompt += "         a. 使用AES加密反馈寄存器，得到16字节密钥流块\n"
                    prompt += "         b. 密文块 = 明文块 XOR 密钥流块（整个16字节）\n"
                    prompt += "         c. 反馈寄存器更新为密钥流块（整个16字节），不是密文！\n"
                    prompt += "      4. **关键：反馈寄存器更新为密钥流块（keystream），不是密文！**\n"
                elif not use_compact_prompt:
                    # DES的OFB模式说明（仅在完整模式下）
                    prompt += "    * **对于OFB-8模式（每次处理1字节）- 这是最常用的OFB模式：**\n"
                    prompt += "      1. **使用完整的DES加密函数加密反馈寄存器（使用密钥）**\n"
                    prompt += "          - 这一步必须调用完整的DES加密算法（密钥调度、IP、16轮Feistel、FP）\n"
                    prompt += "          - 绝对不能只是 `feedback[j] ^ key[j]` 这样的XOR操作！\n"
                    prompt += "          - 得到8字节（64位）的加密结果，这是密钥流（keystream）\n"
                    prompt += "      2. 取加密结果的最左字节（keystream[0]，即第一个字节）作为密钥流字节\n"
                    prompt += "      3. 密文字节 = 明文字节 XOR 密钥流字节\n"
                    prompt += "          - cipher[i] = plain[i] XOR keystream[0]\n"
                    prompt += "      4. **反馈寄存器左移8位，加密结果的最左字节移入反馈寄存器的最右字节**\n"
                    prompt += "          - 实现方式：\n"
                    prompt += "            * 将反馈寄存器向左移动8位（1字节）\n"
                    prompt += "            * 将加密结果的最左字节（keystream[0]）移入反馈寄存器的最右位置\n"
                    prompt += "            * C语言实现：memmove(feedback, feedback+1, 7); feedback[7] = keystream[0];\n"
                    prompt += "          - **关键：反馈寄存器更新为加密结果的最左字节（keystream[0]），不是密文！**\n"
                    prompt += "          - **这是OFB和CFB的关键区别：OFB的反馈是加密结果，CFB的反馈是密文！**\n"
                    prompt += "      5. 重复步骤1-4直到处理完所有明文字节\n"
                    prompt += "      6. 输出所有密文字节的十六进制表示（不输出IV，不输出密钥）\n"
                    prompt += "      7. **重要：OFB模式不填充，密文长度等于明文长度**\n"
                    prompt += "    * **对于OFB-64模式（每次处理8字节块）：**\n"
                    prompt += "      1. **使用完整的DES加密函数加密反馈寄存器（使用密钥）**\n"
                    prompt += "          - 这一步必须调用完整的DES加密算法（密钥调度、IP、16轮Feistel、FP）\n"
                    prompt += "          - 绝对不能只是 `feedback[j] ^ key[j]` 这样的XOR操作！\n"
                    prompt += "          - 得到8字节（64位）的加密结果，这是密钥流（keystream）\n"
                    prompt += "      2. 密文块 = 明文块 XOR 密钥流（整个8字节）\n"
                    prompt += "          - cipher[i:i+8] = plain[i:i+8] XOR keystream\n"
                    prompt += "      3. **反馈寄存器更新为整个加密结果（不是密文！）**\n"
                    prompt += "          - 实现：memcpy(feedback, keystream, 8);\n"
                    prompt += "          - **关键：反馈寄存器更新为整个加密结果（keystream），不是密文！**\n"
                    prompt += "      4. 如果最后一个块不足8字节，只处理实际字节数\n"
            if not use_compact_prompt:
                prompt += "    * **绝对不能只是简单的XOR操作，必须使用真正的加密函数！**\n"
                prompt += "    * **重要：OFB模式的反馈是加密结果（keystream），不是密文！这是与CFB模式的关键区别！**\n"
                prompt += "    * **常见错误：**\n"
                if algorithm.upper() == 'AES':
                    prompt += "      - 使用OFB-128但测试数据期望OFB-8（或反之）\n"
                    prompt += "      - **最严重错误：使用AES.MODE_OFB但测试数据需要OFB-8（必须手动实现OFB-8）**\n"
                    prompt += "      - **最严重错误：反馈寄存器更新为密文（这是CFB模式，不是OFB！）**\n"
                    prompt += "        * 错误：feedback[15] = cipher[i]; （这是CFB模式！）\n"
                    prompt += "        * 正确：feedback = feedback[1:] + [keystream_byte]; （这是OFB模式！）\n"
                else:
                    prompt += "      - 使用OFB-64但测试数据期望OFB-8（或反之）\n"
                    prompt += "      - **最严重错误：反馈寄存器更新为密文（这是CFB模式，不是OFB！）**\n"
                    prompt += "        * 错误：feedback[7] = cipher[i]; （这是CFB模式！）\n"
                    prompt += "        * 正确：feedback[7] = keystream[0]; （这是OFB模式！）\n"
                prompt += "      - 反馈寄存器移位错误（OFB-8必须左移8位）\n"
                prompt += "      - 密钥流生成不正确（必须使用真正的加密函数，不是XOR）\n"
                prompt += "      - 输出了IV或密钥（只应该输出密文）\n"
            else:
                prompt += "    * 反馈更新为加密结果（keystream），不是密文（这是与CFB的区别）\n"
                prompt += "    * 必须使用真正的加密函数，不是简单XOR\n"
            prompt += "\n"
        elif mode == 'CFB':
            if use_compact_prompt:
                # 简化版CFB说明
                prompt += "\n13. **CFB模式要点：**\n"
                if algorithm.upper() == 'DES':
                    prompt += "    * 使用DES加密函数（不是简单XOR）\n"
                    prompt += "    * 通常使用CFB-8（每次1字节），反馈更新为密文（不是加密结果）\n"
                elif algorithm.upper() == 'AES':
                    prompt += "    * 使用AES加密函数（不是简单XOR）\n"
                    prompt += "    * CFB-8（每次1字节）或CFB-128（每次16字节），反馈更新为密文\n"
            else:
                # 完整版CFB说明
                prompt += "\n13. **对于CFB模式，必须严格按照标准实现：**\n"
                if algorithm.upper() == 'DES':
                    prompt += "    * CFB模式使用分组密码（DES）的加密函数，不是简单的XOR\n"
                    prompt += "    * 初始化：反馈寄存器 = IV（8字节）\n"
                    prompt += "    * 对于每个明文块（CFB-8模式，每次处理1字节）：\n"
                    prompt += "      1. 使用真正的DES加密函数加密反馈寄存器（使用密钥），得到加密结果（8字节）\n"
                    prompt += "      2. 取加密结果的最左字节（加密结果[0]）\n"
                    prompt += "      3. cipher[i] = plain[i] XOR 加密结果[0]\n"
                    prompt += "      4. 反馈寄存器左移8位：memmove(feedback, feedback+1, 7); feedback[7] = cipher[i];\n"
                    prompt += "    * **关键：反馈寄存器必须更新为密文，不是加密结果！**\n"
                    prompt += "    * **这是CFB和OFB的关键区别：CFB的反馈是密文，OFB的反馈是加密结果！**\n"
                    prompt += "    * **绝对不能只是简单的XOR操作，必须使用真正的DES加密函数！**\n"
                    prompt += "    * **对于Python，使用pycryptodome库时，必须明确指定segment_size=8（CFB-8模式）：**\n"
                    prompt += "      - `cipher = DES.new(key, DES.MODE_CFB, iv=iv, segment_size=8)`\n"
                elif algorithm.upper() == 'AES':
                    prompt += "    * CFB模式使用分组密码（AES）的加密函数，不是简单的XOR\n"
                    prompt += "    * 初始化：反馈寄存器 = IV（16字节）\n"
                    prompt += "    * **重要：AES CFB模式有两种变体，必须根据测试数据选择正确的变体：**\n"
                    prompt += "      - CFB-8：每次处理1字节（8位），反馈寄存器每次左移8位\n"
                    prompt += "      - CFB-128：每次处理16字节（128位），反馈寄存器每次更新为整个加密结果\n"
                    prompt += "      - **OpenSSL的AES CFB模式默认使用CFB-128（128位反馈）**\n"
                # 如果提供了测试数据，尝试确定应该使用哪个segment_size
                if test_data and 'expected_ciphertext' in test_data:
                    # 尝试使用CFB-8和CFB-128来匹配测试数据
                    try:
                        from Crypto.Cipher import AES
                        plaintext_hex = test_data.get('plaintext', '')
                        key_hex = test_data.get('key', '')
                        iv_hex = test_data.get('iv', '')
                        expected = test_data.get('expected_ciphertext', '').lower().replace(' ', '')
                        
                        if plaintext_hex and key_hex and iv_hex:
                            plaintext = bytes.fromhex(plaintext_hex.replace(' ', ''))
                            key = bytes.fromhex(key_hex.replace(' ', ''))
                            iv = bytes.fromhex(iv_hex.replace(' ', ''))
                            
                            # 测试CFB-8
                            cipher_cfb8 = AES.new(key, AES.MODE_CFB, iv=iv, segment_size=8)
                            result_cfb8 = cipher_cfb8.encrypt(plaintext).hex().lower()
                            
                            # 测试CFB-128
                            cipher_cfb128 = AES.new(key, AES.MODE_CFB, iv=iv, segment_size=128)
                            result_cfb128 = cipher_cfb128.encrypt(plaintext).hex().lower()
                            
                            if result_cfb8 == expected:
                                prompt += f"      - **根据测试数据，必须使用CFB-8模式（segment_size=8）！**\n"
                                prompt += f"      - 测试数据验证：CFB-8模式的结果与预期密文完全匹配\n"
                            elif result_cfb128 == expected:
                                prompt += f"      - **根据测试数据，必须使用CFB-128模式（segment_size=128）！**\n"
                                prompt += f"      - 测试数据验证：CFB-128模式的结果与预期密文完全匹配\n"
                            else:
                                prompt += f"      - **警告：测试数据与CFB-8和CFB-128的结果都不匹配，请仔细检查！**\n"
                                prompt += f"      - CFB-8结果：{result_cfb8[:32]}...\n"
                                prompt += f"      - CFB-128结果：{result_cfb128[:32]}...\n"
                                prompt += f"      - 预期结果：{expected[:32]}...\n"
                    except Exception as e:
                        # 如果测试失败，继续使用通用提示
                        pass
                if not use_compact_prompt:
                    prompt += "    * **对于CFB-128模式（每次处理16字节块）：**\n"
                    prompt += "      1. 使用真正的AES加密函数加密反馈寄存器（使用密钥），得到加密结果（16字节）\n"
                    prompt += "      2. 密文块 = 明文块 XOR 加密结果（整个16字节）\n"
                    prompt += "      3. 反馈寄存器更新为密文块（整个16字节）\n"
                    prompt += "    * **对于CFB-8模式（每次处理1字节）：**\n"
                    prompt += "      1. 使用真正的AES加密函数加密反馈寄存器（使用密钥），得到加密结果（16字节）\n"
                    prompt += "      2. 取加密结果的最左字节（加密结果[0]）\n"
                    prompt += "      3. cipher[i] = plain[i] XOR 加密结果[0]\n"
                    prompt += "      4. 反馈寄存器左移8位：memmove(feedback, feedback+1, 15); feedback[15] = cipher[i];\n"
                    prompt += "    * **关键：反馈寄存器必须更新为密文，不是加密结果！**\n"
                    prompt += "    * **这是CFB和OFB的关键区别：CFB的反馈是密文，OFB的反馈是加密结果！**\n"
                    prompt += "    * **绝对不能只是简单的XOR操作，必须使用真正的AES加密函数！**\n"
                    prompt += "    * **对于Python，使用pycryptodome库时，必须明确指定segment_size：**\n"
                    prompt += "      - CFB-8：`cipher = AES.new(key, AES.MODE_CFB, iv=iv, segment_size=8)`\n"
                    prompt += "      - CFB-128：`cipher = AES.new(key, AES.MODE_CFB, iv=iv, segment_size=128)`（默认）\n"
                    prompt += "    * **对于Python，使用cryptography库时，CFB模式默认使用128位反馈（CFB-128）：**\n"
                    prompt += "      - `modes.CFB(iv)` 默认是CFB-128模式\n"
                    prompt += "      - 如果需要CFB-8，需要使用pycryptodome库并指定segment_size=8\n"
                    prompt += "    * **建议：对于AES CFB模式，优先使用pycryptodome库，可以明确控制segment_size！**\n"
                else:
                    prompt += "    * 反馈更新为密文（不是加密结果），这是与OFB的区别\n"
                    prompt += "    * 必须使用真正的加密函数，不是简单XOR\n"
            prompt += "\n"
        
        prompt += "\n**绝对重要：必须生成完整的、可运行的代码！**\n"
        prompt += "- 不能只是函数声明或框架代码\n"
        prompt += "- 不能只是示例代码或部分实现\n"
        prompt += "- 不能有空的函数体（如 `void function() { }` 或 `void function() { // 实现 }`）\n"
        prompt += "- 所有函数必须有完整的实现，函数体必须完整，不能在中途截断\n"
        prompt += "- **所有语句必须完整，不能只有部分表达式，例如：`temp[0] = gf(...)` 必须完整，不能只有 `temp[0] = gf`**\n"
        prompt += "- **所有函数调用必须完整，包括所有参数和闭合括号，例如：`result = calculate(a, b);` 必须完整**\n"
        prompt += "- **所有赋值语句必须完整，不能只有变量名和等号，例如：`value = expression;` 必须完整**\n"
        prompt += "- 所有常量表（如S盒、置换表）必须有完整的数值\n"
        prompt += "- 代码必须可以直接编译运行，不需要用户补充任何代码\n"
        prompt += "- 对于C语言的DES实现，必须包含所有置换表、S盒的完整数值定义\n"
        prompt += "- 不能使用占位符（如 `/* PC1置换表 */`），必须提供完整的表数据\n"
        prompt += "- 代码必须包含main函数，并且main函数必须完整实现，能够从环境变量读取输入并输出结果\n"
        prompt += "- **代码必须从 `#include` 开始，到 `main` 函数结束，中间不能有任何截断**\n"
        prompt += "- **生成代码后，请检查最后几行，确保代码完整且可以编译，没有未完成的语句或表达式**\n"
        prompt += "- **输出格式必须严格：只输出一行，格式为 '密文: xxxxxx' 或 'ciphertext: xxxxxx'，其中xxxxxx是hex编码的密文（小写，无空格），不要有任何其他输出！**\n"
        prompt += "- **绝对禁止输出解密后的明文、调试信息或其他任何内容！**\n"
        prompt += "- **如果代码包含解密功能用于验证，必须在输出密文后立即退出程序（使用exit()或return），不要输出解密结果！**\n"
        prompt += "- **示例：对于Python，应该使用 `print(f\"密文: {ciphertext_hex}\")` 然后 `exit(0)`，不要输出解密后的明文！**\n"
        prompt += "- **示例：对于C/C++，应该使用 `printf(\"密文: %s\\n\", hexString);` 然后 `return 0;`，不要输出解密后的明文！**\n"
        prompt += "- 代码必须能够处理完整的输入数据，不能只处理部分数据\n"
        prompt += "- 代码执行时间应该在合理范围内（几秒内完成），不能有死循环或无限等待\n"
        prompt += "- **所有代码行必须完整，不能有未完成的语句（如 `cd_bytes[5] = (CD >> 8) & 0` 缺少 `FF;`）**\n"
        prompt += "- **所有语句必须以分号结尾，所有表达式必须完整**\n"
        prompt += "- **检查代码末尾，确保没有未完成的函数或语句**\n"
        if language.lower() in ['cpp', 'c++']:
            prompt += "- **每个include语句必须单独一行，不能连在一起！例如：`#include <string>` 和 `#include <cctype>` 必须分开！**\n"
            prompt += "- **置换表中的所有值都必须是数字（1-64），不能有任何字母！绝对不要使用字母A, B, C, D, E, F作为数字！绝对不要将数字1写成字母l（小写L）！例如：51不能写成5l，13不能写成l3！**\n"
            prompt += "- **如果S盒或常量表中使用十六进制值，必须使用0x格式（如0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F），不能直接使用字母A, B, C, D, E, F！**\n"
            prompt += "- **所有数组元素必须是数字或已定义的常量，不能是未定义的标识符！**\n"
            prompt += "- **所有函数必须有return语句（如果函数返回非void类型）！**\n"
        prompt += "\n"
        
        prompt += "重要：只输出纯代码，不要使用markdown代码块标记（如```python、```c、```cpp等），不要输出任何说明文字、介绍或解释，只输出代码本身！"
        
        return prompt
    
