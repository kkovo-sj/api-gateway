"""
SQLite 数据库 —— 存储客户、API Key、定价、用量记录
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
    """初始化数据库表 + 默认数据"""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            key TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        CREATE TABLE IF NOT EXISTS pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_pattern TEXT NOT NULL,
            input_price_per_1k REAL NOT NULL,
            output_price_per_1k REAL NOT NULL,
            input_cost_per_1k REAL NOT NULL,
            output_cost_per_1k REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS usage_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            api_key_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            revenue_cents INTEGER NOT NULL DEFAULT 0,
            cost_cents INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

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
    """)
    conn.commit()

    # 清空旧定价并插入国内模型定价
    conn.execute("DELETE FROM pricing")

    conn.executescript("""
        -- === DeepSeek === 官方成本: 入¥2/出¥3每百万token → 入¥0.002/出¥0.003每1K
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('deepseek-chat', 0.003, 0.005, 0.002, 0.003);

        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('deepseek-reasoner', 0.003, 0.005, 0.002, 0.003);

        -- === 通义千问 === 官方成本: qwen-turbo 入¥0.3/出¥0.6 → 入¥0.0003/出¥0.0006每1K
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('qwen-turbo', 0.001, 0.002, 0.0003, 0.0006);

        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('qwen-plus', 0.002, 0.006, 0.0008, 0.0048);

        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('qwen-max', 0.004, 0.012, 0.0025, 0.01);

        -- === 智谱 GLM === GLM-4-Flash 官方免费，我们卖最低价
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('glm-4-flash', 0.0005, 0.0005, 0.0001, 0.0001);

        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('glm-4', 0.002, 0.002, 0.001, 0.001);

        -- === 月之暗面 Kimi === 官方成本: 入¥2/出¥10
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('moonshot-v1', 0.004, 0.012, 0.002, 0.01);

        -- === 国外模型（走 88API） ===
        -- GPT-5.5: 88API成本 ¥4/¥24/1M → ¥0.004/¥0.024 per 1K
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('gpt-5', 0.006, 0.030, 0.004, 0.024);
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('gpt-4o', 0.008, 0.025, 0.005, 0.018);
        -- GPT-4o-mini: 88API ¥0.15/¥0.60/1M
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('gpt-4o-mini', 0.001, 0.003, 0.00015, 0.0006);
        -- o3-mini: 88API ¥1.10/¥4.40/1M
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('o3', 0.002, 0.006, 0.0011, 0.0044);

        -- Claude Opus 4.8: 88API ¥25/¥125/1M
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('claude-opus', 0.030, 0.140, 0.025, 0.125);
        -- Claude Sonnet 4.6: 88API ¥15/¥75/1M
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('claude-sonnet', 0.018, 0.085, 0.015, 0.075);
        -- Claude Haiku 4.5: 88API ¥5/¥25/1M
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('claude-haiku', 0.007, 0.030, 0.005, 0.025);

        -- Grok 4.3: 88API ¥1.25/¥2.50/1M
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('grok-', 0.002, 0.004, 0.0013, 0.0025);

        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('deepseek-v4', 0.005, 0.015, 0.003, 0.010);
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('qwq', 0.002, 0.006, 0.001, 0.004);
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('gemini-', 0.008, 0.025, 0.005, 0.018);
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('qwen3-max', 0.005, 0.015, 0.003, 0.010);
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('glm-5', 0.004, 0.010, 0.002, 0.006);
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('kimi-k2', 0.006, 0.018, 0.003, 0.012);

        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('deepseek-r1', 0.003, 0.008, 0.002, 0.005);

        -- 默认定价
        INSERT INTO pricing (model_pattern, input_price_per_1k, output_price_per_1k, input_cost_per_1k, output_cost_per_1k)
        VALUES ('default', 0.005, 0.015, 0.003, 0.010);
    """)
    conn.commit()

    conn.close()
