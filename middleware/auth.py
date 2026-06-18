"""
API Key 鉴权中间件 —— 验证客户 API Key
"""
from fastapi import Request, HTTPException
from database import get_db


async def authenticate(request: Request) -> dict:
    """从 Authorization header 提取 API Key，验证并返回客户信息。
    如果失败则抛出 401。
    路由使用 `customer = await authenticate(request)` 调用。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    api_key = auth[7:].strip()
    if not api_key:
        raise HTTPException(status_code=401, detail="Empty API key")

    db = get_db()
    try:
        row = db.execute(
            """SELECT ak.id as key_id, ak.customer_id, ak.is_active,
                      c.name, c.balance_cents
               FROM api_keys ak
               JOIN customers c ON c.id = ak.customer_id
               WHERE ak.key = ?""",
            (api_key,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=401, detail="Invalid API key")

        if not row["is_active"]:
            raise HTTPException(status_code=403, detail="API key has been disabled")

        return {
            "key_id": row["key_id"],
            "customer_id": row["customer_id"],
            "customer_name": row["name"],
            "balance_cents": row["balance_cents"],
        }
    finally:
        db.close()
