# ios-verification-toolkit

在 **iOS 真机**上提供三组相互独立的本地能力：**轻量日志采集 · App 编译/安装/启动 · UI 自动化**。
日志直接使用 `idevicesyslog`，App 生命周期直接使用 `xcodebuild + devicectl`；只有截图、界面树和点击使用 **WebDriverAgent(WDA) + Appium MCP**。因此只需日志或启动 App 时不会加载 WDA。所有能力均只在本机运行。

---

## 一、三层架构

```
┌─ Skill / AI Agent ──────────────────────────────────────────────┐
├─ tools/ios_log_tool.py ──> idevicesyslog ──> 真机 syslog       │
├─ tools/ios_app_tool.py ──> xcodebuild/devicectl ──> App         │
└─ ios_ui_automation 按需 MCP ──> Appium/WDA ──> 截图/UI 树/点击/隧道 │
└─────────────────────────────────────────────────────────────────┘
```

- **tools/** = 不依赖 WDA 的轻量日志工具与 App 本地工具
- **scripts/** = UI MCP 底座和本机依赖的环境安装器
- **mcp_server/** = 只负责截图、UI 树、点击和隧道的 WDA MCP

原理硬约束:iOS 真机的"跨 App 点击/读屏"只有苹果 **XCUITest** 能做,而 XCUITest 必须以"测试 Runner App(即 WDA)"形式**签名安装到真机**;iOS 17+ 还需 **RemoteXPC 隧道(sudo)**。这些是所有真机 UI 自动化方案(Appium/idb/Fastbot)的共同前提,无法绕过。

---

## 二、从零安装(推荐:一条命令 + 按提示完成人工步骤)

```bash
cd ~/Documents/ios-verification-toolkit
bash scripts/run_all.sh
```

安装源和多团队签名均通过环境变量配置，不在项目中保存真实值：

```bash
export IOS_MCP_NPM_REGISTRY="<TRUSTED_NPM_REGISTRY>"
export IOS_MCP_PYPI="<TRUSTED_PYPI_INDEX>"
# 仅当本机存在多个 Apple Development 团队时需要：
export IOS_MCP_TEAM_ID="<APPLE_TEAM_ID>"
# 端到端脚本需要操作具体 App 时可选：
export IOS_MCP_TARGET_BUNDLE="<TARGET_APP_BUNDLE_ID>"
export IOS_MCP_TARGET_LABELS="<LABEL_1>,<LABEL_2>"
```

> ⚠️ **设备 UDID 不写入配置**：每次启动时通过 `devicectl` 发现当前连接的物理 iPhone/iPad。单台自动选择；零台报错；多台时 Skill 会用自然语言列出全部候选并等待用户回复序号、设备名称或硬件 UDID，再用 `IOS_MCP_UDID`、`IOS_MCP_DEVICE_NAME`、`--udid` 或 `--device-name` 消歧，绝不静默取第一台。
> - Apple Team ID 不写入项目：优先复用本机既有 WDA 构建的签名团队；否则单一开发团队自动发现，仍有歧义时才通过 `IOS_MCP_TEAM_ID` 显式选择。
> - `updatedWDABundleId` 默认由当前 Mac 的稳定硬件标识哈希实时组装；原始硬件 UUID 不写入配置或日志。同一台 Mac 保持一致，不同 Mac 自动得到不同 ID。只有兼容已有 WDA 时才显式设置 `IOS_MCP_WDA_BUNDLE_ID` 覆盖。
> - 日志、截图和运行时状态默认分别写入 `.runtime/logs/`、`.runtime/screenshots/`、`.runtime/state/`；整个 `.runtime/` 已加入 `.gitignore`。需要改位置时可设置 `IOS_MCP_RUNTIME_DIR`：相对路径会放在 `.runtime/` 下，绝对路径应指向仓库外的私有目录。
> - `mcp_server/.venv/` 只是本机依赖环境，不属于 Git 必需内容；`.venv/` 已整体忽略。手工压缩或复制项目时也应主动排除它，因为其中会记录本机 Python 和项目绝对路径。
> - `DEVELOPER_DIR` 默认由 `xcode-select -p` 解析，也可显式覆盖。
> - 前提:目标机已装 Xcode、已在 Xcode 登录开发者账号、iPhone 已开启开发者模式。

`run_all.sh` 会依次执行 6 个阶段。**自动步骤自动跑;遇到必须人工的步骤(信任证书 / 开 UI 自动化 / 建隧道等):**
- **在交互终端里**:打印指引并**原地等待你输入 `Done` 继续**(完成后脚本自动复验并续跑,无需重跑);
- **在后台/无终端环境**:打印指引并退出(码 10),你照做后重新运行同一命令即可断点续跑。

已完成的阶段会自动跳过。

| 阶段 | 内容 | 是否人工 |
|---|---|---|
| 1 | 安装 Appium + xcuitest 驱动 + libimobiledevice | 自动(使用 `IOS_MCP_NPM_REGISTRY` 或本机已有 npm 源) |
| 2 | WDA 签名构建到真机 | 自动;失败会引导你在 **Xcode 登录账号 + 勾选自动签名** |
| 3 | 设备信任证书 + 开启 UI 自动化 | **人工**:iPhone 上信任证书 + 打开 Enable UI Automation |
| 4 | 建立 RemoteXPC 隧道(iOS17+ 必需) | **人工**:另开终端按目标 UDID 启动 `tunnel-creation`(常驻) |
| 5 | 安装 MCP Server 依赖(venv) | 自动(使用 `IOS_MCP_PYPI` 或本机 `PIP_INDEX_URL`) |
| 6 | MCP 端到端验证(截图/可选目标 App/界面图层) | 自动 |

查看进度:`bash scripts/run_all.sh --status`

### 人工步骤的细节(iOS 真机自动化的安全关卡,每道都需显式授权)

**阶段3 · 信任证书 + 开启 UI 自动化**(WDA 首次装机后,两个都要做)
> ① 信任证书:iPhone → 设置 → 通用 → VPN与设备管理 → "开发者App"下选你的开发者证书 → 点"信任"
> ② 开启 UI 自动化:iPhone → 设置 → 开发者(Developer)→ 找到 **Enable UI Automation** → 打开
>
> ⚠️ 两个都必须做。缺信任 → WDA 装了启动不了;缺 UI Automation → WDA 启动了但报
>   `Timed out while enabling automation mode`(XCUITest 无法进入自动化模式)。
> ⚠️ 每次**卸载重装 WDA 后**都要重新信任一次(新证书需重新授权)。

**阶段4 · RemoteXPC 隧道**(必须 sudo,需常驻)
```bash
# 另开一个终端窗口执行,保持开着不要关:
DEVICE_UDID="$(python3 mcp_server/device_discovery.py resolve --field udid)"
sudo appium driver run xcuitest tunnel-creation -- \
  --udid "$DEVICE_UDID" --tunnel-registry-port 42314
```
想免每次输密码 → 一次性配置 NOPASSWD(仅对该命令免密,不全局放开):
```bash
bash scripts/06_setup_tunnel_sudoers.sh          # 先看将写入的规则
bash scripts/06_setup_tunnel_sudoers.sh --apply  # 写入(需一次 sudo)
bash scripts/06_setup_tunnel_sudoers.sh --start-tunnel   # 之后可免密后台拉起
```

---

## 三、按需接入（Skill 推荐方式）

不要把 `ios_ui_automation` 同时注册到 Codex、TRAE、OpenCode 的全局 MCP 配置；这会让每个普通会话都初始化或携带工具定义。全局 Skill 位于 `~/.agents/skills/ios-change-verification`，触发后使用同一套本地桥接命令：

```bash
PY=~/Documents/ios-verification-toolkit/mcp_server/.venv/bin/python
BRIDGE=~/.agents/skills/ios-change-verification/scripts/ios_ui_session.py

# 可选：先查看当时连接的候选设备
"$PY" "$BRIDGE" devices

# 单台自动选择；多台时添加 --udid <硬件UDID> 或 --device-name <唯一名称>
"$PY" "$BRIDGE" start --session 20260710_example
"$PY" "$BRIDGE" call --session 20260710_example tunnel_status '{}'
"$PY" "$BRIDGE" stop --session 20260710_example
```

桥接器只在确需 UI 操作时拉起 MCP stdio 子进程，通过本用户的 Unix socket 保持跨调用状态；`stop` 只恢复本轮临时改变的隧道状态并关闭 UI MCP。日志由独立轻量工具负责，30 分钟自动停止且必须单独清理。

只有明确希望所有会话都获得原生 MCP tool 时，才参考 `mcp_server/mcp_config.example.json` 做客户端注册：

```json
{
  "mcpServers": {
    "ios_ui_automation": {
      "command": "/absolute/path/to/ios-verification-toolkit/mcp_server/.venv/bin/python",
      "args": ["/absolute/path/to/ios-verification-toolkit/mcp_server/server.py"],
      "env": {}
    }
  }
}
```

### 独立本地 tools

| tool | 作用 | WDA 依赖 |
|---|---|---|
| `tools/ios_log_tool.py` | `devices/start/read/status/stop`，持续采集并筛选真机 syslog | 无 |
| `tools/ios_app_tool.py` | `build/install/launch`，本地编译、装机并注入调试启动环境 | 无 |

### UI MCP tools

| tool | 作用 | 参数 |
|---|---|---|
| `get_ui_hierarchy` | 获取当前屏幕界面图层(UI 层级树 XML) | — |
| `tap` | 模拟点击 | `x,y`(坐标)或 `name`(控件名)二选一 |
| `screenshot` | 截图 | `save_path`(可选,省略则返回 base64 PNG) |
| `device_status` | 链路/设备状态 | — |
| `tunnel_status` | 查询 RemoteXPC 隧道 | — |
| `start_tunnel` / `stop_tunnel` | 启停 RemoteXPC 隧道 | — |

---

## 四、目录结构

```
ios-verification-toolkit/
├── README.md
├── .runtime/                       # 运行时私有产物(整目录已 ignore)
│   ├── logs/                       #   Appium/WDA/syslog/隧道日志
│   ├── screenshots/                #   自动保存的验证截图
│   └── state/                      #   session 响应、UI 树与临时状态
├── scripts/                        # MCP 环境安装器
│   ├── run_all.sh                  #   ★ 总控编排(从零到 MCP 可用,人工步骤有引导)
│   ├── 01_install_appium.sh        #   装 Appium/驱动/libimobiledevice(可信 npm 源)
│   ├── 02_start_appium.sh          #   严格保密启动 Appium(仅 127.0.0.1){start|stop|status}
│   ├── 03_verify_e2e.sh            #   端到端验证:截图+可选目标 App+界面图层
│   ├── 04_wda_signing_check.sh     #   WDA 签名预检 + 构建 {check|build}
│   ├── 05_install_mcp.sh           #   装 MCP 依赖(venv,可信 PyPI){--venv|--check}
│   ├── 06_setup_tunnel_sudoers.sh  #   (可选)隧道 NOPASSWD 免密配置
│   ├── 99_uninstall_cleanup.sh     #   卸载清理(含 MCP venv、设备 WDA)
│   └── session_caps.json           #   Appium session 能力
├── tools/                          # 不依赖 WDA 的独立运行时工具
│   ├── ios_log_tool.py             #   idevicesyslog 轻量日志 session
│   └── ios_app_tool.py             #   xcodebuild/devicectl 编译、安装、启动
└── mcp_server/                     # MCP Server 本体
    ├── server.py                   #   7 个 UI tool,只连 Appium/WDA
    ├── device_discovery.py         #   实时发现并安全选择当前连接的 iOS 真机
    ├── runtime_paths.py            #   统一解析并保护运行时目录
    ├── signing_identity.py         #   动态解析本机开发团队与证书
    ├── wda_bundle_id.py            #   按当前 Mac 稳定生成唯一 WDA bundle ID
    ├── test_device_discovery.py    #   0/1/多设备与显式选择规则测试
    ├── verify.py                   #   MCP 驱动真机验证
    ├── requirements.txt            #   mcp>=1.27,<2(稳定线) + requests
    └── mcp_config.example.json     #   客户端接入配置样例
```

---

## 五、严格保密设计

- **网络**:Appium 强制 `--address 127.0.0.1`(默认 0.0.0.0 对外暴露,已覆盖),他人连不进。
- **不安全特性**:不加 `--relaxed-security` / `--allow-insecure`,保持默认关闭。
- **供应链**:镜像地址不写入项目；npm 使用 `IOS_MCP_NPM_REGISTRY` 或本机配置，pip 使用 `IOS_MCP_PYPI`/`PIP_INDEX_URL`；不 sudo 装 npm 全局包，MCP 依赖装在隔离 venv。
- **运行时产物**:日志、构建/安装结果、截图和状态统一落在本地 `.runtime/`；根目录及子目录为 `0700`，工具直接创建的日志/JSON/截图为 `0600`，DerivedData 由 `0700` 祖先目录隔离；`.runtime/` 整体已加入 `.gitignore`，不外传。
- **隧道免密**:若配置 NOPASSWD,仅对 `tunnel-creation` 一条命令免密,不全局放开 sudo。

---

## 六、卸载 / 重置

```bash
bash scripts/99_uninstall_cleanup.sh --yes              # 卸载 appium/驱动/venv/设备WDA、清理运行时产物
bash scripts/99_uninstall_cleanup.sh --yes --purge-brew # 连 libimobiledevice 一起卸
DRY_RUN=1 bash scripts/99_uninstall_cleanup.sh --yes    # 预演不执行
# 隧道免密撤销:sudo rm /etc/sudoers.d/appium-tunnel
```

---

## 七、关键坑位备忘(实测)

- **CC/CXX 环境变量污染**:若 shell 将其指向非编译器程序，xcodebuild 会失败；相关脚本/MCP 会在构建前清除这两个变量。
- **UDID 有两种**：统一发现器会同时返回 Appium 所需的硬件 UDID 和 CoreDevice identifier；MCP、WDA 与隧道只使用前者。不要把 CoreDevice UUID 写入 `IOS_MCP_UDID`。
- **设备选择**：`python3 mcp_server/device_discovery.py list` 可查看实时候选；默认只在恰好一台设备时自动选择，多台必须显式消歧。
- **xcode-select 指向**:需指向完整 Xcode；若被切到 `/Library/Developer/CommandLineTools`，设备发现可能失败。可通过 `DEVELOPER_DIR` 覆盖。
- **签名团队**:优先复用既有 WDA 构建的签名团队；没有可复用结果且存在多个 Apple Development 团队时，必须设置 `IOS_MCP_TEAM_ID`。动态 WDA bundle ID 首次使用仍需对应 provisioning profile。
- **iOS 17+ 真机**:必须 `sudo ... tunnel-creation -- --udid <硬件UDID>` 建 RemoteXPC 隧道且 registry 中出现目标设备；只看到进程存在不代表就绪,否则仍可能报 `Unknown device UDID`。显式指定 `--udid` 还能跳过无关 Apple TV 发现。
- **UI 自动化开关**:iPhone 设置 → 开发者 → `Enable UI Automation` 必须打开,否则 WDA 启动后报 `Timed out while enabling automation mode`。
- **卸载重装 WDA 后需重新信任**:设备侧 WDA 被卸载后重装,是新证书,必须再次在"VPN与设备管理"里信任。
- **App 启动**:不要依赖主屏图标位置；使用 `tools/ios_app_tool.py launch` 按调用方提供的 bundle ID 直接启动并按需注入调试环境。
