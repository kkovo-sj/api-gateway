"""
Anthropic Claude Provider —— 请求/响应格式转换
"""
from __future__ import annotations
import json
import time
import uuid
import httpx
from typing import AsyncIterator

from config import settings
from models.request import ChatCompletionRequest, Message
from providers.base import BaseProvider


class AnthropicProvider(BaseProvider):
    def __init__(self):
        self.api_key = settings.anthropic_api_key
        self.base_url = "https://api.anthropic.com"
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        self.anthropic_version = "2023-06-01"

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "Content-Type": "application/json",
        }

    def _convert_messages(self, messages: list[Message]) -> tuple[list[dict], str | None]:
        """把 OpenAI messages 转成 Anthropic messages。
        返回 (anthropic_messages, system_prompt)
        """
        system_prompt = None
        anthropic_msgs = []

        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            else:
                role = "assistant" if msg.role == "assistant" else "user"
                anthropic_msgs.append({"role": role, "content": msg.content or ""})

        return anthropic_msgs, system_prompt

    async def chat_completion(self, request: ChatCompletionRequest) -> dict:
        """非流式请求，返回原始 dict 以便提取 usage"""
        messages, system = self._convert_messages(request.messages)

        body = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
            "stream": False,
        }
        if system:
            body["system"] = system
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p

        resp = await self.http.post(
            f"{self.base_url}/v1/messages",
            headers=self._headers(),
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic error {resp.status_code}: {resp.text}")

        raw = resp.json()
        return self._to_openai_format(raw, request.model)

    def _to_openai_format(self, raw: dict, model: str) -> dict:
        """Anthropic 响应 → OpenAI 格式字典"""
        content = ""
        for block in raw.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        usage = raw.get("usage", {})
        return {
            "id": raw.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": raw.get("stop_reason", "stop"),
            }],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
        }

    async def chat_completion_stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """流式：Anthropic SSE → OpenAI SSE 格式"""
        messages, system = self._convert_messages(request.messages)

        body = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
            "stream": True,
        }
        if system:
            body["system"] = system
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p

        async with self.http.stream(
            "POST",
            f"{self.base_url}/v1/messages",
            headers=self._headers(),
            json=body,
        ) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                raise RuntimeError(f"Anthropic error {resp.status_code}: {text}")

            msg_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            created = int(time.time())

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data_str = line[6:]
                if not data_str.strip():
                    continue

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "content_block_delta":
                    text = event.get("delta", {}).get("text", "")
                    delta = {"role": "assistant", "content": text}
                    chunk = {
                        "id": msg_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": request.model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"

                elif event_type == "message_stop":
                    chunk = {
                        "id": msg_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": request.model,
                        "choices": [{"index": 0, "delta": {},
                                     "finish_reason": "stop"}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                    yield "data: [DONE]\n\n"

    async def close(self):
        await self.http.aclose()
