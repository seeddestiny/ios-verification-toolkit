#!/usr/bin/env python3
"""Resolve private, project-local directories for runtime artifacts."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    logs: Path
    screenshots: Path
    state: Path


def runtime_paths(
    source: Mapping[str, str] | None = None,
    *,
    project_root: str | os.PathLike[str] | None = None,
) -> RuntimePaths:
    """Return runtime paths without creating them.

    ``IOS_MCP_RUNTIME_DIR`` may be absolute or relative to ``<project>/.runtime``.
    The default is ``<project>/.runtime``. Relative overrides therefore remain
    covered by the project's ignore rule.
    """
    env = os.environ if source is None else source
    project = Path(project_root or PROJECT_ROOT).expanduser().resolve()
    default_root = (project / ".runtime").resolve()
    configured = env.get("IOS_MCP_RUNTIME_DIR", "").strip()
    configured_is_relative = False
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            configured_is_relative = True
            root = default_root / root
    else:
        root = default_root
    root = root.resolve()
    if root == project or root == Path(root.anchor):
        raise ValueError("IOS_MCP_RUNTIME_DIR 必须指向独立子目录，不能是项目根目录或文件系统根目录")
    if configured_is_relative:
        try:
            root.relative_to(default_root)
        except ValueError as exc:
            raise ValueError("相对 IOS_MCP_RUNTIME_DIR 不能跳出 .runtime/") from exc
    try:
        root.relative_to(project)
    except ValueError:
        pass
    else:
        try:
            root.relative_to(default_root)
        except ValueError as exc:
            raise ValueError("项目内的 IOS_MCP_RUNTIME_DIR 必须位于 .runtime/ 下") from exc
    return RuntimePaths(
        root=root,
        logs=root / "logs",
        screenshots=root / "screenshots",
        state=root / "state",
    )


def ensure_runtime_paths(
    source: Mapping[str, str] | None = None,
    *,
    project_root: str | os.PathLike[str] | None = None,
) -> RuntimePaths:
    """Create all runtime directories with user-only permissions."""
    paths = runtime_paths(source, project_root=project_root)
    for path in (paths.root, paths.logs, paths.screenshots, paths.state):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "kind",
        nargs="?",
        choices=("root", "logs", "screenshots", "state"),
        default="root",
    )
    parser.add_argument("--create", action="store_true", help="create all runtime directories")
    args = parser.parse_args()
    paths = ensure_runtime_paths() if args.create else runtime_paths()
    print(getattr(paths, args.kind))


if __name__ == "__main__":
    main()
