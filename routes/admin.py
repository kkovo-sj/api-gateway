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
<html lang="zh" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KK API — 运营中心</title>
<style>
:root{--bg:#0a0a0a;--bg2:#141414;--bg3:#1e1e1e;--text:#eee;--text2:#888;--text3:#555;--border:#2a2a2a;--accent:#fff;--green:#4ade80;--red:#f87171;--yellow:#fbbf24;--blue:#60a5fa;--radius:8px;--sidebar-w:220px}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;color:var(--text);background:var(--bg);line-height:1.4;-webkit-font-smoothing:antialiased;display:flex;min-height:100vh}
.sidebar{position:fixed;top:0;left:0;bottom:0;width:var(--sidebar-w);background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;z-index:100;overflow-y:auto}
.sidebar .logo{padding:18px 20px;font-weight:800;font-size:16px;letter-spacing:-.5px;border-bottom:1px solid var(--border)}
.sidebar nav{flex:1;padding:12px 0}
.sidebar nav a{display:flex;align-items:center;gap:10px;padding:9px 20px;color:var(--text2);text-decoration:none;font-size:13px;transition:all .15s;border-left:2px solid transparent}
.sidebar nav a:hover,.sidebar nav a.active{color:var(--text);background:var(--bg3);border-left-color:var(--accent)}
.sidebar nav a .badge{background:var(--red);color:#fff;font-size:10px;padding:1px 6px;border-radius:10px;margin-left:auto}
.main{margin-left:var(--sidebar-w);flex:1;padding:24px 28px;min-width:0}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.topbar h2{font-size:22px;font-weight:700;letter-spacing:-.5px}
.topbar .actions{display:flex;gap:8px;align-items:center}
.topbar button{background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:7px 14px;border-radius:6px;font-size:12px;cursor:pointer;transition:all .15s}
.topbar button:hover{border-color:var(--text3)}
.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
.stat-card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px}
.stat-card .label{font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.stat-card .value{font-size:24px;font-weight:700;letter-spacing:-1px}
.stat-card .sub{font-size:11px;margin-top:4px}
.green{color:var(--green)}.red{color:var(--red)}.yellow{color:var(--yellow)}.blue{color:var(--blue)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:24px}
.panel{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius)}
.panel .head{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid var(--border);font-size:13px;font-weight:600}
.panel .body{padding:16px 18px;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border);color:var(--text3);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.3px}
td{padding:9px 12px;border-bottom:1px solid var(--border);font-size:12px}
.tag{display:inline-block;padding:3px 8px;border-radius:4px;font-size:11px;font-weight:500}
.tag-success{background:#064e3b;color:#6ee7b7}
.tag-warn{background:#78350f;color:#fbbf24}
.tag-danger{background:#7f1d1d;color:#fca5a5}
.tag-info{background:#1e3a5f;color:#93c5fd}
.chart-area{width:100%;height:200px}
.chart-area canvas{width:100%!important;height:100%!important}
.muted{color:var(--text3);font-size:11px}
.pb-4{padding-bottom:16px}
.tabs{display:flex;gap:2px;margin-bottom:16px}
.tab{padding:8px 16px;border-radius:6px;font-size:12px;cursor:pointer;color:var(--text2);border:1px solid transparent;background:transparent;transition:all .15s}
.tab.active,.tab:hover{color:var(--text);background:var(--bg3);border-color:var(--border)}
@media(max-width:768px){.sidebar{display:none}.main{margin-left:0}.stats-grid{grid-template-columns:repeat(2,1fr)}.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>

<!-- Sidebar -->
<div class="sidebar">
  <div class="logo">KK API 运营中心</div>
  <nav>
    <a href="#dashboard" class="active" onclick="navTo('dashboard',this)">📊 仪表盘</a>
    <a href="#users" onclick="navTo('users',this)">👥 用户管理</a>
    <a href="#suppliers" onclick="navTo('suppliers',this)">🔌 供应商</a>
    <a href="#models" onclick="navTo('models',this)">🧠 模型管理</a>
    <a href="#orders" onclick="navTo('orders',this)">💳 订单系统</a>
    <a href="#finance" onclick="navTo('finance',this)">💰 财务中心</a>
    <a href="#alerts" onclick="navTo('alerts',this)">🔔 告警中心<span class="badge" id="alertBadge">0</span></a>
    <a href="#detection" onclick="navTo('detection',this)">🔍 模型检测</a>
    <a href="#monitor" onclick="navTo('monitor',this)">📡 实时监控</a>
  </nav>
</div>

<!-- Main -->
<div class="main" id="mainContent">

<!-- Dashboard -->
<div id="page-dashboard">
  <div class="topbar"><h2>运营仪表盘</h2><div class="actions"><button onclick="refreshAll()">🔄 刷新</button></div></div>

  <div class="stats-grid" id="statsGrid"></div>

  <div class="grid2">
    <div class="panel"><div class="head">📈 收入趋势（近7天）</div><div class="body"><div class="chart-area"><canvas id="chartRevenue"></canvas></div></div></div>
    <div class="panel"><div class="head">🔥 热门模型 TOP5</div><div class="body" id="topModels"></div></div>
  </div>

  <div class="grid2">
    <div class="panel"><div class="head">👑 用户消费排行</div><div class="body" id="topUsers"></div></div>
    <div class="panel"><div class="head">🔔 最近告警</div><div class="body" id="recentAlerts"></div></div>
  </div>
</div>

<!-- Users -->
<div id="page-users" style="display:none">
  <div class="topbar"><h2>用户管理</h2></div>
  <div class="panel"><div class="head">所有用户</div><div class="body" id="usersTable"></div></div>
</div>

<!-- Suppliers -->
<div id="page-suppliers" style="display:none">
  <div class="topbar"><h2>供应商管理</h2><div class="actions"><button onclick="healthCheck()">🩺 健康检查</button><button onclick="loadCostAnalysis()">📊 成本分析</button></div></div>
  <div class="panel"><div class="head">供应商状态</div><div class="body" id="suppliersTable"></div></div>
</div>

<!-- Models -->
<div id="page-models" style="display:none">
  <div class="topbar"><h2>模型管理</h2></div>
  <div class="panel"><div class="head">定价 & 状态</div><div class="body" id="modelsTable"></div></div>
</div>

<!-- Orders -->
<div id="page-orders" style="display:none">
  <div class="topbar"><h2>订单系统</h2></div>
  <div class="panel"><div class="head">充值订单</div><div class="body" id="ordersTable"></div></div>
</div>

<!-- Finance -->
<div id="page-finance" style="display:none">
  <div class="topbar"><h2>财务中心</h2></div>
  <div class="stats-grid" id="financeStats"></div>
  <div class="panel"><div class="head">📈 收入趋势（近30天）</div><div class="body"><div class="chart-area"><canvas id="chartFinance"></canvas></div></div></div>
  <div class="panel" id="costAnalysisPanel" style="display:none"><div class="head">📊 供应商成本分析</div><div class="body" id="costAnalysis"></div></div>
</div>

<!-- Alerts -->
<div id="page-alerts" style="display:none">
  <div class="topbar"><h2>告警中心</h2></div>
  <div class="panel"><div class="head">所有告警</div><div class="body" id="alertsTable"></div></div>
</div>

<!-- Detection -->
<div id="page-detection" style="display:none">
  <div class="topbar"><h2>模型真实性检测</h2><div class="actions"><button onclick="runDetection()">▶ 运行检测</button></div></div>
  <div class="panel"><div class="head">检测记录</div><div class="body" id="detectionTable"></div></div>
</div>

<!-- Monitor -->
<div id="page-monitor" style="display:none">
  <div class="topbar"><h2>实时监控</h2></div>
  <div class="stats-grid" id="monitorStats"></div>
  <div class="panel"><div class="head">请求日志（最近50条）</div><div class="body" id="monitorLogs"></div></div>
</div>

</div>

<script>
const BASE='',AUTH='Bearer admin123',H={'Authorization':AUTH,'Content-Type':'application/json'}
let alertCount=0

async function refreshAll(){
  const d=await (await fetch(BASE+'/admin/api/dashboard',{headers:H})).json()
  renderDashboard(d)
  document.getElementById('alertBadge').textContent=d.recent_alerts?.length||0
}

function navTo(page,el){
  document.querySelectorAll('.sidebar nav a').forEach(a=>a.classList.remove('active'))
  if(el)el.classList.add('active')
  document.querySelectorAll('[id^="page-"]').forEach(p=>p.style.display='none')
  const target=document.getElementById('page-'+page)
  if(target)target.style.display='block'
  if(page==='dashboard')refreshAll()
  if(page==='users')loadUsers()
  if(page==='suppliers')loadSuppliers()
  if(page==='models')loadModels()
  if(page==='orders')loadOrders()
  if(page==='finance')loadFinance()
  if(page==='alerts')loadAlerts()
  if(page==='detection')loadDetections()
  if(page==='monitor')loadMonitor()
}

function renderDashboard(d){
  const s=document.getElementById('statsGrid')
  s.innerHTML=`
    <div class="stat-card"><div class="label">今日收入</div><div class="value green">¥${(d.today.revenue/100).toFixed(2)}</div><div class="sub muted">今日利润 ¥${(d.today.profit/100).toFixed(2)}</div></div>
    <div class="stat-card"><div class="label">本月收入</div><div class="value">¥${(d.month.revenue/100).toFixed(2)}</div><div class="sub muted">本月利润 ¥${(d.month.profit/100).toFixed(2)}</div></div>
    <div class="stat-card"><div class="label">总收入</div><div class="value">¥${(d.total.revenue/100).toFixed(2)}</div><div class="sub muted">总利润 ¥${(d.total.profit/100).toFixed(2)}</div></div>
    <div class="stat-card"><div class="label">活跃 / 在线</div><div class="value blue">${d.active_keys} <span style="font-size:14px;color:var(--text3)">/ ${d.total_customers}</span></div><div class="sub"><span class="tag tag-success">成功率 ${d.success_rate}%</span></div></div>`

  // Chart
  const ctx=document.getElementById('chartRevenue')?.getContext('2d')
  if(ctx&&d.trend){
    drawChart(ctx,d.trend.map(t=>t.d),d.trend.map(t=>t.rev/100),d.trend.map(t=>t.profit/100))
  }

  // Top models
  const tm=document.getElementById('topModels')
  tm.innerHTML=d.top_models?.map(m=>`<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)"><span>${m.model}</span><span class="muted">${m.c}次 · ¥${(m.r/100).toFixed(2)}</span></div>`).join('')||'暂无数据'

  // Top users
  const tu=document.getElementById('topUsers')
  tu.innerHTML=d.top_users?.map(u=>`<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)"><span>${u.name}</span><span class="muted">¥${(u.r/100).toFixed(2)}</span></div>`).join('')||'暂无数据'

  // Alerts
  document.getElementById('recentAlerts').innerHTML=d.recent_alerts?.map(a=>{
    const sc=a.severity==='critical'?'tag-danger':a.severity==='warning'?'tag-warn':'tag-info'
    return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)"><span><span class="tag ${sc}">${a.severity}</span> ${a.title}</span><span class="muted">${a.created_at}</span></div>`
  }).join('')||'<div class="muted">无告警 ✅</div>'

  alertCount=d.recent_alerts?.length||0
  document.getElementById('alertBadge').textContent=alertCount||''
}

// Simple canvas chart
function drawChart(ctx,labels,data1,data2){
  const w=ctx.canvas.width=ctx.canvas.parentElement.clientWidth
  const h=ctx.canvas.height=200
  const pad=30,xStep=(w-pad*2)/(labels.length-1||1)
  const max=Math.max(...data1,1)
  ctx.clearRect(0,0,w,h)

  // Grid
  ctx.strokeStyle='#2a2a2a';ctx.lineWidth=1
  for(let i=0;i<4;i++){const y=pad+(h-pad*2)/3*i;ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(w-pad,y);ctx.stroke()}

  // Line 1 - Revenue
  ctx.strokeStyle='#60a5fa';ctx.lineWidth=2
  ctx.beginPath();data1.forEach((v,i)=>{const x=pad+i*xStep,y=pad+(h-pad*2)*(1-v/max);i===0?ctx.moveTo(x,y):ctx.lineTo(x,y)});ctx.stroke()

  // Line 2 - Profit
  ctx.strokeStyle='#4ade80';ctx.lineWidth=2
  ctx.beginPath();data2.forEach((v,i)=>{const x=pad+i*xStep,y=pad+(h-pad*2)*(1-v/max);i===0?ctx.moveTo(x,y):ctx.lineTo(x,y)});ctx.stroke()

  // Labels
  ctx.fillStyle='#555';ctx.font='10px Inter,sans-serif';ctx.textAlign='center'
  labels.forEach((l,i)=>{ctx.fillText(l.slice(5),pad+i*xStep,h-8)})
}

// Loaders
async function loadUsers(){
  const r=await (await fetch(BASE+'/admin/api/customers',{headers:H})).json()
  document.getElementById('usersTable').innerHTML=`<table><tr><th>ID</th><th>名称</th><th>余额</th><th>累计消费</th><th>调用次数</th><th>风险</th><th>操作</th></tr>${r.map(c=>`<tr><td>${c.id}</td><td>${c.name}</td><td>¥${(c.balance_cents/100).toFixed(2)}</td><td>¥${((c.total_profit||0)/100).toFixed(2)}</td><td>${c.key_count}</td><td><span class="tag tag-success">低</span></td><td><button class="tab" onclick="alert('封禁/解封 功能')" style="font-size:11px">管理</button></td></tr>`).join('')}</table>`
}

async function loadSuppliers(){
  const r=await (await fetch(BASE+'/admin/api/suppliers',{headers:H})).json()
  document.getElementById('suppliersTable').innerHTML=`<table><tr><th>供应商</th><th>状态</th><th>成功率</th><th>延迟</th><th>余额</th><th>故障</th><th>操作</th></tr>${r.map(s=>`<tr><td>${s.name}</td><td>${s.is_active?'<span class="tag tag-success">在线</span>':'<span class="tag tag-danger">离线</span>'}</td><td>${s.success_rate}%</td><td>${s.avg_latency}ms</td><td>¥${(s.balance_cents/100).toFixed(2)}</td><td>${s.incidents||0}</td><td><button class="tab" onclick="toggleSupplier(${s.id})" style="font-size:11px">${s.is_active?'停用':'启用'}</button></td></tr>`).join('')}</table>`
}

async function toggleSupplier(id){
  await fetch(BASE+'/admin/api/suppliers/'+id+'/toggle',{method:'POST',headers:H})
  loadSuppliers()
}

async function loadModels(){
  const r=await (await fetch(BASE+'/admin/api/pricing',{headers:H})).json()
  document.getElementById('modelsTable').innerHTML=`<table><tr><th>模型</th><th>售价输入</th><th>售价输出</th><th>成本输入</th><th>成本输出</th><th>利润率</th></tr>${r.filter(p=>p.model_pattern!=='default').map(p=>{const m=((p.output_price_per_1k-p.output_cost_per_1k)/(p.output_cost_per_1k||0.001)*100).toFixed(0);return`<tr><td><strong>${p.model_pattern}</strong></td><td>¥${p.input_price_per_1k.toFixed(4)}</td><td>¥${p.output_price_per_1k.toFixed(4)}</td><td>¥${p.input_cost_per_1k.toFixed(4)}</td><td>¥${p.output_cost_per_1k.toFixed(4)}</td><td><span class="tag ${m>50?'tag-success':m>20?'tag-warn':'tag-danger'}">${m}%</span></td></tr>`}).join('')}</table>`
}

async function loadOrders(){
  const r=await (await fetch(BASE+'/admin/api/topup-orders',{headers:H})).json()
  document.getElementById('ordersTable').innerHTML=`<table><tr><th>ID</th><th>客户</th><th>金额</th><th>渠道</th><th>状态</th><th>时间</th><th>操作</th></tr>${r.map(o=>`<tr><td>${o.id}</td><td>${o.customer_name}</td><td>¥${(o.amount_cents/100).toFixed(2)}</td><td>${o.payment_method||'-'}</td><td><span class="tag ${o.status==='confirmed'?'tag-success':o.status==='submitted'?'tag-warn':'tag-info'}">${o.status}</span></td><td>${o.created_at}</td><td>${o.status==='submitted'?`<button class="tab" onclick="approveOrder(${o.id})" style="font-size:11px">批准</button>`:'-'}</td></tr>`).join('')}</table>`
}

async function approveOrder(id){
  await fetch(BASE+'/admin/api/topup-orders/approve',{method:'POST',headers:H,body:JSON.stringify({order_id:id})})
  loadOrders()
}

async function loadFinance(){
  const r=await (await fetch(BASE+'/admin/api/finance',{headers:H})).json()
  document.getElementById('financeStats').innerHTML=`
    <div class="stat-card"><div class="label">总充值</div><div class="value blue">¥${(r.total_topup/100).toFixed(2)}</div></div>
    <div class="stat-card"><div class="label">总收入</div><div class="value">¥${(r.total_revenue/100).toFixed(2)}</div></div>
    <div class="stat-card"><div class="label">总成本</div><div class="value red">¥${(r.total_cost/100).toFixed(2)}</div></div>
    <div class="stat-card"><div class="label">净利润率</div><div class="value green">${r.profit_margin}%</div></div>`
  const ctx=document.getElementById('chartFinance')?.getContext('2d')
  if(ctx&&r.trend){drawChart(ctx,r.trend.map(t=>t.d),r.trend.map(t=>t.rev/100),r.trend.map(t=>(t.rev-t.cost)/100))}
}

async function loadAlerts(){
  const r=await (await fetch(BASE+'/admin/api/alerts',{headers:H})).json()
  document.getElementById('alertsTable').innerHTML=`<table><tr><th>类型</th><th>严重度</th><th>内容</th><th>时间</th><th>状态</th><th>操作</th></tr>${r.map(a=>`<tr><td>${a.alert_type}</td><td><span class="tag ${a.severity==='critical'?'tag-danger':a.severity==='warning'?'tag-warn':'tag-info'}">${a.severity}</span></td><td>${a.title}</td><td>${a.created_at}</td><td>${a.resolved?'<span class="tag tag-success">已解决</span>':'<span class="tag tag-warn">未解决</span>'}</td><td>${!a.resolved?`<button class="tab" onclick="resolveAlert(${a.id})" style="font-size:11px">解决</button>`:'-'}</td></tr>`).join('')}</table>`
}

async function resolveAlert(id){await fetch(BASE+'/admin/api/alerts/'+id+'/resolve',{method:'POST',headers:H});loadAlerts()}

async function loadDetections(){
  const r=await (await fetch(BASE+'/admin/api/detections',{headers:H})).json()
  document.getElementById('detectionTable').innerHTML=`<table><tr><th>供应商</th><th>模型</th><th>评分</th><th>风险</th><th>结论</th><th>时间</th></tr>${r.map(d=>`<tr><td>${d.supplier_name}</td><td>${d.model_name}</td><td>${d.authenticity_score}%</td><td><span class="tag ${d.risk_level.includes('高')?'tag-danger':d.risk_level.includes('中')?'tag-warn':'tag-success'}">${d.risk_level}</span></td><td>${d.verdict}</td><td>${d.created_at}</td></tr>`).join('')}</table>`
}

async function runDetection(){
  const r=await (await fetch(BASE+'/admin/api/detections/run',{method:'POST',headers:H})).json()
  if(r.ok){loadDetections();alert('检测完成！'+r.results.length+'个模型已评估')}
}

async function loadMonitor(){
  document.getElementById('monitorStats').innerHTML=`
    <div class="stat-card"><div class="label">在线用户</div><div class="value blue" id="liveUsers">-</div></div>
    <div class="stat-card"><div class="label">请求/分钟</div><div class="value" id="liveQPS">-</div></div>
    <div class="stat-card"><div class="label">成功率</div><div class="value green" id="liveRate">-</div></div>
    <div class="stat-card"><div class="label">平均延迟</div><div class="value" id="liveLat">-</div></div>`
  const r=await (await fetch(BASE+'/admin/api/stats',{headers:H})).json()
  document.getElementById('liveUsers').textContent=r.active_keys||0
  document.getElementById('liveQPS').textContent='--'
  document.getElementById('liveRate').textContent='99.9%'
  document.getElementById('liveLat').textContent='<100ms'
}

// Init
async function healthCheck(){const r=await fetch(BASE+'/admin/api/suppliers/health-check',{method:'POST',headers:H});const d=await r.json();alert('健康检查完成：'+d.results.length+'个供应商已检测');loadSuppliers()}
async function loadCostAnalysis(){const panel=document.getElementById('costAnalysisPanel');panel.style.display='block';const r=await(await fetch(BASE+'/admin/api/suppliers/cost-analysis',{headers:H})).json();document.getElementById('costAnalysis').innerHTML=`<table><tr><th>供应商</th><th>调用次数</th><th>总成本</th><th>总收入</th><th>利润</th><th>成功率</th><th>延迟</th></tr>${r.map(s=>`<tr><td>${s.supplier_name}</td><td>${s.calls}</td><td>¥${(s.total_cost/100).toFixed(2)}</td><td>¥${(s.total_revenue/100).toFixed(2)}</td><td class="${s.total_profit>0?'green':'red'}">¥${(s.total_profit/100).toFixed(2)}</td><td>${s.success_rate}%</td><td>${s.avg_latency}ms</td></tr>`).join('')}</table>`}

refreshAll()
</script>
</body>
</html>"""
