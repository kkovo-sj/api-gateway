"""
Google Gemini Provider —— 请求/响应格式转换
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


class GeminiProvider(BaseProvider):
    def __init__(self):
        self.api_key = settings.gemini_api_key
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    def _convert_messages(self, messages: list[Message]) -> tuple[list[dict], str | None]:
        """OpenAI messages → Gemini contents"""
        system_instruction = None
        contents = []

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            else:
                role = "model" if msg.role == "assistant" else "user"
                contents.append({
                    "role": role,
                    "parts": [{"text": msg.content or ""}],
                })

        return contents, system_instruction

    def _build_url(self, model: str) -> str:
        return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    async def chat_completion(self, request: ChatCompletionRequest) -> dict:
        """非流式请求"""
        contents, system_instruction = self._convert_messages(request.messages)

        body = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens or 4096,
                "temperature": request.temperature or 0.7,
            },
        }
        if system_instruction:
            body["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }
        if request.top_p is not None:
            body["generationConfig"]["topP"] = request.top_p

        url = self._build_url(request.model)
        resp = await self.http.post(
            url,
            params={"key": self.api_key},
            headers={"Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini error {resp.status_code}: {resp.text}")

        raw = resp.json()
        return self._to_openai_format(raw, request.model)

    def _to_openai_format(self, raw: dict, model: str) -> dict:
        """Gemini 响应 → OpenAI 格式字典"""
        candidates = raw.get("candidates", [])
        text = ""
        finish_reason = "stop"

        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            finish = candidates[0].get("finishReason", "STOP")
            if finish == "MAX_TOKENS":
                finish_reason = "length"

        usage = raw.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    async def chat_completion_stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """流式：Gemini SSE → OpenAI SSE 格式"""
        contents, system_instruction = self._convert_messages(request.messages)

        body = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens or 4096,
                "temperature": request.temperature or 0.7,
            },
        }
        if system_instruction:
            body["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }
        if request.top_p is not None:
            body["generationConfig"]["topP"] = request.top_p

        url = f"{self._build_url(request.model)}?alt=sse"
        async with self.http.stream(
            "POST",
            url,
            params={"key": self.api_key},
            headers={"Content-Type": "application/json"},
            json=body,
        ) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                raise RuntimeError(f"Gemini error {resp.status_code}: {text}")

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

                candidates = event.get("candidates", [])
                if not candidates:
                    continue

                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                text = "".join(p.get("text", "") for p in parts)

                finish_reason = None
                finish = candidates[0].get("finishReason")
                if finish:
                    if finish == "MAX_TOKENS":
                        finish_reason = "length"
                    else:
                        finish_reason = "stop"

                delta = {}
                if text:
                    delta = {"role": "assistant", "content": text}

                chunk = {
                    "id": msg_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": request.model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"

                if finish_reason:
                    yield "data: [DONE]\n\n"

    async def close(self):
        await self.http.aclose()
