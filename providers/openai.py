"""
OpenAI Provider —— 透传为主，是最简单的适配器
"""
import json
import time
import httpx
from typing import AsyncIterator

from config import settings
from models.request import ChatCompletionRequest
from models.response import ChatCompletionResponse
from providers.base import BaseProvider


class OpenAIProvider(BaseProvider):
    def __init__(self):
        self.api_key = settings.openai_api_key
        self.base_url = "https://api.openai.com/v1"
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat_completion(self, request: ChatCompletionRequest) -> dict:
        """非流式请求，返回原始 dict 以便提取 usage"""
        body = request.model_dump(exclude_none=True)
        body["stream"] = False

        resp = await self.http.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text}")

        return resp.json()

    async def chat_completion_stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """流式请求 —— OpenAI 格式直接透传"""
        body = request.model_dump(exclude_none=True)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}

        async with self.http.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
        ) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                raise RuntimeError(f"OpenAI error {resp.status_code}: {text}")

            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield line + "\n"

    async def close(self):
        await self.http.aclose()
