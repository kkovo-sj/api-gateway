"""
OpenAI 兼容的请求模型
"""
from pydantic import BaseModel
from typing import Optional, Literal


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "function", "tool"]
    content: Optional[str] = None
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stream: Optional[bool] = False
    stop: Optional[list[str]] = None
    # 扩展字段
    user: Optional[str] = None
