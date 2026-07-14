# iOS 运行时三工具

## 目录

- [1. 边界](#1-边界)
- [2. 设备选择](#2-设备选择)
- [3. 轻量日志工具](#3-轻量日志工具)
- [4. App 本地工具](#4-app-本地工具)
- [5. WDA UI 工具](#5-wda-ui-工具)
- [6. 场景时序与清理](#6-场景时序与清理)

## 1. 边界

```text
ios_log_tool.py  → idevicesyslog                 → 不启动 MCP/Appium/WDA/隧道
ios_app_tool.py  → xcodebuild + devicectl        → 不启动 MCP/Appium/WDA/隧道
ios_ui_automation MCP → screenshot/UI tree/tap/tunnel → 只有 UI 自动化需要 WDA
```

三个工具的成功和失败相互独立。只需要日志或 App 启动时不得启动 UI MCP；UI MCP 失败也不能把已经通过的本地构建或日志采集改判为失败。

```bash
IOS_TOOL_ROOT=~/Documents/ios-verification-toolkit
IOS_LOG_TOOL="$IOS_TOOL_ROOT/tools/ios_log_tool.py"
IOS_APP_TOOL="$IOS_TOOL_ROOT/tools/ios_app_tool.py"
IOS_UI_PY="$IOS_TOOL_ROOT/mcp_server/.venv/bin/python"
IOS_UI_BRIDGE=~/.agents/skills/ios-change-verification/scripts/ios_ui_session.py
```

若不同 shell 调用之间不保留变量，直接展开绝对路径。

## 2. 设备选择

在两项全局门禁通过后，用系统 Python 执行：

```bash
python3 "$IOS_TOOL_ROOT/mcp_server/device_discovery.py" list
```

输出包含硬件 UDID 与 CoreDevice identifier 字段。若当前只通过 libimobiledevice 发现设备，CoreDevice identifier 可能为空，此时 `devicectl` 可使用它明确支持的当前硬件 UDID。零台停止；单台自动选择；多台时把所有候选转换成自然语言让用户选择，收到回复前不得继续。

- `xcodebuild -destination id=...`、`idevicesyslog`、Appium/WDA 使用硬件 UDID。
- `devicectl` 优先使用 CoreDevice identifier；当前 Xcode 也允许硬件 UDID 或唯一设备名。
- 不把任一标识写成长期默认配置。

## 3. 轻量日志工具

日志工具只依赖系统 Python、统一设备发现器和 `idevicesyslog`。每个场景使用独立 session：

```bash
python3 "$IOS_LOG_TOOL" start \
  --session <runId>-<scene> \
  --udid <hardware-udid> \
  --max-seconds 1800

python3 "$IOS_LOG_TOOL" read \
  --session <runId>-<scene> \
  --lines 200 \
  --contains <marker>

python3 "$IOS_LOG_TOOL" stop \
  --session <runId>-<scene> \
  --lines 1000 \
  --contains <marker> \
  --delete-file
```

- `start` 必须发生在 App 启动和用户操作之前；它不是历史日志查询。
- 默认文件在工具仓库的 `.runtime/logs/`，状态在 `.runtime/state/`，权限分别为 `0600` 与目录 `0700`。
- supervisor 最长运行 30 分钟，防止 Agent 中断后永久残留；所有正常和异常退出仍必须显式 `stop --delete-file`。
- 只返回 marker 命中的最小证据；不要回传完整 syslog。
- 日志工具失败时检查设备连接和 `idevicesyslog`，不要启动 WDA“修复”日志。

## 4. App 本地工具

App 工具不使用 Appium/WDA。它只接受已有 `.xcworkspace`/`.xcodeproj`、scheme、已解析依赖和本机签名状态；固定禁止自动依赖解析/更新，不提供 `-allowProvisioningUpdates` 入口。

全局基线构建：

```bash
python3 "$IOS_APP_TOOL" build \
  --workspace <absolute-existing-workspace> \
  --scheme <scheme> \
  --configuration Debug \
  --destination 'generic/platform=iOS'
```

运行时场景构建并把产物放入已忽略的私有目录：

```bash
python3 "$IOS_APP_TOOL" build \
  --workspace <absolute-existing-workspace> \
  --scheme <scheme> \
  --configuration Debug \
  --destination 'id=<hardware-udid>' \
  --derived-data-path "$IOS_TOOL_ROOT/.runtime/state/derived-data/<runId>-<scene>"
```

成功输出中的 `app_paths` 用于安装。若项目使用 `.xcodeproj`，改用 `--project`。

```bash
python3 "$IOS_APP_TOOL" install \
  --device <core-device-identifier> \
  --app <absolute-built-app>

python3 "$IOS_APP_TOOL" launch \
  --device <core-device-identifier> \
  --bundle-id <target-bundle-id> \
  --environment '{"IOS_CODE_VERIFY_RUN_ID":"<runId>","IOS_CODE_VERIFY_SCENARIO":"<scene>"}' \
  --arguments '[]' \
  --terminate-existing
```

- 启动环境只允许验证 marker/场景开关；工具拒绝名称疑似 token、secret、password 等凭据变量。
- 失败详情和 devicectl JSON 只写入 `.runtime`；不要将这些路径中的产物加入目标业务仓库。
- Xcode 选择顺序是显式 `DEVELOPER_DIR`、有效的 `xcode-select`、本机唯一完整 Xcode；多份 Xcode 有歧义时要求显式选择，不写死版本路径。

## 5. WDA UI 工具

只有场景确需截图、UI 树或点击时才启动：

```bash
"$IOS_UI_PY" "$IOS_UI_BRIDGE" start --session <runId> --udid <hardware-udid>
"$IOS_UI_PY" "$IOS_UI_BRIDGE" call --session <runId> tunnel_status '{}'
"$IOS_UI_PY" "$IOS_UI_BRIDGE" call --session <runId> screenshot '{}'
"$IOS_UI_PY" "$IOS_UI_BRIDGE" call --session <runId> get_ui_hierarchy '{}'
"$IOS_UI_PY" "$IOS_UI_BRIDGE" call --session <runId> tap '{"name":"<control>"}'
"$IOS_UI_PY" "$IOS_UI_BRIDGE" stop --session <runId>
```

UI MCP 不再包含日志、App 构建、安装或启动工具。具体 WDA、隧道和恢复规则见 `mcp-automation.md`。

## 6. 场景时序与清理

不需要 UI：

```text
App build → App install → log start → App launch → 触发业务路径 → log stop/delete
```

需要 UI：

```text
App build → App install → UI MCP/tunnel ready → log start → App launch
→ screenshot/tap/UI tree → log stop/delete → UI MCP stop/restore tunnel
```

任一步失败都先停止已启动的日志 session，再清理临时注入；启动过 UI MCP 时再独立停止 UI 会话。不得假设停止 UI MCP 会顺带停止日志，也不得用 WDA launch 代替 App 工具。
