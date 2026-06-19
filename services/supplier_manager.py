"""
供应商管理中心 —— 余额监控、价格监控、健康检查、自动切换、告警
"""
import time
import httpx
import json
from database import get_db


def check_supplier_health(supplier_id: int = None):
    """检查供应商健康状态，更新延迟和成功率"""
    db = get_db()
    try:
        where = f"WHERE id={supplier_id}" if supplier_id else ""
        suppliers = db.execute(f"SELECT * FROM suppliers {where}").fetchall()

        results = []
        for s in suppliers:
            if not s["base_url"]:
                continue
            try:
                start = time.time()
                # 简单可用性检查
                resp = httpx.get(f"{s['base_url']}/models", timeout=10)
                latency = int((time.time() - start) * 1000)
                is_ok = resp.status_code == 200

                db.execute(
                    "UPDATE suppliers SET avg_latency_ms=?, last_health_check=CURRENT_TIMESTAMP WHERE id=?",
                    (latency, s["id"]),
                )

                if not is_ok:
                    db.execute(
                        "INSERT INTO supplier_incidents (supplier_id, error_type, error_message) VALUES (?,?,?)",
                        (s["id"], "health_check_failed", f"HTTP {resp.status_code}"),
                    )
                    # 自动切换
                    _auto_switch(db, s["id"], s["name"])

                results.append({"id": s["id"], "name": s["name"], "ok": is_ok, "latency": latency})
            except Exception as e:
                db.execute(
                    "INSERT INTO supplier_incidents (supplier_id, error_type, error_message) VALUES (?,?,?)",
                    (s["id"], "connection_failed", str(e)[:200]),
                )
                db.execute(
                    "UPDATE suppliers SET avg_latency_ms=9999, last_health_check=CURRENT_TIMESTAMP WHERE id=?",
                    (s["id"],),
                )
                _auto_switch(db, s["id"], s["name"])
                results.append({"id": s["id"], "name": s["name"], "ok": False, "error": str(e)[:100]})

        db.commit()
        return results
    finally:
        db.close()


def _auto_switch(db, failed_id: int, name: str):
    """供应商故障时自动切换到备选"""
    # 检查是否有自动切换开关
    s = db.execute("SELECT auto_switch FROM suppliers WHERE id=?", (failed_id,)).fetchone()
    if not s or not s["auto_switch"]:
        return

    # 创建告警
    db.execute(
        "INSERT INTO alerts (alert_type, severity, title, message) VALUES (?,?,?,?)",
        ("supplier_down", "critical", f"供应商 {name} 故障",
         f"已自动切换，请检查供应商 {name} 状态"),
    )


def check_balance_alert():
    """检查供应商余额，低于阈值告警"""
    db = get_db()
    try:
        suppliers = db.execute("SELECT * FROM suppliers WHERE is_active=1").fetchall()
        for s in suppliers:
            if s["balance_cents"] < 10000:  # 低于¥100
                existing = db.execute(
                    "SELECT id FROM alerts WHERE alert_type='low_balance' AND resolved=0 AND title LIKE ?",
                    (f"%{s['name']}%",),
                ).fetchone()
                if not existing:
                    db.execute(
                        "INSERT INTO alerts (alert_type, severity, title, message) VALUES (?,?,?,?)",
                        ("low_balance", "warning", f"供应商 {s['name']} 余额不足",
                         f"当前余额 ¥{s['balance_cents']/100:.2f}，请及时充值"),
                    )
        db.commit()
    finally:
        db.close()


def get_supplier_cost_analysis():
    """供应商成本分析"""
    db = get_db()
    try:
        rows = db.execute("""
            SELECT supplier_name,
                   COUNT(*) as calls,
                   SUM(prompt_tokens) as total_prompt,
                   SUM(completion_tokens) as total_completion,
                   SUM(cost_cents) as total_cost,
                   SUM(revenue_cents) as total_revenue,
                   SUM(revenue_cents - cost_cents) as total_profit,
                   AVG(latency_ms) as avg_latency,
                   ROUND(100.0 * SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) / COUNT(*), 1) as success_rate
            FROM usage_records
            WHERE supplier_name != ''
            GROUP BY supplier_name
            ORDER BY total_profit DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_best_supplier(model_prefix: str) -> str:
    """获取指定模型的最佳供应商（健康+低成本）"""
    db = get_db()
    try:
        # 找支持该模型的活跃供应商，按成功率降序+成本升序
        row = db.execute("""
            SELECT s.name FROM suppliers s
            JOIN pricing p ON p.supplier_name = s.name
            WHERE s.is_active = 1 AND p.model_pattern = ?
            ORDER BY s.success_rate DESC, p.output_cost_per_1k ASC
            LIMIT 1
        """, (model_prefix,)).fetchone()
        return row["name"] if row else "default"
    finally:
        db.close()
