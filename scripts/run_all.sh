#!/usr/bin/env bash
#
# run_all.sh — 总控编排:从零把「iOS 真机 截图/点击/日志 MCP」搭起来
# ─────────────────────────────────────────────────────────────────────────────
# 定位:本脚本是「MCP 的环境安装器」。它把底座(Appium/驱动/WDA/隧道)与 MCP
#       依赖一步步装好并验证。自动步骤自动跑;遇到必须人工的环节(Xcode 账号登录、
#       设备信任证书、开启 UI 自动化、sudo 建隧道)会:
#         · 交互终端里:打印指引并【原地等待你输入 Done 继续】(完成后自动复验、续跑)
#         · 后台/管道(无 tty):打印指引并退出码 10,你照做后重跑同一命令续跑
#
# 用法:
#   bash scripts/run_all.sh            # 从头跑(交互式,人工步骤原地等 Done)
#   bash scripts/run_all.sh --from N   # 从第 N 阶段开始
#   bash scripts/run_all.sh --status   # 只检测各阶段状态,不执行
#   bash scripts/run_all.sh --non-interactive # 强制非交互(人工步骤走退出10模式)
#
# 阶段:
#   1 安装 Appium + xcuitest 驱动 + libimobiledevice      (自动)
#   2 WDA 签名构建到真机                                   (自动;失败时引导 Xcode 账号登录)
#   3 设备信任开发者证书                                   (人工:iPhone 上点"信任")
#   4 建立 RemoteXPC 隧道(iOS17+真机必需)                (人工:sudo 常驻)
#   5 安装 MCP Server 依赖(venv)                          (自动)
#   6 端到端验证:MCP 驱动真机 截图/可选目标 App/界面图层  (自动)
#   7 安装 Codex / TRAE CLI 共用 Skill                    (自动;不注册全局 MCP)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
umask 077

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$DIR/.." && pwd)"

# 清除可能污染 xcodebuild 的编译器环境变量。
unset CC CXX
DEVELOPER_DIR="$(python3 "$PROJECT_DIR/mcp_server/xcode_resolver.py" --tool xcodebuild)" || exit 2
export DEVELOPER_DIR

source "$DIR/lib/sanitize_env.sh"
# 确保私有运行时目录存在(卸载后可能被删),否则重定向写日志会失败。
RUNTIME_ROOT="$(python3 "$PROJECT_DIR/mcp_server/runtime_paths.py" root --create)" || exit 2
LOG_DIR="$RUNTIME_ROOT/logs"
SCREENSHOT_DIR="$RUNTIME_ROOT/screenshots"
STATE_DIR="$RUNTIME_ROOT/state"

# 配置(与各子脚本 / MCP 保持一致)。UDID 每次运行时从当前连接设备解析；
# IOS_MCP_UDID / IOS_MCP_DEVICE_NAME 只用于多设备消歧，不提供离线默认值。
DEVICE_UDID="$(python3 "$PROJECT_DIR/mcp_server/device_discovery.py" resolve --field udid)" || exit 2
export IOS_MCP_UDID="$DEVICE_UDID"
TEAM_ID="${IOS_MCP_TEAM_ID:-}"
TEAM_CANDIDATES=()
WDA_BUNDLE_ID="$(python3 "$PROJECT_DIR/mcp_server/wda_bundle_id.py")" || exit 2
TUNNEL_REGISTRY_PORT="42314"
WDA_PROJ="$HOME/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj"
WDA_APP_GLOB="$HOME/Library/Developer/Xcode/DerivedData/WebDriverAgent-*/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app"
VENV_PY="$PROJECT_DIR/mcp_server/.venv/bin/python"
FORCE_NONINTERACTIVE=0

c_info(){ printf "\033[36m[INFO]\033[0m  %s\n" "$*"; }
c_ok(){   printf "\033[32m[ OK ]\033[0m  %s\n" "$*"; }
c_warn(){ printf "\033[33m[WARN]\033[0m  %s\n" "$*"; }
c_err(){  printf "\033[31m[FAIL]\033[0m  %s\n" "$*" >&2; }
c_step(){ printf "\n\033[1;35m########## %s ##########\033[0m\n" "$*"; }

load_team_candidates(){
  local candidate=""
  TEAM_CANDIDATES=()
  while IFS= read -r candidate; do
    [ -n "$candidate" ] && TEAM_CANDIDATES+=("$candidate")
  done < <(python3 "$PROJECT_DIR/mcp_server/signing_identity.py" team-candidates)
  if [ "${#TEAM_CANDIDATES[@]}" -eq 0 ]; then
    c_err "未找到可用 Apple Development 签名身份；请先在 Xcode 登录账号并创建开发证书。"
    return 2
  fi
}

remember_team_id(){
  python3 "$PROJECT_DIR/mcp_server/signing_identity.py" remember-team "$TEAM_ID" >/dev/null
}

# 是否可交互(有 tty 且未强制非交互)。后台任务/管道下为 false。
is_interactive(){ [ -t 0 ] && [ "$FORCE_NONINTERACTIVE" != "1" ]; }

# 打印人工操作指引。
# 交互模式:原地等待用户完成并输入 Done(可 recheck 复验),不退出。
# 非交互模式:打印指引后退出码 10(照原方式,完成后重跑续跑)。
# 用法:need_manual "指引文字" [recheck_cmd]
#   recheck_cmd 可选:一个用于复验"人工步骤是否真的完成"的命令;非空且执行失败会提示重试。
need_manual(){
  local guide="$1"; local recheck="${2:-}"
  printf "\n\033[1;33m========== 需要你手动操作 ==========\033[0m\n"
  printf "%s\n" "$guide"
  if ! is_interactive; then
    printf "\033[1;33m完成后,重新运行以继续:  bash scripts/run_all.sh\033[0m\n"
    printf "\033[1;33m====================================\033[0m\n"
    exit 10
  fi
  # 交互模式:等待 Done
  while true; do
    printf "\033[1;36m完成上述操作后输入 Done 回车继续(输入 q 退出): \033[0m"
    local ans; read -r ans < /dev/tty || { echo; exit 10; }
    case "$ans" in
      Done|done|DONE|d)
        if [ -n "$recheck" ]; then
          if eval "$recheck" >/dev/null 2>&1; then
            c_ok "复验通过,继续。"; return 0
          else
            c_warn "复验未通过——上述操作似乎还没生效,请再确认后重试。"
            continue
          fi
        fi
        c_ok "继续。"; return 0 ;;
      q|Q) c_warn "已退出。稍后可重跑 bash scripts/run_all.sh 续跑。"; exit 10 ;;
      *) c_warn "请输入 Done(或 q 退出)。" ;;
    esac
  done
}

# ── 阶段状态检测 ─────────────────────────────────────────────────────────────
stage1_done(){ command -v appium >/dev/null 2>&1 && appium driver list --installed 2>&1 | grep -q xcuitest; }
stage2_done(){
  local app actual
  for app in $WDA_APP_GLOB; do
    [ -f "$app/Info.plist" ] || continue
    actual="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Info.plist" 2>/dev/null || true)"
    case "$actual" in
      "$WDA_BUNDLE_ID"|"$WDA_BUNDLE_ID.xctrunner") return 0 ;;
    esac
  done
  return 1
}
stage4_tunnel_up(){
  curl -fsS --max-time 2 \
    "http://127.0.0.1:${TUNNEL_REGISTRY_PORT}/remotexpc/tunnels/${DEVICE_UDID}?waitMs=1000" \
    >/dev/null 2>&1
}
stage5_done(){ [ -x "$VENV_PY" ] && "$VENV_PY" -c "import mcp,requests" >/dev/null 2>&1; }
stage7_done(){ bash "$DIR/07_install_skill.sh" --check >/dev/null 2>&1; }

# ── 阶段 1:安装 Appium ──────────────────────────────────────────────────────
run_stage1(){
  c_step "阶段 1 / 安装 Appium + 驱动 + libimobiledevice"
  if stage1_done; then c_ok "已安装,跳过。"; return 0; fi
  bash "$DIR/01_install_appium.sh" || { c_err "安装失败"; exit 1; }
  stage1_done && c_ok "阶段1完成。" || { c_err "安装后仍缺 appium/xcuitest 驱动。"; exit 1; }
}

# ── 阶段 2:WDA 签名构建 ─────────────────────────────────────────────────────
_build_wda_once(){
  ios_mcp_sanitized_env env -u CC -u CXX "$DEVELOPER_DIR/usr/bin/xcodebuild" build-for-testing \
    -project "$WDA_PROJ" -scheme WebDriverAgentRunner \
    -destination "id=$DEVICE_UDID" -allowProvisioningUpdates \
    DEVELOPMENT_TEAM="$TEAM_ID" \
    "CODE_SIGN_IDENTITY=Apple Development" \
    PRODUCT_BUNDLE_IDENTIFIER="$WDA_BUNDLE_ID" \
    GCC_TREAT_WARNINGS_AS_ERRORS=0 COMPILER_INDEX_STORE_ENABLE=NO \
    > "$LOG_DIR/wda_build_runall.log" 2>&1
}

is_team_signing_failure(){
  local log="$1"
  grep -qiE "Unable to log in with account|authentication|were rejected|No profiles for|requires a development team|does not have a valid signing|requires a provisioning profile|provisioning profile.*(doesn't|does not|missing|failed)|No Accounts|certificate.*(not found|missing|invalid|expired)|device.*not registered|register.*device|Developer Portal" "$log" 2>/dev/null
}

open_wda_project(){
  if python3 "$PROJECT_DIR/mcp_server/xcode_project.py" "$WDA_PROJ"; then
    c_ok "已用本轮选中的 Xcode 自动打开 WDA 工程。"
    return 0
  fi
  c_warn "无法自动打开 WDA 工程，请确认 Xcode 与 XCUITest 驱动仍然存在。"
  return 1
}

run_stage2(){
  c_step "阶段 2 / WDA 签名构建到真机"
  if stage2_done; then
    TEAM_ID="$(python3 "$PROJECT_DIR/mcp_server/signing_identity.py" team-id 2>/dev/null || true)"
    if [ -n "$TEAM_ID" ]; then
      export IOS_MCP_TEAM_ID="$TEAM_ID"
      remember_team_id || true
    fi
    c_ok "WDA .app 产物已存在,跳过构建。"
    return 0
  fi
  local log="$LOG_DIR/wda_build_runall.log"

  while true; do
    load_team_candidates || exit 2
    local total="${#TEAM_CANDIDATES[@]}" index=0 attempt=0 signing_failures=0
    c_info "自动发现 $total 个签名团队候选；按本机成功记录和现有 WDA 优先尝试(不回显 Team ID)。"

    for TEAM_ID in "${TEAM_CANDIDATES[@]}"; do
      index=$((index + 1))
      export IOS_MCP_TEAM_ID="$TEAM_ID"
      c_info "尝试签名团队候选 $index/$total..."

      for attempt in 1 2; do
        if _build_wda_once && stage2_done; then
          remember_team_id || { c_err "WDA 已构建，但无法保存本机成功团队状态。"; exit 1; }
          c_ok "WDA 构建签名成功；已记住本机成功团队，后续自动复用。"
          return 0
        fi

        if is_team_signing_failure "$log"; then
          signing_failures=$((signing_failures + 1))
          c_warn "候选 $index/$total 无法完成签名，自动尝试下一个候选。"
          rm -rf "$HOME/Library/Developer/Xcode/DerivedData/WebDriverAgent-"* 2>/dev/null
          break
        fi

        if [ "$attempt" = "1" ]; then
          c_warn "构建出现非签名类失败；清理 WDA 缓存后自动重试一次。"
          rm -rf "$HOME/Library/Developer/Xcode/DerivedData/WebDriverAgent-"* 2>/dev/null
          sleep 2
          continue
        fi

        c_err "WDA 构建失败(非签名类错误),日志末尾:"
        tail -20 "$log"
        if is_interactive; then
          need_manual "请查看上面的错误(完整日志: $log)。修复后输入 Done 重试,或 q 退出。"
          continue 3
        fi
        exit 1
      done
    done

    if [ "$signing_failures" -ge "$total" ]; then
      open_wda_project || true
      need_manual "$(cat <<'EOF'
[脚本已自动尝试所有本机有效开发团队，但都无法完成 WDA 签名]
  WDA 工程已使用本轮选中的 Xcode 自动打开。
  现在请在 Xcode 中手动完成一次 WDA 签名构建：
  1. Settings(Cmd+,)→ Accounts：确认开发账号已登录；如有红色提示，完成重新登录和双因素验证。
  2. TARGETS → WebDriverAgentRunner → Signing & Capabilities：选择可用 Team；
     可继续使用 Automatically manage signing，由 Xcode 生成本机 provisioning profile。
  3. 选择当前连接的真机作为运行目标，执行 Product → Test(Cmd+U)，直到签名构建成功。
  4. 完成后回到终端输入 Done；脚本会读取本机签名结果并自动重试，无需输入 Team ID。
EOF
)"
      c_info "将重新发现候选并自动重试..."
      continue
    fi

    c_err "没有团队候选完成 WDA 构建。完整日志: $log"
    exit 1
  done
}

# ── 阶段 3:设备信任 + UI 自动化开关(通过尝试启动 WDA 探测)──────────────────
run_stage3_trust(){
  c_step "阶段 3 / 设备信任证书 + 开启 UI 自动化"
  local alog="$LOG_DIR/appium-server.log"
  local attempt=0
  while true; do
    attempt=$((attempt+1))
    c_info "尝试建立 session 以检测证书信任 / UI 自动化状态(第 $attempt 次,会真正装并启动 WDA)..."
    c_warn "📱 请拿起手机并【保持解锁】:此过程 iOS 可能弹出确认框,若手机有锁屏密码需在【手机上输入锁屏密码】才能继续。"
    bash "$DIR/03_verify_e2e.sh" > "$LOG_DIR/run_all_trust_probe.log" 2>&1 || true

    # 关卡一:证书未信任
    if grep -qiE "not trusted|has not been explicitly trusted" "$alog" 2>/dev/null; then
      need_manual "$(cat <<EOF
[关卡1/2:WDA 已装到设备,但开发者证书未被信任]
请在 iPhone 上操作:
  设置 → 通用 → VPN与设备管理 → "开发者App"下选择你的开发者证书 → 点"信任"
  📱 点"信任"后,若手机有锁屏密码,会弹框要求【在手机上输入锁屏密码】确认。
(注意:每次卸载重装 WDA 后都需重新信任)
EOF
)"
      # 交互模式下用户已输 Done;非交互模式 need_manual 已 exit。这里重新探测。
      continue
    fi
    # 关卡二:UI 自动化未开启
    if grep -qiE "enabling automation mode|Timed out while enabling automation" "$alog" 2>/dev/null; then
      need_manual "$(cat <<EOF
[关卡2/2:证书已信任,但 UI 自动化未开启]
请在 iPhone 上操作:
  设置 → 开发者(Developer)→ 找到 "Enable UI Automation" → 打开
(缺这个开关,WDA 会报 "Timed out while enabling automation mode")
EOF
)"
      continue
    fi
    c_ok "未检测到证书信任 / UI 自动化问题(或均已就绪)。"
    return 0
  done
}

# ── 阶段 4:RemoteXPC 隧道(iOS17+ 真机必需,需 sudo 常驻)────────────────────
run_stage4_tunnel(){
  c_step "阶段 4 / RemoteXPC 隧道(iOS 17+ 真机必需)"
  if stage4_tunnel_up; then c_ok "隧道 registry 已就绪,跳过。"; return 0; fi

  # 已配置免密 → 直接免密后台拉起(全自动)
  if [ -f /etc/sudoers.d/appium-tunnel ]; then
    c_info "检测到已配置免密,自动拉起隧道..."
    IOS_MCP_UDID="$DEVICE_UDID" IOS_MCP_TUNNEL_REGISTRY_PORT="$TUNNEL_REGISTRY_PORT" \
      bash "$DIR/06_setup_tunnel_sudoers.sh" --start-tunnel >/dev/null 2>&1 || true
    sleep 3
    if stage4_tunnel_up; then c_ok "隧道已自动拉起(免密)。"; return 0; fi
    c_warn "免密拉起失败,转手动流程。"
  fi

  # 非交互:打印指引并退出(照原方式,完成后重跑续跑)
  if ! is_interactive; then
    need_manual "$(cat <<EOF
[iOS 17+ 真机自动化需要 RemoteXPC 隧道,且必须 sudo 常驻运行]
请【另开一个终端窗口】执行(会一直运行,不要关闭):

    sudo appium driver run xcuitest tunnel-creation -- \
      --udid "$DEVICE_UDID" --tunnel-registry-port "$TUNNEL_REGISTRY_PORT"

(想以后免密自动拉起,先跑一次:bash scripts/06_setup_tunnel_sudoers.sh --apply)
EOF
)"
  fi

  # 交互:先咨询是否配置"以后免密"
  printf "\n\033[1;36m隧道需要 sudo 启动。是否配置【以后免密自动拉起】?\033[0m\n"
  printf "  y = 现在配置免密(会用 sudo 写一条只针对隧道命令的 NOPASSWD 规则,需你输一次密码)\n"
  printf "  n = 不配置,本次手动拉起隧道即可\n"
  local ans
  while true; do
    printf "\033[1;36m请选择 [y/n]: \033[0m"; read -r ans < /dev/tty || { ans=n; break; }
    case "$ans" in [Yy]*) ans=y; break;; [Nn]*) ans=n; break;; *) c_warn "请输入 y 或 n。";; esac
  done

  if [ "$ans" = "y" ]; then
    # 用隐藏输入(不回显)收集 sudo 密码,传给 06 --apply(内部用 sudo -S 从 stdin 读)。
    # 安全处理:read -s 不回显;用完立即 unset;临时关闭 xtrace 防止进 -x 日志。
    { set +x; } 2>/dev/null
    local _pw _pw2
    while true; do
      printf "\033[1;36m请输入 Mac 登录密码(用于配置免密,不会显示、仅本地传给 sudo): \033[0m"
      read -rs _pw < /dev/tty; echo
      [ -n "$_pw" ] || { c_warn "密码为空,请重输。"; continue; }
      break
    done
    c_info "配置免密中..."
    if SUDO_PW="$_pw" bash "$DIR/06_setup_tunnel_sudoers.sh" --apply; then
      unset _pw _pw2
      c_info "免密已配置,后台拉起隧道..."
      IOS_MCP_UDID="$DEVICE_UDID" IOS_MCP_TUNNEL_REGISTRY_PORT="$TUNNEL_REGISTRY_PORT" \
        bash "$DIR/06_setup_tunnel_sudoers.sh" --start-tunnel
      sleep 3
      if stage4_tunnel_up; then c_ok "隧道已就绪(已配置免密,以后自动拉起)。"; return 0; fi
      c_warn "免密拉起未成功,请按下面手动方式拉起。"
    else
      unset _pw _pw2
      c_warn "免密配置失败(密码错误或权限问题),转手动拉起。"
    fi
  else
    c_info "好的,不配置免密。后续每次需要隧道时,请手动执行:"
    printf "    \033[1;37msudo appium driver run xcuitest tunnel-creation -- --udid %s --tunnel-registry-port %s\033[0m  (常驻,勿关窗口)\n" "$DEVICE_UDID" "$TUNNEL_REGISTRY_PORT"
    c_info "(改主意了随时可跑: bash scripts/06_setup_tunnel_sudoers.sh --apply 配置免密)"
  fi

  # 无论 y 分支未成功或 n 分支:引导手动拉起并等待 Done + 复验 registry
  need_manual "$(cat <<EOF
请【另开一个终端窗口】执行并保持开着:
    sudo appium driver run xcuitest tunnel-creation -- \
      --udid "$DEVICE_UDID" --tunnel-registry-port "$TUNNEL_REGISTRY_PORT"
看到持续输出即成功。
EOF
  )" "curl -fsS --max-time 2 'http://127.0.0.1:${TUNNEL_REGISTRY_PORT}/remotexpc/tunnels/${DEVICE_UDID}?waitMs=1000'"
  c_ok "隧道已就绪。"
}

# ── 阶段 5:安装 MCP 依赖 ────────────────────────────────────────────────────
run_stage5_mcp(){
  c_step "阶段 5 / 安装 MCP Server 依赖(venv)"
  if stage5_done; then c_ok "MCP venv 依赖已就绪,跳过。"; return 0; fi
  bash "$DIR/05_install_mcp.sh" --venv || { c_err "MCP 依赖安装失败"; exit 1; }
  stage5_done && c_ok "阶段5完成。" || { c_err "MCP venv 校验未通过。"; exit 1; }
}

# ── 阶段 6:MCP 端到端验证 ───────────────────────────────────────────────────
run_stage6_verify(){
  c_step "阶段 6 / MCP 端到端验证(驱动真机)"
  if ! stage4_tunnel_up; then
    c_warn "隧道 registry 中未检测到目标设备,MCP 真机调用会失败。请先完成阶段4。"
  fi
  "$VENV_PY" "$PROJECT_DIR/mcp_server/verify.py" 2>&1 | tee "$LOG_DIR/run_all_mcp_verify.log"
  local vlog="$LOG_DIR/run_all_mcp_verify.log"
  # 真正成功的判据:截图已落盘,且没有任何 tool 报错
  if grep -q "截图已保存" "$vlog" 2>/dev/null \
     && ! grep -qiE "Error executing tool|创建 session 失败|Unknown device" "$vlog" 2>/dev/null; then
    c_ok "MCP 驱动真机成功:截图/可选目标App操作/界面图层 均可用。"
  else
    c_warn "验证未完全通过,请查看 $vlog。"
  fi
}

# ── 阶段 7:安装共享 Skill ──────────────────────────────────────────────────
run_stage7_skill(){
  c_step "阶段 7 / 安装 Codex + TRAE CLI 共用 Skill"
  if stage7_done; then c_ok "共享 Skill 已安装,跳过。"; return 0; fi
  bash "$DIR/07_install_skill.sh" || { c_err "共享 Skill 安装失败"; exit 1; }
  stage7_done && c_ok "阶段7完成。" || { c_err "共享 Skill 安装后校验未通过。"; exit 1; }
}

show_status(){
  c_step "各阶段状态"
  c_info "本轮动态选择目标设备硬件 UDID: $DEVICE_UDID"
  stage1_done      && c_ok "阶段1 安装 Appium/驱动: 已完成"        || c_warn "阶段1: 未完成"
  stage2_done      && c_ok "阶段2 WDA 签名构建: 已完成(.app存在)" || c_warn "阶段2: 未完成"
  c_info                 "阶段3 设备信任: 运行时探测"
  stage4_tunnel_up && c_ok "阶段4 RemoteXPC 隧道: 运行中"          || c_warn "阶段4 隧道: 未运行(需 sudo 常驻)"
  stage5_done      && c_ok "阶段5 MCP 依赖: 已就绪"                || c_warn "阶段5: 未完成"
  stage7_done      && c_ok "阶段7 共享 Skill: 已安装"              || c_warn "阶段7: 未安装"
  echo; c_info "运行时目录: $RUNTIME_ROOT"
  c_info "截图目录: $SCREENSHOT_DIR"
  ls -1 "$SCREENSHOT_DIR" 2>/dev/null | sed 's/^/    /' || true
  c_info "状态目录: $STATE_DIR"
  ls -1 "$STATE_DIR" 2>/dev/null | sed 's/^/    /' || true
}

# ── main ──────────────────────────────────────────────────────────────────────
FROM=1
STATUS_ONLY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --status) STATUS_ONLY=1; shift ;;
    --from)
      [ "$#" -ge 2 ] || { c_err "--from 缺少阶段编号"; exit 2; }
      FROM="$2"; shift 2
      ;;
    --non-interactive) FORCE_NONINTERACTIVE=1; shift ;;
    --help|-h)
      sed -n '11,16s/^# \{0,1\}//p' "$0"
      exit 0
      ;;
    *) c_err "未知参数: $1"; exit 2 ;;
  esac
done
[[ "$FROM" =~ ^[1-7]$ ]] || { c_err "阶段编号必须是 1 到 7"; exit 2; }
[ "$STATUS_ONLY" = "1" ] && { show_status; exit 0; }

c_step "ios-verification-toolkit 总控编排(从阶段 $FROM 开始)"
if is_interactive; then
  c_info "自动步骤自动执行；遇到必须人工的步骤会原地等待 Done 后复验并继续。"
else
  c_info "当前为非交互模式；遇到人工步骤将以退出码 10 停止，完成后重跑即可续跑。"
fi
c_info "本轮动态选择目标设备硬件 UDID: $DEVICE_UDID"

[ "$FROM" -le 1 ] && run_stage1
[ "$FROM" -le 2 ] && run_stage2
[ "$FROM" -le 3 ] && run_stage3_trust
[ "$FROM" -le 4 ] && run_stage4_tunnel
[ "$FROM" -le 5 ] && run_stage5_mcp
[ "$FROM" -le 6 ] && run_stage6_verify
[ "$FROM" -le 7 ] && run_stage7_skill

c_step "全流程结束"
show_status
c_ok "MCP 与共享 Skill 已就绪。Codex / TRAE CLI 可按需使用，不要注册到 Agent 全局 MCP 配置。"
