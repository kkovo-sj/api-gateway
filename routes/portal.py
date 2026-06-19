"""
首页 + 客户自助门户
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from database import get_db

router = APIRouter(tags=["portal"])

# 匿名试用接口
import time as _time
from collections import defaultdict as _dd
_try_limits = _dd(list)

@router.post("/try")
async def try_chat(request: Request):
    """匿名试用——限速：每IP每分钟3次"""
    ip = request.client.host if request.client else "unknown"
    now = _time.time()
    _try_limits[ip] = [t for t in _try_limits[ip] if now - t < 60]
    if len(_try_limits[ip]) >= 3:
        return {"error": {"message": "试用次数用完，请到门户获取API Key"}}
    _try_limits[ip].append(now)

    body = await request.json()
    model = body.get("model", "deepseek-chat")
    msg = body.get("message", "")[:200]

    import httpx
    from config import settings
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.transit_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.transit_api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": msg}], "max_tokens": 200},
        )
        return resp.json()


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
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KK API 中转站</title>
<style>
:root{--bg:#fff;--bg2:#f9f9f8;--text:#111;--text2:#777;--text3:#bbb;--border:#eaeaea;--accent:#111;--r:10px}
[data-theme="dark"]{--bg:#111;--bg2:#1b1b1b;--text:#eee;--text2:#999;--text3:#555;--border:#2a2a2a;--accent:#eee}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;color:var(--text);background:var(--bg);line-height:1.5;-webkit-font-smoothing:antialiased;transition:background .3s,color .3s}
.nav{position:fixed;top:0;left:0;right:0;z-index:100;background:var(--bg);border-bottom:1px solid var(--border);padding:0 24px;height:52px;display:flex;align-items:center;justify-content:space-between;font-size:14px;transition:background .3s}
.nav .logo{font-weight:700;font-size:15px;color:var(--text);text-decoration:none;letter-spacing:-.3px}
.nav .links{display:flex;align-items:center;gap:16px}
.nav .links a{color:var(--text2);text-decoration:none;font-size:13px}
.nav .links a:hover{color:var(--text)}
.dm-btn{width:34px;height:34px;border-radius:50%;border:1px solid var(--border);background:var(--bg);cursor:pointer;font-size:15px;color:var(--text2);display:flex;align-items:center;justify-content:center;transition:all .2s}
.dm-btn:hover{border-color:var(--text3)}
.hero{padding:120px 24px 70px;text-align:center;max-width:640px;margin:0 auto}
.hero .chip{display:inline-block;padding:4px 14px;border-radius:100px;background:var(--bg2);color:var(--text2);font-size:11px;font-weight:500;border:1px solid var(--border);margin-bottom:20px;letter-spacing:.3px}
.hero h1{font-size:2.8em;font-weight:800;letter-spacing:-2px;line-height:1.1;margin-bottom:14px}
.hero p{font-size:1.02em;color:var(--text2);line-height:1.6;margin-bottom:32px}
.btn{display:inline-block;padding:11px 26px;border-radius:100px;font-size:14px;font-weight:600;text-decoration:none;transition:all .2s;border:none;cursor:pointer;letter-spacing:-.2px}
.btn-p{background:var(--accent);color:var(--bg)}
.btn-p:hover{opacity:.85;transform:translateY(-1px)}
.btn-s{background:transparent;color:var(--text);border:1.5px solid var(--border)}
.btn-s:hover{border-color:var(--text3)}
.btns{display:flex;gap:8px;justify-content:center;flex-wrap:wrap}
.wrap{max-width:1060px;margin:0 auto;padding:0 24px}
.sec{padding:60px 0}
.sec .tag{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--text3);margin-bottom:10px}
.sec h2{font-size:1.7em;font-weight:700;letter-spacing:-1px;margin-bottom:36px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border);border:1px solid var(--border);border-radius:var(--r);overflow:hidden}
.grid3 .c{background:var(--bg);padding:32px 24px;transition:background .3s}
.grid3 .c .n{font-size:10px;color:var(--text3);margin-bottom:12px}
.grid3 .c h3{font-size:14px;font-weight:600;margin-bottom:4px}
.grid3 .c p{font-size:12px;color:var(--text2);line-height:1.6}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-weight:500;color:var(--text3);font-size:10px;text-transform:uppercase;letter-spacing:.5px;text-align:left;padding:12px 14px;border-bottom:1px solid var(--border)}
td{padding:12px 14px;border-bottom:1px solid var(--border)}
td .p{font-size:11px;color:var(--text2);display:block;margin-top:2px}
.code{background:var(--bg2);color:var(--text);padding:28px 32px;border-radius:var(--r);overflow-x:auto;font-size:13px;line-height:1.8;font-family:'SF Mono','Fira Code',monospace;max-width:640px;margin:0 auto;border:1px solid var(--border)}
.code .cm{color:var(--text3)}
.foot{border-top:1px solid var(--border);padding:28px 24px;text-align:center;font-size:12px;color:var(--text3);display:flex;gap:20px;justify-content:center;flex-wrap:wrap;align-items:center}
.foot a{color:var(--text2);text-decoration:none}
.foot a:hover{color:var(--text)}
.online{display:inline-block;width:6px;height:6px;border-radius:50%;background:#4ade80;margin-right:4px}
.mbtn{padding:8px 18px;border-radius:100px;border:1px solid var(--border);background:var(--bg);color:var(--text2);font-size:13px;cursor:pointer;transition:all .2s;font-weight:500}
.mbtn.sel,.mbtn:hover{border-color:var(--accent);color:var(--text);background:var(--bg2)}
.copy-btn{padding:3px 10px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text2);font-size:11px;cursor:pointer;transition:all .2s;margin-left:6px}
.copy-btn:hover{border-color:var(--accent);color:var(--text)}
.toast{position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:var(--accent);color:var(--bg);padding:10px 24px;border-radius:100px;font-size:13px;font-weight:600;z-index:999;opacity:0;transition:opacity .3s}
.toast.show{opacity:1}
@media(max-width:768px){.hero h1{font-size:2em}.grid3{grid-template-columns:1fr}}
</style>
</head>
<body>
<nav class="nav">
  <a href="/" class="logo">你的 API 可以接入下面的大模型</a>
  <div class="links">
    <a href="#models">模型</a>
    <a href="#pricing">定价</a>
    <a href="#start">接入</a>
    <a href="/portal">你的 API</a>
    <a href="/chat">AI 聊天</a>
    <a href="/benchmark">测速</a>
    <a href="/docs">接口文档</a>
    <button class="dm-btn" onclick="toggleTheme()" title="夜间模式">◐</button>
  </div>
</nav>
<section class="hero">
  <div class="chip">GPT-5.5 · Claude Opus · Grok · DeepSeek 已接入</div>
  <h1>你可以任意挑选<br>你想要的顶级模型</h1>
  <p>GPT-5.5 — 目前地球上最强的语言模型<br>Claude Opus 4.8 — 编程和推理的终极武器<br>再加上 DeepSeek · Qwen · GLM · Kimi · Grok<br>一个 Key，一把梭，全世界的顶级 AI 听你调遣</p>
  <div class="btns"><a href="/portal" class="btn btn-p">免费获取 API Key</a><a href="#try" class="btn btn-s">先试用</a></div>
</section>
<div class="wrap">
  <section class="sec" id="try">
    <div class="tag">Try Now</div>
    <h2>免费试用，选模型直接聊</h2>
    <div style="max-width:700px;margin:0 auto">
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap" id="modelBtns">
        <button class="mbtn sel" data-model="gpt-5.5">GPT-5.5</button>
        <button class="mbtn" data-model="claude-sonnet-4-6">Claude Sonnet</button>
        <button class="mbtn" data-model="deepseek-chat">DeepSeek V3</button>
        <button class="mbtn" data-model="qwen-turbo">Qwen Turbo</button>
      </div>
      <div style="display:flex;gap:8px">
        <input id="tryInput" type="text" placeholder="输入你的问题，体验顶级模型..." style="flex:1;padding:12px 16px;border:1px solid var(--border);border-radius:var(--r);background:var(--bg);color:var(--text);font-size:14px;outline:none" onkeydown="if(event.key==='Enter')tryChat()">
        <button class="btn btn-p" onclick="tryChat()">发送</button>
      </div>
      <div id="tryResult" style="margin-top:16px;padding:20px;border-radius:var(--r);background:var(--bg2);border:1px solid var(--border);min-height:60px;color:var(--text2);font-size:14px;line-height:1.7;white-space:pre-wrap;display:none"></div>
    </div>
  </section>
  <section class="sec" id="models">
     <div class="tag">WHY US</div>
    <h2>我们可以给你提供最方便的途径</h2>
    <div class="grid3">
      <div class="c"><div class="n">01</div><h3>不用翻墙</h3><p>国内直连，GPT-5.5 和 Claude 随便用</p></div>
      <div class="c"><div class="n">02</div><h3>不需要外币卡</h3><p>人民币支付，微信支付宝都能充值</p></div>
      <div class="c"><div class="n">03</div><h3>不用注册四个平台</h3><p>一个 Key 打通国内外 17 个顶级模型</p></div>
      <div class="c"><div class="n">04</div><h3>三行代码接入</h3><p>OpenAI 格式，改个 base_url 就能用</p></div>
      <div class="c"><div class="n">05</div><h3>比官方便宜</h3><p>同样的模型，更低的价格</p></div>
      <div class="c"><div class="n">06</div><h3>按量计费不浪费</h3><p>用多少扣多少，不用不花钱</p></div>
    </div>
  </section>
  <section class="sec" id="pricing">
    <div class="tag">Pricing</div>
    <h2>也许你找不到第二个像我们一样的平台</h2>
    <div style="overflow-x:auto"><table>
      <thead><tr><th>厂商</th><th>🔥 最强</th><th>🧠 推理</th><th>💬 对话</th><th></th></tr></thead>
      <tbody>
        <tr><td><strong>OpenAI</strong></td><td>GPT-5.5 <span class="p">¥30/1M</span></td><td>o3-mini <span class="p">¥6/1M</span></td><td>GPT-4o-mini <span class="p">¥3/1M</span></td><td><button class="copy-btn" onclick="copyCode('gpt-5.5')">接入 →</button></td></tr>
        <tr><td><strong>Anthropic</strong></td><td>Claude Opus <span class="p">¥140/1M</span></td><td>Sonnet <span class="p">¥85/1M</span></td><td>Haiku <span class="p">¥30/1M</span></td><td><button class="copy-btn" onclick="copyCode('claude-sonnet-4-6')">接入 →</button></td></tr>
        <tr><td><strong>DeepSeek</strong></td><td>V4 Pro <span class="p">¥15/1M</span></td><td>R1 <span class="p">¥8/1M</span></td><td>V3 <span class="p">¥5/1M</span></td><td><button class="copy-btn" onclick="copyCode('deepseek-chat')">接入 →</button></td></tr>
        <tr><td><strong>阿里</strong></td><td>Qwen3-Max <span class="p">¥15/1M</span></td><td>QwQ Plus <span class="p">¥6/1M</span></td><td>Qwen Turbo <span class="p">¥2/1M</span></td><td><button class="copy-btn" onclick="copyCode('qwen-turbo')">接入 →</button></td></tr>
        <tr><td><strong>智谱</strong></td><td>GLM-5 <span class="p">¥10/1M</span></td><td>—</td><td>GLM-4-Flash <span class="p">¥0.5/1M</span></td><td><button class="copy-btn" onclick="copyCode('glm-4-flash')">接入 →</button></td></tr>
        <tr><td><strong>Kimi</strong></td><td>K2.7 Code <span class="p">¥18/1M</span></td><td>—</td><td>K2.6 <span class="p">¥12/1M</span></td><td><button class="copy-btn" onclick="copyCode('kimi-k2.6')">接入 →</button></td></tr>
        <tr><td><strong>Grok</strong></td><td>Grok 4.3 <span class="p">¥4/1M</span></td><td>—</td><td>—</td><td><button class="copy-btn" onclick="copyCode('grok-4.3')">接入 →</button></td></tr>
      </tbody>
    </table></div>
  </section>
  <section class="sec" id="start">
    <div class="tag">Quickstart</div>
    <h2>复制粘贴，开始调用</h2>
    <div class="code">
<span class="cm"># pip install openai</span><br>
client = OpenAI(<br>
&nbsp;&nbsp;api_key=<span>"sk-你的Key"</span>,<br>
&nbsp;&nbsp;base_url=<span>"http://115.159.84.76:8000/v1"</span><br>
)<br>
res = client.chat.completions.create(<br>
&nbsp;&nbsp;model=<span>"deepseek-chat"</span>,<br>
&nbsp;&nbsp;messages=[{"role":"user","content":"你好"}]<br>
)
    </div>
  </section>
</div>
<footer class="foot">
  <a href="/portal">你的 API</a>
  <a href="/docs">API 文档</a>
  <span><span class="online"></span>售后微信：kkovo_sj</span>
  <span>KK API 中转站</span>
</footer>
<script>
const html=document.documentElement
function toggleTheme(){html.dataset.theme=html.dataset.theme==='dark'?'light':'dark';localStorage.setItem('theme',html.dataset.theme)}
(function(){const t=localStorage.getItem('theme');if(t)html.dataset.theme=t;else if(window.matchMedia('(prefers-color-scheme:dark)').matches)html.dataset.theme='dark'})()

// 免费试用
let selModel='gpt-5.5'
document.querySelectorAll('.mbtn').forEach(b=>{b.onclick=()=>{document.querySelectorAll('.mbtn').forEach(x=>x.classList.remove('sel'));b.classList.add('sel');selModel=b.dataset.model}})
async function tryChat(){
  const input=document.getElementById('tryInput'),res=document.getElementById('tryResult')
  if(!input.value.trim())return
  res.style.display='block';res.textContent='思考中...'
  try{
    const r=await fetch('/try',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:selModel,message:input.value})})
    const d=await r.json()
    if(d.choices)res.textContent=d.choices[0].message.content
    else if(d.error&&d.error.message.includes('余额'))res.textContent='试用额度已用完，请到「你的 API」页面充值获取 Key。'
    else res.textContent='出错了，请稍后重试。'
  }catch(e){res.textContent='网络错误，请检查连接。'}
}

// 一键复制接入代码
function copyCode(model){
  const code=`from openai import OpenAI\\n\\nclient = OpenAI(\\n    api_key="sk-你的Key",\\n    base_url="http://115.159.84.76:8000/v1"\\n)\\n\\nresponse = client.chat.completions.create(\\n    model="${model}",\\n    messages=[{"role":"user","content":"你好"}]\\n)\\nprint(response.choices[0].message.content)`
  navigator.clipboard.writeText(code).then(()=>showToast('✅ 已复制 ${model} 接入代码！去「你的 API」获取 Key 即可使用'))
}
function showToast(msg){
  let t=document.getElementById('toast')
  if(!t){t=document.createElement('div');t.id='toast';t.className='toast';document.body.appendChild(t)}
  t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2500)
}
</script>
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
