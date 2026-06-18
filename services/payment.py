"""
xorpay 支付服务
"""
import hashlib
import httpx
from config import settings


def _sign(*args) -> str:
    """MD5 签名：所有参数值拼接 + app_secret"""
    raw = "".join(str(a) for a in args) + settings.xorpay_app_secret
    return hashlib.md5(raw.encode()).hexdigest()


async def create_payment(order_id: str, amount: float, pay_type: str, name: str = "API额度充值") -> dict:
    """调用 xorpay 统一下单接口"""
    if not settings.xorpay_aid or not settings.xorpay_app_secret:
        return {"ok": False, "error": "支付未配置"}

    price = f"{amount:.2f}"
    sign = _sign(name, pay_type, price, order_id, settings.xorpay_notify_url)

    body = {
        "name": name,
        "pay_type": pay_type,
        "price": price,
        "order_id": order_id,
        "notify_url": settings.xorpay_notify_url,
        "sign": sign,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"https://xorpay.com/api/pay/{settings.xorpay_aid}",
            data=body,
        )
        data = resp.json()

    if data.get("status") == "ok":
        return {
            "ok": True,
            "qr_url": data.get("qr_url", ""),       # 二维码内容
            "pay_url": data.get("pay_url", ""),     # 支付链接
            "aoid": data.get("aoid", ""),           # xorpay 订单号
        }
    else:
        return {"ok": False, "error": data.get("status", "unknown")}


def verify_callback(aoid: str, order_id: str, pay_price: str, pay_time: str, sign: str) -> bool:
    """验证 xorpay 回调签名"""
    expected = _sign(aoid, order_id, pay_price, pay_time)
    return sign == expected
