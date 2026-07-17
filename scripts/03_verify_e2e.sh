#!/usr/bin/env bash
#
# 03_verify_e2e.sh — 端到端验证:截图 + 可选目标 App 点击/激活
# ─────────────────────────────────────────────────────────────────────────────
# 在【单一进程生命周期】内完成:
#   1) 启动 Appium(严格保密 127.0.0.1),作为本脚本子进程,trap 退出时清理
#   2) 创建 session -> 触发 WDA 构建签名到真机
#   3) 回到主屏 -> 截图(验证截屏能力)
#   4) 若配置目标 label/bundle ID，则模拟点击或直接激活目标 App
#   5) 再截图并导出 UI 层级
#   6) 删除 session、停止 Appium
#
# 仅用 curl 驱动 Appium 的 HTTP/JSON 接口,不依赖任何 Python/Node 客户端包。
# 截图、状态文件、日志分别落在 .runtime/ 的对应子目录。
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 清除可能污染 Appium 子进程 xcodebuild 的编译器环境变量。
unset CC CXX
DEVELOPER_DIR="$(python3 "$PROJECT_DIR/mcp_server/xcode_resolver.py" --tool xcodebuild)" || exit 2
export DEVELOPER_DIR

source "$PROJECT_DIR/scripts/lib/sanitize_env.sh"
HOST=127.0.0.1; PORT=4723; BASE="http://$HOST:$PORT"
RUNTIME_ROOT="$(python3 "$PROJECT_DIR/mcp_server/runtime_paths.py" root --create)" || exit 2
LOG_DIR="$RUNTIME_ROOT/logs"
SCREENSHOT_DIR="$RUNTIME_ROOT/screenshots"
STATE_DIR="$RUNTIME_ROOT/state"
CAPS="$PROJECT_DIR/scripts/session_caps.json"
WDA_BUNDLE_ID="$(python3 "$PROJECT_DIR/mcp_server/wda_bundle_id.py")" || exit 2
TEAM_ID="$(python3 "$PROJECT_DIR/mcp_server/signing_identity.py" team-id)" || exit 2
DEVICE_UDID="$(python3 "$PROJECT_DIR/mcp_server/device_discovery.py" resolve --field udid)" || exit 2
export IOS_MCP_UDID="$DEVICE_UDID"

# 可选目标 App。label 使用逗号分隔；均不设置时只验证截图与 UI 层级。
TARGET_LABELS=()
CONFIGURED_TARGET_LABELS="$(python3 "$PROJECT_DIR/mcp_server/local_config.py" get target_labels)" || exit 2
TARGET_LABEL_VALUE="${IOS_MCP_TARGET_LABELS:-$CONFIGURED_TARGET_LABELS}"
if [ -n "$TARGET_LABEL_VALUE" ]; then
  IFS=',' read -r -a TARGET_LABELS <<< "$TARGET_LABEL_VALUE"
fi
CONFIGURED_TARGET_BUNDLE="$(python3 "$PROJECT_DIR/mcp_server/local_config.py" get target_bundle_id)" || exit 2
TARGET_BUNDLE_ID="${IOS_MCP_TARGET_BUNDLE:-${TARGET_BUNDLE_ID:-$CONFIGURED_TARGET_BUNDLE}}"

log(){ printf "\033[36m[E2E]\033[0m %s\n" "$*"; }
ok(){  printf "\033[32m[ OK]\033[0m %s\n" "$*"; }
err(){ printf "\033[31m[ERR]\033[0m %s\n" "$*" >&2; }

APPIUM_PID=""
SESSION_ID=""
cleanup(){
  log "清理:删除 session、停止 Appium"
  [ -n "$SESSION_ID" ] && curl -s -X DELETE "$BASE/session/$SESSION_ID" >/dev/null 2>&1
  [ -n "$APPIUM_PID" ] && kill "$APPIUM_PID" 2>/dev/null
  pkill -f "appium --address $HOST" 2>/dev/null
}
trap cleanup EXIT

# ── 1. 启动 Appium(本脚本子进程)────────────────────────────────────────────
log "启动 Appium(仅监听 $HOST:$PORT)"
# env -u CC -u CXX 双保险:确保 Appium fork 出的 xcodebuild 不继承被污染的编译器变量
ios_mcp_sanitized_env env -u CC -u CXX appium --address "$HOST" --port "$PORT" \
       --log "$LOG_DIR/appium-server.log" --log-level info \
       < /dev/null > "$LOG_DIR/appium-e2e.log" 2>&1 &
APPIUM_PID=$!
for i in $(seq 1 30); do
  curl -s "$BASE/status" 2>/dev/null | grep -q '"ready":true' && break
  sleep 1
done
curl -s "$BASE/status" | grep -q '"ready":true' || { err "Appium 未就绪"; exit 1; }
ok "Appium 就绪 (PID=$APPIUM_PID),端口绑定:"
lsof -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null | sed 's/^/    /'

# ── 2. 创建 session(WDA 首次会构建签名,耗时较长)──────────────────────────
log "创建 session -> 触发 WDA 构建签名到真机(首次可能数分钟)"
CAPS_JSON="$(python3 - "$CAPS" "$DEVICE_UDID" "$WDA_BUNDLE_ID" "$TEAM_ID" <<'PY'
import json
import sys

with open(sys.argv[1]) as handle:
    payload = json.load(handle)
payload["capabilities"]["alwaysMatch"]["appium:udid"] = sys.argv[2]
payload["capabilities"]["alwaysMatch"]["appium:updatedWDABundleId"] = sys.argv[3]
payload["capabilities"]["alwaysMatch"]["appium:xcodeOrgId"] = sys.argv[4]
print(json.dumps(payload, separators=(",", ":")))
PY
)" || { err "生成动态 Appium capabilities 失败"; exit 2; }
log "本轮动态选择硬件 UDID: $DEVICE_UDID"
log "本机动态 WDA bundle ID: $WDA_BUNDLE_ID"
curl -sS -X POST "$BASE/session" -H 'Content-Type: application/json' \
     --data "$CAPS_JSON" --max-time 360 -o "$STATE_DIR/session_response.json" \
     -w "    HTTP=%{http_code} TIME=%{time_total}s\n"

SESSION_ID="$(python3 -c "import json,sys;d=json.load(open('$STATE_DIR/session_response.json'));print(d.get('value',{}).get('sessionId',''))" 2>/dev/null)"
if [ -z "$SESSION_ID" ]; then
  err "session 创建失败,响应如下:"; head -c 2000 "$STATE_DIR/session_response.json"; echo; exit 2
fi
ok "session 已建立: $SESSION_ID  (WDA 已构建签名并运行在真机)"

# ── 3. 回主屏 + 截图(验证截屏能力)──────────────────────────────────────────
log "回到主屏幕(mobile: pressButton home)"
# 优先用 mobile: pressButton 按 home 键(比 wda/homescreen 在新 iOS 上更可靠)
curl -s -X POST "$BASE/session/$SESSION_ID/execute/sync" \
     -H 'Content-Type: application/json' \
     -d '{"script":"mobile: pressButton","args":[{"name":"home"}]}' >/dev/null 2>&1
# 兜底再调一次 wda/homescreen
curl -s -X POST "$BASE/session/$SESSION_ID/wda/homescreen" >/dev/null 2>&1
sleep 2
log "截图 #1(主屏)-> $SCREENSHOT_DIR/01_home.png"
curl -s "$BASE/session/$SESSION_ID/screenshot" -o "$STATE_DIR/_shot1.json"
python3 -c "import json,base64;d=json.load(open('$STATE_DIR/_shot1.json'));open('$SCREENSHOT_DIR/01_home.png','wb').write(base64.b64decode(d['value']))" \
  && ok "截图保存成功: $(ls -l "$SCREENSHOT_DIR/01_home.png" | awk '{print $5}') bytes" \
  || { err "截图失败"; head -c 500 "$STATE_DIR/_shot1.json"; }

# ── 4. 可选目标 App 操作:先尝试 label 点击,再按 bundle ID 激活 ─────────────
find_and_tap(){
  local label="$1"
  log "在主屏查找图标: name='$label'"
  local body resp el
  body=$(printf '{"using":"name","value":"%s"}' "$label")
  resp=$(curl -s -X POST "$BASE/session/$SESSION_ID/element" \
         -H 'Content-Type: application/json' -d "$body")
  el=$(python3 -c "import json,sys;d=json.loads('''$resp''');v=d.get('value',{});print(v.get('ELEMENT') or v.get('element-6066-11e4-a52e-4f735466cecf') or '')" 2>/dev/null)
  if [ -n "$el" ]; then
    log "找到图标元素 $el,执行点击"
    curl -s -X POST "$BASE/session/$SESSION_ID/element/$el/click" >/dev/null 2>&1
    return 0
  fi
  return 1
}

current_app(){
  curl -s -X POST "$BASE/session/$SESSION_ID/execute/sync" \
       -H 'Content-Type: application/json' \
       -d '{"script":"mobile: activeAppInfo","args":[]}' \
    | python3 -c "import json,sys;print(json.load(sys.stdin).get('value',{}).get('bundleId',''))" 2>/dev/null
}
if [ "${#TARGET_LABELS[@]}" -gt 0 ]; then
  for lbl in "${TARGET_LABELS[@]}"; do
    if find_and_tap "$lbl"; then ok "已点击主屏图标: $lbl"; break; fi
  done
  sleep 3
fi

if [ -n "$TARGET_BUNDLE_ID" ]; then
  front="$(current_app)"
  log "当前前台 App bundle ID: ${front:-未知}"
  if [ "$front" != "$TARGET_BUNDLE_ID" ]; then
    log "改用配置的 bundle ID 激活目标 App"
    curl -s -X POST "$BASE/session/$SESSION_ID/execute/sync" \
         -H 'Content-Type: application/json' \
         -d "{\"script\":\"mobile: activateApp\",\"args\":[{\"bundleId\":\"${TARGET_BUNDLE_ID}\"}]}" >/dev/null 2>&1
    sleep 4
    front="$(current_app)"
  fi
  if [ "$front" = "$TARGET_BUNDLE_ID" ]; then
    ok "目标 App 已在前台运行 ✅"
  else
    err "未能确认目标 App 在前台。已导出 page source 供排查。"
    curl -s "$BASE/session/$SESSION_ID/source" -o "$STATE_DIR/page_source.xml" 2>/dev/null
  fi
else
  log "未配置目标 App，跳过 App 激活。"
fi

# ── 5. 再截图确认 + 导出当前界面图层(UI树)────────────────────────────────
sleep 1
log "截图 #2(可选操作后)-> $SCREENSHOT_DIR/02_after_action.png"
curl -s "$BASE/session/$SESSION_ID/screenshot" -o "$STATE_DIR/_shot2.json"
python3 -c "import json,base64;d=json.load(open('$STATE_DIR/_shot2.json'));open('$SCREENSHOT_DIR/02_after_action.png','wb').write(base64.b64decode(d['value']))" \
  && ok "截图保存成功: $(ls -l "$SCREENSHOT_DIR/02_after_action.png" | awk '{print $5}') bytes" \
  || err "截图失败"

log "导出当前界面图层(UI 层级树)-> $STATE_DIR/ui_hierarchy.xml"
curl -s "$BASE/session/$SESSION_ID/source" -o "$STATE_DIR/_src.json" 2>/dev/null
python3 -c "import json;d=json.load(open('$STATE_DIR/_src.json'));open('$STATE_DIR/ui_hierarchy.xml','w').write(d.get('value',''))" 2>/dev/null \
  && ok "界面图层已保存: $(ls -l "$STATE_DIR/ui_hierarchy.xml" | awk '{print $5}') bytes" \
  || err "界面图层导出失败"
rm -f "$STATE_DIR/_shot1.json" "$STATE_DIR/_shot2.json" "$STATE_DIR/_src.json"

ok "端到端验证流程执行完毕。截图在 $SCREENSHOT_DIR,状态在 $STATE_DIR,日志在 $LOG_DIR"
log "session 与 Appium 将在脚本退出时自动清理(trap)。"
