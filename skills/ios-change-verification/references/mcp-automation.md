# ios_ui_automation MCP 按需 UI 自动化

## 目录

- [1. 能力边界](#1-能力边界)
- [2. 按需会话](#2-按需会话)
- [3. 七个工具](#3-七个工具)
- [4. UI 操作闭环](#4-ui-操作闭环)
- [5. 设备与隧道](#5-设备与隧道)
- [6. 故障处理](#6-故障处理)

## 1. 能力边界

`ios_ui_automation` MCP 只负责 WDA UI 自动化与所需隧道：截图、UI XML、点击、链路状态和隧道启停。以下能力已经抽离，禁止通过 MCP 替代：

- 真机 syslog：使用 `tools/ios_log_tool.py`，不依赖 WDA。
- App 本地编译、安装与带环境启动：使用 `tools/ios_app_tool.py`，不依赖 WDA。

只需日志或 App 启动的场景不得启动本 MCP。三工具的完整时序见 `runtime-tools.md`。

## 2. 按需会话

默认底座已经安装。Skill 不执行 import/status/doctor、安装更新、全局注册或 `list_tools` 预检；确需 UI 时直接执行第一次真实命令：

```bash
IOS_UI_PY=~/Documents/ios-verification-toolkit/mcp_server/.venv/bin/python
IOS_UI_BRIDGE=~/.agents/skills/ios-change-verification/scripts/ios_ui_session.py

"$IOS_UI_PY" "$IOS_UI_BRIDGE" start --session <runId> --udid <hardware-udid>
"$IOS_UI_PY" "$IOS_UI_BRIDGE" call --session <runId> tunnel_status '{}'
"$IOS_UI_PY" "$IOS_UI_BRIDGE" stop --session <runId>
```

- 不在 Codex、TRAE、OpenCode 等客户端中全局注册 `ios_ui_automation`。
- 每个 `runId` 使用本用户私有 Unix socket；30 分钟无调用自动退出。
- `start` 只建立按需 MCP 进程，不预先调用 tool。
- bridge 默认定位 `~/Documents/ios-verification-toolkit/mcp_server/server.py`；需要覆盖时使用 `IOS_UI_MCP_SERVER`。旧 `IOS_DEVICE_MCP_SERVER` 仅作为兼容别名保留。
- 第一次 `tunnel_status` 或 `start_tunnel` 记录隧道初始状态；`stop` 只恢复本会话改变的隧道并关闭 MCP，不负责日志清理。
- 多设备选择在轻量设备发现阶段完成；启动 UI MCP 时传入同一硬件 UDID。

## 3. 七个工具

| 工具 | 作用 |
|---|---|
| `screenshot` | 截取当前屏幕 |
| `get_ui_hierarchy` | 获取 UI XML 层级树 |
| `tap` | 按 name 或 point 坐标点击 |
| `device_status` | 查看 Appium、WDA、隧道与前台 App 状态 |
| `tunnel_status` | 查询 RemoteXPC 隧道 |
| `start_tunnel` | 启动当前设备隧道 |
| `stop_tunnel` | 停止当前设备隧道 |

不存在 `start_log_capture`、`stop_log_capture`、`get_logs`、`open_app` 或 `launch_app`。真实调用返回未知工具时，先检查 Skill/Server 版本是否一致，不要回退到旧接口。

## 4. UI 操作闭环

在 App 工具已经启动目标 App、日志工具已经开始采集后，每次只执行一个可观察步骤：

```text
screenshot
→ 理解当前页面与目标控件
→ tap(name=...) 或 tap(x, y)
→ screenshot 确认结果
→ 不符合预期时 get_ui_hierarchy 定位真实 name/label/坐标
```

- 优先按 name 点击；坐标单位是 point，不是截图像素。
- 不连续盲点多个坐标。
- UI MCP 只操作当前前台 App，不负责重新启动或注入环境变量。
- 需要重新启动 App 时回到独立 App 工具，不调用已移除的旧 MCP 工具。

## 5. 设备与隧道

- Appium/XCUITest、WDA 和隧道使用硬件 UDID。
- `devicectl` 的 CoreDevice identifier 属于 App 工具；不要把它误传给要求硬件 UDID 的 WDA 路径。
- 已有 Appium/XCUITest 驱动和 WDA 时直接复用，禁止每轮安装或升级。
- WDA 只在真实 session 明确确认缺失时首次安装；检查失败属于 `unknown`，不得当成缺失并静默重装。
- Appium 运行在 Mac 上，设备上运行的是 WDA，不要混称。

## 6. 故障处理

| 现象 | 处理 |
|---|---|
| `Unknown device UDID` | 核对本轮硬件 UDID 与 `tunnel_status` |
| WDA 证书不可信 | 在设备上重新信任开发者证书 |
| `Timed out while enabling automation mode` | 打开设备的 Enable UI Automation |
| `tap(name=...)` 找不到 | 读取 UI hierarchy 后修正 name 或 point |
| 普通会话仍出现原生 `ios_ui_automation` | 存在全局 MCP 注册；由用户清理并新开会话 |
| UI MCP 启动失败 | 清理当前场景日志和临时设施，报告 bridge/server/Appium/WDA/隧道层级；不运行安装器或升级 |
| 日志为空 | 离开本 MCP 排查 `ios_log_tool.py` 的时序、marker 与设备连接 |
| App 未启动或环境变量无效 | 离开本 MCP 排查 `ios_app_tool.py launch --terminate-existing` |

底层安装、签名和隧道说明见 `~/Documents/ios-verification-toolkit/README.md`。
