"""
支付回调接口 —— xorpay webhook
"""
from fastapi import APIRouter, Request
from database import get_db
from services.payment import verify_callback

router = APIRouter(tags=["支付"])


@router.post("/pay/callback")
async def pay_callback(request: Request):
    """xorpay 支付成功回调 —— 自动加余额"""
    form = await request.form()
    data = dict(form)

    aoid = data.get("aoid", "")
    order_id = data.get("order_id", "")
    pay_price = data.get("pay_price", "")
    pay_time = data.get("pay_time", "")
    sign = data.get("sign", "")

    # 验证签名
    if not verify_callback(aoid, order_id, pay_price, pay_time, sign):
        return "sign error"

    # 解析金额（分）
    amount_cents = int(float(pay_price) * 100)

    db = get_db()
    try:
        # 查找订单
        order = db.execute(
            "SELECT * FROM topup_orders WHERE id = ?",
            (int(order_id),),
        ).fetchone()

        if not order:
            return "order not found"

        if order["status"] == "confirmed":
            return "success"  # 已处理，幂等

        # 加余额
        db.execute(
            "UPDATE customers SET balance_cents = balance_cents + ? WHERE id = ?",
            (amount_cents, order["customer_id"]),
        )
        # 更新订单
        db.execute(
            "UPDATE topup_orders SET status='confirmed', payment_ref=?, confirmed_at=CURRENT_TIMESTAMP WHERE id=?",
            (aoid, int(order_id)),
        )
        db.commit()
    finally:
        db.close()

    return "success"
