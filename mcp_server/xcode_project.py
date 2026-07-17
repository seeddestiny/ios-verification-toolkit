#!/usr/bin/env python3
"""Open a local Xcode project with the toolkit-selected Xcode application."""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

try:
    from .env_sanitizer import sanitized_env
    from .xcode_resolver import resolve_developer_dir
except ImportError:
    from env_sanitizer import sanitized_env
    from xcode_resolver import resolve_developer_dir


def xcode_app_for_developer_dir(developer_dir: Path) -> Path:
    path = developer_dir.expanduser().resolve()
    if path.name != "Developer" or path.parent.name != "Contents":
        raise ValueError("解析到的开发者目录不属于完整 Xcode 应用")
    app = path.parent.parent
    if app.suffix != ".app" or not app.is_dir():
        raise ValueError("解析到的开发者目录不属于完整 Xcode 应用")
    return app


def open_xcode_project(
    project: str | Path,
    source: Mapping[str, str] | None = None,
    *,
    developer_dir: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    project_path = Path(project).expanduser().resolve()
    if project_path.suffix not in {".xcodeproj", ".xcworkspace"} or not project_path.is_dir():
        raise ValueError("WDA Xcode 工程不存在；请先安装 XCUITest 驱动")
    selected = developer_dir or resolve_developer_dir("xcodebuild", source)
    xcode_app = xcode_app_for_developer_dir(selected)
    result = runner(
        ["/usr/bin/open", "-a", str(xcode_app), str(project_path)],
        capture_output=True,
        text=True,
        timeout=15,
        env=sanitized_env(source),
    )
    if result.returncode != 0:
        raise RuntimeError("无法自动打开 WDA Xcode 工程")
    return xcode_app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    args = parser.parse_args()
    try:
        open_xcode_project(args.project)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("WDA Xcode 工程已打开。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
