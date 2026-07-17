#!/usr/bin/env python3
"""Build, install, and launch an iOS app without Appium or WDA."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server.env_sanitizer import is_sensitive_env_name, sanitized_env  # noqa: E402
from mcp_server.runtime_paths import ensure_runtime_paths  # noqa: E402
from mcp_server.xcode_resolver import resolve_developer_dir  # noqa: E402


BUILD_SETTING_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
SAFE_PACKAGE_FLAGS = (
    "-disableAutomaticPackageResolution",
    "-onlyUsePackageVersionsFromResolvedFile",
    "-skipPackageUpdates",
)


def _private_output(kind: str, suffix: str) -> Path:
    paths = ensure_runtime_paths()
    directory = paths.logs if kind == "log" else paths.state
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{time.time_ns() % 1_000_000}"
    return directory / f"ios-app-{stamp}{suffix}"


def _tool(required_tool: str, source: Mapping[str, str] | None = None) -> tuple[Path, dict[str, str]]:
    developer_dir = resolve_developer_dir(required_tool, source)
    env = sanitized_env(source)
    env["DEVELOPER_DIR"] = str(developer_dir)
    env.pop("CC", None)
    env.pop("CXX", None)
    return developer_dir / "usr" / "bin" / required_tool, env


def _validate_project(workspace: str, project: str) -> tuple[str, Path]:
    if bool(workspace) == bool(project):
        raise ValueError("必须且只能提供 --workspace 或 --project 之一")
    kind, raw = ("-workspace", workspace) if workspace else ("-project", project)
    path = Path(raw).expanduser().resolve()
    expected = ".xcworkspace" if kind == "-workspace" else ".xcodeproj"
    if path.suffix != expected or not path.exists():
        raise ValueError(f"无效的 {expected} 路径: {path}")
    return kind, path


def build_command(
    xcodebuild: Path,
    *,
    workspace: str = "",
    project: str = "",
    scheme: str,
    configuration: str,
    destination: str,
    derived_data_path: str = "",
    build_settings: Sequence[str] = (),
) -> list[str]:
    kind, path = _validate_project(workspace, project)
    if not scheme.strip():
        raise ValueError("scheme 不能为空")
    settings: list[str] = []
    for setting in build_settings:
        if not BUILD_SETTING_RE.fullmatch(setting):
            raise ValueError(f"build setting 必须为 KEY=VALUE: {setting}")
        key = setting.split("=", 1)[0].upper()
        if key in {"ALLOWPROVISIONINGUPDATES", "ALLOWPROVISIONINGDEVICEREGISTRATION"}:
            raise ValueError(f"禁止自动更新签名或注册设备: {setting}")
        settings.append(setting)
    command = [
        str(xcodebuild),
        kind,
        str(path),
        "-scheme",
        scheme,
        "-configuration",
        configuration,
        "-destination",
        destination,
        *SAFE_PACKAGE_FLAGS,
    ]
    if derived_data_path:
        command.extend(["-derivedDataPath", str(Path(derived_data_path).expanduser().resolve())])
    command.extend(settings)
    command.append("build")
    return command


def _top_level_apps(derived_data_path: str) -> list[str]:
    if not derived_data_path:
        return []
    products = Path(derived_data_path).expanduser().resolve() / "Build" / "Products"
    if not products.is_dir():
        return []
    apps: list[str] = []
    for app in products.rglob("*.app"):
        if any(parent.suffix == ".app" for parent in app.parents):
            continue
        apps.append(str(app))
    return sorted(apps)


def _tail(path: Path, lines: int = 80) -> str:
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(errors="ignore").splitlines()[-max(1, lines):])


def _run_private(
    command: list[str],
    env: Mapping[str, str],
    *,
    timeout: float,
    log_path: Path | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], Path]:
    log_path = log_path or _private_output("log", ".log")
    if log_path.exists():
        raise ValueError(f"为避免覆盖已有文件，日志路径必须不存在: {log_path}")
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path.parent.chmod(0o700)
    with log_path.open("xb") as handle:
        log_path.chmod(0o600)
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=dict(env),
        )
    return result, log_path


def _print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        xcodebuild, env = _tool("xcodebuild")
        command = build_command(
            xcodebuild,
            workspace=args.workspace,
            project=args.project,
            scheme=args.scheme,
            configuration=args.configuration,
            destination=args.destination,
            derived_data_path=args.derived_data_path,
            build_settings=args.build_setting,
        )
        log_path = Path(args.log_path).expanduser().resolve() if args.log_path else None
        result, log_path = _run_private(command, env, timeout=args.timeout, log_path=log_path)
        payload: dict[str, Any] = {
            "ok": result.returncode == 0,
            "action": "build",
            "returncode": result.returncode,
            "log_path": str(log_path),
            "destination": args.destination,
            "app_paths": _top_level_apps(args.derived_data_path) if result.returncode == 0 else [],
        }
        if result.returncode != 0:
            payload["error_tail"] = _tail(log_path)
        return _print(payload)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return _print({"ok": False, "action": "build", "error": str(exc)})


def _devicectl_outputs() -> tuple[Path, Path]:
    return _private_output("state", ".json"), _private_output("log", ".log")


def _run_devicectl(command: list[str], env: Mapping[str, str], timeout: float) -> dict[str, Any]:
    json_path, log_path = _devicectl_outputs()
    if len(command) < 4:
        raise ValueError("devicectl command 不完整")
    output_options = ["--json-output", str(json_path), "--log-output", str(log_path)]
    command = [*command[:4], *output_options, *command[4:]]
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        env=dict(env),
    )
    for path in (json_path, log_path):
        if path.exists():
            path.chmod(0o600)
    payload: dict[str, Any] = {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "result_path": str(json_path) if json_path.exists() else None,
        "log_path": str(log_path) if log_path.exists() else None,
    }
    if result.returncode != 0:
        detail = _tail(log_path) if log_path.exists() else result.stderr.decode(errors="ignore")[-4000:]
        payload["error_tail"] = detail
    return payload


def _cmd_install(args: argparse.Namespace) -> int:
    try:
        app = Path(args.app).expanduser().resolve()
        if app.suffix != ".app" or not app.is_dir():
            raise ValueError(f"无效的 .app 产物: {app}")
        devicectl, env = _tool("devicectl")
        payload = _run_devicectl(
            [str(devicectl), "device", "install", "app", "--device", args.device, str(app)],
            env,
            args.timeout,
        )
        payload.update({"action": "install", "app_path": str(app)})
        return _print(payload)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return _print({"ok": False, "action": "install", "error": str(exc)})


def _decode_environment(value: str) -> dict[str, str]:
    payload = json.loads(value or "{}")
    if not isinstance(payload, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in payload.items()):
        raise ValueError("environment 必须是字符串到字符串的 JSON object")
    sensitive = sorted(key for key in payload if is_sensitive_env_name(key))
    if sensitive:
        raise ValueError(f"禁止通过运行工具注入疑似凭据变量: {', '.join(sensitive)}")
    return payload


def _decode_arguments(value: str) -> list[str]:
    payload = json.loads(value or "[]")
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError("arguments 必须是字符串 JSON array")
    return payload


def launch_command(
    devicectl: Path,
    *,
    device: str,
    bundle_id: str,
    environment: Mapping[str, str],
    arguments: Sequence[str],
    terminate_existing: bool,
) -> list[str]:
    if not device.strip() or not bundle_id.strip():
        raise ValueError("device 和 bundle-id 不能为空")
    command = [str(devicectl), "device", "process", "launch", "--device", device]
    if environment:
        command.extend(["--environment-variables", json.dumps(dict(environment), ensure_ascii=False, separators=(",", ":"))])
    if terminate_existing:
        command.append("--terminate-existing")
    command.append(bundle_id)
    command.extend(arguments)
    return command


def _cmd_launch(args: argparse.Namespace) -> int:
    try:
        environment = _decode_environment(args.environment)
        arguments = _decode_arguments(args.arguments)
        devicectl, env = _tool("devicectl")
        command = launch_command(
            devicectl,
            device=args.device,
            bundle_id=args.bundle_id,
            environment=environment,
            arguments=arguments,
            terminate_existing=args.terminate_existing,
        )
        payload = _run_devicectl(command, env, args.timeout)
        payload.update(
            {
                "action": "launch",
                "bundle_id": args.bundle_id,
                "injected_environment": bool(environment),
                "injected_arguments": bool(arguments),
            }
        )
        return _print(payload)
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return _print({"ok": False, "action": "launch", "error": str(exc)})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="使用已有工程和本地依赖执行 xcodebuild")
    source = build.add_mutually_exclusive_group(required=True)
    source.add_argument("--workspace", default="")
    source.add_argument("--project", default="")
    build.add_argument("--scheme", required=True)
    build.add_argument("--configuration", default="Debug")
    build.add_argument("--destination", default="generic/platform=iOS")
    build.add_argument("--derived-data-path", default="")
    build.add_argument("--build-setting", action="append", default=[])
    build.add_argument("--log-path", default="")
    build.add_argument("--timeout", type=float, default=3600)
    build.set_defaults(func=_cmd_build)

    install = subparsers.add_parser("install", help="使用 devicectl 安装已签名 .app")
    install.add_argument("--device", required=True, help="CoreDevice identifier、硬件 UDID 或唯一设备名")
    install.add_argument("--app", required=True)
    install.add_argument("--timeout", type=float, default=180)
    install.set_defaults(func=_cmd_install)

    launch = subparsers.add_parser("launch", help="使用 devicectl 启动 App 并可注入调试环境")
    launch.add_argument("--device", required=True, help="CoreDevice identifier、硬件 UDID 或唯一设备名")
    launch.add_argument("--bundle-id", required=True)
    launch.add_argument("--environment", default="{}", help="字符串到字符串的 JSON object；禁止凭据")
    launch.add_argument("--arguments", default="[]", help="字符串 JSON array")
    launch.add_argument("--terminate-existing", action=argparse.BooleanOptionalAction, default=True)
    launch.add_argument("--timeout", type=float, default=60)
    launch.set_defaults(func=_cmd_launch)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
