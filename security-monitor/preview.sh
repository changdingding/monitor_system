#!/bin/bash
# 预览环境启动脚本

echo "启动预览服务..."

# 检查是否有构建产物
if [ ! -d "static" ]; then
    echo "错误：static 目录不存在，请先运行构建"
    exit 1
fi

# 使用 Python 启动 HTTP 服务器
cd static
python3 -m http.server 8080 &

echo "预览服务已启动：http://localhost:8080"
echo "按 Ctrl+C 停止服务"

# 等待用户中断
wait
