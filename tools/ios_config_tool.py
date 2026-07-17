#!/usr/bin/env python3
"""Interactively manage private, machine-local toolkit settings."""
from __future__ import annotations

import argparse
import glob
import sys
from collections.abc import Callable
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server.local_config import (  # noqa: E402
    BOOL_KEYS,
    clear_local_config,
    load_local_config,
    update_local_config,
)
from mcp_server.signing_identity import discover_signing_identities  # noqa: E402


def _status(values: dict[str, object]) -> None:
    configured = lambda key: "已固定" if key in values else "自动"
    target = "已配置" if values.get("target_bundle_id") or values.get("target_labels") else "未配置"
    relaxed = [key for key in BOOL_KEYS if values.get(key)]
    print("本机高级配置（值不会写入仓库）：")
    print(f"  Xcode       : {configured('xcode_developer_dir')}")
    print(f"  签名团队    : {configured('signing_team_id')}")
    print(f"  WDA Bundle  : {configured('wda_bundle_id')}")
    print(f"  目标 App    : {target}")
    print(f"  运行时目录  : {configured('runtime_dir')}")
    print(f"  安全放宽项  : {len(relaxed)} 项")


def _choose(prompt: str, options: list[str]) -> int | None:
    for index, label in enumerate(options, 1):
        print(f"  {index}. {label}")
    print("  0. 恢复自动")
    answer = input(f"{prompt}: ").strip()
    if answer == "0":
        return None
    if not answer.isdigit() or not 1 <= int(answer) <= len(options):
        raise ValueError("选择无效")
    return int(answer) - 1


def _xcode_candidates() -> list[Path]:
    discovered = [Path(item) for item in glob.glob("/Applications/Xcode*.app/Contents/Developer")]
    discovered.extend(
        Path(item)
        for item in glob.glob(str(Path.home() / "Applications/Xcode*.app/Contents/Developer"))
    )
    return sorted(
        {
            path.expanduser().resolve()
            for path in discovered
            if (path.expanduser().resolve() / "usr/bin/xcodebuild").is_file()
        }
    )


def _configure_xcode() -> None:
    candidates = _xcode_candidates()
    if not candidates:
        raise ValueError("未发现包含 xcodebuild 的完整 Xcode")
    labels = [path.parents[1].name for path in candidates]
    selected = _choose("选择 Xcode", labels)
    if selected is None:
        update_local_config({}, remove=("xcode_developer_dir",))
    else:
        update_local_config({"xcode_developer_dir": str(candidates[selected])})


def _configure_team() -> None:
    team_ids = list(dict.fromkeys(identity.team_id for identity in discover_signing_identities()))
    if not team_ids:
        raise ValueError("未发现 Apple Development 签名团队")
    labels = [f"签名团队 …{team_id[-4:]}" for team_id in team_ids]
    selected = _choose("选择签名团队", labels)
    if selected is None:
        update_local_config({}, remove=("signing_team_id",))
    else:
        update_local_config({"signing_team_id": team_ids[selected]})


def _configure_text(key: str, prompt: str) -> None:
    value = input(f"{prompt}（留空恢复自动）: ").strip()
    if value:
        update_local_config({key: value})
    else:
        update_local_config({}, remove=(key,))


def _configure_target() -> None:
    bundle_id = input("目标 App Bundle ID（留空则不固定）: ").strip()
    labels = [item.strip() for item in input("主屏名称，多个用逗号分隔（可留空）: ").split(",") if item.strip()]
    updates: dict[str, object] = {}
    remove: list[str] = []
    if bundle_id:
        updates["target_bundle_id"] = bundle_id
    else:
        remove.append("target_bundle_id")
    if labels:
        updates["target_labels"] = labels
    else:
        remove.append("target_labels")
    update_local_config(updates, remove=tuple(remove))


def _yes_no(prompt: str, current: bool) -> bool:
    suffix = "Y/n" if current else "y/N"
    answer = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not answer:
        return current
    if answer in {"y", "yes"}:
        return True
    if answer in {"n", "no"}:
        return False
    raise ValueError("请输入 y 或 n")


def _configure_security() -> None:
    values = load_local_config()
    updates = {
        "allow_insecure_npm": _yes_no("允许本机 npm 使用 HTTP 源", bool(values.get("allow_insecure_npm"))),
        "allow_insecure_pypi": _yes_no("允许本机 pip 使用 HTTP 源", bool(values.get("allow_insecure_pypi"))),
        "allow_public_npm_fallback": _yes_no(
            "本机 npm 源缺包时允许单次回退官方源",
            bool(values.get("allow_public_npm_fallback")),
        ),
    }
    update_local_config(updates)


def configure() -> int:
    actions: list[tuple[str, Callable[[], None]]] = [
        ("选择 Xcode", _configure_xcode),
        ("选择签名团队", _configure_team),
        ("兼容已有 WDA Bundle ID", lambda: _configure_text("wda_bundle_id", "WDA Bundle ID")),
        ("设置目标 App", _configure_target),
        ("修改运行时目录", lambda: _configure_text("runtime_dir", "运行时目录")),
        ("调整供应链安全策略", _configure_security),
    ]
    _status(load_local_config())
    print("\n选择要修改的项目：")
    for index, (label, _) in enumerate(actions, 1):
        print(f"  {index}. {label}")
    print("  0. 退出")
    answer = input("选择: ").strip()
    if answer == "0" or not answer:
        return 0
    if not answer.isdigit() or not 1 <= int(answer) <= len(actions):
        raise ValueError("选择无效")
    actions[int(answer) - 1][1]()
    print("配置已保存；下次运行自动生效。")
    return 0


def reset() -> int:
    answer = input("清空全部本机高级配置并恢复自动选择？ [y/N]: ").strip().lower()
    if answer in {"y", "yes"}:
        clear_local_config()
        print("已恢复自动选择。")
    else:
        print("未修改。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("configure", "show", "reset"), default="configure")
    args = parser.parse_args()
    try:
        if args.action == "show":
            _status(load_local_config())
            return 0
        if args.action == "reset":
            return reset()
        return configure()
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
