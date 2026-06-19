#!/bin/bash
cd /root/api-gateway && git pull && pkill -f uvicorn && sleep 1 && nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 > /dev/null 2>&1 & sleep 2 && curl -s http://localhost:8000/health && echo " ✅ 更新完成" || echo " ❌ 失败"
