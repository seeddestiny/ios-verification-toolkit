#!/usr/bin/env python3
"""
ios_ui_automation MCP Server
─────────────────────────────────────────────────────────────────────────────
把 iOS 真机的「界面图层 / 截图 / 点击 / UI 自动化隧道」封装为 MCP tools，
供 Skill 或任意 MCP 客户端调用。App 编译/安装/启动和 syslog 采集分别由
tools/ios_app_tool.py 与 tools/ios_log_tool.py 处理，不依赖本 MCP 生命周期。

架构:  MCP client ──stdio──> 本 Server ──HTTP──> Appium(127.0.0.1:4723) ──> WDA(真机)

前置(由 scripts/ 安装器一次性完成):
  - Appium + xcuitest 驱动已安装,WDA 已签名装到真机并被信任
  - 运行时:本 Server 会按需启动 Appium(仅监听 127.0.0.1,严格保密)并建 session

设计要点:
  - Appium 只监听 127.0.0.1(严格保密,外部连不进)
  - 清除可能污染 xcodebuild 的 CC/CXX 环境变量
  - session 懒创建 + 复用;断连自动重建
  - 日志与 App 生命周期不进入 WDA session，轻量场景无需启动本 Server
  - 稳定用 v1.x FastMCP(v2 仍为 pre-release)
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any, Optional

import requests
from mcp.server.fastmcp import FastMCP

try:
    from .device_discovery import resolve_target_device
    from .env_sanitizer import sanitized_env
    from .runtime_paths import ensure_runtime_paths
    from .signing_identity import resolve_team_id
    from .wda_bundle_id import build_wda_bundle_id
except ImportError:
    from device_discovery import resolve_target_device
    from env_sanitizer import sanitized_env
    from runtime_paths import ensure_runtime_paths
    from signing_identity import resolve_team_id
    from wda_bundle_id import build_wda_bundle_id

# ── 配置(可用环境变量覆盖)──────────────────────────────────────────────────
APPIUM_HOST = os.environ.get("IOS_MCP_APPIUM_HOST", "127.0.0.1")   # 严格保密:仅本机
APPIUM_PORT = int(os.environ.get("IOS_MCP_APPIUM_PORT", "4723"))
APPIUM_BASE = f"http://{APPIUM_HOST}:{APPIUM_PORT}"

TARGET_DEVICE = resolve_target_device()
DEVICE_UDID = TARGET_DEVICE.udid  # Appium 使用的硬件 UDID，运行时动态选择
WDA_BUNDLE_ID = build_wda_bundle_id()
TUNNEL_REGISTRY_PORT = int(os.environ.get("IOS_MCP_TUNNEL_REGISTRY_PORT", "42314"))
# 是否复用预构建 WDA。默认 false:让 Appium 完整 build+install+launch WDA 到设备
# (设备上没有 WDA 时用 true 会连不上 8100)。装好并常驻后可设 true 提速。
USE_PREBUILT_WDA = os.environ.get("IOS_MCP_USE_PREBUILT_WDA", "false").lower() == "true"
USE_PREINSTALLED_WDA = os.environ.get("IOS_MCP_USE_PREINSTALLED_WDA", "auto").strip().lower()
DEVELOPER_DIR = os.environ.get("DEVELOPER_DIR", "").strip()
if not DEVELOPER_DIR:
    _xcode_select = subprocess.run(
        ["xcode-select", "-p"],
        capture_output=True,
        text=True,
        timeout=10,
        env=sanitized_env(),
    )
    DEVELOPER_DIR = _xcode_select.stdout.strip()
    if _xcode_select.returncode != 0 or not DEVELOPER_DIR:
        raise RuntimeError("无法解析 Xcode Developer 目录，请设置 DEVELOPER_DIR")

if USE_PREINSTALLED_WDA not in {"auto", "true", "false"}:
    raise ValueError("IOS_MCP_USE_PREINSTALLED_WDA 仅支持 auto、true 或 false")

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_PATHS = ensure_runtime_paths()
LOG_DIR = str(RUNTIME_PATHS.logs)
APPIUM_SERVER_LOG = os.path.join(LOG_DIR, "appium-server.log")

# ── 关键:清除可能污染构建的编译器环境变量,并固定 DEVELOPER_DIR ──────────────
for _var in ("CC", "CXX"):
    os.environ.pop(_var, None)
os.environ["DEVELOPER_DIR"] = DEVELOPER_DIR

mcp = FastMCP("ios_ui_automation")

# ── Appium / session 管理 ────────────────────────────────────────────────────
_session_id: Optional[str] = None
_appium_proc: Optional[subprocess.Popen] = None


def _appium_components() -> tuple[bool, str]:
    """只检查本机 Appium 与 XCUITest 驱动，不执行安装或升级。"""
    executable = shutil.which("appium")
    if not executable:
        return False, "本机未安装 Appium"
    try:
        result = subprocess.run(
            [executable, "driver", "list", "--installed"],
            capture_output=True,
            text=True,
            timeout=30,
            env=sanitized_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"无法检查 Appium XCUITest 驱动: {exc}"
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0 or "xcuitest" not in output.lower():
        detail = output
        return False, f"本机未安装 Appium XCUITest 驱动{f': {detail}' if detail else ''}"
    return True, f"Appium 已安装: {executable}；XCUITest 驱动已安装"


def _wda_runner_bundle_id() -> str:
    return WDA_BUNDLE_ID if WDA_BUNDLE_ID.endswith(".xctrunner") else f"{WDA_BUNDLE_ID}.xctrunner"


def _target_wda_state() -> tuple[str, str]:
    """只读检查目标设备上的 WDA，返回 installed、missing 或 unknown。"""
    devicectl = os.path.join(DEVELOPER_DIR, "usr", "bin", "devicectl")
    if not os.path.isfile(devicectl):
        return "unknown", f"找不到 devicectl: {devicectl}"

    device_identifier = TARGET_DEVICE.core_device_identifier or DEVICE_UDID
    try:
        with tempfile.TemporaryDirectory(prefix="ios-mcp-wda-check-") as temp_dir:
            output_path = os.path.join(temp_dir, "apps.json")
            result = subprocess.run(
                [
                    devicectl,
                    "device",
                    "info",
                    "apps",
                    "--device",
                    device_identifier,
                    "--json-output",
                    output_path,
                    "--timeout",
                    "30",
                ],
                capture_output=True,
                text=True,
                timeout=40,
                env=sanitized_env(),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                return "unknown", f"devicectl 检查失败{f': {detail}' if detail else ''}"
            with open(output_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return "unknown", f"无法读取目标设备 App 列表: {exc}"

    expected_bundle_id = _wda_runner_bundle_id()
    apps = (payload.get("result") or {}).get("apps") or []
    for app in apps:
        if app.get("bundleIdentifier") == expected_bundle_id:
            return "installed", f"目标设备已安装 WDA: {expected_bundle_id}"
    return "missing", f"目标设备未安装 WDA: {expected_bundle_id}"


def _should_use_preinstalled_wda() -> bool:
    if USE_PREINSTALLED_WDA == "true":
        return True
    if USE_PREINSTALLED_WDA == "false":
        return False
    state, detail = _target_wda_state()
    if state == "unknown":
        raise RuntimeError(f"{detail}。为避免重复安装 WDA，已停止创建 Appium session")
    return state == "installed"


def _appium_ready(timeout: float = 2.0) -> bool:
    try:
        r = requests.get(f"{APPIUM_BASE}/status", timeout=timeout)
        return r.ok and r.json().get("value", {}).get("ready", False)
    except Exception:
        return False


def _ensure_appium(wait_s: int = 40) -> None:
    """确保 Appium 在跑(仅监听 127.0.0.1)。未跑则拉起并等待就绪。"""
    global _appium_proc
    components_ready, detail = _appium_components()
    if not components_ready:
        raise RuntimeError(f"{detail}。MCP 不会自动重复安装，请仅安装缺失组件后重试")
    if _appium_ready():
        return
    ensure_runtime_paths()
    # env -u CC 双保险:Appium fork 的 xcodebuild 不继承被污染的编译器变量
    env = sanitized_env()
    env.pop("CC", None)
    env.pop("CXX", None)
    nohup_log = os.path.join(LOG_DIR, "appium-mcp-nohup.log")
    logf = open(nohup_log, "ab")
    os.chmod(nohup_log, 0o600)
    with open(APPIUM_SERVER_LOG, "ab"):
        pass
    os.chmod(APPIUM_SERVER_LOG, 0o600)
    try:
        _appium_proc = subprocess.Popen(
            ["appium", "--address", APPIUM_HOST, "--port", str(APPIUM_PORT),
             "--log", APPIUM_SERVER_LOG, "--log-level", "info"],
            stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, env=env, umask=0o077,
        )
    finally:
        logf.close()
    for _ in range(wait_s):
        if _appium_ready():
            return
        time.sleep(1)
    raise RuntimeError(f"Appium 未在 {wait_s}s 内就绪,检查 {APPIUM_SERVER_LOG}")


def _create_session() -> str:
    """先复用目标设备已有 WDA；确认缺失时才允许 Appium 构建安装。"""
    use_preinstalled_wda = _should_use_preinstalled_wda()
    always_match: dict[str, Any] = {
        "platformName": "iOS",
        "appium:automationName": "XCUITest",
        "appium:udid": DEVICE_UDID,
        "appium:updatedWDABundleId": WDA_BUNDLE_ID,
        "appium:noReset": True,
        "appium:newCommandTimeout": 600,
    }
    if use_preinstalled_wda:
        always_match["appium:usePreinstalledWDA"] = True
        if TARGET_DEVICE.os_version:
            always_match["appium:platformVersion"] = TARGET_DEVICE.os_version
    else:
        team_id = resolve_team_id()
        always_match.update(
            {
                "appium:xcodeOrgId": team_id,
                "appium:xcodeSigningId": "Apple Development",
                "appium:usePrebuiltWDA": USE_PREBUILT_WDA,
            }
        )
    caps = {"capabilities": {"alwaysMatch": always_match, "firstMatch": [{}]}}
    r = requests.post(f"{APPIUM_BASE}/session", json=caps, timeout=360)
    data = r.json()
    sid = (data.get("value") or {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"创建 session 失败: {str(data)[:500]}")
    return sid


def _session(force_new: bool = False) -> str:
    """获取可用 session:懒创建 + 复用 + 失效重建。"""
    global _session_id
    _ensure_appium()
    if _session_id and not force_new:
        # 健康检查:能拿到 window size 视为存活
        try:
            r = requests.get(f"{APPIUM_BASE}/session/{_session_id}/window/size", timeout=8)
            if r.ok:
                return _session_id
        except Exception:
            pass
    _session_id = _create_session()
    return _session_id


def _sess_req(method: str, path: str, **kw) -> requests.Response:
    """带一次自动重建 session 的请求封装。"""
    sid = _session()
    url = f"{APPIUM_BASE}/session/{sid}{path}"
    r = requests.request(method, url, timeout=kw.pop("timeout", 60), **kw)
    if r.status_code in (404, 500) and "session" in r.text.lower():
        sid = _session(force_new=True)
        url = f"{APPIUM_BASE}/session/{sid}{path}"
        r = requests.request(method, url, timeout=60, **kw)
    return r


def _active_bundle_id() -> str:
    try:
        r = _sess_req("POST", "/execute/sync",
                      json={"script": "mobile: activeAppInfo", "args": []})
        return (r.json().get("value") or {}).get("bundleId", "")
    except Exception:
        return ""


# ── MCP Tools ────────────────────────────────────────────────────────────────
@mcp.tool()
def screenshot(save_path: str = "") -> str:
    """对 iOS 真机当前屏幕截图。

    Args:
        save_path: 可选。若提供,则把 PNG 存到该绝对路径并返回路径;
                   否则返回 base64 编码的 PNG 字符串(data:image/png;base64,...)。
    """
    r = _sess_req("GET", "/screenshot")
    b64 = r.json().get("value", "")
    if not b64:
        return "ERROR: 截图失败(空数据)"
    if save_path:
        with open(save_path, "wb") as f:
            f.write(base64.b64decode(b64))
        os.chmod(save_path, 0o600)
        return f"截图已保存: {save_path} ({os.path.getsize(save_path)} bytes)"
    return "data:image/png;base64," + b64


@mcp.tool()
def get_ui_hierarchy() -> str:
    """获取当前屏幕的界面图层(UI 层级树,XML)。
    包含可见控件的类型、名称/label、坐标、可点击性等,可用于定位要点击的元素。
    """
    r = _sess_req("GET", "/source")
    xml = r.json().get("value", "")
    return xml or "ERROR: 获取界面图层失败"


@mcp.tool()
def tap(x: float = -1, y: float = -1, name: str = "") -> str:
    """模拟点击。两种方式二选一:
    - 按坐标: 提供 x, y(单位:点/point,非像素)
    - 按元素名: 提供 name(匹配控件的 name/label,如按钮文字)

    Args:
        x: 点击的 x 坐标(point)。与 name 二选一。
        y: 点击的 y 坐标(point)。
        name: 目标控件的 name/label。与坐标二选一。
    """
    if name:
        r = _sess_req("POST", "/element",
                      json={"using": "name", "value": name})
        v = (r.json().get("value") or {})
        el = v.get("ELEMENT") or v.get("element-6066-11e4-a52e-4f735466cecf")
        if not el:
            return f"ERROR: 未找到名为 '{name}' 的元素"
        _sess_req("POST", f"/element/{el}/click")
        return f"已点击元素: name='{name}'"
    if x >= 0 and y >= 0:
        # W3C actions:pointer 在 (x,y) 点按
        actions = {
            "actions": [{
                "type": "pointer", "id": "finger1",
                "parameters": {"pointerType": "touch"},
                "actions": [
                    {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pause", "duration": 60},
                    {"type": "pointerUp", "button": 0},
                ],
            }]
        }
        _sess_req("POST", "/actions", json=actions)
        return f"已点击坐标: ({int(x)}, {int(y)})"
    return "ERROR: 请提供 name,或 x/y 坐标"


@mcp.tool()
def device_status() -> str:
    """返回设备/链路状态:Appium 是否就绪、session 是否存活、当前前台 App。"""
    tunnel = _tunnel_running()
    ready = _appium_ready()
    components_ready, components_detail = _appium_components()
    wda_state, wda_detail = _target_wda_state()
    # 隧道未起时不尝试创建 WDA session，避免预检卡在 360 秒 session timeout。
    front = _active_bundle_id() if components_ready and ready and tunnel and wda_state != "unknown" else ""
    return (
        f"Appium 组件: {components_detail}\n"
        f"Appium 服务({APPIUM_BASE}): {'就绪' if ready else '未运行'}\n"
        f"RemoteXPC 隧道: {'运行中' if tunnel else '未运行'}\n"
        f"目标设备: {TARGET_DEVICE.name}\n"
        f"硬件 UDID: {DEVICE_UDID}\n"
        f"型号/系统: {TARGET_DEVICE.model or TARGET_DEVICE.product_type} / "
        f"{TARGET_DEVICE.os_version or '未知'}\n"
        f"连接方式: {TARGET_DEVICE.transport or '未知'}\n"
        f"WDA 检查: {wda_detail}\n"
        f"WDA 策略: {'复用已安装 WDA' if wda_state == 'installed' else '确认缺失后首次 session 安装' if wda_state == 'missing' else '检测失败，不安装'}\n"
        f"当前前台 App: {front or '(未知/无session)'}"
    )


# ── 隧道管理(RemoteXPC tunnel,iOS17+ 真机必需)──────────────────────────────
_TUNNEL_SCRIPT = os.path.join(PROJECT_DIR, "scripts", "06_setup_tunnel_sudoers.sh")


def _tunnel_running() -> bool:
    try:
        response = requests.get(
            f"http://127.0.0.1:{TUNNEL_REGISTRY_PORT}"
            f"/remotexpc/tunnels/{DEVICE_UDID}?waitMs=500",
            timeout=2,
        )
        return response.ok
    except Exception:
        return False


@mcp.tool()
def tunnel_status() -> str:
    """查询 RemoteXPC 隧道是否在运行(iOS 17+ 真机自动化的通信通道)。"""
    return "隧道运行中 ✅" if _tunnel_running() else "隧道未运行 ❌(截图/点击前需先 start_tunnel)"


@mcp.tool()
def start_tunnel() -> str:
    """启动 RemoteXPC 隧道(后台常驻)。需已通过 06_setup_tunnel_sudoers.sh --apply 配置免密;
    否则会因需要 sudo 密码而失败。就绪判定基于 registry 中存在当前 UDID，
    不是只看 tunnel-creation 进程。"""
    if _tunnel_running():
        return "隧道已在运行,无需启动。"
    if not os.path.exists(_TUNNEL_SCRIPT):
        return f"ERROR: 找不到隧道脚本 {_TUNNEL_SCRIPT}"
    r = subprocess.run(["bash", _TUNNEL_SCRIPT, "--start-tunnel"],
                       capture_output=True, text=True, timeout=30)
    if _tunnel_running():
        return "隧道已启动 ✅"
    return ("启动失败(通常是未配置免密,需 sudo 密码)。\n"
            "请在终端执行:sudo appium driver run xcuitest tunnel-creation -- "
            f"--udid {DEVICE_UDID} --tunnel-registry-port {TUNNEL_REGISTRY_PORT}\n"
            f"或先配置免密:bash {_TUNNEL_SCRIPT} --apply\n"
            f"--- 输出 ---\n{(r.stdout + r.stderr)[:400]}")


@mcp.tool()
def stop_tunnel() -> str:
    """关闭 RemoteXPC 隧道。需已配置免密(--apply 已把关闭命令纳入 NOPASSWD);
    否则会因需要 sudo 密码而失败,此时请在终端执行 sudo pkill -f 'xcuitest tunnel-creation'。"""
    if not _tunnel_running():
        return "隧道未在运行,无需关闭。"
    if not os.path.exists(_TUNNEL_SCRIPT):
        return f"ERROR: 找不到隧道脚本 {_TUNNEL_SCRIPT}"
    r = subprocess.run(["bash", _TUNNEL_SCRIPT, "--stop-tunnel"],
                       capture_output=True, text=True, timeout=30)
    if not _tunnel_running():
        return "隧道已关闭 ✅"
    return ("关闭失败(通常是未配置关闭免密,需 sudo 密码)。\n"
            "请在终端执行:sudo pkill -f 'xcuitest tunnel-creation'\n"
            f"--- 输出 ---\n{(r.stdout + r.stderr)[:400]}")


if __name__ == "__main__":
    mcp.run()   # 默认 stdio 传输
