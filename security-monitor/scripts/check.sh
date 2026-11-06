#!/usr/bin/env bash
# 本地一键检查（与 .github/workflows/ci.yml 相同的检查项）
#   1. 后端：pytest（需要本地 PostgreSQL，连接参数见 tests/conftest.py 的 TEST_DB_* 变量）
#   2. 前端：tsc 类型检查 + vite 构建
#
# 用法：
#   bash scripts/check.sh              # 全部
#   bash scripts/check.sh backend      # 只跑后端
#   bash scripts/check.sh frontend     # 只跑前端
#
# 环境变量：
#   PYTHON     用于跑测试的 Python（默认 .venv/bin/python，不存在则 python3）；需已安装 requirements-dev.txt
#   CHECK_TMP  前端构建用的临时目录（默认 mktemp）。
#              WSL 下 /mnt/* 盘上 npm ci 常失败，所以前端总是复制到 Linux 文件系统里构建。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHAT="${1:-all}"

backend() {
  echo "==> 后端测试"
  local py="${PYTHON:-}"
  if [[ -z "$py" ]]; then
    if [[ -x "$ROOT/.venv/bin/python" ]] && "$ROOT/.venv/bin/python" -c "import pytest" 2>/dev/null; then
      py="$ROOT/.venv/bin/python"
    else
      py="python3"
    fi
  fi
  if ! "$py" -c "import pytest, fastapi" 2>/dev/null; then
    echo "[!] $py 缺少依赖，请先执行：$py -m pip install -r requirements-dev.txt（或设置 PYTHON=...）" >&2
    exit 1
  fi
  (cd "$ROOT" && "$py" -m pytest -q)
}

frontend() {
  echo "==> 前端类型检查与构建"
  local tmp="${CHECK_TMP:-$(mktemp -d)}"
  mkdir -p "$tmp"
  (cd "$ROOT/web" && tar --exclude=node_modules --exclude=dist -cf - .) | tar -xf - -C "$tmp"
  (cd "$tmp" && npm ci --no-audit --no-fund --loglevel=error && npm run build)
  echo "    构建产物：$tmp/dist"
}

case "$WHAT" in
  backend) backend ;;
  frontend) frontend ;;
  all) backend; frontend ;;
  *) echo "用法：$0 [all|backend|frontend]" >&2; exit 2 ;;
esac
echo "==> 全部通过"
