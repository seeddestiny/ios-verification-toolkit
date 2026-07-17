#!/usr/bin/env bash
#
# 01_install_appium.sh
# ─────────────────────────────────────────────────────────────────────────────
# 第一步:安装 Appium + XCUITest 驱动 + libimobiledevice(真机日志用)。
#
# 设计原则(严格保密 / 供应链安全):
#   1. 使用 npm 解析出的本机有效配置；本机未配置时自然回落 npm 官方源。
#   2. 全程不使用 sudo 安装 npm 全局包(避免给安装脚本 root 权限)。
#   3. WDA(WebDriverAgent)采用"运行时临时安装"策略:本脚本只安装 xcuitest
#      驱动(内含 WDA 源码),不在此构建/签名 WDA。WDA 入口见文件末尾 install_wda()。
#
# 用法:
#   bash 01_install_appium.sh             # 正常安装
#   bash 01_install_appium.sh --check     # 只做环境体检,不安装任何东西
#   bash 01_install_appium.sh --dry-run   # 打印将要执行的命令,但不真正执行
#
# 退出码: 0=成功 / 非0=失败
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── 可配置项 ────────────────────────────────────────────────────────────────
PUBLIC_REGISTRY="https://registry.npmjs.org/"
TRUSTED_REGISTRY=""
REGISTRY_SOURCE=""
APPIUM_DRIVER="xcuitest"
DRY_RUN=0
CHECK_ONLY=0

# ── 输出辅助 ────────────────────────────────────────────────────────────────
c_info()  { printf "\033[36m[INFO]\033[0m  %s\n" "$*"; }
c_ok()    { printf "\033[32m[ OK ]\033[0m  %s\n" "$*"; }
c_warn()  { printf "\033[33m[WARN]\033[0m  %s\n" "$*"; }
c_err()   { printf "\033[31m[FAIL]\033[0m  %s\n" "$*" >&2; }
c_step()  { printf "\n\033[1;34m===== %s =====\033[0m\n" "$*"; }

# 包一层,支持 DRY_RUN 预演。
run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf "\033[90m(dry-run) %s\033[0m\n" "$*"
  else
    eval "$@"
  fi
}

# ── 0. 前置工具体检 ───────────────────────────────────────────────────────────
check_prereqs() {
  c_step "0. 前置环境体检"
  local ok=1

  if command -v node >/dev/null 2>&1; then
    c_ok "node: $(node -v)"
  else
    c_err "node 未安装。请先安装 Node.js(建议经 brew: brew install node)。"; ok=0
  fi

  if command -v npm >/dev/null 2>&1; then
    c_ok "npm: $(npm -v)"
  else
    c_err "npm 未安装。"; ok=0
  fi

  if command -v brew >/dev/null 2>&1; then
    c_ok "brew: $(brew --version | head -1)"
  else
    c_warn "brew 未安装,后续 libimobiledevice 将无法通过 brew 安装。"
  fi

  local resolved_developer_dir=""
  resolved_developer_dir="$(python3 "$PROJECT_DIR/mcp_server/xcode_resolver.py" --tool xcodebuild 2>/dev/null || true)"
  if [ -n "$resolved_developer_dir" ]; then
    DEVELOPER_DIR="$resolved_developer_dir"
    export DEVELOPER_DIR
    c_ok "Xcode 命令行: $("$DEVELOPER_DIR/usr/bin/xcodebuild" -version 2>/dev/null | head -1)"
  else
    c_warn "未检测到唯一可用的完整 Xcode；如安装了多份，请运行本机配置工具选择。"
  fi

  [ "$ok" = "1" ] || { c_err "前置工具缺失,终止。"; exit 1; }
}

# ── 1. npm 源:完全遵循 npm 解析出的本机有效配置 ────────────────────────────
# 只对本脚本子进程固定解析结果，不修改 ~/.npmrc。
resolve_registry() {
  local current=""
  current="$(npm config get registry 2>/dev/null || true)"
  if [ -n "$current" ] && [ "$current" != "unknown" ]; then
    TRUSTED_REGISTRY="$current"
  else
    TRUSTED_REGISTRY="$PUBLIC_REGISTRY"
  fi
  if [ "${TRUSTED_REGISTRY%/}" = "${PUBLIC_REGISTRY%/}" ]; then
    REGISTRY_SOURCE="npm 官方默认源"
  else
    REGISTRY_SOURCE="本机 npm 有效配置"
  fi
}

setup_registry() {
  c_step "1. 配置 npm 源(供应链安全)"
  local metadata scheme host has_credentials

  resolve_registry
  if [ -z "$TRUSTED_REGISTRY" ] || [ "$TRUSTED_REGISTRY" = "unknown" ]; then
    c_err "未找到可信 npm 源。请先修复本机 npm 配置。"
    exit 1
  fi

  metadata="$(REGISTRY_URL="$TRUSTED_REGISTRY" node -e '
try {
  const value = new URL(process.env.REGISTRY_URL);
  console.log([value.protocol.replace(":", ""), value.hostname, value.username || value.password ? "1" : "0"].join("\t"));
} catch (_) {
  process.exit(2);
}
' 2>/dev/null || true)"
  IFS=$'\t' read -r scheme host has_credentials <<< "$metadata"
  [ -n "$host" ] && { [ "$scheme" = "https" ] || [ "$scheme" = "http" ]; } \
    || { c_err "解析到的 npm registry 不是合法 HTTP(S) URL"; exit 1; }
  [ "$has_credentials" = "0" ] \
    || { c_err "npm registry URL 不得内嵌账号或令牌；请使用本机 npm 认证配置。"; exit 1; }
  local allow_insecure
  allow_insecure="$(python3 "$PROJECT_DIR/mcp_server/local_config.py" get allow_insecure_npm)" || exit 2
  if [ "$scheme" != "https" ] && [ "$allow_insecure" != "1" ]; then
    c_err "npm registry 不是 HTTPS；如确认该源可信，请通过本机配置工具调整安全策略。"
    exit 1
  fi

  c_info "npm 源选择: $REGISTRY_SOURCE(不回显地址)"
  c_info "探测本轮可信 npm 源的 Appium 元数据"
  local body
  if body="$(curl -fsS --connect-timeout 5 --max-time 10 "${TRUSTED_REGISTRY%/}/appium" 2>/dev/null)" \
     && printf "%s" "$body" | grep -q '"dist-tags"'; then
    c_ok "可信 npm 源可达，返回了合法元数据。"
  else
    c_err "可信 npm 源不可达或未返回合法元数据。"
    c_warn "请检查网络或修复本机 npm 配置。"
    exit 1
  fi
  export npm_config_registry="$TRUSTED_REGISTRY"
}

# ── 2. 安装 Appium(全局,但绝不 sudo)────────────────────────────────────────
install_appium() {
  c_step "2. 安装 Appium"

  if command -v appium >/dev/null 2>&1; then
    c_ok "appium 已安装: $(appium -v 2>/dev/null)。如需升级请手动 npm i -g appium。"
    return 0
  fi

  c_info "npm 全局安装 appium(不使用 sudo)..."
  # 不加 sudo;若提示权限问题,应调整 npm prefix 而不是用 sudo。
  run "npm install -g appium"
  command -v appium >/dev/null 2>&1 && c_ok "appium 安装完成: $(appium -v 2>/dev/null)" \
    || { c_err "appium 安装后仍找不到可执行文件,检查 npm prefix 与 PATH。"; [ "$DRY_RUN" = "1" ] || exit 1; }
}

# ── 3. 安装 XCUITest 驱动(内含 WDA 源码)─────────────────────────────────────
# 可信镜像可能尚未同步部分公开传递依赖。仅当本机配置允许公网源时，才对驱动
# 安装这一条内部调用临时使用公网 registry；不会修改全局 npm 配置。
install_xcuitest_driver() {
  c_step "3. 安装 XCUITest 驱动"

  if appium driver list --installed 2>&1 | grep -q "$APPIUM_DRIVER"; then
    c_ok "xcuitest 驱动已安装。"
    return 0
  fi

  c_info "安装 appium 驱动(使用本轮可信源): ${APPIUM_DRIVER}"
  if run "appium driver install ${APPIUM_DRIVER}"; then
    c_ok "xcuitest 驱动安装完成。"
    return 0
  fi

  if [ "${TRUSTED_REGISTRY%/}" = "${PUBLIC_REGISTRY%/}" ]; then
    c_err "npm 官方源安装失败；没有其它源可安全回退。"
    [ "$DRY_RUN" = "1" ] || exit 1
    return
  fi
  local allow_public_fallback
  allow_public_fallback="$(python3 "$PROJECT_DIR/mcp_server/local_config.py" get allow_public_npm_fallback)" || exit 2
  if [ "$allow_public_fallback" != "1" ]; then
    c_err "本机 npm 源安装失败，且未允许官方源回退。可通过本机配置工具调整策略。"
    [ "$DRY_RUN" = "1" ] || exit 1
    return
  fi
  c_warn "仅对驱动安装命令临时使用公网 npm 源。"
  if [ "$DRY_RUN" = "1" ]; then
    printf "\033[90m(dry-run) appium driver install %s (官方源回退)\033[0m\n" "$APPIUM_DRIVER"
    return 0
  fi
  if npm_config_registry="$PUBLIC_REGISTRY" appium driver install "$APPIUM_DRIVER"; then
    c_ok "xcuitest 驱动安装完成(公网 fallback，本机全局配置未改变)。"
    return 0
  fi
  c_err "xcuitest 驱动安装失败(可信源与公网源均失败)。"
  [ "$DRY_RUN" = "1" ] || exit 1
}

configure_wda_bundle_id() {
  c_step "3b. 写入当前 Mac 的 WDA Bundle ID"
  python3 "$PROJECT_DIR/mcp_server/wda_project_config.py" \
    || { c_err "WDA 工程动态 Bundle ID 配置失败。"; exit 1; }
}

# ── 4. 安装 libimobiledevice(真机系统日志 idevicesyslog / 端口转发 iproxy)────
install_libimobiledevice() {
  c_step "4. 安装 libimobiledevice(真机日志 & USB 端口转发)"

  if command -v idevicesyslog >/dev/null 2>&1 && command -v iproxy >/dev/null 2>&1; then
    c_ok "idevicesyslog / iproxy 已安装。"
    return 0
  fi

  if ! command -v brew >/dev/null 2>&1; then
    c_warn "brew 不可用,跳过 libimobiledevice 安装(真机系统日志能力将缺失)。"
    return 0
  fi

  c_info "brew 安装 libimobiledevice"
  run "brew install libimobiledevice"
  c_ok "libimobiledevice 安装完成。"
}

# ── 5. WDA 安装入口(运行时临时安装,此处仅占位说明,不执行构建)───────────────
# 按需求:WDA 采用运行时临时安装。真正的构建/签名由跑自动化时的 Appium session
# 自动完成(通过 appium:xcodeOrgId / appium:updatedWDABundleId 等 capability)。
# 这里保留一个手动入口,供需要"预构建常驻 WDA"时调用,默认不触发。
install_wda() {
  c_step "5. WDA 安装入口(默认不执行)"
  c_info "WDA 采用【运行时临时安装】策略,本安装脚本不构建 WDA。"
  c_info "运行自动化时,Appium 会用以下 capability 自动构建并签名 WDA 到真机:"
  cat <<'EOF'
    appium:udid                = <你的 iPhone UDID>
    appium:xcodeOrgId          = <你的开发者 TeamID>
    appium:xcodeSigningId      = Apple Development
    appium:updatedWDABundleId  = <按当前 Mac 稳定生成的唯一 ID>
EOF
  c_info "如未来需要【预构建常驻 WDA】,可在此处接入 xcodebuild WDA scheme(当前留空)。"
}

# ── 6. 安装后总结 ─────────────────────────────────────────────────────────────
summary() {
  c_step "安装结果汇总"
  printf "  npm registry      : 本轮已配置(未修改全局 ~/.npmrc)\n"
  printf "  appium            : %s\n" "$(command -v appium >/dev/null 2>&1 && appium -v 2>/dev/null || echo 未安装)"
  printf "  xcuitest driver   : %s\n" "$(appium driver list --installed 2>&1 | grep -q xcuitest && echo 已安装 || echo 未安装)"
  printf "  idevicesyslog     : %s\n" "$(command -v idevicesyslog >/dev/null 2>&1 && echo 已安装 || echo 未安装)"
  printf "  iproxy            : %s\n" "$(command -v iproxy >/dev/null 2>&1 && echo 已安装 || echo 未安装)"
  echo
  c_ok "第一步(安装)完成。WDA 将在第一次运行自动化时由 Appium 临时构建。"
  c_info "下一步:用严格保密参数启动 Appium,见后续脚本。"
  c_info "  正确启动:appium --address 127.0.0.1 --port 4723 --log-level info"
  c_info "  注意:--allow-cors 是布尔 flag,默认即 false(禁用),无需也不要写 '--allow-cors false'。"
}

# ── main ──────────────────────────────────────────────────────────────────────
main() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --check) CHECK_ONLY=1 ;;
      --dry-run) DRY_RUN=1 ;;
      --help|-h)
        printf '用法: bash scripts/01_install_appium.sh [--check] [--dry-run]\n'
        return 0
        ;;
      *) c_err "未知参数: $arg"; return 2 ;;
    esac
  done
  c_step "Appium + WDA 安装(ios-verification-toolkit / 第一步)"
  [ "$DRY_RUN" = "1" ] && c_warn "DRY_RUN 模式:只打印命令,不实际执行。"

  check_prereqs

  if [ "$CHECK_ONLY" = "1" ]; then
    c_info "--check 模式:仅体检,不安装。"
    setup_registry
    exit 0
  fi

  setup_registry
  install_appium
  install_xcuitest_driver
  [ "$DRY_RUN" = "1" ] || configure_wda_bundle_id
  install_libimobiledevice
  install_wda
  summary
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
