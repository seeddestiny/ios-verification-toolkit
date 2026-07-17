#!/usr/bin/env python3
"""Resolve a complete local Xcode without changing global xcode-select state."""
from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Sequence

try:
    from .env_sanitizer import sanitized_env
    from .local_config import load_local_config
except ImportError:
    from env_sanitizer import sanitized_env
    from local_config import load_local_config


def _valid_developer_dir(path: Path, required_tool: str) -> bool:
    return (path / "usr" / "bin" / required_tool).is_file()


def resolve_developer_dir(
    required_tool: str,
    source: Mapping[str, str] | None = None,
    *,
    candidates: Sequence[Path] | None = None,
) -> Path:
    """Resolve Xcode locally while preserving the machine-wide selection."""
    environment = os.environ if source is None else source
    explicit = environment.get("DEVELOPER_DIR", "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if _valid_developer_dir(path, required_tool):
            return path
        raise RuntimeError(f"DEVELOPER_DIR 中找不到 {required_tool}: {path}")

    configured = ""
    if source is None:
        configured = str(load_local_config().get("xcode_developer_dir") or "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if _valid_developer_dir(path, required_tool):
            return path
        raise RuntimeError(
            f"本机配置选择的 Xcode 中找不到 {required_tool}；"
            "请运行 python3 tools/ios_config_tool.py 重新选择"
        )

    try:
        selected = subprocess.run(
            ["xcode-select", "-p"],
            capture_output=True,
            text=True,
            timeout=10,
            env=sanitized_env(environment),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        selected = None
    if selected and selected.returncode == 0 and selected.stdout.strip():
        path = Path(selected.stdout.strip()).expanduser().resolve()
        if _valid_developer_dir(path, required_tool):
            return path

    if candidates is None:
        discovered = [
            Path(item)
            for item in glob.glob("/Applications/Xcode*.app/Contents/Developer")
        ]
        discovered.extend(
            Path(item)
            for item in glob.glob(
                str(Path.home() / "Applications/Xcode*.app/Contents/Developer")
            )
        )
    else:
        discovered = list(candidates)
    valid = sorted(
        {
            path.expanduser().resolve()
            for path in discovered
            if _valid_developer_dir(path.expanduser().resolve(), required_tool)
        }
    )
    if len(valid) == 1:
        return valid[0]
    if not valid:
        raise RuntimeError(
            f"未找到包含 {required_tool} 的完整 Xcode"
        )
    raise RuntimeError(
        "发现多个可用 Xcode，无法安全选择；"
        "请运行 python3 tools/ios_config_tool.py 完成本机选择"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", default="xcodebuild")
    args = parser.parse_args()
    try:
        print(resolve_developer_dir(args.tool))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
