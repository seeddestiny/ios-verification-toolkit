#!/usr/bin/env python3
"""Read and securely persist machine-local toolkit configuration."""
from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any


CONFIG_VERSION = 1
CONFIG_FILE = (
    Path.home()
    / "Library"
    / "Application Support"
    / "ios-verification-toolkit"
    / "config.json"
)

STRING_KEYS = {
    "runtime_dir",
    "signing_team_id",
    "target_bundle_id",
    "wda_bundle_id",
    "xcode_developer_dir",
}
LIST_KEYS = {"target_labels"}
BOOL_KEYS = {
    "allow_insecure_npm",
    "allow_insecure_pypi",
    "allow_public_npm_fallback",
}
ALLOWED_KEYS = STRING_KEYS | LIST_KEYS | BOOL_KEYS

_TEAM_ID_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,254}$")


def _validate_bundle_id(value: str, key: str) -> str:
    if value.endswith(".") or ".." in value or not _BUNDLE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{key} 不是合法的 bundle ID")
    return value


def _validate_value(key: str, value: Any) -> Any:
    if key not in ALLOWED_KEYS:
        raise ValueError(f"不支持的本机配置项: {key}")
    if key in BOOL_KEYS:
        if not isinstance(value, bool):
            raise ValueError(f"{key} 必须是布尔值")
        return value
    if key in LIST_KEYS:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{key} 必须是字符串列表")
        cleaned = [item.strip() for item in value if item.strip()]
        if any("\x00" in item for item in cleaned):
            raise ValueError(f"{key} 包含非法字符")
        return cleaned
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{key} 必须是非空字符串")
    cleaned = value.strip()
    if key == "signing_team_id" and not _TEAM_ID_PATTERN.fullmatch(cleaned):
        raise ValueError("signing_team_id 必须是 10 位大写字母或数字")
    if key in {"target_bundle_id", "wda_bundle_id"}:
        return _validate_bundle_id(cleaned, key)
    if key == "xcode_developer_dir" and not Path(cleaned).expanduser().is_absolute():
        raise ValueError("xcode_developer_dir 必须是绝对路径")
    return cleaned


def validate_config(values: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a config payload."""
    if not isinstance(values, Mapping):
        raise ValueError("本机配置必须是 JSON 对象")
    return {key: _validate_value(key, value) for key, value in values.items()}


def load_local_config(config_file: Path | None = None) -> dict[str, Any]:
    """Load the private config, returning an empty mapping when absent."""
    path = CONFIG_FILE if config_file is None else Path(config_file)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("本机配置无法读取或不是合法 JSON") from exc
    if not isinstance(payload, dict) or payload.get("version") != CONFIG_VERSION:
        raise ValueError("本机配置版本不受支持；请通过配置工具重置")
    return validate_config(payload.get("values", {}))


def save_local_config(
    values: Mapping[str, Any],
    config_file: Path | None = None,
) -> None:
    """Atomically save config with user-only directory and file permissions."""
    normalized = validate_config(values)
    path = CONFIG_FILE if config_file is None else Path(config_file)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {"version": CONFIG_VERSION, "values": normalized},
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            stream.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def update_local_config(
    updates: Mapping[str, Any],
    *,
    remove: tuple[str, ...] = (),
    config_file: Path | None = None,
) -> dict[str, Any]:
    """Update selected values and return the normalized result."""
    values = load_local_config(config_file)
    for key in remove:
        if key not in ALLOWED_KEYS:
            raise ValueError(f"不支持的本机配置项: {key}")
        values.pop(key, None)
    values.update(updates)
    normalized = validate_config(values)
    save_local_config(normalized, config_file)
    return normalized


def clear_local_config(config_file: Path | None = None) -> None:
    path = CONFIG_FILE if config_file is None else Path(config_file)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def config_value(key: str, default: Any = "", config_file: Path | None = None) -> Any:
    if key not in ALLOWED_KEYS:
        raise ValueError(f"不支持的本机配置项: {key}")
    return load_local_config(config_file).get(key, default)


def main() -> int:
    """Internal read interface used by shell scripts; users use the tools wrapper."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("action", choices=("get",))
    parser.add_argument("key", choices=sorted(ALLOWED_KEYS))
    args = parser.parse_args()
    try:
        value = config_value(args.key, False if args.key in BOOL_KEYS else "")
    except ValueError as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 2
    if isinstance(value, bool):
        print("1" if value else "0")
    elif isinstance(value, list):
        print(",".join(value))
    else:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
