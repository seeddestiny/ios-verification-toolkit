#!/usr/bin/env python3
"""发现并选择当前连接的物理 iOS/iPadOS 设备。

默认不保存任何设备 UDID。选择优先级：
1. 调用参数或 IOS_MCP_UDID 显式指定的已连接设备；
2. 调用参数或 IOS_MCP_DEVICE_NAME 唯一匹配的设备；
3. 当前只有一台候选设备时自动选择；
4. 零台或多台未消歧时明确失败。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterable

try:
    from .env_sanitizer import sanitized_env
except ImportError:
    from env_sanitizer import sanitized_env


class DeviceSelectionError(RuntimeError):
    """当前设备状态不足以安全确定唯一目标。"""


@dataclass(frozen=True)
class ConnectedDevice:
    name: str
    udid: str
    core_device_identifier: str = ""
    product_type: str = ""
    model: str = ""
    os_version: str = ""
    transport: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _run(command: list[str], timeout: float = 25) -> subprocess.CompletedProcess[str]:
    env = sanitized_env()
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _discover_with_devicectl() -> tuple[list[ConnectedDevice], str]:
    output_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            prefix="ios-device-list-",
            suffix=".json",
            delete=False,
        ) as temp:
            output_path = temp.name
        result = _run(
            ["xcrun", "devicectl", "list", "devices", "--json-output", output_path]
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return [], f"devicectl 失败: {detail[:300]}"
        payload = json.loads(Path(output_path).read_text())
    except FileNotFoundError:
        return [], "未找到 xcrun/devicectl"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return [], f"devicectl 设备发现异常: {exc}"
    finally:
        if output_path:
            try:
                Path(output_path).unlink()
            except FileNotFoundError:
                pass

    devices: list[ConnectedDevice] = []
    for raw in payload.get("result", {}).get("devices", []):
        hardware = raw.get("hardwareProperties") or {}
        connection = raw.get("connectionProperties") or {}
        properties = raw.get("deviceProperties") or {}
        platform = str(hardware.get("platform") or "")
        device_type = str(hardware.get("deviceType") or "")
        if hardware.get("reality") != "physical":
            continue
        if platform not in {"iOS", "iPadOS"}:
            continue
        if not (device_type.startswith("iPhone") or device_type.startswith("iPad") or device_type.startswith("iPod")):
            continue
        if connection.get("tunnelState") != "connected":
            continue
        udid = str(hardware.get("udid") or "").strip()
        if not udid:
            continue
        devices.append(
            ConnectedDevice(
                name=str(properties.get("name") or device_type or udid),
                udid=udid,
                core_device_identifier=str(raw.get("identifier") or ""),
                product_type=str(hardware.get("productType") or ""),
                model=str(hardware.get("marketingName") or device_type),
                os_version=str(properties.get("osVersionNumber") or ""),
                transport=str(connection.get("transportType") or ""),
            )
        )
    return sorted(devices, key=lambda item: (item.name.casefold(), item.udid)), ""


def _idevice_value(udid: str, key: str, network: bool) -> str:
    command = ["ideviceinfo"]
    if network:
        command.append("--network")
    command.extend(["-u", udid, "-k", key])
    try:
        result = _run(command, timeout=8)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _discover_with_libimobiledevice() -> tuple[list[ConnectedDevice], str]:
    found: dict[str, set[str]] = {}
    errors: list[str] = []
    for flag, transport in (("-l", "wired"), ("-n", "network")):
        try:
            result = _run(["idevice_id", flag], timeout=8)
        except FileNotFoundError:
            return [], "未找到 idevice_id"
        except subprocess.TimeoutExpired:
            errors.append(f"idevice_id {flag} 超时")
            continue
        if result.returncode != 0:
            errors.append((result.stderr or result.stdout).strip())
            continue
        for udid in result.stdout.splitlines():
            udid = udid.strip()
            if udid:
                found.setdefault(udid, set()).add(transport)

    devices: list[ConnectedDevice] = []
    for udid, transports in found.items():
        network_only = "wired" not in transports
        product_type = _idevice_value(udid, "ProductType", network_only)
        if not product_type.startswith(("iPhone", "iPad", "iPod")):
            continue
        devices.append(
            ConnectedDevice(
                name=_idevice_value(udid, "DeviceName", network_only) or udid,
                udid=udid,
                product_type=product_type,
                os_version=_idevice_value(udid, "ProductVersion", network_only),
                transport="wired" if "wired" in transports else "network",
            )
        )
    return sorted(devices, key=lambda item: (item.name.casefold(), item.udid)), "; ".join(filter(None, errors))


def discover_connected_ios_devices() -> list[ConnectedDevice]:
    devices, devicectl_error = _discover_with_devicectl()
    if devices:
        return devices
    fallback, fallback_error = _discover_with_libimobiledevice()
    if fallback:
        return fallback
    details = "; ".join(filter(None, (devicectl_error, fallback_error)))
    if details:
        raise DeviceSelectionError(f"未发现当前连接的物理 iPhone/iPad。{details}")
    return []


def _candidate_lines(devices: Iterable[ConnectedDevice]) -> str:
    lines = []
    for device in devices:
        summary = f"{device.name} | {device.udid}"
        extras = [device.model, device.os_version, device.transport]
        extras = [item for item in extras if item]
        if extras:
            summary += " | " + " | ".join(extras)
        lines.append(f"- {summary}")
    return "\n".join(lines)


def select_target_device(
    devices: list[ConnectedDevice],
    explicit_udid: str = "",
    name_selector: str = "",
) -> ConnectedDevice:
    explicit_udid = explicit_udid.strip()
    name_selector = name_selector.strip()
    if explicit_udid:
        matches = [item for item in devices if item.udid.casefold() == explicit_udid.casefold()]
        if len(matches) == 1:
            return matches[0]
        candidates = _candidate_lines(devices) or "(无)"
        raise DeviceSelectionError(
            f"指定的 IOS_MCP_UDID 当前未连接: {explicit_udid}\n当前候选:\n{candidates}"
        )

    if name_selector:
        exact = [item for item in devices if item.name.casefold() == name_selector.casefold()]
        matches = exact or [
            item for item in devices if name_selector.casefold() in item.name.casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        candidates = _candidate_lines(matches or devices) or "(无)"
        reason = "匹配到多台设备" if len(matches) > 1 else "没有匹配设备"
        raise DeviceSelectionError(
            f"IOS_MCP_DEVICE_NAME {reason}: {name_selector}\n候选:\n{candidates}"
        )

    if len(devices) == 1:
        return devices[0]
    if not devices:
        raise DeviceSelectionError("未发现当前连接的物理 iPhone/iPad")
    raise DeviceSelectionError(
        "检测到多台当前可用的 iPhone/iPad，需要你选择本轮验证设备，"
        "禁止静默选择第一台。\n目前待选设备：\n"
        + _candidate_lines(devices)
        + "\n请使用 --udid/--device-name，或设置 IOS_MCP_UDID/IOS_MCP_DEVICE_NAME。"
    )


def resolve_target_device(
    explicit_udid: str | None = None,
    name_selector: str | None = None,
) -> ConnectedDevice:
    devices = discover_connected_ios_devices()
    return select_target_device(
        devices,
        explicit_udid if explicit_udid is not None else os.environ.get("IOS_MCP_UDID", ""),
        name_selector if name_selector is not None else os.environ.get("IOS_MCP_DEVICE_NAME", ""),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="发现并安全选择当前连接的 iOS 真机")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="以 JSON 列出当前候选设备")
    resolve = subparsers.add_parser("resolve", help="按规则解析唯一目标设备")
    group = resolve.add_mutually_exclusive_group()
    group.add_argument("--udid", default=None)
    group.add_argument("--name", default=None)
    resolve.add_argument(
        "--field",
        choices=("json", "udid", "name", "core-device-id"),
        default="json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "list":
            devices = discover_connected_ios_devices()
            print(json.dumps({"ok": True, "devices": [item.to_dict() for item in devices]}, ensure_ascii=False))
            return 0
        device = resolve_target_device(args.udid, args.name)
        if args.field == "udid":
            print(device.udid)
        elif args.field == "name":
            print(device.name)
        elif args.field == "core-device-id":
            print(device.core_device_identifier or device.udid)
        else:
            print(json.dumps(device.to_dict(), ensure_ascii=False))
        return 0
    except DeviceSelectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
