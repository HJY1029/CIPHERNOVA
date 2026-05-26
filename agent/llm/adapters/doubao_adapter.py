from typing import Dict, Optional, Any
from agent.llm.base import BaseLLMAdapter
from utils.logger import setup_logger

logger = setup_logger()

class DoubaoAdapter(BaseLLMAdapter):
    """Doubao适配器"""
    
    def _resolve_max_context(self) -> int:
        """
        推断豆包 / 火山方舟推理接入点的上下文上限。
        ep- 形式的 endpoint ID 名称中通常不含 4k/8k，旧逻辑会误用 8192 导致长 prompt 在本地被拒绝。
        可在 config.yaml 的 doubao 下设置 context_limit（整数）覆盖。
        """
        raw = self.config.get("context_limit")
        if raw is None:
            raw = self.config.get("max_context")
        if raw is not None:
            try:
                n = int(raw)
                if n > 0:
                    return n
            except (TypeError, ValueError):
                pass
        model = (self.config.get("model") or "doubao-pro-4k").strip()
        ml = model.lower()
        if "256k" in ml or ("256" in ml and "k" in ml):
            return 262144
        if "128k" in ml or ("128" in ml and "k" in ml):
            return 131072
        if "32k" in ml or ("32" in ml and "k" in ml):
            return 32768
        if "pro-4k" in ml or "4k" in ml:
            return 4096
        if "8k" in ml:
            return 8192
        if ml.startswith("ep-"):
            return 131072
        return 8192
    
    def _calculate_max_tokens(self, prompt: str, system_prompt: Optional[str] = None) -> int:
        """
        根据模型上下文长度限制和输入长度动态计算max_tokens
        
        Returns:
            计算得到的max_tokens值
        """
        model = self.config.get('model', 'doubao-pro-4k')
        max_context = self._resolve_max_context()
        
        # 估算输入token数
        input_text = prompt
        if system_prompt:
            input_text = system_prompt + "\n\n" + prompt
        
        # 估算：中文字符按2个token计算，英文按0.25个token计算
        chinese_chars = sum(1 for c in input_text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(input_text) - chinese_chars
        estimated_input_tokens = chinese_chars * 2 + other_chars * 0.25
        
        # 预留一些token用于响应格式和系统开销
        reserved_tokens = 500
        
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
        
        logger.info(f"Doubao模型 {model} - 上下文限制: {max_context}, 估算输入tokens: {int(estimated_input_tokens)}, 可用tokens: {available_tokens}, 设置max_tokens: {max_tokens}, 总tokens: {int(estimated_input_tokens) + max_tokens}")
        
        return max_tokens
    
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        try:
            from openai import AsyncOpenAI
            base_url = self.config.get('base_url', 'https://ark.cn-beijing.volces.com/api/v3')
            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=base_url
            )
            
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            model = self.config.get('model', 'doubao-pro-4k')
            
            # 检查是否是占位符
            if model == 'ep-xxx' or model.startswith('ep-xxx'):
                raise ValueError(
                    "豆包模型未配置！\n"
                    "请在 config.yaml 中将 doubao 的 model 字段替换为你的实际 endpoint ID。\n"
                    "获取 endpoint ID 的方法：\n"
                    "1. 登录火山引擎控制台\n"
                    "2. 进入豆包大模型服务\n"
                    "3. 创建或查看你的 endpoint，获取 endpoint ID（格式如：ep-20241201-xxxxx）\n"
                    "4. 将 config.yaml 中的 model: \"ep-xxx\" 替换为你的实际 endpoint ID\n"
                    "例如：model: \"ep-20241201-xxxxx\""
                )
            
            # 动态计算max_tokens，确保不超过模型上下文限制
            # 如果输入tokens超过限制，会抛出ValueError
            try:
                max_tokens = self._calculate_max_tokens(prompt, system_prompt)
            except ValueError as e:
                # 输入tokens超过限制，返回友好的错误信息
                logger.error(f"输入tokens超过上下文限制: {e}")
                raise ValueError(f"输入内容过长，超过了模型的上下文限制。{str(e)}")
            
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,  # 降低temperature以加快生成速度，同时保持代码质量
                max_tokens=max_tokens  # 设置较大的max_tokens，确保代码不会被截断
            )
            return response.choices[0].message.content
        except ValueError:
            # 重新抛出ValueError（占位符错误）
            raise
        except Exception as e:
            error_msg = str(e)
            # 检查是否是模型不存在错误
            if 'NotFound' in error_msg or 'does not exist' in error_msg or '404' in error_msg or 'InvalidEndpointOrModel' in error_msg:
                model = self.config.get('model', 'doubao-pro-4k')
                friendly_error = (
                    f"豆包模型 '{model}' 不存在或无法访问。\n\n"
                    f"可能的原因：\n"
                    f"1. endpoint ID 配置错误（当前配置：{model}）\n"
                    f"2. 该 endpoint 不存在或已被删除\n"
                    f"3. 你的 API 密钥没有访问该 endpoint 的权限\n\n"
                    f"解决方法：\n"
                    f"1. 登录火山引擎控制台，确认你的 endpoint ID\n"
                    f"2. 检查 config.yaml 中的 model 配置是否正确\n"
                    f"3. 确认你的 API 密钥有权限访问该 endpoint\n"
                    f"4. 如果 endpoint ID 正确但仍无法访问，请联系火山引擎技术支持\n\n"
                    f"原始错误: {error_msg}"
                )
                logger.error(f"Doubao API错误: {friendly_error}")
                raise ValueError(friendly_error)
            else:
                logger.error(f"Doubao API错误: {error_msg}")
                raise

