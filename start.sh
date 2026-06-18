#!/bin/bash
# ========================================
# LLM API Gateway 启动脚本
# ========================================

cd "$(dirname "$0")"

# 检查 .env 是否存在
if [ ! -f .env ]; then
    echo "⚠️  未找到 .env 文件，正在从 .env.example 创建..."
    cp .env.example .env
    echo "✅ 已创建 .env，请编辑填入你的 API Key:"
    echo "   nano .env"
    exit 1
fi

# 安装依赖
echo "📦 检查依赖..."
python3 -m pip install -r requirements.txt -q 2>/dev/null

echo ""
echo "🚀 启动 LLM API Gateway..."
echo ""
echo "  首页:       http://localhost:8000"
echo "  客户门户:   http://localhost:8000/portal"
echo "  管理后台:   http://localhost:8000/admin/dashboard"
echo "  API 文档:   http://localhost:8000/docs"
echo "  API 端点:   http://localhost:8000/v1/chat/completions"
echo ""

python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
