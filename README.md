# ios-verification-toolkit

在 **iOS 真机**上提供三组相互独立的本地能力：**轻量日志采集 · App 编译/安装/启动 · UI 自动化**。
日志直接使用 `idevicesyslog`，App 生命周期直接使用 `xcodebuild + devicectl`；只有截图、界面树和点击使用 **WebDriverAgent(WDA) + Appium MCP**。因此只需日志或启动 App 时不会加载 WDA。所有能力均只在本机运行。

---

## 设计原则

这类工具更适合遵循以下工程原则，而不是笼统称为“MIT 原则”：

- **Local-first**：设备信息、日志、截图、构建产物和凭据只在本机处理。
- **最小权限**：Appium 仅监听 `127.0.0.1`；只有 RemoteXPC 隧道需要受限的 `sudo` 权限。
- **默认安全、专用配置**：拒绝 URL 内嵌凭据、未授权 HTTP 和多个 PyPI 源；需要降低限制时只能通过本机交互式配置工具明确选择。
- **单一职责与按需加载**：日志、App 编译运行和 UI 自动化相互独立；只有 UI 操作才启动 WDA/MCP。
- **最小持久化**：动态发现设备、签名团队和 Bundle ID，运行时文件统一进入被 Git 忽略的私有目录。

MIT 通常指 **MIT License**，它是软件使用和分发许可证，不是开发原则。当前仓库尚未附加 `LICENSE`，因此不声明为 MIT 授权；若决定采用 MIT License，应单独加入完整许可证文件。工程实现则以 **Local-first + Least Privilege + Secure by Default + Separation of Concerns** 为主要准则，比单独使用 KISS、SOLID 或“MIT 原则”更贴合本项目的隐私和设备控制场景。

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

## 二、从零安装(无需单独设置 npm/PyPI 源)

### 1. 安装前提

- macOS 已安装完整 Xcode，并至少启动过一次。
- Xcode 已登录可用于真机签名的 Apple 账号。
- iPhone/iPad 已开启开发者模式并通过 USB 连接。
- 本机已有 Homebrew、Node.js/npm 和 Python 3；缺少 Node.js 或 Python 时可执行：

```bash
brew install node python
```

### 2. 克隆并安装

```bash
git clone https://github.com/seeddestiny/ios-verification-toolkit.git
cd ios-verification-toolkit
bash scripts/run_all.sh
```

正常安装不需要执行任何 `export`，脚本会优先读取本机已有的 npm/pip 设置；本机没有配置时使用对应官方源。安装器只为自身子进程设置源，不会修改 `~/.npmrc`、pip 配置或 Shell 启动文件。

### 3. 源选择优先级

| 顺序 | npm | PyPI |
|---:|---|---|
| 1 | npm 按自身原生优先级解析出的本机有效配置 | pip 按自身原生优先级解析出的本机有效配置 |
| 2 | `https://registry.npmjs.org/` | `https://pypi.org/simple` |

仓库不定义 npm/PyPI 源覆盖项，也不会写入 `.npmrc`、pip 配置或 Shell 启动文件。npm/pip 自身已经能合并当前进程、本项目、用户和全局配置，安装器只读取最终生效值；本机没有自定义值时才使用官方默认源。因此安装命令始终不需要拼接源参数。

所有 registry/index URL 都不得内嵌凭据。PyPI 检测到额外索引会停止，避免依赖混淆；本机 npm 源安装 XCUITest 驱动失败时也不会静默混用官方源。确需允许 HTTP 或单次回退官方 npm 源时，通过下一节的专用工具修改本机安全策略。

### 4. 高级本机配置（通常不需要）

自动选择出现歧义，或确需兼容已有 WDA/固定目标 App 时，运行唯一的交互式配置入口：

```bash
python3 tools/ios_config_tool.py
```

工具会在交互中选择 Xcode、签名团队、WDA Bundle ID、目标 App、运行时目录或供应链安全策略。具体值不会进入命令行历史；配置保存在当前用户的 `~/Library/Application Support/ios-verification-toolkit/config.json`，目录权限为 `0700`、文件为 `0600`，不属于 Git 仓库。可用 `python3 tools/ios_config_tool.py show` 只查看脱敏状态，或用 `python3 tools/ios_config_tool.py reset` 交互式恢复全部自动选择。

PyPI 只安装 Python MCP 适配层的 `mcp` 与 `requests`，不承载 WDA。安装使用不继承系统包的 venv，只接受 wheel，且不会升级 pip。若要彻底只使用 npm，需要把 Python MCP Server/Bridge 改写为 Node/TypeScript，不能直接从 npm 安装 Python 包替代。

> ⚠️ **设备 UDID 不写入配置**：每次启动时通过 `devicectl` 发现当前连接的物理 iPhone/iPad。单台自动选择；零台报错；多台时 Skill 会列出全部候选并等待用户选择，绝不静默取第一台。
> - Apple Team ID 不写入项目或 Git：脚本自动发现全部有效开发证书，按上次成功团队、既有 WDA、其余候选的顺序尝试。只有签名类失败才切换团队；首个成功结果以 `0600` 权限保存在被 Git 忽略的 `.runtime/state/signing-team.json`，后续自动复用。普通安装无需查询或输入 Team ID。
> - `updatedWDABundleId` 默认由当前 Mac 的稳定硬件标识哈希实时组装；原始硬件 UUID 不写入配置或日志。同一台 Mac 保持一致，不同 Mac 自动得到不同 ID。只有兼容已有 WDA 时才通过本机配置工具固定。
> - 日志、截图和运行时状态默认分别写入 `.runtime/logs/`、`.runtime/screenshots/`、`.runtime/state/`；整个 `.runtime/` 已加入 `.gitignore`。需要仓库外目录时通过本机配置工具修改。
> - `mcp_server/.venv/` 只是本机依赖环境，不属于 Git 必需内容；`.venv/` 已整体忽略。手工压缩或复制项目时也应主动排除它，因为其中会记录本机 Python 和项目绝对路径。
> - Xcode 默认使用有效且包含 `xcodebuild` 的 `xcode-select`，否则自动使用本机唯一一份完整 Xcode；安装多份时由本机配置工具交互选择。解析结果只传给当前安装/MCP 子进程，不执行 `sudo xcode-select --switch`，也不修改其它应用使用的系统全局配置。
> - 前提:目标机已装 Xcode、已在 Xcode 登录开发者账号、iPhone 已开启开发者模式。

`run_all.sh` 会依次执行 7 个阶段。**自动步骤自动跑;遇到必须人工的步骤(信任证书 / 开 UI 自动化 / 建隧道等):**
- **在交互终端里**:打印指引并**原地等待你输入 `Done` 继续**(完成后脚本自动复验并续跑,无需重跑);
- **在后台/无终端环境**:打印指引并退出(码 10),你照做后重新运行同一命令即可断点续跑。

已完成的阶段会自动跳过。

| 阶段 | 内容 | 是否人工 |
|---|---|---|
| 1 | 安装 Appium + xcuitest 驱动 + libimobiledevice | 自动(本机 npm 配置优先，否则使用官方源) |
| 2 | WDA 签名构建到真机 | 自动；签名失败会用本轮选中的 Xcode 自动打开 WDA 工程，再引导账号登录和自动签名 |
| 3 | 设备信任证书 + 开启 UI 自动化 | **人工**:iPhone 上信任证书 + 打开 Enable UI Automation |
| 4 | 建立 RemoteXPC 隧道(iOS17+ 必需) | **人工**:另开终端按目标 UDID 启动 `tunnel-creation`(常驻) |
| 5 | 安装 MCP Server 依赖(venv) | 自动(本机 pip 配置优先，否则使用官方 PyPI；始终单一源) |
| 6 | MCP 端到端验证(截图/可选目标 App/界面图层) | 自动 |
| 7 | 安装 Codex / TRAE CLI 共用 Skill | 自动(链接到 `~/.agents/skills`，不注册全局 MCP) |

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
# 自动发现当前设备并在后台启动；未配置免密时会正常提示 sudo：
bash scripts/06_setup_tunnel_sudoers.sh --start-tunnel
```
想免每次输密码 → 一次性配置 NOPASSWD(仅对该命令免密,不全局放开):
```bash
bash scripts/06_setup_tunnel_sudoers.sh          # 先看将写入的规则
bash scripts/06_setup_tunnel_sudoers.sh --apply  # 写入(需一次 sudo)
bash scripts/06_setup_tunnel_sudoers.sh --start-tunnel   # 之后可免密后台拉起
```

---

## 三、按需接入（Skill 推荐方式）

不要把 `ios_ui_automation` 同时注册到 Codex、TRAE CLI、OpenCode 的全局 MCP 配置；这会让每个普通会话都初始化或携带工具定义。仓库内 `skills/ios-change-verification/` 是可版本管理的 Skill 源码；`run_all.sh` 的阶段 7 会自动把它以软链接安装到 Codex 与 TRAE CLI 都会扫描的 `~/.agents/skills/ios-change-verification`。因此仓库更新后无需重复复制，Agent 新会话即可使用同一版本。

只安装或检查 Skill 配置时，可独立执行：

```bash
bash scripts/07_install_skill.sh
bash scripts/07_install_skill.sh --check
```

安装器不会覆盖目标位置已有的真实目录或指向其它位置的软链接；发现冲突时会保留原内容并停止，等待人工确认。Skill 被发现后仍不会创建全局 MCP 注册，只有实际需要截图、UI 树或点击时才会按需启动 UI MCP。

```bash
# 可选：先查看当时连接的候选设备
mcp_server/.venv/bin/python skills/ios-change-verification/scripts/ios_ui_session.py devices

# 单台自动选择；多台由 Skill 先询问用户并在内部完成本轮选择
mcp_server/.venv/bin/python skills/ios-change-verification/scripts/ios_ui_session.py start --session 20260710_example
mcp_server/.venv/bin/python skills/ios-change-verification/scripts/ios_ui_session.py call --session 20260710_example tunnel_status '{}'
mcp_server/.venv/bin/python skills/ios-change-verification/scripts/ios_ui_session.py stop --session 20260710_example
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
│   ├── 07_install_skill.sh         #   安装 Codex/TRAE CLI 共用 Skill(软链接)
│   ├── 99_uninstall_cleanup.sh     #   卸载清理(含 Skill 链接、MCP venv、设备 WDA)
│   └── session_caps.json           #   Appium session 能力
├── tools/                          # 不依赖 WDA 的独立运行时工具
│   ├── ios_log_tool.py             #   idevicesyslog 轻量日志 session
│   ├── ios_app_tool.py             #   xcodebuild/devicectl 编译、安装、启动
│   └── ios_config_tool.py          #   交互修改私有本机高级配置
├── skills/
│   └── ios-change-verification/    # 可安装的 Codex Skill 源码与按需 UI 桥接器
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
- **供应链**:npm 与 pip 都使用各自解析出的本机有效配置，否则使用官方源；仓库不内置公司 npm/PyPI 域名，也不提供项目级源覆盖变量。本机 npm 源缺包时必须通过交互式配置工具授权，才能为驱动安装单次回退官方源。pip 始终只使用单一 index，不接受额外索引或 URL 内嵌凭据。Appium 不使用 sudo 全局安装，MCP Python 依赖装在隔离 venv。
- **运行时产物**:日志、构建/安装结果、截图和状态统一落在本地 `.runtime/`；根目录及子目录为 `0700`，工具直接创建的日志/JSON/截图为 `0600`，DerivedData 由 `0700` 祖先目录隔离；`.runtime/` 整体已加入 `.gitignore`，不外传。
- **隧道免密**:若配置 NOPASSWD,仅对 `tunnel-creation` 一条命令免密,不全局放开 sudo。

---

## 六、卸载 / 重置

```bash
bash scripts/99_uninstall_cleanup.sh --yes              # 卸载 appium/驱动/venv/设备WDA/Skill链接、清理运行时产物
bash scripts/99_uninstall_cleanup.sh --yes --purge-brew # 连 libimobiledevice 一起卸
bash scripts/99_uninstall_cleanup.sh --yes --dry-run    # 预演不执行
# 隧道免密撤销:sudo rm /etc/sudoers.d/appium-tunnel
```

卸载脚本只删除确实指向当前仓库的 `~/.agents/skills/ios-change-verification` 软链接；用户自行维护的目录或其它链接不会被删除。

---

## 七、关键坑位备忘(实测)

- **CC/CXX 环境变量污染**:若 shell 将其指向非编译器程序，xcodebuild 会失败；相关脚本/MCP 会在构建前清除这两个变量。
- **UDID 有两种**：统一发现器会同时返回 Appium 所需的硬件 UDID 和 CoreDevice identifier；MCP、WDA 与隧道只使用前者。设备标识只在当前运行内部传递，不写入长期配置。
- **设备选择**：`python3 mcp_server/device_discovery.py list` 可查看实时候选；默认只在恰好一台设备时自动选择，多台必须显式消歧。
- **Xcode 选择**:`xcode-select` 指向 `/Library/Developer/CommandLineTools` 时，脚本会忽略该无效候选并自动使用本机唯一一份完整 Xcode；若存在多份完整 Xcode，运行本机配置工具交互选择即可，无需也不建议修改全局 `xcode-select`。
- **签名团队**:自动发现有效 Apple Development 团队并逐个验证，优先复用上次成功团队和既有 WDA；签名类失败才切换候选，非签名错误不会被换团队掩盖。成功团队只保存在权限为 `0600` 的 Git 忽略运行时状态中。动态 WDA bundle ID 首次成功时由 Xcode 自动管理对应 provisioning profile。
- **iOS 17+ 真机**:必须通过隧道脚本为当前设备建立 RemoteXPC 隧道，且 registry 中出现目标设备；只看到进程存在不代表就绪，否则仍可能报 `Unknown device UDID`。脚本会自动传递当前硬件 UDID，避免无关 Apple TV 发现干扰。
- **UI 自动化开关**:iPhone 设置 → 开发者 → `Enable UI Automation` 必须打开,否则 WDA 启动后报 `Timed out while enabling automation mode`。
- **卸载重装 WDA 后需重新信任**:设备侧 WDA 被卸载后重装,是新证书,必须再次在"VPN与设备管理"里信任。
- **App 启动**:不要依赖主屏图标位置；使用 `tools/ios_app_tool.py launch` 按调用方提供的 bundle ID 直接启动并按需注入调试环境。
