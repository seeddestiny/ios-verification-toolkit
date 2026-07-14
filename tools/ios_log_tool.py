#!/usr/bin/env python3
"""Lightweight iOS syslog capture without MCP, Appium, or WDA."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server.device_discovery import (  # noqa: E402
    DeviceSelectionError,
    discover_connected_ios_devices,
    resolve_target_device,
)
from mcp_server.env_sanitizer import sanitized_env  # noqa: E402
from mcp_server.runtime_paths import ensure_runtime_paths  # noqa: E402


SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _validate_session(value: str) -> str:
    if not SESSION_RE.fullmatch(value):
        raise ValueError("session 仅允许 1..64 位字母、数字、点、下划线和连字符")
    return value


def _session_paths(session: str) -> tuple[Path, Path]:
    session = _validate_session(session)
    paths = ensure_runtime_paths()
    digest = hashlib.sha256(session.encode("utf-8")).hexdigest()[:20]
    return (
        paths.state / f"ios-log-{digest}.json",
        paths.logs / f"device-syslog-{digest}-{time.strftime('%Y%m%d-%H%M%S')}.log",
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.write("\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"状态文件格式错误: {path}")
    return payload


def _process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _is_owned_supervisor(pid: int, state_path: Path) -> bool:
    if not _process_running(pid):
        return False
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        timeout=3,
        env=sanitized_env(),
    )
    command = result.stdout.strip()
    return (
        result.returncode == 0
        and str(Path(__file__).resolve()) in command
        and "_capture" in command
        and str(state_path) in command
    )


def _tail(path: Path, lines: int, contains: str = "") -> str:
    if not path.is_file():
        return ""
    limit = max(1, min(int(lines), 5000))
    captured = path.read_text(errors="ignore").splitlines()
    if contains:
        captured = [line for line in captured if contains in line]
    return "\n".join(captured[-limit:])


def _stop_child(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _capture(args: argparse.Namespace) -> int:
    state_path = Path(args.state_file).resolve()
    output_path = Path(args.output).resolve()
    base_state: dict[str, Any] = {
        "session": args.session,
        "supervisor_pid": os.getpid(),
        "device_udid": args.udid,
        "output_path": str(output_path),
        "daemon_log": str(getattr(args, "daemon_log", "")),
        "started_at": time.time(),
    }
    executable = shutil.which("idevicesyslog")
    if not executable:
        _write_json(state_path, {**base_state, "status": "error", "error": "未安装 idevicesyslog(libimobiledevice)"})
        return 2

    try:
        output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        output_path.parent.chmod(0o700)
        output_file = output_path.open("xb")
        output_path.chmod(0o600)
    except OSError as exc:
        _write_json(state_path, {**base_state, "status": "error", "error": f"无法创建日志文件: {exc}"})
        return 2

    stop_requested = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            [executable, "-u", args.udid],
            stdin=subprocess.DEVNULL,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            env=sanitized_env(),
        )
        output_file.close()
        time.sleep(0.3)
        if proc.poll() is not None:
            _write_json(
                state_path,
                {**base_state, "status": "error", "child_pid": proc.pid, "error": f"idevicesyslog 启动后立即退出(code={proc.returncode})"},
            )
            return 2

        _write_json(state_path, {**base_state, "status": "running", "child_pid": proc.pid})
        deadline = time.monotonic() + max(1, args.max_seconds)
        while not stop_requested and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        expired = not stop_requested and time.monotonic() >= deadline
        child_failed = not stop_requested and not expired and proc.poll() not in (None, 0)
        _stop_child(proc)
        status = "expired" if expired else "error" if child_failed else "stopped"
        final_state = {
            **base_state,
            "status": status,
            "child_pid": proc.pid,
            "stopped_at": time.time(),
        }
        if child_failed:
            final_state["error"] = f"idevicesyslog 异常退出(code={proc.returncode})"
        _write_json(
            state_path,
            final_state,
        )
        return 2 if child_failed else 0
    except Exception as exc:
        if not output_file.closed:
            output_file.close()
        if proc is not None:
            _stop_child(proc)
        _write_json(state_path, {**base_state, "status": "error", "error": f"日志采集失败: {exc}"})
        return 2


def _print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


def _cmd_devices(_args: argparse.Namespace) -> int:
    try:
        devices = discover_connected_ios_devices()
        return _print({"ok": True, "devices": [device.to_dict() for device in devices]})
    except DeviceSelectionError as exc:
        return _print({"ok": False, "error": str(exc), "devices": []})


def _cmd_start(args: argparse.Namespace) -> int:
    try:
        state_path, default_output = _session_paths(args.session)
        if state_path.exists():
            state = _read_json(state_path)
            pid = int(state.get("supervisor_pid") or 0)
            if state.get("status") == "running" and _is_owned_supervisor(pid, state_path):
                return _print({"ok": True, "already_running": True, **state})
            return _print({"ok": False, "error": "该 session 存在未处理的日志状态，请先执行 stop", "state": str(state_path)})
        device = resolve_target_device(args.udid, args.device_name)
        output_path = Path(args.output).expanduser().resolve() if args.output else default_output
        if output_path.exists():
            return _print({"ok": False, "error": f"为避免覆盖已有文件，输出路径必须不存在: {output_path}"})
        if not output_path.parent.is_dir():
            return _print({"ok": False, "error": f"输出目录不存在: {output_path.parent}"})

        daemon_log = ensure_runtime_paths().logs / f"ios-log-supervisor-{state_path.stem}.log"
        with daemon_log.open("ab", buffering=0) as handle:
            daemon_log.chmod(0o600)
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "_capture",
                    "--session",
                    args.session,
                    "--udid",
                    device.udid,
                    "--output",
                    str(output_path),
                    "--state-file",
                    str(state_path),
                    "--daemon-log",
                    str(daemon_log),
                    "--max-seconds",
                    str(args.max_seconds),
                ],
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                start_new_session=True,
                env=sanitized_env(),
                umask=0o077,
            )
        deadline = time.monotonic() + args.startup_timeout
        while time.monotonic() < deadline:
            if state_path.exists():
                state = _read_json(state_path)
                if state.get("status") == "running":
                    return _print({"ok": True, "device": device.to_dict(), **state})
                if state.get("status") == "error":
                    return _print({"ok": False, **state})
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        return _print({"ok": False, "error": "轻量日志采集器未在限定时间内就绪", "daemon_log": str(daemon_log)})
    except (DeviceSelectionError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return _print({"ok": False, "error": str(exc)})


def _load_session(session: str) -> tuple[Path, dict[str, Any]]:
    state_path, _ = _session_paths(session)
    if not state_path.is_file():
        raise FileNotFoundError(f"日志 session 不存在: {session}")
    return state_path, _read_json(state_path)


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        state_path, state = _load_session(args.session)
        pid = int(state.get("supervisor_pid") or 0)
        state["running"] = state.get("status") == "running" and _is_owned_supervisor(pid, state_path)
        return _print({"ok": True, **state})
    except (FileNotFoundError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return _print({"ok": False, "error": str(exc), "running": False})


def _cmd_read(args: argparse.Namespace) -> int:
    try:
        _state_path, state = _load_session(args.session)
        path = Path(str(state["output_path"]))
        return _print({"ok": True, "status": state.get("status"), "output_path": str(path), "logs": _tail(path, args.lines, args.contains)})
    except (FileNotFoundError, KeyError, OSError, ValueError) as exc:
        return _print({"ok": False, "error": str(exc)})


def _cmd_stop(args: argparse.Namespace) -> int:
    try:
        state_path, state = _load_session(args.session)
    except FileNotFoundError:
        return _print({"ok": True, "already_stopped": True, "session": args.session})
    try:
        pid = int(state.get("supervisor_pid") or 0)
        if state.get("status") == "running" and _is_owned_supervisor(pid, state_path):
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline and _process_running(pid):
                time.sleep(0.05)
        state = _read_json(state_path)
        output_path = Path(str(state["output_path"]))
        daemon_log = Path(str(state.get("daemon_log") or "")) if state.get("daemon_log") else None
        logs = _tail(output_path, args.lines, args.contains)
        if args.delete_file and output_path.exists():
            output_path.unlink()
        if args.delete_file and daemon_log and daemon_log.exists():
            daemon_log.unlink()
        state_path.unlink(missing_ok=True)
        return _print(
            {
                "ok": True,
                "session": args.session,
                "logs": logs,
                "output_path": None if args.delete_file else str(output_path),
                "deleted": args.delete_file,
            }
        )
    except (KeyError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return _print({"ok": False, "error": str(exc)})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    devices = subparsers.add_parser("devices", help="列出当前连接的物理 iPhone/iPad")
    devices.set_defaults(func=_cmd_devices)

    start = subparsers.add_parser("start", help="启动独立的轻量 syslog 采集")
    start.add_argument("--session", required=True)
    selector = start.add_mutually_exclusive_group()
    selector.add_argument("--udid")
    selector.add_argument("--device-name")
    start.add_argument("--output", help="可选的绝对输出路径；默认写入 .runtime/logs")
    start.add_argument("--max-seconds", type=int, default=1800)
    start.add_argument("--startup-timeout", type=float, default=5)
    start.set_defaults(func=_cmd_start)

    status = subparsers.add_parser("status", help="查询日志采集状态")
    status.add_argument("--session", required=True)
    status.set_defaults(func=_cmd_status)

    read = subparsers.add_parser("read", help="读取当前采集文件")
    read.add_argument("--session", required=True)
    read.add_argument("--lines", type=int, default=200)
    read.add_argument("--contains", default="")
    read.set_defaults(func=_cmd_read)

    stop = subparsers.add_parser("stop", help="停止采集并读取/删除日志")
    stop.add_argument("--session", required=True)
    stop.add_argument("--lines", type=int, default=500)
    stop.add_argument("--contains", default="")
    stop.add_argument("--delete-file", action="store_true")
    stop.add_argument("--timeout", type=float, default=5)
    stop.set_defaults(func=_cmd_stop)

    capture = subparsers.add_parser("_capture", help=argparse.SUPPRESS)
    capture.add_argument("--session", required=True)
    capture.add_argument("--udid", required=True)
    capture.add_argument("--output", required=True)
    capture.add_argument("--state-file", required=True)
    capture.add_argument("--daemon-log", default="")
    capture.add_argument("--max-seconds", type=int, required=True)
    capture.set_defaults(func=_capture)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
