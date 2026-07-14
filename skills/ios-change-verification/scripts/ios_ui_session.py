#!/usr/bin/env python3
"""按 Skill 生命周期启动仅负责 UI 自动化的 ios_ui_automation MCP。

该脚本用一个仅本用户可访问的 Unix socket 维持 MCP stdio 会话：

  python ios_ui_session.py start --session <runId>
  python ios_ui_session.py call --session <runId> tunnel_status '{}'
  python ios_ui_session.py stop --session <runId>

必须使用 ios_ui_automation MCP 工程的 venv Python 运行本脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SUPPORTED_TOOLS = {
    "device_status",
    "tunnel_status",
    "start_tunnel",
    "stop_tunnel",
    "screenshot",
    "get_ui_hierarchy",
    "tap",
}
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
DEFAULT_SERVER = (
    Path.home()
    / "Documents"
    / "ios-verification-toolkit"
    / "mcp_server"
    / "server.py"
)


def _validate_session(value: str) -> str:
    if not SESSION_RE.fullmatch(value):
        raise ValueError("session 仅允许 1..64 位字母、数字、点、下划线和连字符")
    return value


def _runtime_paths(session_name: str) -> tuple[Path, Path, Path]:
    digest = hashlib.sha256(session_name.encode("utf-8")).hexdigest()[:20]
    root = Path("/tmp") / f"ios-change-verification-{os.getuid()}"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root / f"{digest}.sock", root / f"{digest}.pid", root / f"{digest}.log"


def _server_path() -> Path:
    configured = (
        os.environ.get("IOS_UI_MCP_SERVER")
        or os.environ.get("IOS_DEVICE_MCP_SERVER")
        or str(DEFAULT_SERVER)
    )
    return Path(configured).expanduser()


def _run_discovery(arguments: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    discovery = _server_path().with_name("device_discovery.py")
    if not discovery.is_file():
        raise FileNotFoundError(f"找不到设备发现器: {discovery}")
    return subprocess.run(
        [sys.executable, str(discovery), *arguments],
        capture_output=True,
        text=True,
        timeout=30,
        env=env or dict(os.environ),
    )


def _device_selection_prompt(devices: list[dict[str, Any]]) -> str:
    lines = ["检测到多台当前可用的 iPhone/iPad，需要你选择本轮验证设备："]
    for index, device in enumerate(devices, start=1):
        details = [device.get("model", ""), device.get("os_version", ""), device.get("transport", "")]
        details = [str(item) for item in details if item]
        suffix = f"（{'，'.join(details)}）" if details else ""
        lines.append(f"{index}. {device.get('name') or '未命名设备'}{suffix}")
        lines.append(f"   硬件 UDID：{device.get('udid') or '未知'}")
    lines.append("请回复序号、设备名称或硬件 UDID；收到你的选择前，我不会启动真机验证。")
    return "\n".join(lines)


def _inventory_response() -> dict[str, Any]:
    result = _run_discovery(["list"])
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout).strip()}
    payload = json.loads(result.stdout)
    devices = payload.get("devices", [])
    if not devices:
        return {
            "ok": False,
            "needs_device_selection": False,
            "message": "当前没有检测到已连接的物理 iPhone/iPad，请连接并信任设备后再继续。",
            "devices": [],
        }
    if len(devices) > 1:
        return {
            "ok": False,
            "needs_device_selection": True,
            "message": _device_selection_prompt(devices),
            "devices": devices,
        }
    return {
        "ok": True,
        "needs_device_selection": False,
        "message": f"检测到一台可用设备，将自动选择：{devices[0].get('name')}",
        "devices": devices,
    }


def _cmd_devices(_: argparse.Namespace) -> int:
    try:
        response = _inventory_response()
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return _print_response({"ok": False, "error": str(exc)})
    return _print_response(response)


def _send(session_name: str, payload: dict[str, Any], timeout: float = 420) -> dict[str, Any]:
    sock_path, _, _ = _runtime_paths(session_name)
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    chunks: list[bytes] = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(sock_path))
        client.sendall(raw)
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    if not chunks:
        raise RuntimeError("按需 ios_ui_automation 会话未返回数据")
    return json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))


def _print_response(response: dict[str, Any]) -> int:
    print(json.dumps(response, ensure_ascii=False))
    return 0 if response.get("ok") else 1


def _remove_stale_files(session_name: str) -> None:
    sock_path, pid_path, _ = _runtime_paths(session_name)
    for path in (sock_path, pid_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _ping(session_name: str, timeout: float = 2) -> dict[str, Any] | None:
    try:
        return _send(session_name, {"tool": "__ping__", "arguments": {}}, timeout)
    except (FileNotFoundError, ConnectionError, OSError, TimeoutError, ValueError, RuntimeError):
        return None


def _cmd_start(args: argparse.Namespace) -> int:
    session_name = _validate_session(args.session)
    existing = _ping(session_name)
    if existing:
        existing["already_running"] = True
        return _print_response(existing)

    server_path = _server_path()
    if not server_path.is_file():
        return _print_response({"ok": False, "error": f"找不到 MCP Server: {server_path}"})
    has_selector = bool(
        args.udid
        or args.device_name
        or os.environ.get("IOS_MCP_UDID")
        or os.environ.get("IOS_MCP_DEVICE_NAME")
    )
    if not has_selector:
        try:
            inventory = _inventory_response()
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            return _print_response({"ok": False, "error": str(exc)})
        if not inventory.get("ok"):
            return _print_response(inventory)
    child_env = dict(os.environ)
    discovery_args = ["resolve", "--field", "json"]
    if args.udid:
        child_env.pop("IOS_MCP_DEVICE_NAME", None)
        child_env["IOS_MCP_UDID"] = args.udid
        discovery_args.extend(["--udid", args.udid])
    elif args.device_name:
        child_env.pop("IOS_MCP_UDID", None)
        child_env["IOS_MCP_DEVICE_NAME"] = args.device_name
        discovery_args.extend(["--name", args.device_name])
    try:
        discovered = _run_discovery(discovery_args, child_env)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return _print_response({"ok": False, "error": str(exc)})
    if discovered.returncode != 0:
        return _print_response(
            {"ok": False, "error": (discovered.stderr or discovered.stdout).strip()}
        )
    try:
        selected_device = json.loads(discovered.stdout)
    except json.JSONDecodeError as exc:
        return _print_response({"ok": False, "error": f"设备发现结果不是合法 JSON: {exc}"})
    child_env["IOS_MCP_UDID"] = selected_device["udid"]
    child_env["IOS_MCP_DEVICE_NAME"] = selected_device["name"]
    child_env["IOS_MCP_SELECTED_DEVICE_JSON"] = json.dumps(
        selected_device, ensure_ascii=False
    )
    _remove_stale_files(session_name)
    sock_path, _, log_path = _runtime_paths(session_name)
    log_file = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "_serve",
            "--session",
            session_name,
            "--idle-timeout",
            str(args.idle_timeout),
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
        env=child_env,
    )
    log_file.close()

    deadline = time.monotonic() + args.startup_timeout
    while time.monotonic() < deadline:
        response = _ping(session_name)
        if response:
            response["started"] = True
            response["socket"] = str(sock_path)
            return _print_response(response)
        if proc.poll() is not None:
            break
        time.sleep(0.1)

    detail = ""
    try:
        detail = log_path.read_text(errors="ignore")[-1200:]
    except OSError:
        pass
    return _print_response(
        {
            "ok": False,
            "error": f"按需 ios_ui_automation 会话未在 {args.startup_timeout}s 内就绪",
            "log": detail,
        }
    )


def _cmd_call(args: argparse.Namespace) -> int:
    session_name = _validate_session(args.session)
    try:
        arguments = json.loads(args.arguments)
    except json.JSONDecodeError as exc:
        return _print_response({"ok": False, "error": f"arguments 不是合法 JSON: {exc}"})
    if not isinstance(arguments, dict):
        return _print_response({"ok": False, "error": "arguments 必须是 JSON object"})
    try:
        response = _send(
            session_name,
            {"tool": args.tool, "arguments": arguments},
            timeout=args.timeout,
        )
    except (FileNotFoundError, ConnectionError, OSError, TimeoutError, RuntimeError) as exc:
        response = {"ok": False, "error": f"按需 ios_ui_automation 会话不可用: {exc}"}
    return _print_response(response)


def _cmd_status(args: argparse.Namespace) -> int:
    session_name = _validate_session(args.session)
    response = _ping(session_name)
    if response is None:
        response = {"ok": False, "session": session_name, "running": False}
    else:
        response["running"] = True
    return _print_response(response)


def _cmd_stop(args: argparse.Namespace) -> int:
    session_name = _validate_session(args.session)
    sock_path, _, _ = _runtime_paths(session_name)
    try:
        response = _send(
            session_name,
            {"tool": "__close__", "arguments": {}},
            timeout=args.timeout,
        )
    except (FileNotFoundError, ConnectionError, OSError, TimeoutError, RuntimeError):
        _remove_stale_files(session_name)
        response = {
            "ok": True,
            "session": session_name,
            "already_stopped": True,
        }
    deadline = time.monotonic() + 5
    while sock_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    response["socket_removed"] = not sock_path.exists()
    return _print_response(response)


def _result_text(result: Any) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content)


async def _call_mcp(
    session: ClientSession,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    try:
        result = await session.call_tool(tool, arguments)
        text = _result_text(result)
        failed = (
            getattr(result, "isError", False)
            or text.startswith("ERROR:")
            or "Error executing tool" in text
        )
        return {"ok": not failed, "tool": tool, "result": text}
    except Exception as exc:
        return {"ok": False, "tool": tool, "error": f"{type(exc).__name__}: {exc}"}


async def _serve(args: argparse.Namespace) -> int:
    session_name = _validate_session(args.session)
    server_path = _server_path()
    sock_path, pid_path, _ = _runtime_paths(session_name)
    _remove_stale_files(session_name)
    loop = asyncio.get_running_loop()
    close_event = asyncio.Event()
    call_lock = asyncio.Lock()
    cleanup_lock = asyncio.Lock()
    state: dict[str, Any] = {
        "last_activity": loop.time(),
        "cleanup_done": False,
        "initial_tunnel_running": None,
        "started_tunnel_by_session": False,
        "device": json.loads(os.environ.get("IOS_MCP_SELECTED_DEVICE_JSON", "{}")),
    }

    for signame in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signame, close_event.set)
        except NotImplementedError:
            pass

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        env=dict(os.environ),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()

            async def cleanup() -> list[dict[str, Any]]:
                async with cleanup_lock:
                    if state["cleanup_done"]:
                        return []
                    state["cleanup_done"] = True
                    results: list[dict[str, Any]] = []
                    current = await _call_mcp(mcp_session, "tunnel_status", {})
                    tunnel_is_running = (
                        current.get("ok")
                        and "隧道运行中" in current.get("result", "")
                    )
                    if state["initial_tunnel_running"] is False and (
                        state["started_tunnel_by_session"] or tunnel_is_running
                    ):
                        results.append(await _call_mcp(mcp_session, "stop_tunnel", {}))
                    return results

            async def handle(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
            ) -> None:
                response: dict[str, Any]
                should_close = False
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=10)
                    request = json.loads(raw.decode("utf-8"))
                    tool = request.get("tool")
                    arguments = request.get("arguments", {})
                    if not isinstance(tool, str) or not isinstance(arguments, dict):
                        raise ValueError("请求必须包含字符串 tool 和 object arguments")
                    state["last_activity"] = loop.time()
                    if tool == "__ping__":
                        response = {
                            "ok": True,
                            "session": session_name,
                            "pid": os.getpid(),
                            "device": state["device"],
                        }
                    elif tool == "__close__":
                        async with call_lock:
                            cleanup_results = await cleanup()
                        response = {
                            "ok": True,
                            "session": session_name,
                            "stopped": True,
                            "cleanup": cleanup_results,
                        }
                        should_close = True
                    elif tool not in SUPPORTED_TOOLS:
                        response = {"ok": False, "error": f"未知 ios_ui_automation tool: {tool}"}
                    else:
                        async with call_lock:
                            if tool == "start_tunnel" and state["initial_tunnel_running"] is None:
                                initial = await _call_mcp(mcp_session, "tunnel_status", {})
                                if not initial.get("ok"):
                                    response = initial
                                else:
                                    state["initial_tunnel_running"] = (
                                        "隧道运行中" in initial.get("result", "")
                                    )
                                    response = await _call_mcp(mcp_session, tool, arguments)
                            else:
                                response = await _call_mcp(mcp_session, tool, arguments)
                                if (
                                    tool == "tunnel_status"
                                    and state["initial_tunnel_running"] is None
                                    and response.get("ok")
                                ):
                                    state["initial_tunnel_running"] = (
                                        "隧道运行中" in response.get("result", "")
                                    )
                        if tool == "start_tunnel" and response.get("ok"):
                            state["started_tunnel_by_session"] = True
                except Exception as exc:
                    response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                if should_close:
                    close_event.set()

            async def expire_when_idle() -> None:
                while not close_event.is_set():
                    await asyncio.sleep(min(30.0, max(1.0, args.idle_timeout / 2)))
                    if loop.time() - state["last_activity"] >= args.idle_timeout:
                        close_event.set()

            unix_server = await asyncio.start_unix_server(handle, path=str(sock_path))
            os.chmod(sock_path, 0o600)
            pid_path.write_text(f"{os.getpid()}\n")
            os.chmod(pid_path, 0o600)
            idle_task = asyncio.create_task(expire_when_idle())
            try:
                await close_event.wait()
            finally:
                unix_server.close()
                await unix_server.wait_closed()
                idle_task.cancel()
                try:
                    await idle_task
                except asyncio.CancelledError:
                    pass
                await cleanup()
                for path in (sock_path, pid_path):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按 Skill 生命周期运行 ios_ui_automation MCP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    devices = subparsers.add_parser("devices", help="列出当前连接的物理 iPhone/iPad")
    devices.set_defaults(func=_cmd_devices)

    start = subparsers.add_parser("start", help="启动独立按需 MCP 会话")
    start.add_argument("--session", required=True)
    selector = start.add_mutually_exclusive_group()
    selector.add_argument("--udid", help="多设备时显式选择硬件 UDID")
    selector.add_argument("--device-name", help="多设备时按唯一设备名选择")
    start.add_argument("--idle-timeout", type=int, default=1800)
    start.add_argument("--startup-timeout", type=float, default=20)
    start.set_defaults(func=_cmd_start)

    call = subparsers.add_parser("call", help="调用会话中的一个 ios_ui_automation tool")
    call.add_argument("--session", required=True)
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}")
    call.add_argument("--timeout", type=float, default=420)
    call.set_defaults(func=_cmd_call)

    status = subparsers.add_parser("status", help="检查按需会话")
    status.add_argument("--session", required=True)
    status.set_defaults(func=_cmd_status)

    stop = subparsers.add_parser("stop", help="清理并停止按需 MCP 会话")
    stop.add_argument("--session", required=True)
    stop.add_argument("--timeout", type=float, default=60)
    stop.set_defaults(func=_cmd_stop)

    serve = subparsers.add_parser("_serve", help=argparse.SUPPRESS)
    serve.add_argument("--session", required=True)
    serve.add_argument("--idle-timeout", type=int, default=1800)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "_serve":
        return asyncio.run(_serve(args))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
