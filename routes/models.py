"""
/v1/models —— 列出所有可用模型
三级：最强 / 推理 / 对话
"""
from fastapi import APIRouter
from models.response import ModelList, ModelInfo

router = APIRouter(prefix="/v1", tags=["📋 模型列表"])

AVAILABLE_MODELS = [
    # OpenAI — 最强: GPT-5.5 / 推理: o3-mini / 对话: GPT-4o-mini
    ModelInfo(id="gpt-5.5", created=1747000000, owned_by="openai"),
    ModelInfo(id="o3-mini", created=1735000000, owned_by="openai"),
    ModelInfo(id="gpt-4o-mini", created=1721174717, owned_by="openai"),
    # Anthropic — 最强: Opus 4.8 / 推理: Sonnet 4.6 / 对话: Haiku 4.5
    ModelInfo(id="claude-opus-4-8", created=1759000000, owned_by="anthropic"),
    ModelInfo(id="claude-sonnet-4-6", created=1745443200, owned_by="anthropic"),
    ModelInfo(id="claude-haiku-4-5-20251001", created=1759276800, owned_by="anthropic"),
    # DeepSeek — 最强: V4 Pro / 推理: R1 / 对话: V3
    ModelInfo(id="deepseek-v4-pro", created=1759000000, owned_by="deepseek"),
    ModelInfo(id="deepseek-r1", created=1735000000, owned_by="deepseek"),
    ModelInfo(id="deepseek-chat", created=1700000000, owned_by="deepseek"),
    # 阿里 — 最强: Qwen3 Max / 推理: QwQ Plus / 对话: Qwen Turbo
    ModelInfo(id="qwen3-max-2026-01-23", created=1758000000, owned_by="alibaba"),
    ModelInfo(id="qwq-plus", created=1755000000, owned_by="alibaba"),
    ModelInfo(id="qwen-turbo", created=1700000000, owned_by="alibaba"),
    # 智谱 — 最强: GLM-5 / 对话: GLM-4 Flash
    ModelInfo(id="glm-5", created=1759000000, owned_by="zhipu"),
    ModelInfo(id="glm-4-flash", created=1700000000, owned_by="zhipu"),
    # Kimi — 最强: K2.7 / 对话: K2.6
    ModelInfo(id="kimi-k2.7-code", created=1759000000, owned_by="moonshot"),
    ModelInfo(id="kimi-k2.6", created=1755000000, owned_by="moonshot"),
    # Grok
    ModelInfo(id="grok-4.3", created=1759000000, owned_by="xai"),
]


@router.get("/models", summary="获取模型列表", description="返回所有可用的大模型及其信息。")
async def list_models():
    return ModelList(data=AVAILABLE_MODELS)
