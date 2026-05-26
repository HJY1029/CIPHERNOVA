from typing import Dict, Optional, Any
from agent.llm.base import BaseLLMAdapter, is_qwen_coder_local_provider
from utils.logger import setup_logger

logger = setup_logger()


def _ollama_transient_http_error(exc: BaseException) -> bool:
    """本地 Ollama 短时不可用、连接被重置、读一半断开等，可重试；上下文超限等不可重试。"""
    try:
        from openai import APIConnectionError, APITimeoutError

        if isinstance(exc, (APIConnectionError, APITimeoutError)):
            return True
    except ImportError:
        pass
    try:
        import httpx

        if isinstance(
            exc,
            (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.ConnectTimeout,
                httpx.ReadError,
                httpx.WriteError,
                httpx.RemoteProtocolError,
                httpx.NetworkError,
            ),
        ):
            return True
    except ImportError:
        pass
    msg = str(exc).lower()
    if "connection error" in msg or "connection reset" in msg:
        return True
    if "remote protocol error" in msg or "premature close" in msg:
        return True
    if "timed out" in msg or "timeout" in msg:
        return True
    return False

class OpenAIAdapter(BaseLLMAdapter):
    """OpenAI 官方 API 或任意 OpenAI 兼容端点（如本地 Ollama /v1）。"""
    
    def _log_backend_label(self) -> str:
        pid = (self.config.get("_llm_provider_id") or "openai").strip().lower()
        if is_qwen_coder_local_provider(pid):
            return f"本地 Ollama（{pid} · OpenAI 兼容 HTTP）"
        if pid == "openai":
            return "OpenAI"
        if pid == "codex":
            return "Codex（CloseAI / OpenAI 兼容 API）"
        return f"{pid}（OpenAI 兼容 API）"
    
    def _calculate_max_tokens(self, prompt: str, system_prompt: Optional[str] = None) -> int:
        """
        根据模型上下文长度限制和输入长度动态计算max_tokens
        
        Returns:
            计算得到的max_tokens值
        """
        # 获取模型名称
        model = (self.config.get('model') or 'gpt-4').strip()
        
        # 不同模型的最大上下文长度（tokens）
        model_context_limits = {
            'gpt-4': 8192,
            # GPT-5 Codex / 代理映射名：上下文以网关为准，此处给较大默认以免误判 8k
            'gpt-5-codex': 256000,
            'gpt-4-turbo': 128000,
            'gpt-4-turbo-preview': 128000,
            'gpt-4-0125-preview': 128000,
            'gpt-4-1106-preview': 128000,
            'gpt-3.5-turbo': 16385,
            'gpt-3.5-turbo-16k': 16385,
            # DeepSeek：走 OpenAI 兼容客户端时也必须登记，否则会误用未知模型默认 8192
            'deepseek-chat': 64000,
            'deepseek-coder': 64000,
            'deepseek-reasoner': 64000,
        }
        
        cfg_max_ctx = self.config.get('max_context')
        unknown_default = 8192
        if cfg_max_ctx is not None:
            try:
                unknown_default = int(cfg_max_ctx)
            except (TypeError, ValueError):
                pass
        
        max_context = model_context_limits.get(model.lower(), unknown_default)
        # 本地 / Ollama 常见：Qwen2.5 Coder 等默认按 32K 上下文估算（避免误判为 8192）
        if max_context == 8192 and "qwen" in model.lower():
            max_context = 32768
        # DeepSeek 系列名称若仍落入未知默认（如带后缀的自定义名）
        if max_context <= 8192 and "deepseek" in model.lower():
            max_context = 64000
        
        # 估算输入token数（简单估算：1 token ≈ 4个字符）
        input_text = prompt
        if system_prompt:
            input_text = system_prompt + "\n\n" + prompt
        
        # 更准确的估算：中文字符按2个token计算，英文按0.25个token计算
        chinese_chars = sum(1 for c in input_text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(input_text) - chinese_chars
        estimated_input_tokens = chinese_chars * 2 + other_chars * 0.25
        
        # 预留一些token用于响应格式和系统开销
        # 当输入很大时，减少预留空间，让更多空间用于输出
        if int(estimated_input_tokens) > max_context * 0.8:
            # 输入超过80%上下文时，只预留200 tokens
            reserved_tokens = 200
        elif int(estimated_input_tokens) > max_context * 0.6:
            # 输入超过60%上下文时，预留300 tokens
            reserved_tokens = 300
        else:
            # 正常情况下预留500 tokens
            reserved_tokens = 500
        
        # 计算可用的max_tokens（确保不超过上下文限制）
        # 关键：max_tokens必须满足：estimated_input_tokens + max_tokens <= max_context
        available_tokens = max_context - int(estimated_input_tokens) - reserved_tokens
        
        # 获取配置的max_tokens
        configured_max_tokens = self.config.get('max_tokens', 16384)
        
        # 检查是否通过代理，代理可能对max_tokens有限制
        # 注意：某些代理（如openai-proxy.org）确实限制了max_tokens为4096
        # 我们需要检测并应用这个限制，同时依赖截断检测和继续生成机制来生成完整代码
        base_url = self.config.get('base_url', None)
        proxy_max_tokens_limit = None
        if base_url and 'openai-proxy.org' in base_url:
            # 通过代理时，gpt-4-turbo的max_tokens被限制为4096
            if 'turbo' in model.lower():
                proxy_max_tokens_limit = 4096
                logger.info(f"检测到代理，gpt-4-turbo的max_tokens限制为 {proxy_max_tokens_limit}（将通过截断检测和继续生成机制确保代码完整）")
        
        # 根据可用token数和上下文限制，智能设置max_tokens
        # 对于代码生成任务，需要足够的tokens来生成完整代码
        # 关键约束：estimated_input_tokens + max_tokens <= max_context
        max_tokens_upper_bound = max_context - int(estimated_input_tokens)
        
        # 当输入很大时（超过70%上下文），直接使用max_tokens_upper_bound，不减去reserved_tokens
        # 这样可以最大化输出空间，避免代码被截断
        if int(estimated_input_tokens) > max_context * 0.7:
            # 输入很大，直接使用所有可用空间，最大化输出
            max_tokens = max(500, max_tokens_upper_bound)
            max_tokens = min(configured_max_tokens, max_tokens)
        elif available_tokens < 2000:
            # 当可用token数很少时，直接使用所有可用空间，不设置最小值限制
            # 如果available_tokens为负数，说明输入已经很大，只能使用实际可用的值
            if available_tokens <= 0:
                # 输入太大，只能使用实际可用的token数（至少500）
                max_tokens = max(500, max_tokens_upper_bound)
            else:
                # 可用token数很少但为正数，直接使用max_tokens_upper_bound，尽可能使用所有可用空间
                max_tokens = max_tokens_upper_bound
            # 确保不超过配置值
            max_tokens = min(configured_max_tokens, max_tokens)
        else:
            # 可用token数充足时，使用正常逻辑，至少保留2000
            max_tokens = min(configured_max_tokens, max(available_tokens, 2000), max_tokens_upper_bound)
        
        # 最终安全检查：确保max_tokens不为负数且至少为500
        max_tokens = max(max_tokens, 500)
        
        # 应用代理的max_tokens限制（如果存在）
        if proxy_max_tokens_limit:
            max_tokens = min(max_tokens, proxy_max_tokens_limit)
            logger.info(f"应用代理限制，max_tokens调整为 {max_tokens}")
        
        # 关键检查：如果输入tokens已经超过或接近上下文限制，拒绝请求
        if int(estimated_input_tokens) >= max_context:
            raise ValueError(
                f"输入tokens ({int(estimated_input_tokens)}) 超过或等于上下文限制 ({max_context})。"
                f"请减少prompt长度或使用支持更大上下文的模型（如gpt-4-turbo，支持128K上下文）。"
            )
        
        # 检查总tokens是否超过限制（留出100 tokens的安全边界）
        total_tokens = int(estimated_input_tokens) + max_tokens
        if total_tokens > max_context - 100:
            # 如果总tokens超过限制，减少max_tokens
            max_tokens = max(500, max_context - int(estimated_input_tokens) - 100)
            # 再次应用代理限制
            if proxy_max_tokens_limit:
                max_tokens = min(max_tokens, proxy_max_tokens_limit)
            logger.warning(
                f"输入tokens ({int(estimated_input_tokens)}) 接近上下文限制 ({max_context})，"
                f"已将max_tokens调整为 {max_tokens} 以确保不超过限制"
            )
        
        backend = self._log_backend_label()
        logger.info(
            f"{backend} | 模型 {model} - 上下文限制: {max_context}, 估算输入tokens: {int(estimated_input_tokens)}, "
            f"可用tokens: {available_tokens}, 设置max_tokens: {max_tokens}, 总tokens: {int(estimated_input_tokens) + max_tokens}"
        )
        
        return max_tokens
    
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        import asyncio
        import httpx
        from openai import AsyncOpenAI

        # 支持代理URL配置
        base_url = self.config.get('base_url', None)
        provider_id = (self.config.get("_llm_provider_id") or "").strip().lower()
        is_qwen_local = is_qwen_coder_local_provider(provider_id)
        # 本地 Ollama：prompt 长 + max_tokens 大时，CPU 上 7B 可能远超默认 HTTP 读超时（常见 600s）
        default_http_timeout = 7200.0 if is_qwen_local else 600.0
        try:
            http_timeout = float(
                self.config.get("request_timeout_seconds", default_http_timeout)
            )
        except (TypeError, ValueError):
            http_timeout = default_http_timeout
        http_timeout = max(60.0, http_timeout)
        # 显式拆分 connect / read / write：单参数 Timeout 在部分 httpx/SDK 组合下易与默认 read=600s 混淆；
        # 对本地 Ollama 长生成，read/write 必须与配置一致，并在 create() 上再传一层以免被覆盖。
        connect_s = 120.0 if is_qwen_local else 60.0
        # 本地 Ollama：pool 若固定为 300s，长 prompt + 大 max_tokens 时会在约 5min 出现
        # Request timed out（与 read=7200 无关）。pool 须与读/写同级，避免被客户端栈提前掐断。
        pool_s = http_timeout if is_qwen_local else 120.0
        timeout = httpx.Timeout(
            connect=connect_s,
            read=http_timeout,
            write=http_timeout,
            pool=pool_s,
        )
        max_retries = self.config.get("max_retries")
        if max_retries is None:
            max_retries = 0 if is_qwen_local else 2
        else:
            try:
                max_retries = int(max_retries)
            except (TypeError, ValueError):
                max_retries = 2
        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        logger.info(
            f"{self._log_backend_label()} | HTTP timeout connect={connect_s}s read/write={http_timeout}s "
            f"pool={pool_s}s, max_retries={max_retries}"
        )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # 动态计算max_tokens，确保不超过模型上下文限制
        # 如果输入tokens超过限制，会抛出ValueError
        try:
            max_tokens = self._calculate_max_tokens(prompt, system_prompt)
        except ValueError as e:
            # 输入tokens超过限制，返回友好的错误信息
            logger.error(f"输入tokens超过上下文限制: {e}")
            raise ValueError(f"输入内容过长，超过了模型的上下文限制。{str(e)}")

        attempts_max = 6 if is_qwen_local else 1
        backoff_s = 1.5
        last_exc: Optional[BaseException] = None
        # 部分网关上的 Codex / 推理模型不接受 chat.completions 的 temperature（报 invalid_request_error）
        omit_temp = bool(self.config.get("omit_temperature"))
        temp_cfg = self.config.get("temperature")
        try:
            temperature = float(temp_cfg) if temp_cfg is not None else 0.3
        except (TypeError, ValueError):
            temperature = 0.3

        for attempt in range(attempts_max):
            try:
                create_kwargs: Dict[str, Any] = {
                    "model": self.config.get("model", "gpt-4"),
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                }
                if not omit_temp:
                    create_kwargs["temperature"] = temperature
                response = await client.chat.completions.create(**create_kwargs)
                return response.choices[0].message.content
            except Exception as e:
                last_exc = e
                can_retry = (
                    is_qwen_local
                    and attempt < attempts_max - 1
                    and _ollama_transient_http_error(e)
                )
                if can_retry:
                    logger.warning(
                        f"{self._log_backend_label()} 暂态失败 ({attempt + 1}/{attempts_max}): {e} — "
                        f"{backoff_s:.1f}s 后重试"
                    )
                    await asyncio.sleep(backoff_s)
                    backoff_s = min(backoff_s * 2.0, 45.0)
                    continue
                logger.error(f"{self._log_backend_label()} 请求错误: {e}")
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("OpenAIAdapter.generate: unreachable")

