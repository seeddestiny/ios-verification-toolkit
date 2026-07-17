#!/usr/bin/env bash
#
# 04_wda_signing_check.sh — WDA 签名就绪度预检 + 一键构建签名
# ─────────────────────────────────────────────────────────────────────────────
# 背景:WDA 的动态 bundle ID 需要匹配本机开发团队和 provisioning profile；
#   命令行自动生成 profile 时还依赖有效的 Xcode 账号登录态。
#
# 本脚本做两件事:
#   [check]  逐项检查签名前置条件,任一不满足则给出精确的人工修复指引。
#   [build]  条件满足时,直接用 xcodebuild 构建并签名 WDA 安装到真机(预构建),
#            之后 Appium 用 usePrebuiltWDA 复用,无需每次重建。
#
# 用法:
#   bash 04_wda_signing_check.sh check     # 只检查,不构建(默认)
#   bash 04_wda_signing_check.sh build     # 检查通过后构建签名 WDA 到真机
#
# 关键前置(需人工一次性完成,脚本无法代劳):
#   打开 Xcode → Settings → Accounts → 检查开发账号登录态
#   (走双因素验证刷新登录态),这样 -allowProvisioningUpdates 才能在线生成 profile。
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── 清除可能污染构建的编译器环境变量 ────────────────────────────────────────
unset CC CXX
DEVELOPER_DIR="$(python3 "$PROJECT_DIR/mcp_server/xcode_resolver.py" --tool xcodebuild)" || exit 2
export DEVELOPER_DIR

# ── 配置(自动选择上次成功、既有 WDA 或本机首个有效签名团队)────────────────
TEAM_ID="$(python3 "$PROJECT_DIR/mcp_server/signing_identity.py" team-id)" || exit 2
CERT_CN="$(IOS_MCP_TEAM_ID="$TEAM_ID" python3 "$PROJECT_DIR/mcp_server/signing_identity.py" certificate-name)" || exit 2
WDA_BUNDLE_ID="$(python3 "$PROJECT_DIR/mcp_server/wda_bundle_id.py")" || exit 2
source "$PROJECT_DIR/scripts/lib/sanitize_env.sh"
DEVICE_UDID="$(python3 "$PROJECT_DIR/mcp_server/device_discovery.py" resolve --field udid)" || exit 2
export IOS_MCP_UDID="$DEVICE_UDID"
WDA_PROJ="$HOME/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj"

c_info(){ printf "\033[36m[INFO]\033[0m  %s\n" "$*"; }
c_ok(){   printf "\033[32m[ OK ]\033[0m  %s\n" "$*"; }
c_warn(){ printf "\033[33m[WARN]\033[0m  %s\n" "$*"; }
c_err(){  printf "\033[31m[FAIL]\033[0m  %s\n" "$*" >&2; }
c_step(){ printf "\n\033[1;34m===== %s =====\033[0m\n" "$*"; }

CHECK_PASS=1        # 综合前置(含 profile)是否齐全
HARD_FAIL=0         # 硬性前置(证书/工程)缺失,build 也无法进行
fail(){ CHECK_PASS=0; c_err "$1"; }
hard_fail(){ CHECK_PASS=0; HARD_FAIL=1; c_err "$1"; }

# ── 1. 证书有效性 ─────────────────────────────────────────────────────────────
check_cert() {
  c_step "1. 开发证书有效性"
  local certificate
  certificate="$(security find-certificate -c "$CERT_CN" -p 2>/dev/null)"
  if [ -z "$certificate" ]; then
    hard_fail "未找到所选开发团队对应的有效证书。需先在 Xcode 登录账号并生成开发证书。"
    return
  fi
  if printf '%s' "$certificate" | openssl x509 -checkend 0 -noout 2>/dev/null; then
    c_ok "所选开发证书在有效期内。"
  else
    hard_fail "所选开发证书已过期。需在 Xcode 重新生成开发证书。"
  fi
}

# ── 2. WDA 的 provisioning profile 是否存在 ───────────────────────────────────
check_profile() {
  c_step "2. WDA provisioning profile"
  local dir="$HOME/Library/MobileDevice/Provisioning Profiles"
  local hit=0
  for p in "$dir"/*.mobileprovision; do
    [ -f "$p" ] || continue
    local xml
    xml="$(security cms -D -i "$p" 2>/dev/null)"
    # 匹配 team 且 app-id 能覆盖 WDA(通配 * 或精确匹配 xctrunner)
    if printf "%s" "$xml" | grep -q "$TEAM_ID" \
       && printf "%s" "$xml" | grep -qiE "${WDA_BUNDLE_ID}|${TEAM_ID}\.\*"; then
      hit=$((hit+1))
    fi
  done
  if [ "$hit" -gt 0 ]; then
    c_ok "找到可用于 WDA 的 provisioning profile($hit 个)。"
  else
    fail "没有可用于当前开发团队和动态 WDA bundle ID 的 provisioning profile。"
    c_warn "这是本机的核心卡点。修复见文末【人工修复步骤】。"
  fi
}

# ── 3. 账号登录态(能否在线自动生成 profile)─────────────────────────────────
check_account_login() {
  c_step "3. 账号登录态(-allowProvisioningUpdates 需要)"
  # keychain 里是否有 Xcode 账号凭证
  if security find-generic-password -s "Xcode-Account" >/dev/null 2>&1; then
    c_ok "检测到账号相关钥匙串凭证(登录态可能有效)。"
  else
    c_warn "未检测到有效的 Xcode 账号钥匙串凭证。"
    c_warn "账号可能已添加但登录会话失效 —— 命令行在线生成 profile 会被拒。"
    c_warn "需在 Xcode GUI 重新登录刷新(见文末步骤)。此项不计入硬失败。"
  fi
}

# ── 4. 设备与 WDA 工程 ────────────────────────────────────────────────────────
check_device_and_proj() {
  c_step "4. 设备连接 & WDA 工程"
  if xcrun devicectl list devices 2>/dev/null | grep -qi connected; then
    c_ok "检测到已连接真机。"
  else
    c_warn "未通过 devicectl 检测到已连接真机(可能需重连/信任)。"
  fi
  if [ -d "$WDA_PROJ" ]; then c_ok "WDA 工程存在。"; else hard_fail "WDA 工程不存在: $WDA_PROJ(先装 xcuitest 驱动)。"; fi
}

# ── 人工修复指引 ─────────────────────────────────────────────────────────────
print_manual_fix() {
  printf "\n\033[1;33m========== 人工修复步骤(一次性,脚本无法代劳)==========\033[0m\n"
  cat <<EOF
根因:WDA 的动态 bundle ID 没有 provisioning profile，自动生成需账号在线登录。
请按下面做一次，之后即可复用本机生成的 bundle ID。

  1) 打开 Xcode → 菜单 Settings(Cmd+,)→ Accounts 标签
  2) 选中用于本机开发签名的账号
       - 若显示需要重新登录/红色提示 → 点击重新登录,输入密码 + 双因素验证码
       - 确保下方能看到对应开发团队
  3) 用 Xcode 打开 WDA 工程:
       open "${WDA_PROJ}"
  4) 选中 TARGETS → WebDriverAgentRunner → Signing & Capabilities:
       - 勾选 "Automatically manage signing"
       - Team 选择与本机 Apple Development 证书匹配的团队
       - 等待 Xcode 自动生成 profile(几秒~几十秒)
  5) 选一次真机为目标设备,Product → Test(Cmd+U)跑一次,让 profile 落地并信任
       - 首次真机运行:iPhone 设置 → 通用 → VPN与设备管理 → 信任开发者证书
  6) 完成后回到终端执行:
       bash scripts/04_wda_signing_check.sh build

  验证成功标志:~/Library/MobileDevice/Provisioning Profiles/ 下出现匹配
  当前开发团队和动态 WDA bundle ID 的 profile。
EOF
  printf "\033[1;33m=======================================================\033[0m\n"
}

# ── build:预构建签名 WDA 到真机 ─────────────────────────────────────────────
build_wda() {
  c_step "构建并签名 WDA 到真机(预构建)"
  c_info "执行 xcodebuild build-for-testing(带 -allowProvisioningUpdates)..."
  ios_mcp_sanitized_env xcodebuild build-for-testing \
    -project "$WDA_PROJ" \
    -scheme WebDriverAgentRunner \
    -destination "id=$DEVICE_UDID" \
    -allowProvisioningUpdates \
    DEVELOPMENT_TEAM="$TEAM_ID" \
    "CODE_SIGN_IDENTITY=Apple Development" \
    PRODUCT_BUNDLE_IDENTIFIER="$WDA_BUNDLE_ID" \
    GCC_TREAT_WARNINGS_AS_ERRORS=0 COMPILER_INDEX_STORE_ENABLE=NO \
    2>&1 | grep -iE "error:|succeeded|signing|profile|install|TEST BUILD" | tail -25
  local rc=${PIPESTATUS[0]}
  if [ "$rc" = "0" ]; then
    python3 "$PROJECT_DIR/mcp_server/signing_identity.py" remember-team "$TEAM_ID" >/dev/null \
      || { c_err "WDA 已构建，但无法保存本机成功团队状态。"; return 1; }
    c_ok "WDA 构建签名成功。后续 Appium 可用 appium:usePrebuiltWDA=true 复用。"
  else
    c_err "WDA 构建失败(rc=$rc)。若报账号登录被拒,请先完成【人工修复步骤】。"
    print_manual_fix
  fi
  return "$rc"
}

# ── main ──────────────────────────────────────────────────────────────────────
main() {
  local mode="${1:-check}"
  c_step "WDA 签名就绪度预检 (mode=$mode)"
  c_info "已从本机签名身份解析开发团队；动态 Bundle=$WDA_BUNDLE_ID"
  c_info "本轮动态选择硬件 UDID=$DEVICE_UDID"

  check_cert
  check_profile
  check_account_login
  check_device_and_proj

  c_step "预检结论"
  # 注意:缺 provisioning profile 不是 build 的硬阻断 —— -allowProvisioningUpdates
  # 会在构建时在线生成。因此 build 模式下即使 profile 缺失也继续尝试构建;
  # 只有证书/工程/设备这类真正的硬前提缺失才阻断。
  if [ "$mode" = "build" ]; then
    if [ "$HARD_FAIL" = "1" ]; then
      c_err "存在硬性前置缺失(证书/工程),无法构建。"
      print_manual_fix
      exit 1
    fi
    [ "$CHECK_PASS" = "1" ] || c_warn "profile 尚未就绪,将依赖 -allowProvisioningUpdates 在线生成。"
    build_wda
  else
    if [ "$CHECK_PASS" = "1" ]; then
      c_ok "签名前置条件齐全。"
      c_info "如需构建签名 WDA 到真机,运行: bash $0 build"
    else
      c_err "签名前置条件不满足(通常是缺 provisioning profile)。"
      print_manual_fix
      exit 1
    fi
  fi
}

main "$@"
