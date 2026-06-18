"""
管理后台 API —— 管理客户、API Key、定价、查看利润
"""
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import settings
from database import get_db, init_db
from services.billing import get_profit_summary

router = APIRouter(prefix="/admin", tags=["admin"])

# ---- Pydantic models for admin API ----

class CreateCustomerRequest(BaseModel):
    name: str
    initial_balance_cents: int = 10000  # 默认 $100

class CreateAPIKeyRequest(BaseModel):
    customer_id: int

class UpdatePricingRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_pattern: str
    input_price_per_1k: float   # 卖给客户的价格
    output_price_per_1k: float
    input_cost_per_1k: float    # 你的上游成本
    output_cost_per_1k: float

class TopUpRequest(BaseModel):
    customer_id: int
    amount_cents: int


# ---- Auth helper ----

def _check_admin(request: Request):
    """简单的密码鉴权"""
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "")
    if token != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid admin password")
    return True


# ---- Admin API endpoints ----

@router.get("/dashboard")
async def dashboard_html():
    """管理后台网页"""
    return HTMLResponse(content=ADMIN_HTML)


@router.get("/api/stats")
async def get_stats(request: Request):
    """获取利润统计"""
    _check_admin(request)
    return get_profit_summary()


@router.get("/api/customers")
async def list_customers(request: Request):
    """列出所有客户"""
    _check_admin(request)
    db = get_db()
    try:
        customers = db.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM api_keys ak WHERE ak.customer_id = c.id) as key_count,
                      (SELECT COALESCE(SUM(revenue_cents - cost_cents), 0)
                       FROM usage_records WHERE customer_id = c.id) as total_profit
               FROM customers c
               ORDER BY c.created_at DESC"""
        ).fetchall()
        return [dict(r) for r in customers]
    finally:
        db.close()


@router.post("/api/customers")
async def create_customer(body: CreateCustomerRequest, request: Request):
    """创建客户"""
    _check_admin(request)
    db = get_db()
    try:
        cursor = db.execute(
            "INSERT INTO customers (name, balance_cents) VALUES (?, ?)",
            (body.name, body.initial_balance_cents),
        )
        db.commit()
        return {"id": cursor.lastrowid, "name": body.name}
    finally:
        db.close()


@router.post("/api/keys")
async def create_api_key(body: CreateAPIKeyRequest, request: Request):
    """为客户生成 API Key"""
    _check_admin(request)
    key = "sk-" + secrets.token_hex(24)
    db = get_db()
    try:
        customer = db.execute(
            "SELECT id, name FROM customers WHERE id = ?", (body.customer_id,)
        ).fetchone()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        db.execute(
            "INSERT INTO api_keys (customer_id, key) VALUES (?, ?)",
            (body.customer_id, key),
        )
        db.commit()
        return {
            "key": key,
            "customer_id": body.customer_id,
            "customer_name": customer["name"],
        }
    finally:
        db.close()


@router.post("/api/customers/topup")
async def top_up_balance(body: TopUpRequest, request: Request):
    """给客户充值"""
    _check_admin(request)
    db = get_db()
    try:
        db.execute(
            "UPDATE customers SET balance_cents = balance_cents + ? WHERE id = ?",
            (body.amount_cents, body.customer_id),
        )
        db.commit()
        customer = db.execute(
            "SELECT name, balance_cents FROM customers WHERE id = ?",
            (body.customer_id,),
        ).fetchone()
        return {
            "customer_id": body.customer_id,
            "name": customer["name"],
            "new_balance_cents": customer["balance_cents"],
        }
    finally:
        db.close()


@router.get("/api/pricing")
async def list_pricing(request: Request):
    """列出所有定价规则"""
    _check_admin(request)
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM pricing ORDER BY model_pattern").fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.post("/api/pricing")
async def upsert_pricing(body: UpdatePricingRequest, request: Request):
    """新增或更新定价规则"""
    _check_admin(request)
    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM pricing WHERE model_pattern = ?",
            (body.model_pattern,),
        ).fetchone()

        if existing:
            db.execute(
                """UPDATE pricing SET
                   input_price_per_1k=?, output_price_per_1k=?,
                   input_cost_per_1k=?, output_cost_per_1k=?
                   WHERE model_pattern=?""",
                (body.input_price_per_1k, body.output_price_per_1k,
                 body.input_cost_per_1k, body.output_cost_per_1k,
                 body.model_pattern),
            )
        else:
            db.execute(
                """INSERT INTO pricing
                   (model_pattern, input_price_per_1k, output_price_per_1k,
                    input_cost_per_1k, output_cost_per_1k)
                   VALUES (?, ?, ?, ?, ?)""",
                (body.model_pattern, body.input_price_per_1k, body.output_price_per_1k,
                 body.input_cost_per_1k, body.output_cost_per_1k),
            )
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ---- 充值订单管理 ----

class ApproveOrderRequest(BaseModel):
    order_id: int

@router.get("/api/topup-orders")
async def list_topup_orders(request: Request):
    """列出所有充值订单"""
    _check_admin(request)
    db = get_db()
    try:
        orders = db.execute(
            """SELECT o.*, c.name as customer_name
               FROM topup_orders o
               JOIN customers c ON c.id = o.customer_id
               ORDER BY o.created_at DESC LIMIT 50"""
        ).fetchall()
        return [dict(o) for o in orders]
    finally:
        db.close()


@router.post("/api/topup-orders/approve")
async def approve_topup_order(body: ApproveOrderRequest, request: Request):
    """批准充值订单，自动加余额"""
    _check_admin(request)
    db = get_db()
    try:
        order = db.execute("SELECT * FROM topup_orders WHERE id = ?", (body.order_id,)).fetchone()
        if not order:
            return {"ok": False, "error": "订单不存在"}
        if order["status"] != "submitted":
            return {"ok": False, "error": f"订单状态为 {order['status']}，无法批准"}

        # 加余额
        db.execute(
            "UPDATE customers SET balance_cents = balance_cents + ? WHERE id = ?",
            (order["amount_cents"], order["customer_id"]),
        )
        # 更新订单状态
        db.execute(
            "UPDATE topup_orders SET status='confirmed', confirmed_at=CURRENT_TIMESTAMP WHERE id=?",
            (body.order_id,),
        )
        db.commit()

        customer = db.execute("SELECT name, balance_cents FROM customers WHERE id=?", (order["customer_id"],)).fetchone()
        return {
            "ok": True,
            "customer_name": customer["name"],
            "new_balance_cents": customer["balance_cents"],
        }
    finally:
        db.close()


# ---- 内嵌管理后台 HTML ----

ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Inter', -apple-system, sans-serif; background: #fafafa; color: #111; -webkit-font-smoothing: antialiased; }
.nav { position: fixed; top: 0; left: 0; right: 0; z-index: 100; background: rgba(255,255,255,0.8); backdrop-filter: blur(20px); border-bottom: 1px solid #eee; padding: 0 32px; height: 56px; display: flex; align-items: center; justify-content: space-between; font-size: 0.85em; }
.nav .logo { font-weight: 700; }
.nav .row { display: flex; gap: 8px; align-items: center; }
.nav input { padding: 6px 10px; border-radius: 6px; border: 1.5px solid #e5e5e5; font-size: 0.85em; outline: none; width: 150px; }
.nav input:focus { border-color: #111; }
.nav button { padding: 6px 14px; border-radius: 6px; background: #111; color: #fff; border: none; font-size: 0.8em; cursor: pointer; }
.nav .status { font-size: 0.8em; }
.container { max-width: 1080px; margin: 80px auto 40px; padding: 0 32px; }
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
.row { display: flex; gap: 8px; margin-bottom: 12px; align-items: center; flex-wrap: wrap; }
input[type="text"], input[type="number"] { padding: 8px 12px; border-radius: 6px; border: 1.5px solid #e5e5e5; font-size: 0.85em; outline: none; }
input:focus { border-color: #111; }
.btn { padding: 8px 16px; border-radius: 6px; border: none; font-size: 0.85em; font-weight: 600; cursor: pointer; background: #111; color: #fff; transition: background .2s; }
.btn:hover { background: #333; }
.btn-sm { padding: 5px 12px; border-radius: 6px; border: 1px solid #e5e5e5; background: #fff; font-size: 0.8em; cursor: pointer; transition: all .2s; }
.btn-sm:hover { border-color: #111; }
.customer-row { cursor: pointer; transition: background .1s; }
.customer-row:hover { background: #f8f8f8; }
.customer-row.selected { background: #f0f0f0; }
.msg { padding: 10px 14px; border-radius: 6px; margin-top: 8px; font-size: 0.85em; display: none; }
.msg.ok { background: #f5f5f5; color: #111; display: block; }
.msg.err { background: #f5f5f5; color: #111; display: block; border: 1px solid #e5e5e5; }
.msg code { background: #e5e5e5; padding: 2px 6px; border-radius: 3px; }
.empty { text-align: center; color: #ccc; padding: 40px; }
</style>
</head>
<body>

<nav class="nav">
  <span class="logo">管理后台</span>
  <div class="row">
    <input id="pwdInput" type="password" placeholder="密码" value="admin123" />
    <button onclick="login()">登录</button>
    <span class="status" id="authStatus"></span>
  </div>
</nav>

<div class="container">

  <div class="stats" id="stats"></div>

  <div class="section">
    <h3>客户管理</h3>
    <div class="row">
      <input id="customerName" placeholder="客户名称" style="width:150px" />
      <input id="initialBalance" placeholder="初始余额（分）" value="10000" type="number" style="width:150px" />
      <button class="btn" onclick="createCustomer()">创建客户</button>
    </div>
    <table><thead><tr><th>ID</th><th>名称</th><th>余额</th><th>Key数</th><th>贡献利润</th><th></th></tr></thead>
    <tbody id="customerList"><tr><td colspan="6" class="empty">登录后加载</td></tr></tbody></table>
  </div>

  <div class="section">
    <h3>密钥与充值</h3>
    <div class="row">
      <input id="keyCustomerId" placeholder="客户 ID" type="number" style="width:130px" />
      <button class="btn" onclick="createKey()">生成 Key</button>
    </div>
    <div class="row">
      <input id="topupCustomerId" placeholder="客户 ID" type="number" style="width:130px" />
      <input id="topupAmount" placeholder="充值金额（分）" value="10000" type="number" style="width:150px" />
      <button class="btn" onclick="topUp()">充值</button>
    </div>
    <div id="keyMsg" class="msg"></div>
    <div id="topupMsg" class="msg"></div>
  </div>

  <div class="section">
    <h3>模型定价</h3>
    <table><thead><tr><th>匹配模式</th><th>售价-输入</th><th>售价-输出</th><th>成本-输入</th><th>成本-输出</th><th>利润率</th></tr></thead>
    <tbody id="pricingList"><tr><td colspan="6" class="empty">登录后加载</td></tr></tbody></table>
    <div class="row" style="margin-top:12px">
      <input id="pModel" placeholder="匹配模式" style="width:140px" />
      <input id="pSellIn" placeholder="售价-输入" type="number" step="0.0001" style="width:100px" />
      <input id="pSellOut" placeholder="售价-输出" type="number" step="0.0001" style="width:100px" />
      <input id="pCostIn" placeholder="成本-输入" type="number" step="0.0001" style="width:100px" />
      <input id="pCostOut" placeholder="成本-输出" type="number" step="0.0001" style="width:100px" />
      <button class="btn" onclick="updatePricing()">保存</button>
    </div>
    <div id="pricingMsg" class="msg"></div>
  </div>

  <div class="section">
    <h3>充值审批</h3>
    <table><thead><tr><th>订单ID</th><th>客户</th><th>金额</th><th>方式</th><th>状态</th><th>交易号</th><th>操作</th></tr></thead>
    <tbody id="topupList"><tr><td colspan="7" class="empty">登录后加载</td></tr></tbody></table>
    <div id="topupApproveMsg" class="msg"></div>
  </div>

  <div class="section">
    <h3>用量统计</h3>
    <table><thead><tr><th>模型</th><th>请求数</th><th>收入</th><th>利润</th></tr></thead>
    <tbody id="modelStats"><tr><td colspan="4" class="empty">登录后加载</td></tr></tbody></table>
  </div>
</div>

<script>
let H = null;

function login() {
  H = { 'Authorization': 'Bearer ' + (document.getElementById('pwdInput').value || 'admin123'), 'Content-Type': 'application/json' };
  fetch('/admin/api/stats', {headers: H})
    .then(r => {
      if (!r.ok) throw new Error('密码错误');
      document.getElementById('authStatus').textContent = '已登录';
      document.getElementById('authStatus').style.color = '#333';
      loadAll();
    })
    .catch(e => { document.getElementById('authStatus').textContent = e.message; document.getElementById('authStatus').style.color = '#999'; H = null; });
}

document.getElementById('pwdInput').addEventListener('keydown', e => { if (e.key === 'Enter') login(); });
setTimeout(login, 300);

function msg(id, type, html) {
  const el = document.getElementById(id);
  el.className = 'msg ' + type;
  el.innerHTML = html;
  setTimeout(() => { el.className = 'msg'; el.innerHTML = ''; }, 6000);
}

async function loadAll() {
  if (!H) return;
  const stats = await (await fetch('/admin/api/stats', {headers: H})).json();
  const t = stats.total;
  document.getElementById('stats').innerHTML =
    `<div class="stat"><div class="label">总请求</div><div class="value">${t.total_requests||0}</div></div>
     <div class="stat"><div class="label">总收入</div><div class="value">¥${((t.total_revenue||0)/100).toFixed(2)}</div></div>
     <div class="stat"><div class="label">总成本</div><div class="value">¥${((t.total_cost||0)/100).toFixed(2)}</div></div>
     <div class="stat"><div class="label">总利润</div><div class="value">¥${((t.total_profit||0)/100).toFixed(2)}</div></div>
     <div class="stat"><div class="label">总Token</div><div class="value">${((t.total_prompt_tokens + t.total_completion_tokens)||0).toLocaleString()}</div></div>`;

  const cs = await (await fetch('/admin/api/customers', {headers: H})).json();
  const cb = document.getElementById('customerList');
  cb.innerHTML = cs.length === 0
    ? '<tr><td colspan="6" class="empty">暂无客户，在上方创建</td></tr>'
    : cs.map(c => `<tr class="customer-row" onclick="sel(${c.id})" id="crow-${c.id}">
        <td><strong>${c.id}</strong></td><td>${c.name}</td><td>¥${(c.balance_cents/100).toFixed(2)}</td><td>${c.key_count}</td><td>¥${((c.total_profit||0)/100).toFixed(2)}</td>
        <td>
          <button class="btn-sm" onclick="event.stopPropagation();sel(${c.id});document.getElementById('keyCustomerId').value=${c.id};createKey();">生成Key</button>
          <button class="btn-sm" onclick="event.stopPropagation();sel(${c.id});topupPrompt(${c.id},'${c.name.replace(/'/g,"\\'")}');">充值</button>
        </td></tr>`).join('');

  const pr = await (await fetch('/admin/api/pricing', {headers: H})).json();
  document.getElementById('pricingList').innerHTML = pr.map(p => {
    const m = ((p.output_price_per_1k - p.output_cost_per_1k) / (p.output_cost_per_1k || 0.001) * 100).toFixed(0);
    return `<tr><td><span class="tag">${p.model_pattern}</span></td><td>¥${p.input_price_per_1k.toFixed(4)}</td><td>¥${p.output_price_per_1k.toFixed(4)}</td><td>¥${p.input_cost_per_1k.toFixed(4)}</td><td>¥${p.output_cost_per_1k.toFixed(4)}</td><td>${m}%</td></tr>`;
  }).join('');

  const ms = stats.by_model;
  document.getElementById('modelStats').innerHTML = ms.length === 0
    ? '<tr><td colspan="4" class="empty">暂无用量数据</td></tr>'
    : ms.map(m => `<tr><td><span class="tag">${m.model}</span></td><td>${m.requests}</td><td>¥${((m.revenue||0)/100).toFixed(2)}</td><td>¥${((m.profit||0)/100).toFixed(2)}</td></tr>`).join('');

  // 充值订单
  const orders = await (await fetch('/admin/api/topup-orders', {headers: H})).json();
  document.getElementById('topupList').innerHTML = orders.length === 0
    ? '<tr><td colspan="7" class="empty">暂无充值订单</td></tr>'
    : orders.map(o => `<tr>
        <td>${o.id}</td><td>${o.customer_name}</td><td>¥${(o.amount_cents/100).toFixed(2)}</td>
        <td>${o.payment_method||'-'}</td><td>${o.status}</td><td style="font-size:0.8em;max-width:120px;overflow:hidden">${o.payment_ref||'-'}</td>
        <td>${o.status==='submitted' ? '<button class="btn-sm" onclick="approveTopup('+o.id+')">批准</button>' : o.status==='confirmed' ? '已到账' : o.status}</td>
      </tr>`).join('');
}

async function approveTopup(oid) {
  const r = await fetch('/admin/api/topup-orders/approve', {method:'POST', headers:H, body:JSON.stringify({order_id:oid})});
  const d = await r.json();
  if (d.ok) { msg('topupApproveMsg','ok',d.customer_name+' 已到账 ¥'+(d.new_balance_cents/100).toFixed(2)); loadAll(); }
  else { msg('topupApproveMsg','err',d.error); }
}

function sel(id) {
  document.querySelectorAll('.customer-row').forEach(r => r.classList.remove('selected'));
  const row = document.getElementById('crow-' + id);
  if (row) row.classList.add('selected');
  document.getElementById('keyCustomerId').value = id;
  document.getElementById('topupCustomerId').value = id;
}

async function createCustomer() {
  const n = document.getElementById('customerName').value.trim();
  if (!n) return;
  const b = parseInt(document.getElementById('initialBalance').value) || 10000;
  await fetch('/admin/api/customers', {method:'POST', headers:H, body:JSON.stringify({name:n, initial_balance_cents:b})});
  document.getElementById('customerName').value = '';
  msg('keyMsg','ok','已创建: '+n);
  loadAll();
}

async function createKey() {
  const cid = parseInt(document.getElementById('keyCustomerId').value);
  if (!cid) { msg('keyMsg','err','请先输入客户 ID 或点击上方客户行'); return; }
  const r = await fetch('/admin/api/keys', {method:'POST', headers:H, body:JSON.stringify({customer_id:cid})});
  const d = await r.json();
  if (!r.ok) { msg('keyMsg','err',d.detail||'生成失败'); return; }
  msg('keyMsg','ok','新 Key: <code>'+d.key+'</code> — 请立即复制');
}

function topupPrompt(id, name) {
  const a = prompt('给 ' + name + ' 充值多少分？(100分=¥1):', '10000');
  if (!a) return;
  document.getElementById('topupCustomerId').value = id;
  document.getElementById('topupAmount').value = a;
  topUp();
}

async function topUp() {
  const cid = parseInt(document.getElementById('topupCustomerId').value);
  const amt = parseInt(document.getElementById('topupAmount').value);
  if (!cid || !amt || amt <= 0) { msg('topupMsg','err','请输入有效的客户ID和金额'); return; }
  const r = await fetch('/admin/api/customers/topup', {method:'POST', headers:H, body:JSON.stringify({customer_id:cid, amount_cents:amt})});
  const d = await r.json();
  if (!r.ok) { msg('topupMsg','err',d.detail||'充值失败'); return; }
  msg('topupMsg','ok',d.name+': 新余额 ¥'+(d.new_balance_cents/100).toFixed(2));
  loadAll();
}

async function updatePricing() {
  const mp = document.getElementById('pModel').value.trim();
  if (!mp) { msg('pricingMsg','err','请输入模型匹配模式'); return; }
  await fetch('/admin/api/pricing', {method:'POST', headers:H, body:JSON.stringify({
    model_pattern: mp,
    input_price_per_1k: parseFloat(document.getElementById('pSellIn').value) || 0,
    output_price_per_1k: parseFloat(document.getElementById('pSellOut').value) || 0,
    input_cost_per_1k: parseFloat(document.getElementById('pCostIn').value) || 0,
    output_cost_per_1k: parseFloat(document.getElementById('pCostOut').value) || 0,
  })});
  document.getElementById('pModel').value = '';
  msg('pricingMsg','ok','已保存: '+mp);
  loadAll();
}
</script>
</body>
</html>"""
