#!/usr/bin/env bash
#
# 06_setup_tunnel_sudoers.sh — (可选)配置 NOPASSWD sudoers,让 RemoteXPC 隧道免密自动拉起
# ─────────────────────────────────────────────────────────────────────────────
# 背景:iOS 17+ 真机自动化需要 `sudo appium driver run xcuitest tunnel-creation`
#       创建 TUN 隧道(需 root)。默认每次要输密码,无法全自动。
#
# 本脚本用"精确到单条命令的 NOPASSWD"方式(而非全局放开 sudo)配置免密,兼顾自动化与安全:
#   - 只允许当前用户免密执行 tunnel-creation 这一条命令
#   - 用 visudo -c 校验语法,写入 /etc/sudoers.d/ 独立文件,不改主 sudoers
#
# 安全说明:这会让该条命令无需密码即可获得 root 执行。请确认你了解并接受此风险。
#          撤销:sudo rm /etc/sudoers.d/appium-tunnel
#
# 用法:
#   bash 06_setup_tunnel_sudoers.sh          # 打印将写入的规则并给出确认命令(不自动写)
#   bash 06_setup_tunnel_sudoers.sh --apply  # 实际写入(本步骤本身需要 sudo 密码一次)
#   bash 06_setup_tunnel_sudoers.sh --start-tunnel   # 自动选设备并后台拉起；未免密时提示 sudo
#   bash 06_setup_tunnel_sudoers.sh --stop-tunnel    # 关闭隧道；未免密时提示 sudo
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
umask 077

c_info(){ printf "\033[36m[INFO]\033[0m  %s\n" "$*"; }
c_ok(){   printf "\033[32m[ OK ]\033[0m  %s\n" "$*"; }
c_warn(){ printf "\033[33m[WARN]\033[0m  %s\n" "$*"; }
c_err(){  printf "\033[31m[FAIL]\033[0m  %s\n" "$*" >&2; }
c_step(){ printf "\n\033[1;34m===== %s =====\033[0m\n" "$*"; }

USER_NAME="$(id -un)"
APPIUM_BIN="$(command -v appium || echo /opt/homebrew/bin/appium)"
NODE_BIN="$(command -v node || echo /opt/homebrew/bin/node)"
SUDOERS_FILE="/etc/sudoers.d/appium-tunnel"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ROOT="$(python3 "$PROJECT_DIR/mcp_server/runtime_paths.py" root --create)" || exit 2
LOG_DIR="$RUNTIME_ROOT/logs"
DEVICE_UDID="${IOS_MCP_UDID:-}"
TUNNEL_REGISTRY_PORT="${IOS_MCP_TUNNEL_REGISTRY_PORT:-42314}"

# sudoers 规则:允许当前用户免密【启动】和【关闭】隧道。
# 注:appium 是 node 脚本,实际执行是 node + appium.js;为稳妥同时允许 appium 可执行文件。
#     关闭用精确的 pkill -f 匹配串(只杀隧道进程),同样精确到命令,不放开任意 kill。
PKILL_BIN="$(command -v pkill || echo /usr/bin/pkill)"
RULE_START="${APPIUM_BIN} driver run xcuitest tunnel-creation, ${APPIUM_BIN} driver run xcuitest tunnel-creation *"
# 关闭用 pkill -f 匹配 "tunnel-creation"(同时覆盖 sudo/appium 包装进程与实际的
# tunnel-creation.mjs 工作进程;后者父进程退出后会变孤儿,匹配串必须能命中它)。
RULE_STOP="${PKILL_BIN} -f tunnel-creation"
RULE="${USER_NAME} ALL=(root) NOPASSWD: ${RULE_START}, ${RULE_STOP}"

print_plan(){
  c_step "将写入的 NOPASSWD 规则(精确到单条命令)"
  echo "  文件: $SUDOERS_FILE"
  echo "  规则: $RULE"
  echo
  c_warn "安全提示:此规则让上述命令免密以 root 运行。仅在你信任本机环境时使用。"
  c_info "确认无误后执行写入(会要求一次 sudo 密码):"
  c_info "    bash $0 --apply"
  c_info "撤销:sudo rm $SUDOERS_FILE"
}

apply(){
  c_step "写入 sudoers(需要一次 sudo 密码)"
  local tmp; tmp="$(mktemp)"
  printf '%s\n' "$RULE" > "$tmp"

  # sudo 调用封装:若提供了 SUDO_PW(由调用方用隐藏输入 read -s 收集并传入),
  # 用 sudo -S 从 stdin 读密码(密码不回显、不落盘);否则由 sudo 自己交互弹密码。
  # 安全:关闭 xtrace 防止密码进 -x 日志;函数返回后调用方应立即 unset SUDO_PW。
  _sudo(){
    if [ -n "${SUDO_PW:-}" ]; then
      { set +x; } 2>/dev/null
      printf '%s\n' "$SUDO_PW" | sudo -S -p '' "$@"
    else
      sudo "$@"
    fi
  }

  if ! _sudo visudo -c -f "$tmp" >/dev/null 2>&1; then
    c_err "sudoers 语法校验失败或密码错误,已中止(未写入)。"; rm -f "$tmp"; exit 1
  fi
  _sudo install -m 0440 "$tmp" "$SUDOERS_FILE" || { c_err "写入失败(密码错误?)"; rm -f "$tmp"; exit 1; }
  rm -f "$tmp"
  _sudo visudo -c >/dev/null 2>&1 && c_ok "已写入并校验通过: $SUDOERS_FILE" || { c_err "整体 sudoers 校验失败,请检查"; exit 1; }
  c_info "现在可免密拉起隧道:  bash $0 --start-tunnel"
  c_info "也可免密关闭隧道:    bash $0 --stop-tunnel"
}

start_tunnel(){
  c_step "后台拉起 RemoteXPC 隧道"
  DEVICE_UDID="$(python3 "$PROJECT_DIR/mcp_server/device_discovery.py" resolve --field udid)" || exit 2
  export IOS_MCP_UDID="$DEVICE_UDID"
  c_info "本轮动态选择目标设备: $DEVICE_UDID"
  if [ ! -f "$SUDOERS_FILE" ]; then
    c_info "尚未配置隧道免密；本次需要输入 sudo 密码。"
    sudo -v || { c_err "sudo 授权失败"; exit 1; }
  fi
  mkdir -p "$LOG_DIR"
  tunnel_ready(){
    pgrep -f "tunnel-creation" >/dev/null 2>&1 &&
      curl -fsS --max-time 2 \
        "http://127.0.0.1:${TUNNEL_REGISTRY_PORT}/remotexpc/tunnels/${DEVICE_UDID}?waitMs=1000" \
        >/dev/null 2>&1
  }
  if tunnel_ready; then
    c_ok "隧道已在运行。"; return 0
  fi
  # 清理只有进程、没有 ready registry 的残留实例，避免端口冲突。
  if pgrep -f "tunnel-creation" >/dev/null 2>&1; then
    sudo -n "$PKILL_BIN" -f tunnel-creation >/dev/null 2>&1 || true
    sleep 1
  fi
  # 后台常驻；若未安装 NOPASSWD，前面的 sudo -v 已在前台完成本轮授权。
  # 显式指定 --udid，跳过无关 Apple TV 发现；否则部分 xcuitest-driver 版本会在
  # USB 隧道已建好后等待 Apple TV，并错误清理刚建好的 registry。
  sudo -n "$APPIUM_BIN" driver run xcuitest tunnel-creation -- \
    --udid "$DEVICE_UDID" --tunnel-registry-port "$TUNNEL_REGISTRY_PORT" \
    > "$LOG_DIR/tunnel.log" 2>&1 &
  local i
  for i in $(seq 1 25); do
    if tunnel_ready; then
      c_ok "隧道 registry 已就绪(日志: $LOG_DIR/tunnel.log)。"
      return 0
    fi
    pgrep -f "tunnel-creation" >/dev/null 2>&1 || break
    sleep 1
  done
  sudo -n "$PKILL_BIN" -f tunnel-creation >/dev/null 2>&1 || true
  c_err "隧道进程未形成可用 registry。检查 $LOG_DIR/tunnel.log"; exit 1
}

stop_tunnel(){
  c_step "关闭 RemoteXPC 隧道"
  if ! pgrep -f "tunnel-creation" >/dev/null 2>&1; then
    c_ok "隧道未在运行,无需关闭。"; return 0
  fi
  # 优先免密关闭(依赖 --apply 已把 pkill 规则纳入 NOPASSWD)
  if sudo -n "$PKILL_BIN" -f tunnel-creation 2>/dev/null; then
    :
  elif [ -t 0 ]; then
    c_warn "免密关闭不可用(未配置或规则未含关闭)。用交互 sudo 关闭(需输密码)..."
    sudo "$PKILL_BIN" -f tunnel-creation
  else
    c_err "免密关闭不可用；非交互模式禁止 sudo 读取 MCP stdin。"; exit 1
  fi
  local i
  for i in $(seq 1 5); do
    if ! pgrep -f "tunnel-creation" >/dev/null 2>&1; then
      c_ok "隧道已关闭。"; return 0
    fi
    sleep 1
  done
  c_err "隧道仍在运行,关闭失败。"; exit 1
}

case "${1:-}" in
  --apply)         apply ;;
  --start-tunnel)  start_tunnel ;;
  --stop-tunnel)   stop_tunnel ;;
  *)               print_plan ;;
esac
