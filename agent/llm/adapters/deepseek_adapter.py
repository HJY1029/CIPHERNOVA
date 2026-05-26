import httpx
import json
from typing import Dict, Optional, Any
from agent.llm.base import BaseLLMAdapter
from utils.logger import setup_logger

logger = setup_logger()

class DeepSeekAdapter(BaseLLMAdapter):
    """DeepSeek适配器"""
    
    def _calculate_max_tokens(self, prompt: str, system_prompt: Optional[str] = None) -> int:
        """
        根据模型上下文长度限制和输入长度动态计算max_tokens
        
        Returns:
            计算得到的max_tokens值
        """
        # 获取模型名称（strip 避免配置里多余空格导致字典匹配失败）
        model = (self.config.get('model') or 'deepseek-chat').strip()
        
        # DeepSeek模型的最大上下文长度（tokens）
        model_context_limits = {
            'deepseek-chat': 64000,
            'deepseek-reasoner': 64000,
            'deepseek-coder': 64000,
        }
        
        # 获取模型的最大上下文长度，默认64000；可用 config.yaml max_context 覆盖
        max_context = model_context_limits.get(model.lower(), 64000)
        oc = self.config.get('max_context')
        if oc is not None:
            try:
                max_context = int(oc)
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
        
        logger.info(f"DeepSeek模型 {model} - 上下文限制: {max_context}, 估算输入tokens: {int(estimated_input_tokens)}, 可用tokens: {available_tokens}, 设置max_tokens: {max_tokens}, 总tokens: {int(estimated_input_tokens) + max_tokens}")
        
        return max_tokens
    
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        try:
            base_url = self.config.get('base_url', 'https://api.deepseek.com')
            model = self.config.get('model', 'deepseek-chat')
            base_url_str = str(base_url)
            # 记录实际使用的配置，用于调试
            logger.info(f"DeepSeek配置 - base_url: {base_url_str}, model: {model}")
            is_thinking_model = 'v3.2_speciale' in base_url_str.lower() or 'speciale' in base_url_str.lower()
            if is_thinking_model:
                logger.warning(f"检测到思考模式端点（已过期），建议更新配置为标准端点: {base_url_str}")
            
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
            
            # 如果是思考模式模型，使用直接HTTP请求以支持showThinking参数
            if is_thinking_model:
                # 对于V3.2-Speciale，根据DeepSeek官方文档：
                # 1. 使用特殊端点：https://api.deepseek.com/v3.2_speciale_expires_on_20251215
                # 2. 模型名称应该是 "deepseek-reasoner"（不是 "deepseek-chat"）
                # 3. 端点路径应该是 /chat/completions（不是 /v1/chat/completions）
                thinking_model = 'deepseek-reasoner'  # V3.2-Speciale端点使用的模型名称
                
                # 构建请求体
                request_body = {
                    "model": thinking_model,
                    "messages": messages,
                    "temperature": 0.7,
                    "stream": False,  # 根据文档示例添加
                    "max_tokens": max_tokens  # 设置较大的max_tokens，确保代码不会被截断
                }
                
                # 尝试添加showThinking参数（如果API需要）
                request_body["showThinking"] = True
                
                # 调试：记录请求体（用于排查问题）
                logger.debug(f"DeepSeek V3.2-Speciale 请求体: {json.dumps(request_body, indent=2, ensure_ascii=False)}")
                
                # 确定API端点
                # base_url是 https://api.deepseek.com/v3.2_speciale_expires_on_20251215
                # 根据DeepSeek文档，端点路径应该是 /chat/completions（直接路径）
                if base_url.endswith('/'):
                    api_url = f"{base_url}chat/completions"
                else:
                    api_url = f"{base_url}/chat/completions"
                
                # 发送HTTP请求
                async with httpx.AsyncClient(timeout=300.0) as client:
                    response = await client.post(
                        api_url,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {self.api_key}"
                        },
                        json=request_body
                    )
                    
                    # 如果请求失败，显示详细错误信息
                    if response.status_code != 200:
                        error_detail = response.text
                        try:
                            error_json = response.json()
                            error_detail = json.dumps(error_json, indent=2, ensure_ascii=False)
                        except:
                            pass
                        raise Exception(f"DeepSeek API请求失败 (状态码: {response.status_code}): {error_detail}\n请求URL: {api_url}\n请求体: {json.dumps(request_body, indent=2, ensure_ascii=False)}")
                    
                    result = response.json()
                    
                    # 提取响应内容
                    if result.get('choices') and len(result['choices']) > 0:
                        return result['choices'][0]['message']['content']
                    else:
                        raise ValueError(f"API响应格式错误: {result}")
            else:
                # 普通模型统一使用HTTP直连，避免不同 SDK/代理对 base_url 拼接差异导致 404
                api_url = f"{base_url.rstrip('/')}/chat/completions"
                request_body = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": max_tokens,
                }
                async with httpx.AsyncClient(timeout=300.0) as client:
                    response = await client.post(
                        api_url,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {self.api_key}",
                        },
                        json=request_body,
                    )
                    if response.status_code != 200:
                        detail = response.text
                        try:
                            detail = json.dumps(response.json(), ensure_ascii=False)
                        except Exception:
                            pass
                        raise Exception(
                            f"DeepSeek API请求失败 (状态码: {response.status_code}): {detail}\n"
                            f"请求URL: {api_url}"
                        )
                    result = response.json()
                    if result.get("choices") and len(result["choices"]) > 0:
                        return result["choices"][0]["message"]["content"]
                    raise ValueError(f"API响应格式错误: {result}")
        except ValueError as e:
            # 输入tokens超过限制的错误，直接抛出
            raise
        except Exception as e:
            err_s = str(e)
            logger.error(f"DeepSeek API错误: {e}")
            if '404' in err_s or 'not found' in err_s.lower():
                logger.error(
                    "提示：若使用官方 API，请确认 config 中 base_url 为 https://api.deepseek.com/v1 "
                    "（末尾含 /v1）；缺 /v1 或重复路径可能导致 404。"
                )
            raise

