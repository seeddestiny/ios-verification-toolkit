"""Build a stable, per-Mac WebDriverAgent bundle identifier."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import uuid
from collections.abc import Mapping

try:
    from .env_sanitizer import sanitized_env
except ImportError:
    from env_sanitizer import sanitized_env


DEFAULT_WDA_BUNDLE_PREFIX = "com.iosdevice.mcp.WebDriverAgentRunner"
_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,254}$")
_PLATFORM_UUID_PATTERN = re.compile(r'"IOPlatformUUID"\s*=\s*"([^"]+)"')


def _validate_bundle_id(value: str, label: str) -> str:
    if not value or value.endswith(".") or ".." in value or not _BUNDLE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{label} 不是合法的 bundle ID: {value!r}")
    return value


def _platform_machine_seed(environment: Mapping[str, str]) -> str:
    explicit_seed = environment.get("IOS_MCP_MACHINE_ID", "").strip()
    if explicit_seed:
        return explicit_seed

    ioreg = shutil.which("ioreg") or "/usr/sbin/ioreg"
    try:
        result = subprocess.run(
            [ioreg, "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=5,
            env=sanitized_env(environment),
        )
        match = _PLATFORM_UUID_PATTERN.search(result.stdout)
        if result.returncode == 0 and match:
            return match.group(1)
    except (OSError, subprocess.TimeoutExpired):
        pass

    machine_id = Path("/etc/machine-id")
    try:
        value = machine_id.read_text().strip()
        if value:
            return value
    except OSError:
        pass

    return f"{socket.gethostname()}:{uuid.getnode():012x}"


def build_wda_bundle_id(
    source: Mapping[str, str] | None = None,
    *,
    machine_seed: str | None = None,
) -> str:
    """Return an override or a stable bundle ID derived from the current Mac."""
    environment = os.environ if source is None else source
    explicit_bundle_id = environment.get("IOS_MCP_WDA_BUNDLE_ID", "").strip()
    if explicit_bundle_id:
        return _validate_bundle_id(explicit_bundle_id, "IOS_MCP_WDA_BUNDLE_ID")

    prefix = environment.get("IOS_MCP_WDA_BUNDLE_PREFIX", DEFAULT_WDA_BUNDLE_PREFIX).strip()
    _validate_bundle_id(prefix, "IOS_MCP_WDA_BUNDLE_PREFIX")
    seed = _platform_machine_seed(environment) if machine_seed is None else machine_seed.strip()
    if not seed:
        raise ValueError("无法取得用于生成 WDA bundle ID 的机器标识")
    machine_token = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return _validate_bundle_id(f"{prefix}.m{machine_token}", "动态 WDA bundle ID")


def main() -> int:
    try:
        print(build_wda_bundle_id())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
