"""
通用 OpenAI 兼容 Provider —— 国内模型（DeepSeek、通义千问、智谱GLM、月之暗面等）
"""
import json
import time
import httpx
from typing import AsyncIterator

from config import settings
from models.request import ChatCompletionRequest
from providers.base import BaseProvider


class OpenAICompatProvider(BaseProvider):
    """通用 OpenAI 兼容接口适配器
    适用于所有实现了 OpenAI /v1/chat/completions 格式的国内模型
    """

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat_completion(self, request: ChatCompletionRequest) -> dict:
        body = request.model_dump(exclude_none=True)
        body["stream"] = False

        resp = await self.http.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Provider error {resp.status_code}: {resp.text}")

        return resp.json()

    async def chat_completion_stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
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
                raise RuntimeError(f"Provider error {resp.status_code}: {text}")

            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield line + "\n"

    async def close(self):
        await self.http.aclose()
