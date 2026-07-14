#!/usr/bin/env bash
#
# 02_start_appium.sh — 以严格保密参数启动 Appium 并常驻
# ─────────────────────────────────────────────────────────────────────────────
# 严格保密要点:
#   --address 127.0.0.1   只监听本机回环,杜绝同网段他人连入(Appium 默认是
#                         0.0.0.0 对外暴露,这里显式覆盖为最安全值)。
#   不加 --allow-cors      浏览器跨域默认禁用。
#   不加 --relaxed-security / --allow-insecure   所有不安全特性保持默认关闭。
#   日志只写本地 .runtime/logs/ 目录,不外传。
#
# 用法:
#   bash 02_start_appium.sh          # 启动并常驻(若已在运行则复用)
#   bash 02_start_appium.sh stop     # 停止
#   bash 02_start_appium.sh status   # 查看状态
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_DIR/scripts/lib/sanitize_env.sh"
HOST=127.0.0.1
PORT=4723
RUNTIME_ROOT="$(python3 "$PROJECT_DIR/mcp_server/runtime_paths.py" root --create)" || exit 2
LOG_DIR="$RUNTIME_ROOT/logs"
PIDFILE="$LOG_DIR/appium.pid"

is_up() { curl -s "http://$HOST:$PORT/status" 2>/dev/null | grep -q '"ready":true'; }

start() {
  if is_up; then echo "[OK] Appium 已在运行: http://$HOST:$PORT"; return 0; fi
  echo "[INFO] 启动 Appium(仅监听 $HOST:$PORT)..."
  # nohup + 重定向 + 后台,并写 pidfile;< /dev/null 防止继承 stdin 被关闭时退出
  ios_mcp_sanitized_env nohup appium --address "$HOST" --port "$PORT" \
        --log "$LOG_DIR/appium-server.log" --log-level info \
        < /dev/null >> "$LOG_DIR/appium-nohup.log" 2>&1 &
  echo $! > "$PIDFILE"
  # 等待就绪(最多 30s)
  for i in $(seq 1 30); do
    if is_up; then
      echo "[OK] Appium 就绪 (PID=$(cat "$PIDFILE")) — http://$HOST:$PORT"
      lsof -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null | grep -q 127.0.0.1 \
        && echo "[OK] 已确认仅监听 127.0.0.1(严格保密)。"
      return 0
    fi
    sleep 1
  done
  echo "[FAIL] Appium 30s 内未就绪,检查 $LOG_DIR/appium-server.log"; return 1
}

stop() {
  if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null && echo "[OK] 已停止 PID=$(cat "$PIDFILE")"; rm -f "$PIDFILE"; fi
  pkill -f "appium --address $HOST" 2>/dev/null && echo "[OK] 已清理残留 appium 进程" || true
}

status() {
  if is_up; then
    echo "[OK] 运行中: http://$HOST:$PORT"
    lsof -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null
  else echo "[--] 未运行"; fi
}

case "${1:-start}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  *) echo "用法: $0 {start|stop|status}"; exit 1 ;;
esac
