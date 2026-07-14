# 构建、持续日志与验证证据

本参考说明如何选择当前仓库的构建入口、建立不会漏掉操作阶段的日志证据，并在每个场景后撤销验证设施。

## 目录

- [0. 渐进式检查门禁](#0-渐进式检查门禁)
- [1. 本地编译入口与禁止项](#1-本地编译入口与禁止项)
- [2. 构建与隧道时序](#2-构建与隧道时序)
- [3. 持续日志采集](#3-持续日志采集)
- [4. 判断与业务修复](#4-判断与业务修复)
- [5. 诊断计时与正式性能测试](#5-诊断计时与正式性能测试)
- [6. 场景级清理](#6-场景级清理)

## 0. 渐进式检查门禁

构建和真机验证不得并列或倒序执行，固定顺序为：

```text
整个目标 diff/commit 的全局静态检查通过
→ 整个干净业务基线的全局本地编译通过
→ 运行时场景 A：注入 → 装机包编译 → 运行 → 清理
→ 运行时场景 B：注入 → 装机包编译 → 运行 → 清理
→ ...
```

- 静态检查和本地基线编译是全局门禁，以整个目标改动为单位；只有运行时检查按场景拆分。场景装机包编译属于运行时执行步骤，不是新的基线编译。
- 静态检查失败时不编译；基线编译失败时不发现设备、不启动日志/App/UI 工具、不启动 WDA/隧道、不注入运行时代码。
- 编译暴露真实业务问题并修复后，先回到静态检查，再重新编译。
- 运行时证据触发真实业务修复后，先清理当前场景全部临时设施；再让新的完整业务基线重新通过全局静态检查和全局编译。两者通过后更新场景矩阵，重跑当前场景、受修复影响的已通过场景和剩余场景。
- 临时埋点自身编译失败只修复验证设施，不把它误判为业务缺陷。

## 1. 本地编译入口与禁止项

只使用当前仓库已经存在且当前机器已经具备的编译输入。优先直接执行苹果本地命令：

```bash
# 全局基线：尚未选择真机时编译现有工程
xcrun xcodebuild \
  -workspace <现有App>.xcworkspace \
  -scheme <现有Scheme> \
  -configuration Debug \
  -destination 'generic/platform=iOS' \
  -disableAutomaticPackageResolution \
  -onlyUsePackageVersionsFromResolvedFile \
  -skipPackageUpdates \
  build

# 运行时场景：按本轮动态选择的真机本地编译
xcrun xcodebuild \
  -workspace <现有App>.xcworkspace \
  -scheme <现有Scheme> \
  -configuration Debug \
  -destination 'id=<硬件UDID>' \
  -disableAutomaticPackageResolution \
  -onlyUsePackageVersionsFromResolvedFile \
  -skipPackageUpdates \
  build

# 使用 devicectl 返回的 CoreDevice identifier 安装已签名 .app
xcrun devicectl device install app \
  --device <CoreDeviceIdentifier> <本地构建产物.app>
```

若项目使用 `.xcodeproj`，把 `-workspace` 替换为 `-project`。直接调用 `xcodebuild` 与经 `xcrun` 调用等价；使用当前仓库已有命令前，必须先确认它只执行本地编译，不包含下列禁止步骤。

**禁止：**

- `pod install`、`pod update`、`bundle install`、`bundle exec pod ...`。
- Swift Package resolve/update、`xcodebuild -resolvePackageDependencies`，以及 npm/yarn/pnpm、brew、gem 等依赖安装或更新。
- workspace/project 生成、代码生成前置、bootstrap、环境初始化、修改 lockfile/Pods/`Package.resolved` 或其他依赖产物。
- `-allowProvisioningUpdates`、设备注册、证书/profile 创建或更新；现有签名不完整时报告环境阻塞。
- 远程/云端编译，或把源码和构建输入发送到外部构建环境。

现有 `.xcworkspace` / `.xcodeproj`、scheme、已解析依赖、签名或本地 SDK 缺失时，停止并报告具体环境阻塞；不要通过安装、更新、生成或 bootstrap 修复环境。

**编译失败处理**：源码编译错误 → 在原范围内修复 → 回到全局静态检查 → 静态通过后重新执行本地编译。依赖、签名、生成产物或参数缺失 → 环境阻塞，不执行依赖/工程准备，也不切换远程编译。

## 2. 构建与隧道时序

1. 对整个目标改动完成一次全局静态检查，并在不发现设备、不启动任何运行时工具的情况下通过一次全局本地基线编译。
2. 进入运行时阶段后选择设备并注入单场景临时设施，先做语法/最小编译检查。
3. 用独立 App 工具构建并安装本地调试包；此时不启动 WDA，纯编译也不操作隧道。
4. 若场景需要 UI 自动化，安装完成后才启动按需 UI MCP 并记录隧道初始状态；纯日志场景跳过。
5. 用独立日志工具开始采集，再用 App 工具启动目标 App；随后执行必要的 UI 操作。
6. 场景结束先停止并删除日志，再停止 UI MCP 并恢复它改变的隧道状态。

把失败分类：

- 源码编译错误：**修复代码后先回到静态检查，静态通过再重试本地编译，循环直到编译通过**（始终本地，不切远程）。
- 依赖、工程生成产物或签名缺失：报告环境阻塞；不执行安装、更新、生成或 bootstrap。
- WDA/安装错误：按运行时环境错误处理，不算业务验证失败。
- 自动化链路错误：检查隧道、设备、Appium session。
- 业务行为不符：只有日志证据足够时才修改业务逻辑。

## 3. 持续日志采集

`tools/ios_log_tool.py` 直接管理 `idevicesyslog`，不经过 MCP/Appium/WDA。它从启动时刻开始流式采集，不是历史日志查询。因此必须遵循：

```text
ios_log_tool.py start
→ ios_app_tool.py launch --terminate-existing --environment ...
→ 执行 UI 操作
→ ios_log_tool.py stop --contains <marker> --delete-file
```

不要在操作完成后才临时运行 `idevicesyslog` 三秒，也不要把 `read` 描述为“最近系统日志”。它只读取对应日志 session 已经开始的持续采集文件。日志工具失败时不得启动 WDA 尝试修复。

每个场景使用唯一 marker：

```text
VERIFY_<runId>_<scenario> step=cache count=50
VERIFY_<runId>_<scenario> step=fallback replacedCache=false
VERIFY_<runId>_<scenario> step=real replacedFallback=true cacheIntact=true
```

证据要求：

- 包含 runId、场景、步骤和最小必要判断字段。
- 记录构建来源（分支/commit/工作树状态）、设备和包类型。
- 日志为空时先排查采集时序、tag 白名单、marker 和构建产物。
- 对完整 syslog 只在本机短暂保存；返回证据时按 marker 筛选并脱敏，随后删除采集文件。

## 4. 判断与业务修复

验证模式允许直接修复日志已定位的原业务逻辑问题：

1. 用技术方案、现有接口契约和目标 diff 确定预期。
2. 用日志证明实际行为与预期的具体差异。
3. 在原业务语义和目标模块内修正根因。
4. 停止采集并清理本场景全部验证设施，只保留真实业务修复。
5. 对新的完整业务基线重新执行全局静态检查和全局本地基线编译。
6. 两项全局门禁均通过后，更新运行时场景矩阵，重新注入并执行当前场景、受修复影响的已通过场景和剩余场景。
7. 运行时通过后立即执行场景级清理，只保留真实业务修复；全部场景结束后再做最终全局静态残留扫描和本地编译。

若修复会改变产品语义、公共 API、协议或扩大需求范围，停止并请求确认。证据不足时先补日志，不根据“可能是”直接改业务代码。

## 5. 诊断计时与正式性能测试

`CFAbsoluteTimeGetCurrent()` 差值或单次 `os_signpost` 只适合诊断哪一步可能较慢，不能单独形成性能结论。

正式性能结论至少需要：

- 固定设备、包类型和输入数据。
- 预热后多次采样。
- 报告样本数以及中位数/分位数，而不是单个最好值。
- 优先使用 Instruments、MetricKit 或 os_signpost 区间。
- 说明日志/埋点自身的额外开销和未控制变量。

如果用户只要求调用链探查，把结果表述为“本次运行的诊断耗时”，不要泛化为稳定性能指标。

## 6. 场景级清理

每个场景的退出路径都执行：

1. `ios_log_tool.py stop --delete-file`，即使流程失败或中断；它不依赖 UI MCP 是否成功启动。
2. 按注入清单删除该场景全部日志、hardcode、mock、环境变量门控和辅助代码。
3. 搜索 marker 及 `IOS_CODE_VERIFY_RUN_ID` / `IOS_CODE_VERIFY_SCENARIO`，确认零残留。
4. 保留真实业务修复与用户原有改动；禁止整文件 reset/checkout。
5. 做语法或最小编译检查。
6. 清理完成前不进入下一场景，也不执行 `git add`/commit。

任何 commit 前再次扫描待提交文件。验证日志、探查日志和 hardcode 永远不进入 Git 历史。
