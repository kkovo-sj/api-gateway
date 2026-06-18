"""
首页 + 客户自助门户
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from database import get_db

router = APIRouter(tags=["portal"])


@router.get("/", response_class=HTMLResponse)
async def landing():
    return LANDING_HTML


@router.get("/portal", response_class=HTMLResponse)
async def portal():
    return PORTAL_HTML


@router.post("/portal/api/login")
async def portal_login(request: Request):
    """客户用 API Key 登录查看自己的信息"""
    body = await request.json()
    api_key = body.get("api_key", "").strip()
    if not api_key:
        return {"ok": False, "error": "请输入 API Key"}

    db = get_db()
    try:
        row = db.execute(
            """SELECT ak.id as key_id, ak.customer_id, ak.key, ak.is_active, ak.created_at,
                      c.name, c.balance_cents, c.created_at as customer_since
               FROM api_keys ak
               JOIN customers c ON c.id = ak.customer_id
               WHERE ak.key = ?""",
            (api_key,),
        ).fetchone()

        if not row:
            return {"ok": False, "error": "API Key 无效"}

        if not row["is_active"]:
            return {"ok": False, "error": "该 API Key 已被禁用"}

        # 查用量
        usage_rows = db.execute(
            """SELECT model, COUNT(*) as requests,
                      SUM(prompt_tokens) as total_prompt,
                      SUM(completion_tokens) as total_completion,
                      SUM(revenue_cents) as total_cost,
                      MAX(created_at) as last_used
               FROM usage_records
               WHERE customer_id = ?
               GROUP BY model
               ORDER BY total_cost DESC""",
            (row["customer_id"],),
        ).fetchall()

        # 最近 20 条记录
        recent = db.execute(
            """SELECT model, prompt_tokens, completion_tokens, revenue_cents, created_at
               FROM usage_records
               WHERE customer_id = ?
               ORDER BY created_at DESC
               LIMIT 20""",
            (row["customer_id"],),
        ).fetchall()

        # 总用量
        total_usage = db.execute(
            """SELECT COUNT(*) as total_requests,
                      COALESCE(SUM(prompt_tokens), 0) as total_prompt,
                      COALESCE(SUM(completion_tokens), 0) as total_completion,
                      COALESCE(SUM(revenue_cents), 0) as total_cost
               FROM usage_records
               WHERE customer_id = ?""",
            (row["customer_id"],),
        ).fetchone()

        return {
            "ok": True,
            "customer": {
                "name": row["name"],
                "balance_cents": row["balance_cents"],
                "customer_since": row["customer_since"],
            },
            "api_key": {
                "key": row["key"],
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
            },
            "total_usage": dict(total_usage),
            "by_model": [dict(r) for r in usage_rows],
            "recent": [dict(r) for r in recent],
        }
    finally:
        db.close()


@router.post("/portal/api/topup/create")
async def create_topup(request: Request):
    """客户创建充值订单"""
    body = await request.json()
    api_key = body.get("api_key", "").strip()
    amount_cents = body.get("amount_cents", 0)
    payment_method = body.get("payment_method", "")

    if not api_key or amount_cents <= 0:
        return {"ok": False, "error": "参数错误"}

    db = get_db()
    try:
        row = db.execute(
            "SELECT ak.customer_id FROM api_keys ak WHERE ak.key = ? AND ak.is_active = 1",
            (api_key,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "API Key 无效"}

        cursor = db.execute(
            "INSERT INTO topup_orders (customer_id, amount_cents, payment_method) VALUES (?, ?, ?)",
            (row["customer_id"], amount_cents, payment_method),
        )
        db.commit()
        return {"ok": True, "order_id": cursor.lastrowid, "amount_cents": amount_cents}
    finally:
        db.close()


@router.post("/portal/api/topup/confirm")
async def confirm_topup(request: Request):
    """客户提交付款凭证"""
    body = await request.json()
    api_key = body.get("api_key", "").strip()
    order_id = body.get("order_id", 0)
    payment_ref = body.get("payment_ref", "")

    db = get_db()
    try:
        row = db.execute(
            "SELECT ak.customer_id FROM api_keys ak WHERE ak.key = ? AND ak.is_active = 1",
            (api_key,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "API Key 无效"}

        db.execute(
            "UPDATE topup_orders SET status='submitted', payment_ref=? WHERE id=? AND customer_id=?",
            (payment_ref, order_id, row["customer_id"]),
        )
        db.commit()
        return {"ok": True, "message": "已提交，等待确认"}
    finally:
        db.close()


@router.post("/portal/api/topup/pay")
async def pay_topup_order(request: Request):
    """调用 xorpay 创建支付"""
    body = await request.json()
    api_key = body.get("api_key", "").strip()
    order_id = body.get("order_id", 0)
    pay_type = body.get("pay_type", "native")

    db = get_db()
    try:
        row = db.execute(
            "SELECT ak.customer_id, o.amount_cents, o.status FROM api_keys ak JOIN topup_orders o ON o.customer_id = ak.customer_id WHERE ak.key = ? AND o.id = ?",
            (api_key, order_id),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "订单不存在"}
        if row["status"] != "pending":
            return {"ok": False, "error": "订单状态异常"}

        from services.payment import create_payment
        result = await create_payment(
            order_id=str(order_id),
            amount=row["amount_cents"] / 100,
            pay_type=pay_type,
        )

        if result.get("ok"):
            db.execute(
                "UPDATE topup_orders SET payment_ref=? WHERE id=?",
                (result.get("aoid", ""), order_id),
            )
            db.commit()

        return result
    finally:
        db.close()


@router.get("/portal/api/topup/orders")
async def get_topup_orders(api_key: str = ""):
    """客户查看自己的充值订单"""
    if not api_key:
        return {"ok": False, "error": "缺少 API Key"}

    db = get_db()
    try:
        row = db.execute(
            "SELECT customer_id FROM api_keys WHERE key = ? AND is_active = 1",
            (api_key,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "API Key 无效"}

        orders = db.execute(
            "SELECT * FROM topup_orders WHERE customer_id = ? ORDER BY created_at DESC LIMIT 20",
            (row["customer_id"],),
        ).fetchall()
        return {"ok": True, "orders": [dict(o) for o in orders]}
    finally:
        db.close()


LANDING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>API 中转站</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; color: #111; background: #fafafa; line-height: 1.5; -webkit-font-smoothing: antialiased; }
.nav { position: fixed; top: 0; left: 0; right: 0; z-index: 100; background: rgba(255,255,255,0.8); backdrop-filter: blur(20px); border-bottom: 1px solid #eee; padding: 0 32px; height: 56px; display: flex; align-items: center; justify-content: space-between; font-size: 0.85em; }
.nav .logo { font-weight: 700; }
.nav a { color: #666; text-decoration: none; margin-left: 24px; }
.nav a:hover { color: #111; }
.hero { padding: 160px 32px 100px; text-align: center; max-width: 720px; margin: 0 auto; }
.hero h1 { font-size: 3em; font-weight: 800; letter-spacing: -1.5px; line-height: 1.1; margin-bottom: 20px; }
.hero p { font-size: 1.1em; color: #666; line-height: 1.7; margin-bottom: 40px; }
.btn { display: inline-block; padding: 12px 32px; border-radius: 100px; font-size: 0.9em; font-weight: 600; text-decoration: none; transition: all .2s; border: none; }
.btn-dark { background: #111; color: #fff; }
.btn-dark:hover { background: #333; }
.btn-ghost { background: transparent; color: #111; border: 1.5px solid #ddd; }
.btn-ghost:hover { border-color: #111; }
.btn-group { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
.container { max-width: 1040px; margin: 0 auto; padding: 0 32px; }
.section { padding: 80px 0; }
.section-label { font-size: 0.75em; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; color: #999; margin-bottom: 12px; }
.section h2 { font-size: 2em; font-weight: 700; letter-spacing: -1px; margin-bottom: 48px; }
.features { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: #e5e5e5; border: 1px solid #e5e5e5; border-radius: 8px; overflow: hidden; }
.feature-card { background: #fff; padding: 40px 32px; }
.feature-card .num { font-size: 0.7em; color: #ccc; margin-bottom: 16px; }
.feature-card h3 { font-size: 1em; font-weight: 600; margin-bottom: 6px; }
.feature-card p { font-size: 0.85em; color: #888; line-height: 1.6; }
.pricing-table { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: #e5e5e5; border: 1px solid #e5e5e5; border-radius: 8px; overflow: hidden; }
.pricing-card { background: #fff; padding: 40px 28px; text-align: center; }
.pricing-card h3 { font-size: 1em; font-weight: 600; margin-bottom: 4px; }
.pricing-card .sub { font-size: 0.8em; color: #999; margin-bottom: 20px; }
.pricing-card .price { font-size: 2.2em; font-weight: 800; letter-spacing: -1px; }
.pricing-card .price span { font-size: 0.4em; color: #999; font-weight: 400; }
.pricing-card .specs { margin-top: 20px; font-size: 0.8em; color: #888; line-height: 2; }
.code-block { background: #111; color: #e5e5e5; padding: 32px 36px; border-radius: 8px; overflow-x: auto; font-size: 0.85em; line-height: 1.8; font-family: 'SF Mono', monospace; max-width: 680px; margin: 0 auto; }
.code-block .c { color: #666; }
.footer { border-top: 1px solid #eee; padding: 40px 32px; text-align: center; font-size: 0.8em; color: #999; }
@media (max-width: 768px) {
  .hero h1 { font-size: 2em; }
  .features { grid-template-columns: 1fr; }
  .pricing-table { grid-template-columns: 1fr 1fr; }
}
</style>
</head>
<body>

<nav class="nav">
  <span class="logo">API 中转站</span>
  <span>
    <a href="/docs">接口文档</a>
    <a href="/portal">客户门户</a>
  </span>
</nav>

<section class="hero">
  <h1>一个 Key，调用所有国产大模型</h1>
  <p>DeepSeek &middot; GPT-4o &middot; Claude &middot; 千问 &middot; GLM &middot; Kimi<br>国内外大模型一站接入 · OpenAI 兼容 · 按量计费</p>
  <div class="btn-group">
    <a href="/portal" class="btn btn-dark">进入门户</a>
    <a href="/docs" class="btn btn-ghost">接口文档</a>
  </div>
</section>

<div class="container">
  <section class="section">
    <div class="section-label">优势</div>
    <h2>为什么选我们</h2>
    <div class="features">
      <div class="feature-card"><div class="num">01</div><h3>一键接入</h3><p>OpenAI 兼容格式，改一行 base_url 即可</p></div>
      <div class="feature-card"><div class="num">02</div><h3>四家聚合</h3><p>一个 Key 调用 DeepSeek·千问·GLM·Kimi</p></div>
      <div class="feature-card"><div class="num">03</div><h3>流式输出</h3><p>完整 SSE 实时推流</p></div>
      <div class="feature-card"><div class="num">04</div><h3>按量扣费</h3><p>不用不花钱，随时查余额</p></div>
      <div class="feature-card"><div class="num">05</div><h3>消费透明</h3><p>每笔调用都有明细记录</p></div>
      <div class="feature-card"><div class="num">06</div><h3>价格实惠</h3><p>低于主流中转站均价</p></div>
    </div>
  </section>

  <section class="section">
    <div class="section-label">定价</div>
    <h2>每家三级 · 按需选择</h2>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:0.9em">
      <thead><tr style="border-bottom:2px solid #111">
        <th style="padding:12px;text-align:left">厂商</th>
        <th style="padding:12px;text-align:left">🔥 最强</th>
        <th style="padding:12px;text-align:left">🧠 推理</th>
        <th style="padding:12px;text-align:left">💬 对话</th>
      </tr></thead>
      <tbody>
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:12px"><strong>OpenAI</strong></td>
          <td style="padding:12px">GPT-5.5<br><span style="font-size:0.8em;color:#999">¥30/1M</span></td>
          <td style="padding:12px">o3-mini<br><span style="font-size:0.8em;color:#999">¥6/1M</span></td>
          <td style="padding:12px">GPT-4o-mini<br><span style="font-size:0.8em;color:#999">¥3/1M</span></td>
        </tr>
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:12px"><strong>Anthropic</strong></td>
          <td style="padding:12px">Claude Opus 4.8<br><span style="font-size:0.8em;color:#999">¥140/1M</span></td>
          <td style="padding:12px">Claude Sonnet 4.6<br><span style="font-size:0.8em;color:#999">¥85/1M</span></td>
          <td style="padding:12px">Claude Haiku 4.5<br><span style="font-size:0.8em;color:#999">¥30/1M</span></td>
        </tr>
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:12px"><strong>DeepSeek</strong></td>
          <td style="padding:12px">V4 Pro<br><span style="font-size:0.8em;color:#999">¥15/1M</span></td>
          <td style="padding:12px">R1<br><span style="font-size:0.8em;color:#999">¥8/1M</span></td>
          <td style="padding:12px">V3<br><span style="font-size:0.8em;color:#999">¥5/1M</span></td>
        </tr>
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:12px"><strong>阿里</strong></td>
          <td style="padding:12px">Qwen3-Max<br><span style="font-size:0.8em;color:#999">¥15/1M</span></td>
          <td style="padding:12px">QwQ Plus<br><span style="font-size:0.8em;color:#999">¥6/1M</span></td>
          <td style="padding:12px">Qwen Turbo<br><span style="font-size:0.8em;color:#999">¥2/1M</span></td>
        </tr>
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:12px"><strong>智谱</strong></td>
          <td style="padding:12px">GLM-5<br><span style="font-size:0.8em;color:#999">¥10/1M</span></td>
          <td style="padding:12px">—</td>
          <td style="padding:12px">GLM-4-Flash<br><span style="font-size:0.8em;color:#999">¥0.5/1M</span></td>
        </tr>
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:12px"><strong>Kimi</strong></td>
          <td style="padding:12px">K2.7 Code<br><span style="font-size:0.8em;color:#999">¥18/1M</span></td>
          <td style="padding:12px">—</td>
          <td style="padding:12px">K2.6<br><span style="font-size:0.8em;color:#999">¥12/1M</span></td>
        </tr>
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:12px"><strong>Grok</strong></td>
          <td style="padding:12px">Grok 4.3<br><span style="font-size:0.8em;color:#999">¥4/1M</span></td>
          <td style="padding:12px">—</td>
          <td style="padding:12px">—</td>
        </tr>
    </table>
    </div>
  </section>

  <section class="section">
    <div class="section-label">接入</div>
    <h2>三行代码开始</h2>
    <div class="code-block">
<span class="c"># pip install openai</span><br>
client = OpenAI(<br>
&nbsp;&nbsp;api_key=<span style="color:#fff">"sk-你的Key"</span>,<br>
&nbsp;&nbsp;base_url=<span style="color:#fff">"http://你的地址:8000/v1"</span><br>
)<br>
res = client.chat.completions.create(<br>
&nbsp;&nbsp;model=<span style="color:#fff">"deepseek-chat"</span>,<br>
&nbsp;&nbsp;messages=[{<span style="color:#fff">"role":"user","content":"你好"</span>}]<br>
)
    </div>
  </section>
</div>

<footer class="footer"><p>API 中转站 &copy; 2026</p></footer>
</body>
</html>"""


PORTAL_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>客户门户</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Inter', -apple-system, sans-serif; background: #fafafa; color: #111; -webkit-font-smoothing: antialiased; }
.nav { position: fixed; top: 0; left: 0; right: 0; z-index: 100; background: rgba(255,255,255,0.8); backdrop-filter: blur(20px); border-bottom: 1px solid #eee; padding: 0 32px; height: 56px; display: flex; align-items: center; justify-content: space-between; font-size: 0.85em; }
.nav .logo { font-weight: 700; }
.nav a { color: #666; text-decoration: none; }
.nav a:hover { color: #111; }
.container { max-width: 880px; margin: 80px auto 40px; padding: 0 32px; }
.login-box { max-width: 400px; margin: 120px auto; text-align: center; }
.login-box h2 { font-size: 1.4em; font-weight: 700; margin-bottom: 6px; }
.login-box p { color: #999; font-size: 0.9em; margin-bottom: 24px; }
.login-box input { width: 100%; padding: 12px 16px; border: 1.5px solid #e5e5e5; border-radius: 8px; font-size: 0.9em; text-align: center; outline: none; }
.login-box input:focus { border-color: #111; }
.login-box button { width: 100%; padding: 12px; margin-top: 10px; border-radius: 8px; background: #111; color: #fff; border: none; font-size: 0.9em; font-weight: 600; cursor: pointer; }
.login-box button:hover { background: #333; }
.error { background: #f5f5f5; padding: 10px; border-radius: 6px; margin-top: 12px; display: none; font-size: 0.85em; }
.portal { display: none; }
.stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 1px; background: #e5e5e5; border: 1px solid #e5e5e5; border-radius: 8px; overflow: hidden; margin-bottom: 1px; }
.stat { background: #fff; padding: 24px 20px; text-align: center; }
.stat .label { font-size: 0.7em; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
.stat .value { font-size: 1.3em; font-weight: 700; letter-spacing: -0.5px; }
.section { background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; padding: 28px; margin-bottom: 1px; }
.section h3 { font-size: 0.8em; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #999; margin-bottom: 16px; }
table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid #f5f5f5; }
th { font-weight: 500; color: #999; font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.5px; }
.tag { display: inline-block; background: #f5f5f5; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; font-family: 'SF Mono', monospace; }
code { font-family: 'SF Mono', monospace; font-size: 0.85em; background: #f5f5f5; padding: 3px 8px; border-radius: 4px; word-break: break-all; }
.btn { padding: 8px 16px; border-radius: 6px; border: none; font-size: 0.85em; font-weight: 600; cursor: pointer; background: #111; color: #fff; }
.btn:hover { background: #333; }
.btn-sm { padding: 5px 12px; border-radius: 6px; border: 1px solid #e5e5e5; background: #fff; font-size: 0.8em; cursor: pointer; }
.btn-sm:hover { border-color: #111; }
.empty { text-align: center; color: #ccc; padding: 40px; }
.msg { padding: 10px 14px; border-radius: 6px; margin-top: 8px; font-size: 0.85em; display: none; }
.msg.ok { background: #f5f5f5; display: block; }
.msg.err { background: #f5f5f5; border: 1px solid #e5e5e5; display: block; }
.recharge-box { margin-top: 12px; padding: 20px; background: #f9f9f9; border-radius: 8px; display: none; }
.recharge-box h4 { margin-bottom: 12px; font-size: 0.95em; }
.amount-btns { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.amount-btn { padding: 10px 20px; border: 1.5px solid #e5e5e5; border-radius: 8px; background: #fff; cursor: pointer; font-size: 0.9em; transition: all .15s; }
.amount-btn:hover { border-color: #111; }
.amount-btn.sel { border-color: #111; background: #111; color: #fff; }
.qr-box { text-align: center; padding: 20px; }
.qr-box img { max-width: 200px; border-radius: 8px; }
.qr-box p { font-size: 0.85em; color: #888; margin-top: 8px; }
.pay-tabs { display: flex; gap: 1px; margin-bottom: 16px; }
.pay-tab { flex: 1; padding: 10px; text-align: center; border: 1.5px solid #e5e5e5; cursor: pointer; font-size: 0.85em; font-weight: 500; background: #fff; }
.pay-tab:first-child { border-radius: 6px 0 0 6px; }
.pay-tab:last-child { border-radius: 0 6px 6px 0; }
.pay-tab.sel { border-color: #111; background: #111; color: #fff; }
</style>
</head>
<body>

<nav class="nav">
  <span class="logo">客户门户</span>
  <a href="/">← 首页</a>
</nav>

<div class="container">

  <div class="login-box" id="loginBox">
    <h2>登录门户</h2>
    <p>输入你的 API Key 查看用量和余额</p>
    <input id="apiKeyInput" type="text" placeholder="sk-xxxxxxxx" />
    <button onclick="doLogin()">查询</button>
    <div class="error" id="loginError"></div>
  </div>

  <div class="portal" id="portalData">
    <div class="stats" id="overviewStats"></div>

    <div class="section">
      <h3>账户信息</h3>
      <div id="accountInfo"></div>
      <button class="btn" style="margin-top:12px" onclick="showRecharge()">充值</button>
    </div>

    <div class="section" id="rechargeSection" style="display:none">
      <h3>余额充值</h3>
      <div class="pay-tabs">
        <div class="pay-tab sel" onclick="selPay('wechat')" id="tabWechat">微信支付</div>
        <div class="pay-tab" onclick="selPay('alipay')" id="tabAlipay">支付宝</div>
      </div>
      <div class="amount-btns">
        <div class="amount-btn" onclick="selAmt(1000)">¥10</div>
        <div class="amount-btn sel" onclick="selAmt(5000)">¥50</div>
        <div class="amount-btn" onclick="selAmt(10000)">¥100</div>
        <div class="amount-btn" onclick="selAmt(20000)">¥200</div>
        <div class="amount-btn" onclick="selAmt(50000)">¥500</div>
      </div>
      <div class="qr-box" id="qrBox">
        <img id="qrImg" src="/static/wechat-qr.jpg" style="max-width:220px;border-radius:8px" onerror="this.onerror=null;this.style.display='none';document.getElementById('qrPlaceholder').style.display='block'" />
        <p id="qrPlaceholder" style="color:#999;display:none">收款码未上传，请将微信/支付宝收款码截图放到 static 文件夹<br>命名为 wechat-qr.jpg 和 alipay-qr.jpg</p>
        <p style="font-size:0.8em;color:#999;margin-top:6px">请使用 <strong id="payMethodLabel">微信</strong> 扫码支付 ¥<strong id="payAmountLabel">50</strong></p>
      </div>
      <input id="payRef" placeholder="付款后输入微信/支付宝交易单号" style="width:100%;padding:10px;border:1.5px solid #e5e5e5;border-radius:6px;margin:12px 0;font-size:0.9em" />
      <button class="btn" onclick="submitPay()">提交付款凭证</button>
      <div id="rechargeMsg" class="msg"></div>
    </div>

    <div class="section">
      <h3>我的密钥</h3>
      <div id="apiKeyInfo"></div>
    </div>

    <div class="section">
      <h3>用量统计（按模型）</h3>
      <table><thead><tr><th>模型</th><th>请求数</th><th>输入Token</th><th>输出Token</th><th>消费</th></tr></thead>
      <tbody id="modelUsage"></tbody></table>
    </div>

    <div class="section">
      <h3>最近调用</h3>
      <table><thead><tr><th>时间</th><th>模型</th><th>输入</th><th>输出</th><th>消费</th></tr></thead>
      <tbody id="recentRecords"></tbody></table>
    </div>
  </div>
</div>

<script>
let cd = null, selAmount = 5000, selPayMethod = 'wechat';

async function doLogin() {
  const k = document.getElementById('apiKeyInput').value.trim();
  const e = document.getElementById('loginError');
  if (!k) { e.textContent = '请输入 API Key'; e.style.display = 'block'; return; }
  try {
    const r = await fetch('/portal/api/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:k})});
    const d = await r.json();
    if (!d.ok) { e.textContent = d.error; e.style.display = 'block'; return; }
    cd = d; e.style.display = 'none';
    document.getElementById('loginBox').style.display = 'none';
    document.getElementById('portalData').style.display = 'block';
    render();
  } catch(x) { e.textContent = '网络错误'; e.style.display = 'block'; }
}

function render() {
  const c = cd.customer, tu = cd.total_usage;
  document.getElementById('overviewStats').innerHTML =
    `<div class="stat"><div class="label">余额</div><div class="value">¥${(c.balance_cents/100).toFixed(2)}</div></div>
     <div class="stat"><div class="label">请求数</div><div class="value">${tu.total_requests||0}</div></div>
     <div class="stat"><div class="label">输入Token</div><div class="value">${(tu.total_prompt||0).toLocaleString()}</div></div>
     <div class="stat"><div class="label">输出Token</div><div class="value">${(tu.total_completion||0).toLocaleString()}</div></div>
     <div class="stat"><div class="label">累计消费</div><div class="value">¥${((tu.total_cost||0)/100).toFixed(2)}</div></div>`;

  document.getElementById('accountInfo').innerHTML =
    `<p>名称: <strong>${c.name}</strong> &nbsp;|&nbsp; 余额: <strong>¥${(c.balance_cents/100).toFixed(2)}</strong> &nbsp;|&nbsp; 注册: ${c.customer_since}</p>`;

  const ak = cd.api_key;
  document.getElementById('apiKeyInfo').innerHTML =
    `<p><code>${ak.key}</code> <button class="btn-sm" onclick="navigator.clipboard.writeText('${ak.key}')">复制</button></p>
     <p style="font-size:0.82em;color:#999;margin-top:4px">状态: ${ak.is_active ? '正常' : '已禁用'} · 创建: ${ak.created_at}</p>`;

  const bm = cd.by_model;
  document.getElementById('modelUsage').innerHTML = bm.length === 0
    ? '<tr><td colspan="5" class="empty">暂无数据</td></tr>'
    : bm.map(m => `<tr><td><span class="tag">${m.model}</span></td><td>${m.requests}</td><td>${(m.total_prompt||0).toLocaleString()}</td><td>${(m.total_completion||0).toLocaleString()}</td><td>¥${((m.total_cost||0)/100).toFixed(4)}</td></tr>`).join('');

  const rc = cd.recent;
  document.getElementById('recentRecords').innerHTML = rc.length === 0
    ? '<tr><td colspan="5" class="empty">暂无数据</td></tr>'
    : rc.map(r => `<tr><td>${r.created_at}</td><td><span class="tag">${r.model}</span></td><td>${(r.prompt_tokens||0).toLocaleString()}</td><td>${(r.completion_tokens||0).toLocaleString()}</td><td>¥${((r.revenue_cents||0)/100).toFixed(4)}</td></tr>`).join('');
}

function showRecharge() {
  document.getElementById('rechargeSection').style.display = 'block';
  document.getElementById('rechargeSection').scrollIntoView({behavior:'smooth'});
}

function selPay(m) {
  selPayMethod = m;
  document.getElementById('tabWechat').className = m==='wechat'?'pay-tab sel':'pay-tab';
  document.getElementById('tabAlipay').className = m==='alipay'?'pay-tab sel':'pay-tab';
  document.getElementById('payMethodLabel').textContent = m==='wechat'?'微信':'支付宝';
  var img = document.getElementById('qrImg');
  img.src = '/static/' + m + '-qr.jpg';
  img.style.display = '';
  document.getElementById('qrPlaceholder').style.display = 'none';
  img.onerror = function() {
    // 试一下 .png
    if (img.src.indexOf('.jpg') > -1) {
      img.src = img.src.replace('.jpg', '.png');
    } else {
      img.style.display = 'none';
      document.getElementById('qrPlaceholder').style.display = 'block';
    }
  };
}
function selAmt(a) { selAmount = a; document.querySelectorAll('.amount-btn').forEach(b => b.classList.remove('sel')); event.target.classList.add('sel'); document.getElementById('payAmountLabel').textContent = (a/100); }

async function submitPay() {
  const amount = selAmount / 100;
  const r = await fetch('/portal/api/topup/create', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:cd.api_key.key, amount_cents:selAmount, payment_method:selPayMethod})});
  const d = await r.json();
  if (!d.ok) { msg('rechargeMsg','err',d.error); return; }

  // 调用 xorpay 下单
  const pr = await fetch('/portal/api/topup/pay', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:cd.api_key.key, order_id:d.order_id, pay_type:selPayMethod==='wechat'?'native':'alipay'})});
  const pd = await pr.json();

  if (pd.ok && pd.qr_url) {
    // xorpay 模式：显示支付二维码
    document.getElementById('qrImg').src = pd.qr_url;
    document.getElementById('qrImg').style.display = '';
    document.getElementById('qrPlaceholder').style.display = 'none';
    document.getElementById('payMethodLabel').textContent = selPayMethod==='wechat'?'微信':'支付宝';
    document.getElementById('payAmountLabel').textContent = amount;
    document.getElementById('payRef').style.display = 'none';
    document.querySelector('#rechargeSection button.btn').style.display = 'none';
    msg('rechargeMsg','ok','请扫码支付 ¥'+amount+'，支付成功后余额自动到账');
  } else {
    // 兜底：手动模式
    const ref = document.getElementById('payRef').value.trim();
    if (!ref) { msg('rechargeMsg','err','请输入付款交易单号'); return; }
    const r2 = await fetch('/portal/api/topup/confirm', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:cd.api_key.key, order_id:d.order_id, payment_ref:ref})});
    const d2 = await r2.json();
    if (d2.ok) { msg('rechargeMsg','ok','已提交充值 ¥'+amount+'，等待确认'); }
    else { msg('rechargeMsg','err',d2.error); }
  }
}

function msg(id, type, html) {
  const el = document.getElementById(id);
  el.className = 'msg ' + type;
  el.innerHTML = html;
  setTimeout(() => { el.className = 'msg'; el.innerHTML = ''; }, 8000);
}

document.getElementById('apiKeyInput').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
</script>
</body>
</html>"""
