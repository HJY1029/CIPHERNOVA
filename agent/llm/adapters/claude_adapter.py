from typing import Dict, Optional, Any
import httpx
from agent.llm.base import BaseLLMAdapter
from utils.logger import setup_logger

logger = setup_logger()

# 与 Anthropic 文档一致；聚合平台可能使用不同 ID，以控制台「可用模型」为准
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"


def _normalize_anthropic_sdk_base_url(base_url: Optional[str]) -> Optional[str]:
    """
    AsyncAnthropic 会在 base_url 后拼接 /v1/messages。
    若配置写成 .../v1/messages，会变成 .../v1/messages/v1/messages 导致 404。
    """
    if not base_url:
        return None
    u = str(base_url).strip().rstrip("/")
    low = u.lower()
    for suf in ("/v1/messages",):
        if low.endswith(suf):
            u = u[: -len(suf)].rstrip("/")
            logger.warning(
                "Claude base_url 不应包含路径 %s（SDK 会自动追加）。已规范为: %s",
                suf,
                u,
            )
            break
    return u or None


def _derive_openai_compatible_base_url(base_url: Optional[str]) -> Optional[str]:
    """
    从 anthropic 风格地址推导 OpenAI 兼容 chat.completions 的 base_url。
    例：
      https://x/anthropic -> https://x/v1
      https://x/anthropic/v1/messages -> https://x/v1
    """
    if not base_url:
        return None
    u = str(base_url).strip().rstrip("/")
    low = u.lower()
    if low.endswith("/v1/messages"):
        u = u[: -len("/v1/messages")].rstrip("/")
        low = u.lower()
    if low.endswith("/anthropic"):
        u = u[: -len("/anthropic")].rstrip("/")
    if not u:
        return None
    if not u.lower().endswith("/v1"):
        u = f"{u}/v1"
    return u


def _openai_fallback_base_urls(base_url: Optional[str]) -> list[str]:
    """Anthropic 404 时依次尝试的 OpenAI 兼容 base_url（均指向 .../v1/chat/completions）。"""
    out: list[str] = []
    if not base_url:
        return out
    root = _derive_openai_compatible_base_url(base_url)
    if root and root not in out:
        out.append(root)
    u = str(base_url).strip().rstrip("/")
    low = u.lower()
    if low.endswith("/v1/messages"):
        u = u[: -len("/v1/messages")].rstrip("/")
        low = u.lower()
    if low.endswith("/anthropic"):
        anthropic_v1 = f"{u}/v1"
        if anthropic_v1 not in out:
            out.append(anthropic_v1)
    return out


def _is_not_found_error(exc: BaseException) -> bool:
    if getattr(exc, "status_code", None) == 404:
        return True
    try:
        from anthropic import NotFoundError

        if isinstance(exc, NotFoundError):
            return True
    except ImportError:
        pass
    msg = str(exc).lower()
    return "404" in msg or "not found" in msg or "page not found" in msg


def _is_transport_timeout_or_unreachable(exc: BaseException) -> bool:
    """连接失败、读超时等：部分代理上 Anthropic 路径不可用，可改走 OpenAI 兼容。"""
    try:
        from anthropic import APITimeoutError, APIConnectionError

        if isinstance(exc, (APITimeoutError, APIConnectionError)):
            return True
    except ImportError:
        pass
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.NetworkError,
        ),
    ):
        return True
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg:
        return True
    if "connection" in msg and ("refused" in msg or "reset" in msg or "aborted" in msg):
        return True
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        return _is_transport_timeout_or_unreachable(cause)
    return False


def _should_try_openai_compatible_fallback(exc: BaseException) -> bool:
    return _is_not_found_error(exc) or _is_transport_timeout_or_unreachable(exc)


class ClaudeAdapter(BaseLLMAdapter):
    """Claude适配器"""

    def _is_openai_compatible_endpoint(self, base_url: Optional[str]) -> bool:
        if not base_url:
            return False
        u = str(base_url).lower()
        # anthropic 原生路径（如 /anthropic/v1/messages）不走 OpenAI chat.completions
        if '/anthropic/' in u or u.endswith('/v1/messages'):
            return False
        return '/v1' in u or 'openai' in u
    
    def _calculate_max_tokens(self, prompt: str, system_prompt: Optional[str] = None) -> int:
        """
        根据模型上下文长度限制和输入长度动态计算max_tokens
        
        Returns:
            计算得到的max_tokens值
        """
        # 获取模型名称
        model = (self.config.get('model') or DEFAULT_CLAUDE_MODEL).strip()
        
        # Claude模型的最大上下文长度（tokens）
        # 注意：如果通过代理使用OpenAI兼容API，可能受到代理的上下文限制
        model_context_limits = {
            # Claude 4.x（官方 Messages API，约 1M / 200k 以文档为准）
            'claude-opus-4-7': 1_000_000,
            'claude-sonnet-4-6': 1_000_000,
            'claude-haiku-4-5-20251001': 200_000,
            'claude-haiku-4-5': 200_000,
            # 旧版 4.x（仍可能被部分平台使用）
            'claude-opus-4-6': 1_000_000,
            'claude-sonnet-4-5-20250929': 200_000,
            'claude-sonnet-4-5': 200_000,
            # Claude 3.x
            'claude-3-5-sonnet-20241022': 200_000,
            'claude-3-opus-20240229': 200_000,
            'claude-3-sonnet-20240229': 200_000,
            'claude-3-haiku-20240307': 200_000,
        }
        
        # 默认按模型限制；允许通过配置显式覆盖，避免被硬编码 8192 误杀
        max_context = model_context_limits.get(model.lower(), 200_000)
        cfg_max_ctx = self.config.get('max_context')
        if cfg_max_ctx is not None:
            try:
                max_context = int(cfg_max_ctx)
            except (TypeError, ValueError):
                pass
        
        # 估算输入token数
        input_text = prompt
        if system_prompt:
            input_text = system_prompt + "\n\n" + prompt
        
        # 估算：中文字符按2个token计算，英文按0.25个token计算
        chinese_chars = sum(1 for c in input_text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(input_text) - chinese_chars
        estimated_input_tokens = chinese_chars * 2 + other_chars * 0.25
        
        # 预留一些token用于响应格式和系统开销
        reserved_tokens = 1000 if max_context > 100000 else 500
        
        # 计算可用的max_tokens（确保不超过上下文限制）
        # 关键：max_tokens必须满足：estimated_input_tokens + max_tokens <= max_context
        available_tokens = max_context - int(estimated_input_tokens) - reserved_tokens
        
        # 获取配置的max_tokens
        configured_max_tokens = self.config.get('max_tokens', 16384)
        
        # 根据可用token数和上下文限制，智能设置max_tokens
        # 对于代码生成任务，需要足够的tokens来生成完整代码
        # 关键约束：estimated_input_tokens + max_tokens <= max_context
        max_tokens_upper_bound = max_context - int(estimated_input_tokens)
        
        if available_tokens < 1000:
            # 当可用token数非常少时，优先确保不超过上下文限制
            # 如果available_tokens为负数，说明输入已经很大，只能使用实际可用的值
            if available_tokens <= 0:
                # 输入太大，只能使用实际可用的token数（至少500，但不超过上限）
                max_tokens = max(500, max_tokens_upper_bound)
            else:
                # 可用token数很少但为正数，至少保留1000（如果可能）
                max_tokens = max(1000, available_tokens)
            # 确保不超过配置值和上下文限制
            max_tokens = min(configured_max_tokens, max_tokens, max_tokens_upper_bound)
        elif available_tokens < 2000:
            # 可用token数较少时，使用实际可用的值，但至少保留1500
            max_tokens = min(configured_max_tokens, max(available_tokens, 1500), max_tokens_upper_bound)
        else:
            # 可用token数充足时，使用正常逻辑，至少保留2000
            max_tokens = min(configured_max_tokens, max(available_tokens, 2000), max_tokens_upper_bound)
        
        # 最终安全检查：确保max_tokens不为负数
        max_tokens = max(max_tokens, 500)
        
        # 关键检查：如果输入tokens已经超过或接近上下文限制，拒绝请求
        if int(estimated_input_tokens) >= max_context:
            raise ValueError(
                f"输入tokens ({int(estimated_input_tokens)}) 超过或等于上下文限制 ({max_context})。"
                f"请减少prompt长度或使用支持更大上下文的模型。"
            )
        
        # 检查总tokens是否超过限制（留出100 tokens的安全边界）
        total_tokens = int(estimated_input_tokens) + max_tokens
        if total_tokens > max_context - 100:
            # 如果总tokens超过限制，减少max_tokens
            max_tokens = max(500, max_context - int(estimated_input_tokens) - 100)
            logger.warning(
                f"输入tokens ({int(estimated_input_tokens)}) 接近上下文限制 ({max_context})，"
                f"已将max_tokens调整为 {max_tokens} 以确保不超过限制"
            )
        
        logger.info(f"Claude模型 {model} - 上下文限制: {max_context}, 估算输入tokens: {int(estimated_input_tokens)}, 可用tokens: {available_tokens}, 设置max_tokens: {max_tokens}, 总tokens: {int(estimated_input_tokens) + max_tokens}")
        
        return max_tokens
    
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        try:
            base_url = self.config.get('base_url', None)
            model = self.config.get('model', DEFAULT_CLAUDE_MODEL)
            # 动态计算max_tokens，确保不超过模型上下文限制
            try:
                max_tokens = self._calculate_max_tokens(prompt, system_prompt)
            except ValueError as e:
                logger.error(f"输入tokens超过上下文限制: {e}")
                raise ValueError(f"输入内容过长，超过了模型的上下文限制。{str(e)}")
            
            # OpenAI 兼容端点：chat.completions
            if self._is_openai_compatible_endpoint(base_url):
                return await self._generate_via_openai_compatible(
                    base_url=base_url,
                    model=model,
                    max_tokens=max_tokens,
                    prompt=prompt,
                    system_prompt=system_prompt,
                )
            else:
                # Anthropic 原生接口（支持代理的 anthropic base_url）
                from anthropic import AsyncAnthropic
                anthropic_base = _normalize_anthropic_sdk_base_url(base_url)
                if anthropic_base:
                    logger.info("Claude Anthropic SDK base_url: %s（请求路径为 .../v1/messages）", anthropic_base)
                client = AsyncAnthropic(
                    api_key=self.api_key,
                    base_url=anthropic_base,
                )
                
                messages = [{"role": "user", "content": prompt}]
                response = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens,  # 动态计算的max_tokens
                    system=system_prompt or "",
                    messages=messages
                )
                return response.content[0].text
        except Exception as e:
            # 兜底：404、超时、连接失败时尝试 OpenAI 兼容（/v1 或 /anthropic/v1）
            cfg_base = self.config.get("base_url")
            if _should_try_openai_compatible_fallback(e) and not self._is_openai_compatible_endpoint(cfg_base):
                max_tokens_fb = self._calculate_max_tokens(prompt, system_prompt)
                model_fb = self.config.get("model", DEFAULT_CLAUDE_MODEL)
                last_fb_err: Optional[BaseException] = None
                for fb in _openai_fallback_base_urls(cfg_base):
                    reason = (
                        "404/路径不存在"
                        if _is_not_found_error(e)
                        else "网络超时或连接异常"
                    )
                    logger.warning(
                        "Claude Anthropic 路径失败（%s），回退尝试 OpenAI 兼容: %s/chat/completions",
                        reason,
                        fb,
                    )
                    try:
                        return await self._generate_via_openai_compatible(
                            base_url=fb,
                            model=model_fb,
                            max_tokens=max_tokens_fb,
                            prompt=prompt,
                            system_prompt=system_prompt,
                        )
                    except Exception as fallback_error:
                        last_fb_err = fallback_error
                        logger.warning("OpenAI 兼容回退 %s 失败: %s", fb, fallback_error)
                if last_fb_err is not None:
                    logger.error("Claude 全部 OpenAI 兼容回退均失败，最后错误: %s", last_fb_err)

            bu = self.config.get("base_url")
            abu = _normalize_anthropic_sdk_base_url(bu) if bu and not self._is_openai_compatible_endpoint(bu) else bu
            logger.error(
                "Claude API错误: %s（配置 base_url=%r，Anthropic 路径下实际 SDK base=%r）",
                e,
                bu,
                abu,
            )
            raise

    async def _generate_via_openai_compatible(
        self,
        base_url: Optional[str],
        model: str,
        max_tokens: int,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> str:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=base_url
        )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,  # 降低temperature以加快生成速度
            max_tokens=max_tokens  # 动态计算的max_tokens
        )
        return response.choices[0].message.content

