#!/usr/bin/env bash
# Linux Agent 一键安装（配置已写入，直接运行即可）
# 用法: sudo bash install.sh
set -euo pipefail

AGENT_SRC="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="/opt/security-monitor-agent"
SERVICE_NAME="security-monitor-agent"

if [ "$(id -u)" -ne 0 ]; then
  echo "请使用 root 或 sudo 运行："
  echo "  sudo bash $0"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "未检测到 python3，正在尝试安装…"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y && apt-get install -y python3
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3
  else
    echo "请先手动安装 python3 后重试"
    exit 1
  fi
fi

if [ ! -f "$AGENT_SRC/config.json" ]; then
  echo "安装包缺少 config.json"
  exit 1
fi
if [ ! -f "$AGENT_SRC/agent.py" ]; then
  echo "安装包缺少 agent.py"
  exit 1
fi

mkdir -p "$INSTALL_DIR"
cp -f "$AGENT_SRC/agent.py" "$INSTALL_DIR/agent.py"
cp -f "$AGENT_SRC/config.json" "$INSTALL_DIR/config.json"
chmod 755 "$INSTALL_DIR/agent.py"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Security Monitor Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=$(command -v python3) ${INSTALL_DIR}/agent.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
echo
echo "========================================"
echo " 安装完成！Agent 已启动并设置开机自启"
echo " 查看状态: systemctl status ${SERVICE_NAME}"
echo " 安装目录: ${INSTALL_DIR}"
echo "========================================"
