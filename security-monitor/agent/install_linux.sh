#!/usr/bin/env bash
# 在目标 Linux 服务器执行: sudo bash install_linux.sh
set -euo pipefail
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="security-monitor-agent"

if ! command -v python3 >/dev/null 2>&1; then
  echo "请先安装 python3"
  exit 1
fi

if [ ! -f "$AGENT_DIR/config.json" ]; then
  cp "$AGENT_DIR/config.example.json" "$AGENT_DIR/config.json"
  echo "已创建 config.json，请填写 server_url 与 agent_token 后重新执行"
  exit 1
fi

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Security Monitor Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=${AGENT_DIR}
ExecStart=$(command -v python3) ${AGENT_DIR}/agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
echo "已安装并启动: systemctl status ${SERVICE_NAME}"
