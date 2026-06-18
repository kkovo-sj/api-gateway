"""
简易内存速率限制 —— 防暴力破解和滥用
"""
from __future__ import annotations
import time
from collections import defaultdict
from fastapi import Request
from fastapi.responses import JSONResponse

RATE_LIMIT = 60
WINDOW = 60

STRICT_LIMITS = {
    "/admin/": 10,
    "/portal/api/login": 20,
}

_requests = defaultdict(list)


def rate_limit(request: Request) -> JSONResponse | None:
    """超出限制返回 429，否则返回 None"""
    ip = request.client.host if request.client else "unknown"
    now = time.time()

    _requests[ip] = [t for t in _requests[ip] if now - t < WINDOW]

    limit = RATE_LIMIT
    for path, l in STRICT_LIMITS.items():
        if request.url.path.startswith(path):
            limit = l
            break

    if len(_requests[ip]) >= limit:
        return JSONResponse(
            status_code=429,
            content={"error": {"message": "请求太频繁，请稍后重试", "type": "rate_limit"}},
        )

    _requests[ip].append(now)
    return None
