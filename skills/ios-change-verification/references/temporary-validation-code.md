# iOS 临时验证代码约定

编写日志、hardcode 或 mock 返回值前遵守本约定，确保验证设施仅在指定场景生效，并能在场景结束后完整撤销。

## 目录

- [1. 两层门控](#1-两层门控)
- [2. 日志入口与过滤](#2-日志入口与过滤)
- [3. marker 与脱敏](#3-marker-与脱敏)
- [4. 可撤销 hardcode 模板](#4-可撤销-hardcode-模板)
- [5. 场景级清理与提交门禁](#5-场景级清理与提交门禁)

## 1. 两层门控

所有临时验证代码必须同时具备：

1. **编译门控**：复用当前仓库已有的本地调试编译条件；没有自定义条件时使用 `#if DEBUG`。
2. **场景门控**：使用 `IOS_CODE_VERIFY_SCENARIO`，避免临时代码影响其他调试启动。

不要把任意调试/测试编译宏单独当作 hardcode 开关。

```swift
#if DEBUG
let verifyEnvironment = ProcessInfo.processInfo.environment
let verifyRunId = verifyEnvironment["IOS_CODE_VERIFY_RUN_ID"] ?? "unknown"
let verifyScenario = verifyEnvironment["IOS_CODE_VERIFY_SCENARIO"]

if verifyScenario == "EmptyData" {
    // 仅该场景的临时行为
}
#endif
```

如果仓库使用其他调试编译条件，先从工程配置和相邻代码确认，再替换示例中的 `DEBUG`；不要把其他项目的私有宏复制进来。

## 2. 日志入口与过滤

写日志前检查当前模块实际使用的日志入口及其实现：

1. 复用模块已有的 logger、category 或 tag，不引入新的日志框架。
2. 确认日志级别、tag/category 白名单、采样和编译条件的执行顺序。
3. 不假设 error/warn 级别一定绕过白名单；若过滤发生在级别判断之后，所有级别仍可能被丢弃。
4. 把 `VERIFY_<runId>_<场景>` / `PROBE_<runId>_<主题>` 放在日志正文，tag/category 继续使用当前模块已验证可见的值。
5. 若必须临时调整过滤规则，该调整也属于验证设施，必须受两层门控并纳入清理清单。

以下是结构示意，不代表具体项目 API：

```swift
#if DEBUG
if verifyScenario == "CacheMerge" {
    existingProjectLogger.info(
        "VERIFY_\(verifyRunId)_CacheMerge step=merge cached=\(cachedCount) fallback=\(fallbackCount)"
    )
}
#endif
```

日志为空时先验证采集时序、编译门控、过滤规则和 marker，不得直接推断分支未执行。

## 3. marker 与脱敏

- 每轮生成唯一 `runId`，每个场景使用唯一 marker，防止旧日志或并发运行混入证据。
- 只记录判断所需的最小字段，例如分支、计数、布尔状态、错误分类和诊断耗时。
- 禁止记录 token、cookie、账号、联系人、用户原始内容、完整媒体路径、设备标识或其他不必要的敏感数据。
- 必须记录业务标识时，使用截断、不可逆散列或虚构测试数据，并在报告中继续脱敏。

## 4. 可撤销 hardcode 模板

临时 override 只能在目标场景生效；其他本地调试启动继续走真实逻辑：

```swift
let effectiveFlag: Bool
#if DEBUG
if ProcessInfo.processInfo.environment["IOS_CODE_VERIFY_SCENARIO"] == "FeatureOn" {
    effectiveFlag = true
} else {
    effectiveFlag = realFeatureFlagValue
}
#else
effectiveFlag = realFeatureFlagValue
#endif
```

异常或空数据场景同理：

```swift
#if DEBUG
if verifyScenario == "EmptyData" {
    existingProjectLogger.info("VERIFY_\(verifyRunId)_EmptyData forced=true count=0")
    return []
}
#endif
```

注入时记录文件、位置、marker、原表达式、临时表达式和辅助类型，便于精确撤销。

## 5. 场景级清理与提交门禁

每个场景完成、失败、阻塞或中断后立即：

1. 删除本场景全部 marker 日志。
2. 删除环境变量读取、hardcode、mock 返回值、临时过滤调整和辅助类型。
3. 保留经授权的真实业务修复。
4. 搜索本场景 marker 及 `IOS_CODE_VERIFY_RUN_ID` / `IOS_CODE_VERIFY_SCENARIO`，确认零残留。
5. 对照场景开始前的用户改动，禁止用 reset/checkout 覆盖整个文件。
6. 做语法或最小编译检查后才进入下一场景。

临时代码不得进入 Git 历史。任何 commit 前扫描待提交文件；只要存在验证 marker、探查 marker 或临时 hardcode，就禁止 `git add` 和 commit。
