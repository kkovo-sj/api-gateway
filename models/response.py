"""
OpenAI 兼容的响应模型
"""
from pydantic import BaseModel
from typing import Optional, Literal


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChoiceDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None


class Choice(BaseModel):
    index: int
    message: Optional[dict] = None
    delta: Optional[ChoiceDelta] = None
    finish_reason: Optional[Literal["stop", "length", "content_filter", "tool_calls"]] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Optional[Usage] = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str


class ModelList(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


class ErrorResponse(BaseModel):
    error: dict
