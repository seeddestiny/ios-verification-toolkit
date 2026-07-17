#!/usr/bin/env bash
#
# 99_uninstall_cleanup.sh
# ─────────────────────────────────────────────────────────────────────────────
# 卸载 / 清理:把 01_install_appium.sh 安装的一切恢复原状。
#
# 清理范围:
#   1. Appium 全局 npm 包
#   2. Appium 驱动 & 缓存目录 ~/.appium
#   3. 保留用户 npm registry 配置(本工具从不修改)
#   4. ~/.npmrc 安全检查(不打印内容、不修改非空文件)
#   5. 设备上的 WDA App(WebDriverAgentRunner,运行时临时装的)
#   6. 本工程产生的运行时缓存(截图 / 日志 / 临时文件)
#   7. 指向本仓库的共享 Skill 安装链接
#   8.(可选)libimobiledevice —— 默认保留,加 --purge-brew 才卸
#
# 用法:
#   bash 99_uninstall_cleanup.sh                # 交互式,逐项确认
#   bash 99_uninstall_cleanup.sh --yes          # 不询问,全部清理(保留 brew 包)
#   bash 99_uninstall_cleanup.sh --yes --purge-brew   # 连 libimobiledevice 一起卸
#   bash 99_uninstall_cleanup.sh --dry-run      # 只打印将执行的操作,不实际执行
#
# 注意:本脚本只清理本工具引入的东西,不会动你的 Xcode / 证书 / node 本身。
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail   # 不用 -e:清理脚本应尽量跑完所有步骤,单步失败不中断

# ── 配置 ──────────────────────────────────────────────────────────────────────
DEVICE_UDID="${IOS_MCP_UDID:-${DEVICE_UDID:-}}" # 留空则按统一规则发现；多台时不静默选第一台
WDA_BUNDLE_PREFIX="WebDriverAgentRunner"  # 匹配设备上的 WDA
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ROOT="$(python3 "$PROJECT_DIR/mcp_server/runtime_paths.py" root)" || exit 2
DRY_RUN=0
ASSUME_YES=0
PURGE_BREW=0

# ── 输出 ──────────────────────────────────────────────────────────────────────
c_info()  { printf "\033[36m[INFO]\033[0m  %s\n" "$*"; }
c_ok()    { printf "\033[32m[ OK ]\033[0m  %s\n" "$*"; }
c_warn()  { printf "\033[33m[WARN]\033[0m  %s\n" "$*"; }
c_err()   { printf "\033[31m[FAIL]\033[0m  %s\n" "$*" >&2; }
c_step()  { printf "\n\033[1;34m===== %s =====\033[0m\n" "$*"; }

run() {
  if [ "$DRY_RUN" = "1" ]; then printf "\033[90m(dry-run) %s\033[0m\n" "$*"; else eval "$@"; fi
}

# 交互确认:--yes 时直接通过。
confirm() {
  [ "$ASSUME_YES" = "1" ] && return 0
  local ans
  read -r -p "$(printf '\033[33m[?]\033[0m %s [y/N] ' "$1")" ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

# ── 1. 卸载 Appium 全局包 ─────────────────────────────────────────────────────
remove_appium() {
  c_step "1. 卸载 Appium 全局包"
  if command -v appium >/dev/null 2>&1; then
    if confirm "卸载全局 appium($(appium -v 2>/dev/null))?"; then
      run "npm uninstall -g appium"
      c_ok "appium 已卸载。"
    else c_info "跳过 appium 卸载。"; fi
  else
    c_ok "appium 未安装,无需处理。"
  fi
}

# ── 2. 清理 Appium 驱动 & 缓存目录 ────────────────────────────────────────────
remove_appium_home() {
  c_step "2. 清理 Appium 驱动 & 缓存(~/.appium)"
  # 先尝试用 appium 卸驱动(若 appium 还在)
  if command -v appium >/dev/null 2>&1; then
    if appium driver list --installed 2>/dev/null | grep -q xcuitest; then
      run "appium driver uninstall xcuitest" || c_warn "xcuitest 驱动卸载失败,将随目录删除一并清理。"
    fi
  fi
  if [ -d "$HOME/.appium" ]; then
    if confirm "删除 Appium 缓存目录 ~/.appium(含驱动、WDA 构建产物)?"; then
      run "rm -rf \"$HOME/.appium\""
      c_ok "~/.appium 已删除。"
    else c_info "跳过 ~/.appium 删除。"; fi
  else
    c_ok "~/.appium 不存在。"
  fi
  # 顺带清 WDA 的 DerivedData(Appium 构建 WDA 会留在这里)
  local wda_dd="$HOME/Library/Developer/Xcode/DerivedData"
  if [ -d "$wda_dd" ]; then
    local hits
    hits="$(find "$wda_dd" -maxdepth 1 -iname 'WebDriverAgent*' 2>/dev/null)"
    if [ -n "$hits" ]; then
      if confirm "删除 WDA 的 DerivedData 构建缓存?"; then
        run "find \"$wda_dd\" -maxdepth 1 -iname 'WebDriverAgent*' -exec rm -rf {} +"
        c_ok "WDA DerivedData 已清理。"
      fi
    fi
  fi
}

# ── 3. 保留用户 npm registry ────────────────────────────────────────────────
restore_registry() {
  c_step "3. 保留 npm registry"
  c_ok "安装器只读取本机 npm 有效配置，从不写入；卸载时保持不动。"
}

# ── 4. 清理 ~/.npmrc 中本工具写入的项 ─────────────────────────────────────────
clean_npmrc() {
  c_step "4. 检查 ~/.npmrc 残留"
  local f="$HOME/.npmrc"
  if [ -f "$f" ]; then
    c_info "检测到 ~/.npmrc；为避免回显 token、账号或内部域名，不展示其内容。"
    if [ ! -s "$f" ]; then
      if confirm "~/.npmrc 已为空,删除该文件?"; then
        run "rm -f \"$f\""; c_ok "空的 ~/.npmrc 已删除。"
      fi
    else
      c_warn "~/.npmrc 仍有内容(可能含你其它配置),为安全起见不自动删除。"
      c_warn "如需检查，请在本机手动查看并确认无敏感 registry 或认证配置残留。"
    fi
  else
    c_ok "~/.npmrc 不存在。"
  fi
}

# ── 5. 卸载设备上的 WDA App ───────────────────────────────────────────────────
remove_wda_on_device() {
  c_step "5. 卸载真机上的 WDA(WebDriverAgentRunner)"
  # 复用 MCP 的设备选择规则：单台自动，多台必须显式 IOS_MCP_UDID。
  local selected
  if [ -n "$DEVICE_UDID" ]; then
    selected="$(IOS_MCP_UDID="$DEVICE_UDID" python3 "$PROJECT_DIR/mcp_server/device_discovery.py" resolve --field udid 2>/dev/null)"
  else
    selected="$(python3 "$PROJECT_DIR/mcp_server/device_discovery.py" resolve --field udid 2>/dev/null)"
  fi
  DEVICE_UDID="$selected"
  if [ -z "$DEVICE_UDID" ]; then
    c_warn "未探测到已连接真机,跳过 WDA 卸载。如需手动:devicectl device uninstall app --device <UDID> <bundleId>"
    return 0
  fi
  c_info "目标设备: $DEVICE_UDID"
  # 列出设备上匹配 WebDriverAgentRunner 的 bundleId
  local apps
  apps="$(xcrun devicectl device info apps --device "$DEVICE_UDID" 2>/dev/null \
    | grep -i "$WDA_BUNDLE_PREFIX" | awk '{print $1}')"
  if [ -z "$apps" ]; then
    c_ok "设备上未发现 WDA App($WDA_BUNDLE_PREFIX),无需卸载。"
    return 0
  fi
  while IFS= read -r bid; do
    [ -z "$bid" ] && continue
    if confirm "从设备卸载 WDA: $bid ?"; then
      run "xcrun devicectl device uninstall app --device \"$DEVICE_UDID\" \"$bid\""
      c_ok "已卸载 $bid"
    fi
  done <<< "$apps"
}

# ── 6. 清理本工程运行时缓存(截图/日志/临时文件)──────────────────────────────
clean_project_runtime() {
  c_step "6. 清理本工程运行时缓存"
  # 前四项兼容清理旧版本曾使用的目录；新版本统一使用 RUNTIME_ROOT。
  local targets=("$RUNTIME_ROOT" "$PROJECT_DIR/output" "$PROJECT_DIR/logs" \
                 "$PROJECT_DIR/log" "$PROJECT_DIR/.tmp" \
                 "$PROJECT_DIR"/*.log "$PROJECT_DIR"/scripts/*.log)
  local found=0
  for t in "${targets[@]}"; do
    if [ -e "$t" ]; then
      found=1
      if confirm "删除运行时产物: $t ?"; then run "rm -rf \"$t\""; c_ok "已删除 $t"; fi
    fi
  done
  [ "$found" = "0" ] && c_ok "无运行时缓存产物。"
}

# ── 6b. 清理 MCP Server 虚拟环境 ─────────────────────────────────────────────
clean_mcp_venv() {
  c_step "6b. 清理 MCP Server 虚拟环境"
  local venv="$PROJECT_DIR/mcp_server/.venv"
  if [ -d "$venv" ]; then
    if confirm "删除 MCP 虚拟环境: $venv ?"; then
      run "rm -rf \"$venv\""; c_ok "MCP venv 已删除。"
    else c_info "跳过 MCP venv 删除。"; fi
  else
    c_ok "MCP venv 不存在。"
  fi
}

# ── 7. 清理指向本仓库的共享 Skill 安装链接 ────────────────────────────────
clean_skill_link() {
  c_step "7. 清理共享 Skill 安装链接"
  local target="$HOME/.agents/skills/ios-change-verification"
  if [ ! -L "$target" ] && [ ! -e "$target" ]; then
    c_ok "共享 Skill 安装链接不存在。"
    return 0
  fi
  if ! bash "$PROJECT_DIR/scripts/07_install_skill.sh" --check >/dev/null 2>&1; then
    c_warn "目标不是本安装器创建的当前仓库链接，保留不动: $target"
    return 0
  fi
  if confirm "删除共享 Skill 安装链接: $target ?"; then
    run "bash \"$PROJECT_DIR/scripts/07_install_skill.sh\" --uninstall"
  else
    c_info "保留共享 Skill 安装链接。"
  fi
}

# ── 8.(可选)卸载 libimobiledevice ──────────────────────────────────────────
remove_brew_pkgs() {
  c_step "8. (可选)卸载 libimobiledevice"
  if [ "$PURGE_BREW" != "1" ]; then
    c_info "默认保留 libimobiledevice(其它工具可能也在用)。如需卸载请加 --purge-brew。"
    return 0
  fi
  if command -v brew >/dev/null 2>&1 && brew list libimobiledevice >/dev/null 2>&1; then
    if confirm "brew 卸载 libimobiledevice?"; then
      run "brew uninstall libimobiledevice"; c_ok "libimobiledevice 已卸载。"
    fi
  else
    c_ok "libimobiledevice 未通过 brew 安装或不存在。"
  fi
}

# ── 总结 ──────────────────────────────────────────────────────────────────────
summary() {
  c_step "清理结果汇总"
  printf "  appium          : %s\n" "$(command -v appium >/dev/null 2>&1 && echo 仍在 || echo 已移除)"
  printf "  ~/.appium       : %s\n" "$([ -d "$HOME/.appium" ] && echo 仍在 || echo 已移除)"
  printf "  npm registry    : 未回显(避免泄漏本机源地址)\n"
  printf "  ~/.npmrc        : %s\n" "$([ -f "$HOME/.npmrc" ] && echo 存在 || echo 不存在)"
  printf "  shared Skill    : %s\n" "$([ -e "$HOME/.agents/skills/ios-change-verification/SKILL.md" ] && echo 仍在 || echo 已移除)"
  printf "  idevicesyslog   : %s\n" "$(command -v idevicesyslog >/dev/null 2>&1 && echo 仍在 || echo 已移除)"
  echo
  c_ok "清理完成。Xcode / 签名证书 / node 本身均未触碰。"
}

usage() {
  grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -30
}

# ── main ──────────────────────────────────────────────────────────────────────
main() {
  for arg in "$@"; do
    case "$arg" in
      --yes|-y)      ASSUME_YES=1 ;;
      --purge-brew)  PURGE_BREW=1 ;;
      --dry-run)     DRY_RUN=1 ;;
      --help|-h)     usage; exit 0 ;;
      *) c_warn "未知参数: $arg" ;;
    esac
  done

  c_step "Appium + WDA 卸载清理(ios-verification-toolkit)"
  [ "$DRY_RUN" = "1" ] && c_warn "DRY_RUN 模式:只打印,不实际执行。"
  [ "$ASSUME_YES" = "1" ] && c_warn "--yes 模式:不再逐项询问。"

  remove_appium
  remove_appium_home
  restore_registry
  clean_npmrc
  remove_wda_on_device
  clean_project_runtime
  clean_mcp_venv
  clean_skill_link
  remove_brew_pkgs
  summary
}

main "$@"
