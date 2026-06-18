"""
/v1/chat/completions —— 核心路由
"""
import json
import time
import traceback

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

from models.request import ChatCompletionRequest
from middleware.auth import authenticate
from services.router import get_provider_name
from services.billing import charge_customer

router = APIRouter(prefix="/v1", tags=["💬 对话接口"])


@router.post("/chat/completions", summary="聊天补全", description="OpenAI 兼容的聊天接口。设置 stream=true 启用流式输出。")
async def chat_completions(request: Request, body: ChatCompletionRequest):
    # 1. 鉴权
    customer = await authenticate(request)

    # 2. 余额检查
    if customer["balance_cents"] <= 0:
        return JSONResponse(
            status_code=402,
            content={"error": {"message": "余额不足，请联系管理员充值", "type": "insufficient_balance"}},
        )

    # 3. 路由
    provider_name = get_provider_name(body.model)
    provider = _get_provider(provider_name)

    # 4. 执行请求
    if body.stream:
        return await _handle_stream(provider, body, customer)
    else:
        return await _handle_normal(provider, body, customer)

# Provider 实例懒加载
_providers = {}


def _get_provider(name: str):
    from config import settings

    if name not in _providers:
        if name == "deepseek":
            from providers.openai_compat import OpenAICompatProvider
            _providers[name] = OpenAICompatProvider(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )
        elif name == "qwen":
            from providers.openai_compat import OpenAICompatProvider
            _providers[name] = OpenAICompatProvider(
                api_key=settings.qwen_api_key,
                base_url=settings.qwen_base_url,
            )
        elif name == "zhipu":
            from providers.openai_compat import OpenAICompatProvider
            _providers[name] = OpenAICompatProvider(
                api_key=settings.zhipu_api_key,
                base_url=settings.zhipu_base_url,
            )
        elif name == "moonshot":
            from providers.openai_compat import OpenAICompatProvider
            _providers[name] = OpenAICompatProvider(
                api_key=settings.moonshot_api_key,
                base_url=settings.moonshot_base_url,
            )
        elif name in ("openai", "anthropic", "gemini"):
            # 国外模型统一走中转商
            if not settings.transit_api_key:
                raise RuntimeError("国外模型未配置中转，请在 .env 填写 TRANSIT_API_KEY")
            from providers.openai_compat import OpenAICompatProvider
            _providers[name] = OpenAICompatProvider(
                api_key=settings.transit_api_key,
                base_url=settings.transit_base_url,
            )
        else:
            raise ValueError(f"Unknown provider: {name}")
    return _providers[name]


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数：英文 ~4 字符/token，中文 ~1.5 字符/token"""
    if not text:
        return 0
    # 简单的字符数/4 估算
    return max(1, len(text) // 4)


def _count_input_chars(messages: list) -> int:
    """计算输入消息的总字符数"""
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, 'content') else msg.get('content', '')
        if content:
            total += len(content)
    return total


async def _handle_normal(provider, body: ChatCompletionRequest, customer: dict):
    """处理非流式请求"""
    try:
        raw = await provider.chat_completion(body)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": {"message": str(e), "type": "provider_error"}},
        )

    # 提取 token 用量
    usage = raw.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    # 如果上游没给 token 数，用字符数估算
    if not prompt_tokens:
        input_chars = _count_input_chars(body.messages)
        prompt_tokens = _estimate_tokens(str(input_chars))
    if not completion_tokens:
        # 从响应内容估算
        content = ""
        for choice in raw.get("choices", []):
            content += choice.get("message", {}).get("content", "")
        completion_tokens = _estimate_tokens(content)

    # 扣费
    billing = None
    try:
        billing = charge_customer(
            customer["customer_id"],
            customer["key_id"],
            body.model,
            prompt_tokens,
            completion_tokens,
        )
    except Exception:
        traceback.print_exc()

    # 把计费信息加到响应里
    if billing:
        raw.setdefault("usage", {})
        raw["usage"]["cost_cents"] = billing["revenue_cents"]
        raw["usage"]["profit_cents"] = billing["profit_cents"]

    return raw


async def _handle_stream(provider, body: ChatCompletionRequest, customer: dict):
    """处理流式请求 —— 累积文本内容，流结束后估算 token 并扣费"""
    state = {
        "text": "",          # 累积的输出文本
        "prompt_tokens": 0,  # 来自 usage chunk 的真实值
        "completion_tokens": 0,
        "charged": False,
    }

    async def generate():
        try:
            async for chunk in provider.chat_completion_stream(body):
                # 尝试从 chunk 中提取 usage（OpenAI stream_options 模式）
                if chunk.startswith("data: ") and chunk != "data: [DONE]\n\n":
                    try:
                        data = json.loads(chunk[6:].strip())
                        # 收集文本内容
                        choices = data.get("choices", [])
                        for choice in choices:
                            delta = choice.get("delta", {})
                            if "content" in delta and delta["content"]:
                                state["text"] += delta["content"]
                        # 检查有没有 usage 信息
                        usage = data.get("usage", {})
                        if usage:
                            state["prompt_tokens"] = usage.get("prompt_tokens", 0)
                            state["completion_tokens"] = usage.get("completion_tokens", 0)
                    except (json.JSONDecodeError, KeyError):
                        pass

                yield chunk

            # 流结束后扣费
            if not state["charged"]:
                state["charged"] = True
                prompt_tk = state["prompt_tokens"] or _estimate_tokens(
                    str(_count_input_chars(body.messages))
                )
                completion_tk = state["completion_tokens"] or _estimate_tokens(state["text"])

                if prompt_tk or completion_tk:
                    try:
                        charge_customer(
                            customer["customer_id"],
                            customer["key_id"],
                            body.model,
                            prompt_tk,
                            completion_tk,
                        )
                    except Exception:
                        pass

        except Exception as e:
            traceback.print_exc()
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'provider_error'}})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
