#!/bin/sh
# 边缘采集器 Linux 一键安装 / 修改配置（systemd）
#   sudo sh linux/install.sh            安装或更新（交互确认总部地址与 Token，回车保持当前值）
#   sudo sh linux/install.sh --config   只修改参数并重启
#   sudo sh linux/install.sh --uninstall
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DIR=/opt/security-monitor-probe
UNIT=/etc/systemd/system/security-monitor-probe.service

if [ "$(id -u)" != "0" ]; then echo "请用 root 运行：sudo sh $0 $*"; exit 1; fi

if [ "$1" = "--uninstall" ]; then
  systemctl disable --now security-monitor-probe 2>/dev/null || true
  rm -f "$UNIT"; systemctl daemon-reload
  echo "已卸载服务，目录 $DIR 保留（含日志与暂存），可手动删除"; exit 0
fi

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then echo "未找到 python3，请先安装：apt install python3 / yum install python3"; exit 1; fi

mkdir -p "$DIR"
if [ "$1" != "--config" ]; then
  cp "$SRC/probe.py" "$SRC/snmp_client.py" "$DIR/"
  [ -f "$SRC/请先阅读.txt" ] && cp "$SRC/请先阅读.txt" "$DIR/" || true
  mkdir -p "$DIR/linux" && cp "$SRC/linux/install.sh" "$DIR/linux/" 2>/dev/null || true
fi
[ -f "$DIR/config.json" ] || cp "$SRC/config.json" "$DIR/config.json"

CUR_URL=$("$PY" -c "import json;print(json.load(open('$DIR/config.json',encoding='utf-8')).get('server_url',''))")
CUR_TOKEN=$("$PY" -c "import json;print(json.load(open('$DIR/config.json',encoding='utf-8')).get('probe_token',''))")
if [ -t 0 ]; then
  printf "总部地址 [%s]: " "$CUR_URL"; read -r URL
  printf "站点 Token [%s****]: " "$(echo "$CUR_TOKEN" | cut -c1-6)"; read -r TOKEN
fi
URL="${URL:-$CUR_URL}"; TOKEN="${TOKEN:-$CUR_TOKEN}"
"$PY" - "$DIR/config.json" "$URL" "$TOKEN" <<'PYEOF'
import json, sys
path, url, token = sys.argv[1:4]
cfg = json.load(open(path, encoding="utf-8"))
cfg["server_url"] = url.strip().rstrip("/")
cfg["probe_token"] = token.strip()
json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
chmod 600 "$DIR/config.json"

cat > "$UNIT" <<UNITEOF
[Unit]
Description=Security Monitor Edge Probe
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$PY $DIR/probe.py --service
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNITEOF
systemctl daemon-reload
systemctl enable security-monitor-probe >/dev/null 2>&1
systemctl restart security-monitor-probe

echo "== 连接测试 =="
(cd "$DIR" && "$PY" probe.py --test) || echo "[!] 连接测试失败，服务已启动，网络恢复后会自动上报"
echo "已安装到 $DIR，服务名 security-monitor-probe（开机自启）"
echo "查看日志：tail -f $DIR/probe.log    修改参数：sudo sh $DIR/linux/install.sh --config"
