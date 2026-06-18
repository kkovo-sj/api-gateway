"""
计费逻辑：计算每笔请求的成本、收入、利润
"""
from database import get_db
from services.router import get_pricing


def calculate_billing(model: str, prompt_tokens: int, completion_tokens: int) -> dict:
    """计算一笔请求的 cost / revenue / profit（单位：cents，1 cent = $0.01）"""
    pricing = get_pricing(model)
    if not pricing:
        pricing = {
            "input_price_per_1k": 0.005,
            "output_price_per_1k": 0.015,
            "input_cost_per_1k": 0.001,
            "output_cost_per_1k": 0.005,
        }

    revenue = (
        prompt_tokens * pricing["input_price_per_1k"] / 1000
        + completion_tokens * pricing["output_price_per_1k"] / 1000
    )
    cost = (
        prompt_tokens * pricing["input_cost_per_1k"] / 1000
        + completion_tokens * pricing["output_cost_per_1k"] / 1000
    )

    # 转成 cents（整数）
    revenue_cents = round(revenue * 100)
    cost_cents = round(cost * 100)

    return {
        "revenue_cents": revenue_cents,
        "cost_cents": cost_cents,
        "profit_cents": revenue_cents - cost_cents,
        "pricing_used": pricing["model_pattern"],
    }


def charge_customer(customer_id: int, api_key_id: int, model: str,
                    prompt_tokens: int, completion_tokens: int) -> dict:
    """从客户余额中扣费，记录用量"""
    billing = calculate_billing(model, prompt_tokens, completion_tokens)

    db = get_db()
    try:
        # 扣余额
        db.execute(
            "UPDATE customers SET balance_cents = balance_cents - ? WHERE id = ?",
            (billing["revenue_cents"], customer_id),
        )

        # 记录用量
        db.execute(
            """INSERT INTO usage_records
               (customer_id, api_key_id, model, prompt_tokens, completion_tokens,
                revenue_cents, cost_cents)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (customer_id, api_key_id, model, prompt_tokens, completion_tokens,
             billing["revenue_cents"], billing["cost_cents"]),
        )
        db.commit()
    finally:
        db.close()

    return billing


def get_profit_summary() -> dict:
    """获取利润汇总"""
    db = get_db()
    try:
        total = db.execute(
            """SELECT
                 COUNT(*) as total_requests,
                 COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                 COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                 COALESCE(SUM(revenue_cents), 0) as total_revenue,
                 COALESCE(SUM(cost_cents), 0) as total_cost,
                 COALESCE(SUM(revenue_cents - cost_cents), 0) as total_profit
               FROM usage_records"""
        ).fetchone()

        by_customer = db.execute(
            """SELECT c.name, c.balance_cents,
                      COALESCE(SUM(u.revenue_cents), 0) as revenue,
                      COALESCE(SUM(u.revenue_cents - u.cost_cents), 0) as profit
               FROM customers c
               LEFT JOIN usage_records u ON u.customer_id = c.id
               GROUP BY c.id"""
        ).fetchall()

        by_model = db.execute(
            """SELECT model,
                      COUNT(*) as requests,
                      COALESCE(SUM(revenue_cents), 0) as revenue,
                      COALESCE(SUM(revenue_cents - cost_cents), 0) as profit
               FROM usage_records
               GROUP BY model
               ORDER BY revenue DESC"""
        ).fetchall()

        return {
            "total": dict(total),
            "by_customer": [dict(r) for r in by_customer],
            "by_model": [dict(r) for r in by_model],
        }
    finally:
        db.close()
