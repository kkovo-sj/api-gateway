"""
Provider 抽象基类
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator
from models.request import ChatCompletionRequest
from models.response import ChatCompletionResponse


class BaseProvider(ABC):
    @abstractmethod
    async def chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """非流式请求"""
        ...

    @abstractmethod
    async def chat_completion_stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """流式请求，yield 每一行 SSE 数据"""
        ...

    @staticmethod
    def make_error_response(message: str, code: str = "internal_error") -> ChatCompletionResponse:
        return ChatCompletionResponse(
            id="error",
            created=0,
            model="unknown",
            choices=[],
        )
