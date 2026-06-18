# API 中转站 项目文档

## 项目结构
```
api-gateway/
├── main.py              # 入口，uvicorn 启动
├── config.py            # .env 配置读取
├── database.py          # SQLite 初始化 + 定价
├── models/              # Pydantic 数据模型
├── providers/           # LLM 上游适配器
├── routes/              # API 路由
│   ├── chat.py          # /v1/chat/completions
│   ├── models.py        # /v1/models
│   ├── portal.py        # 首页 + 客户门户 + 充值
│   ├── admin.py         # 管理后台
│   └── pay.py           # xorpay 回调
├── services/            # 业务逻辑
│   ├── router.py        # 模型路由
│   ├── billing.py       # 计费扣费
│   └── payment.py       # xorpay 支付
├── middleware/           # 鉴权中间件
├── static/              # 收款码图片
├── .env                 # 实际配置（不要提交）
└── .env.example         # 配置模板
```

## 端口
- `http://localhost:8000` — 首页
- `/portal` — 客户门户
- `/admin/dashboard` — 管理后台 (密码 admin123)
- `/docs` — API 文档
- `/v1/chat/completions` — 聊天接口
- `/v1/models` — 模型列表

## 启动
```bash
cd /Users/kk/api-gateway
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

## 模型路由
全部国外模型 + 带日期国产模型 → 88API (`TRANSIT_BASE_URL`)
国产直连 → DeepSeek/Qwen/GLM/Moonshot 官方

## 充值流程
1. 顾客在门户创建订单
2. 扫码付款（或走 xorpay 自动回调）
3. 管理后台审批 → 余额自动到账

## 定价
所有价格在 database.py 的 init_db 函数中，含成本价和售价。
修改价格后需删 gateway.db 重启。

## 注意
- 不要提交 .env 文件
- 删 gateway.db 会清空所有客户数据和定价
- QwQ 模型仅支持流式模式
