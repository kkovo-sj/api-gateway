"""
模型路由：根据请求的 model name 匹配到对应的 provider
"""
from __future__ import annotations
from database import get_db


# 模型前缀 → provider 名称映射
MODEL_PREFIX_MAP = [
    # 走 88API 中转
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("claude-", "openai"),
    ("grok-", "openai"),
    ("qwen-turbo-2025", "openai"),
    ("qwq-plus-2025", "openai"),
    ("qwen3-max-2026", "openai"),
    ("deepseek-r1", "openai"),
    ("kimi-k2", "openai"),
    # 国产直连
    ("deepseek-v4", "deepseek"),
    ("deepseek-chat", "deepseek"),
    ("deepseek-reasoner", "deepseek"),
    ("qwen-turbo", "qwen"),
    ("qwen-plus", "qwen"),
    ("qwq", "openai"),
    ("qwq-32b", "qwen"),
    ("qwen3-max", "qwen"),
    ("glm", "zhipu"),
    ("moonshot", "moonshot"),
    ("kimi", "moonshot"),
]


def get_provider_name(model: str) -> str:
    """根据 model name 获取 provider 名称"""
    for prefix, name in MODEL_PREFIX_MAP:
        if model.startswith(prefix):
            return name
    return "deepseek"  # 默认走 DeepSeek


def get_pricing(model: str) -> dict | None:
    """查找 model 对应的定价。最长前缀匹配。"""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM pricing ORDER BY LENGTH(model_pattern) DESC"
        ).fetchall()

        for row in rows:
            pattern = row["model_pattern"]
            if pattern == "default":
                continue
            if model.startswith(pattern):
                return dict(row)

        # fallback to default
        default = db.execute(
            "SELECT * FROM pricing WHERE model_pattern='default'"
        ).fetchone()
        return dict(default) if default else None
    finally:
        db.close()
