"""
LLM API Gateway —— 主入口
"""
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from middleware.rate_limit import rate_limit

from config import settings
from database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="AI 大模型 API 中转站",
    description="""
## 一套接口，调用所有国产大模型

支持模型：**DeepSeek** · **通义千问** · **智谱 GLM** · **Kimi**

### 接入方式

跟 OpenAI 官方接口 100% 兼容，改一行 `base_url` 即可：

```python
from openai import OpenAI
client = OpenAI(
    api_key="sk-你的API Key",
    base_url="http://你的地址:8000/v1"
)
```

### 鉴权

所有接口需要在请求头中携带 API Key：
```
Authorization: Bearer sk-你的API Key
```

### 流式响应

设置 `stream: true` 即可获得 SSE 实时推流。
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# 自定义 OpenAPI schema 汉化
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="AI 大模型 API 中转站",
        version="1.0.0",
        description="统一调用 DeepSeek、通义千问、智谱GLM、Kimi 的 API 网关",
        routes=app.routes,
    )
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

# 安全中间件
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # 速率限制
    limit_response = rate_limit(request)
    if limit_response:
        return limit_response
    response = await call_next(request)
    # 安全头
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# 静态文件（付款码图片等）
app.mount("/static", StaticFiles(directory="static"), name="static")

from routes.chat import router as chat_router
from routes.models import router as models_router
from routes.admin import router as admin_router
from routes.portal import router as portal_router
from routes.pay import router as pay_router
from routes.admin_ext import router as admin_ext_router

app.include_router(portal_router)
app.include_router(pay_router)
app.include_router(chat_router)
app.include_router(models_router)
app.include_router(admin_router)
app.include_router(admin_ext_router)


@app.get("/health", summary="健康检查", tags=["系统"])
async def health():
    """检查服务是否正常运行"""
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
