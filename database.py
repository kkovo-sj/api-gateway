"""
SQLite 数据库 —— 企业级多表结构
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "gateway.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        -- ===== 客户 =====
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT DEFAULT '',
            balance_cents INTEGER NOT NULL DEFAULT 0,
            total_spent_cents INTEGER NOT NULL DEFAULT 0,
            total_calls INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            risk_level TEXT DEFAULT 'low',
            tags TEXT DEFAULT '',
            is_banned INTEGER NOT NULL DEFAULT 0,
            last_login TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ===== API Keys =====
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            key TEXT NOT NULL UNIQUE,
            name TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            last_used TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        -- ===== 定价 =====
        CREATE TABLE IF NOT EXISTS pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_pattern TEXT NOT NULL,
            input_price_per_1k REAL NOT NULL,
            output_price_per_1k REAL NOT NULL,
            input_cost_per_1k REAL NOT NULL,
            output_cost_per_1k REAL NOT NULL,
            cache_price_per_1k REAL DEFAULT 0,
            supplier_name TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER DEFAULT 0
        );

        -- ===== 用量记录 =====
        CREATE TABLE IF NOT EXISTS usage_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            api_key_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            supplier_name TEXT DEFAULT '',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            revenue_cents INTEGER NOT NULL DEFAULT 0,
            cost_cents INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        -- ===== 充值订单 =====
        CREATE TABLE IF NOT EXISTS topup_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_method TEXT DEFAULT '',
            payment_ref TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confirmed_at TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        -- ===== 供应商 =====
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            api_key TEXT DEFAULT '',
            base_url TEXT DEFAULT '',
            balance_cents INTEGER DEFAULT 0,
            total_cost_cents INTEGER DEFAULT 0,
            success_rate REAL DEFAULT 100.0,
            avg_latency_ms INTEGER DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            priority INTEGER DEFAULT 0,
            auto_switch INTEGER DEFAULT 1,
            last_health_check TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ===== 供应商故障记录 =====
        CREATE TABLE IF NOT EXISTS supplier_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL,
            error_type TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            resolved INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        );

        -- ===== 告警 =====
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            title TEXT NOT NULL,
            message TEXT DEFAULT '',
            resolved INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ===== 模型检测日志 =====
        CREATE TABLE IF NOT EXISTS model_detection_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_name TEXT DEFAULT '',
            model_name TEXT NOT NULL,
            authenticity_score REAL DEFAULT 100.0,
            similarity_score REAL DEFAULT 0,
            risk_level TEXT DEFAULT 'low',
            verdict TEXT DEFAULT '高度可信',
            detail TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ===== 管理员日志 =====
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user TEXT DEFAULT 'admin',
            action TEXT NOT NULL,
            detail TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ===== 系统配置 =====
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
    """)
    conn.commit()

    # 插入默认供应商
    existing_s = conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    if existing_s == 0:
        conn.executescript("""
            INSERT INTO suppliers (name, api_key, base_url, priority) VALUES ('DeepSeek官方', '', 'https://api.deepseek.com/v1', 1);
            INSERT INTO suppliers (name, api_key, base_url, priority) VALUES ('88API中转', '', 'https://api.88api.shop/v1', 2);
            INSERT INTO suppliers (name, api_key, base_url, priority) VALUES ('Qwen官方', '', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 3);
            INSERT INTO suppliers (name, api_key, base_url, priority) VALUES ('Zhipu官方', '', 'https://open.bigmodel.cn/api/paas/v4', 4);
            INSERT INTO suppliers (name, api_key, base_url, priority) VALUES ('Moonshot官方', '', 'https://api.moonshot.cn/v1', 5);
        """)
        conn.commit()

    # 定价初始化（仅首次）
    existing = conn.execute("SELECT COUNT(*) FROM pricing").fetchone()[0]
    if existing == 0:
        conn.executescript("""
            INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k, supplier_name) VALUES
            ('gpt-5', 0.006, 0.030, 0.004, 0.024, '88API中转'),
            ('gpt-4o', 0.004, 0.015, 0.0025, 0.010, '88API中转'),
            ('gpt-4o-mini', 0.001, 0.003, 0.00015, 0.0006, '88API中转'),
            ('o3', 0.002, 0.006, 0.0011, 0.0044, '88API中转'),
            ('claude-opus', 0.030, 0.140, 0.025, 0.125, '88API中转'),
            ('claude-sonnet', 0.018, 0.085, 0.015, 0.075, '88API中转'),
            ('claude-haiku', 0.007, 0.030, 0.005, 0.025, '88API中转'),
            ('deepseek-chat', 0.003, 0.005, 0.002, 0.003, 'DeepSeek官方'),
            ('deepseek-reasoner', 0.003, 0.005, 0.002, 0.003, 'DeepSeek官方'),
            ('deepseek-v4', 0.005, 0.015, 0.003, 0.010, 'DeepSeek官方'),
            ('deepseek-r1', 0.003, 0.008, 0.002, 0.005, 'DeepSeek官方'),
            ('qwen-turbo', 0.001, 0.002, 0.0003, 0.0006, 'Qwen官方'),
            ('qwen-plus', 0.002, 0.006, 0.0008, 0.0048, 'Qwen官方'),
            ('qwen3-max', 0.005, 0.015, 0.003, 0.010, 'Qwen官方'),
            ('qwq', 0.002, 0.006, 0.001, 0.004, 'Qwen官方'),
            ('glm-4-flash', 0.0005, 0.0005, 0.0001, 0.0001, 'Zhipu官方'),
            ('glm-5', 0.004, 0.010, 0.002, 0.006, 'Zhipu官方'),
            ('moonshot-v1', 0.004, 0.012, 0.002, 0.01, 'Moonshot官方'),
            ('kimi-k2', 0.006, 0.018, 0.003, 0.012, 'Moonshot官方'),
            ('grok-', 0.002, 0.004, 0.0013, 0.0025, '88API中转'),
            ('default', 0.005, 0.015, 0.003, 0.010, '');
        """)
        conn.commit()

    conn.close()
