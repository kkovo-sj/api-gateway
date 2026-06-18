"""
企业级管理后台 API —— 仪表盘、供应商、告警、检测、财务
"""
from fastapi import APIRouter, Request, HTTPException
from database import get_db
from config import settings

router = APIRouter(prefix="/admin/api", tags=["📊 管理后台"])


def _auth(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {settings.admin_password}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


# ========== 总览仪表盘 ==========

@router.get("/dashboard")
async def get_dashboard(request: Request):
    _auth(request)
    db = get_db()
    try:
        now = db.execute("SELECT datetime('now') as n, date('now') as d").fetchone()
        today = now["d"]

        # 收入利润
        today_rev = db.execute("SELECT COALESCE(SUM(revenue_cents),0) r FROM usage_records WHERE date(created_at)=?", (today,)).fetchone()["r"]
        month_rev = db.execute("SELECT COALESCE(SUM(revenue_cents),0) r FROM usage_records WHERE strftime('%Y-%m',created_at)=strftime('%Y-%m','now')").fetchone()["r"]
        total_rev = db.execute("SELECT COALESCE(SUM(revenue_cents),0) r FROM usage_records").fetchone()["r"]
        today_cost = db.execute("SELECT COALESCE(SUM(cost_cents),0) r FROM usage_records WHERE date(created_at)=?", (today,)).fetchone()["r"]
        month_cost = db.execute("SELECT COALESCE(SUM(cost_cents),0) r FROM usage_records WHERE strftime('%Y-%m',created_at)=strftime('%Y-%m','now')").fetchone()["r"]
        total_cost = db.execute("SELECT COALESCE(SUM(cost_cents),0) r FROM usage_records").fetchone()["r"]

        # 活跃数据
        active_keys = db.execute("SELECT COUNT(*) c FROM api_keys WHERE is_active=1 AND last_used > datetime('now','-24 hours')").fetchone()["c"]
        total_customers = db.execute("SELECT COUNT(*) c FROM customers WHERE is_banned=0").fetchone()["c"]
        today_tokens = db.execute("SELECT COALESCE(SUM(prompt_tokens+completion_tokens),0) t FROM usage_records WHERE date(created_at)=?", (today,)).fetchone()["t"]
        month_tokens = db.execute("SELECT COALESCE(SUM(prompt_tokens+completion_tokens),0) t FROM usage_records WHERE strftime('%Y-%m',created_at)=strftime('%Y-%m','now')").fetchone()["t"]
        success_rate = db.execute("SELECT ROUND(100.0*SUM(CASE WHEN status='success' THEN 1 ELSE 0 END)/MAX(COUNT(*),1),1) r FROM usage_records WHERE created_at > datetime('now','-24 hours')").fetchone()["r"]

        # 供应商余额
        supplier_balance = db.execute("SELECT COALESCE(SUM(balance_cents),0) b FROM suppliers").fetchone()["b"]

        # 最近告警
        alerts = db.execute("SELECT * FROM alerts WHERE resolved=0 ORDER BY created_at DESC LIMIT 5").fetchall()

        # 热门模型
        top_models = db.execute("SELECT model,COUNT(*) c,SUM(revenue_cents) r FROM usage_records WHERE created_at > datetime('now','-7 days') GROUP BY model ORDER BY c DESC LIMIT 5").fetchall()

        # 用户消费排行
        top_users = db.execute("SELECT c.name,SUM(u.revenue_cents) r FROM usage_records u JOIN customers c ON c.id=u.customer_id WHERE u.created_at > datetime('now','-7 days') GROUP BY u.customer_id ORDER BY r DESC LIMIT 5").fetchall()

        # 收入趋势（近7天）
        trend = db.execute("""
            SELECT date(created_at) d, SUM(revenue_cents) rev, SUM(cost_cents) cost, SUM(revenue_cents-cost_cents) profit
            FROM usage_records WHERE created_at > datetime('now','-7 days')
            GROUP BY d ORDER BY d
        """).fetchall()

        return {
            "today": {
                "revenue": today_rev, "cost": today_cost, "profit": today_rev - today_cost,
                "tokens": today_tokens
            },
            "month": {
                "revenue": month_rev, "cost": month_cost, "profit": month_rev - month_cost,
                "tokens": month_tokens
            },
            "total": {
                "revenue": total_rev, "cost": total_cost, "profit": total_rev - total_cost
            },
            "active_keys": active_keys,
            "total_customers": total_customers,
            "success_rate": success_rate,
            "supplier_balance": supplier_balance,
            "recent_alerts": [dict(a) for a in alerts],
            "top_models": [dict(m) for m in top_models],
            "top_users": [dict(u) for u in top_users],
            "trend": [dict(t) for t in trend],
        }
    finally:
        db.close()


# ========== 供应商管理 ==========

@router.get("/suppliers")
async def get_suppliers(request: Request):
    _auth(request)
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM suppliers ORDER BY priority").fetchall()
        suppliers = []
        for s in rows:
            r = dict(s)
            # 成功率、延迟
            stats = db.execute("SELECT COALESCE(ROUND(100.0*SUM(CASE WHEN status='success' THEN 1 ELSE 0 END)/MAX(COUNT(*),1),1),100) rate, COALESCE(AVG(latency_ms),0) lat FROM usage_records WHERE supplier_name=? AND created_at > datetime('now','-24 hours')", (s["name"],)).fetchone()
            r["success_rate"] = stats["rate"]
            r["avg_latency"] = stats["lat"]
            # 故障次数
            r["incidents"] = db.execute("SELECT COUNT(*) c FROM supplier_incidents WHERE supplier_id=? AND resolved=0", (s["id"],)).fetchone()["c"]
            suppliers.append(r)
        return suppliers
    finally:
        db.close()


@router.post("/suppliers/{sid}/toggle")
async def toggle_supplier(sid: int, request: Request):
    _auth(request)
    db = get_db()
    try:
        s = db.execute("SELECT is_active FROM suppliers WHERE id=?", (sid,)).fetchone()
        if not s: return {"ok": False, "error": "不存在"}
        db.execute("UPDATE suppliers SET is_active=? WHERE id=?", (0 if s["is_active"] else 1, sid))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ========== 告警中心 ==========

@router.get("/alerts")
async def get_alerts(request: Request):
    _auth(request)
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.post("/alerts/{aid}/resolve")
async def resolve_alert(aid: int, request: Request):
    _auth(request)
    db = get_db()
    db.execute("UPDATE alerts SET resolved=1 WHERE id=?", (aid,))
    db.commit()
    db.close()
    return {"ok": True}


# ========== 模型检测 ==========

@router.get("/detections")
async def get_detections(request: Request):
    _auth(request)
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM model_detection_logs ORDER BY created_at DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.post("/detections/run")
async def run_detection(request: Request):
    """模拟运行一次模型真实性检测"""
    _auth(request)
    import random
    db = get_db()
    try:
        models = db.execute("SELECT DISTINCT model_pattern, supplier_name FROM pricing WHERE model_pattern != 'default'").fetchall()
        results = []
        for m in models:
            score = round(random.uniform(85, 100), 1)
            risk = "低风险" if score > 95 else ("中风险" if score > 88 else "高风险")
            db.execute("INSERT INTO model_detection_logs (supplier_name, model_name, authenticity_score, risk_level, verdict) VALUES (?,?,?,?,?)",
                       (m["supplier_name"], m["model_pattern"], score, risk, "高度可信" if score > 95 else "可能降级"))
            results.append({"model": m["model_pattern"], "score": score, "risk": risk})
        db.commit()
        return {"ok": True, "results": results}
    finally:
        db.close()


# ========== 财务中心 ==========

@router.get("/finance")
async def get_finance(request: Request):
    _auth(request)
    db = get_db()
    try:
        total_rev = db.execute("SELECT COALESCE(SUM(revenue_cents),0) r FROM usage_records").fetchone()["r"]
        total_cost = db.execute("SELECT COALESCE(SUM(cost_cents),0) r FROM usage_records").fetchone()["r"]
        total_topup = db.execute("SELECT COALESCE(SUM(amount_cents),0) r FROM topup_orders WHERE status='confirmed'").fetchone()["r"]

        # 按天趋势（近30天）
        trend = db.execute("""
            SELECT date(created_at) d, SUM(revenue_cents) rev, SUM(cost_cents) cost
            FROM usage_records WHERE created_at > datetime('now','-30 days')
            GROUP BY d ORDER BY d
        """).fetchall()

        return {
            "total_revenue": total_rev,
            "total_cost": total_cost,
            "total_topup": total_topup,
            "gross_profit": total_rev - total_cost,
            "net_profit": total_rev - total_cost,
            "profit_margin": round(100 * (total_rev - total_cost) / max(total_rev, 1), 1),
            "trend": [dict(t) for t in trend],
        }
    finally:
        db.close()


# ========== 管理员日志 ==========

@router.get("/admin-logs")
async def get_admin_logs(request: Request):
    _auth(request)
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM admin_logs ORDER BY created_at DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


# ========== 供应商利润分析 ==========

@router.get("/supplier-profit")
async def get_supplier_profit(request: Request):
    _auth(request)
    db = get_db()
    try:
        rows = db.execute("""
            SELECT supplier_name, COUNT(*) calls, SUM(revenue_cents) rev, SUM(cost_cents) cost,
                   SUM(revenue_cents-cost_cents) profit
            FROM usage_records WHERE supplier_name != ''
            GROUP BY supplier_name ORDER BY profit DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()
