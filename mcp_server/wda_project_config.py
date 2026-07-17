#!/usr/bin/env python3
"""Persist the machine-specific WDA bundle ID into the local Appium project."""
from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

try:
    from .wda_bundle_id import build_wda_bundle_id
except ImportError:
    from wda_bundle_id import build_wda_bundle_id


DEFAULT_WDA_PROJECT = (
    Path.home()
    / ".appium"
    / "node_modules"
    / "appium-xcuitest-driver"
    / "node_modules"
    / "appium-webdriveragent"
    / "WebDriverAgent.xcodeproj"
)
_OBJECT_ID = r"[A-F0-9]{24}"


def _object_body(text: str, object_id: str) -> tuple[int, int, str]:
    pattern = re.compile(
        rf"(?ms)^\t\t{re.escape(object_id)} /\* [^\n]* \*/ = \{{\n"
        rf"(?P<body>.*?)^\t\t\}};"
    )
    match = pattern.search(text)
    if not match:
        raise ValueError("WDA 工程结构不受支持：找不到目标配置对象")
    return match.start("body"), match.end("body"), match.group("body")


def _runner_configuration_ids(text: str) -> list[str]:
    target_pattern = re.compile(
        rf"(?ms)^\t\t(?P<id>{_OBJECT_ID}) /\* WebDriverAgentRunner \*/ = \{{\n"
        rf"(?P<body>.*?)^\t\t\}};"
    )
    config_list_id = ""
    for match in target_pattern.finditer(text):
        body = match.group("body")
        if "isa = PBXNativeTarget;" not in body or "name = WebDriverAgentRunner;" not in body:
            continue
        config_match = re.search(
            rf"buildConfigurationList = (?P<id>{_OBJECT_ID}) ", body
        )
        if not config_match:
            raise ValueError("WDA Runner target 缺少构建配置列表")
        config_list_id = config_match.group("id")
        break
    if not config_list_id:
        raise ValueError("WDA 工程结构不受支持：找不到 WebDriverAgentRunner target")

    _, _, list_body = _object_body(text, config_list_id)
    configurations = re.search(
        r"(?ms)buildConfigurations = \((?P<items>.*?)\);", list_body
    )
    if not configurations:
        raise ValueError("WDA Runner target 缺少 Debug/Release 配置")
    ids = re.findall(rf"^\s*({_OBJECT_ID}) /\*", configurations.group("items"), re.MULTILINE)
    if not ids:
        raise ValueError("WDA Runner target 没有可修改的构建配置")
    return ids


def render_wda_project(text: str, bundle_id: str) -> tuple[str, int]:
    """Return a project with only the iOS runner bundle IDs updated."""
    configuration_ids = _runner_configuration_ids(text)
    updated = text
    replacements = 0
    for object_id in configuration_ids:
        start, end, body = _object_body(updated, object_id)
        pattern = re.compile(
            r"(?m)^(?P<indent>\s*)PRODUCT_BUNDLE_IDENTIFIER = [^;]+;$"
        )
        rendered, count = pattern.subn(
            rf"\g<indent>PRODUCT_BUNDLE_IDENTIFIER = {bundle_id};",
            body,
            count=1,
        )
        if count != 1:
            raise ValueError("WDA Runner 构建配置缺少唯一 Bundle ID")
        updated = updated[:start] + rendered + updated[end:]
        replacements += count
    return updated, replacements


def configure_wda_project(
    project: str | Path = DEFAULT_WDA_PROJECT,
    *,
    bundle_id: str | None = None,
    validator: Callable[..., subprocess.CompletedProcess[str]] | None = subprocess.run,
) -> bool:
    project_path = Path(project).expanduser().resolve()
    pbxproj = project_path / "project.pbxproj"
    if project_path.suffix != ".xcodeproj" or not pbxproj.is_file():
        raise ValueError("WDA Xcode 工程不存在；请先安装 XCUITest 驱动")
    selected_bundle_id = bundle_id or build_wda_bundle_id()
    original = pbxproj.read_text(encoding="utf-8")
    rendered, replacements = render_wda_project(original, selected_bundle_id)
    if replacements < 1:
        raise ValueError("没有修改任何 WDA Runner Bundle ID")
    if rendered == original:
        return False

    mode = stat.S_IMODE(pbxproj.stat().st_mode)
    temporary = pbxproj.parent / f".{pbxproj.name}.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
        if validator is not None:
            result = validator(
                ["/usr/bin/plutil", "-lint", str(temporary)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                raise ValueError("动态 Bundle ID 写入后工程格式校验失败")
        os.replace(temporary, pbxproj)
        pbxproj.chmod(mode)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", default=str(DEFAULT_WDA_PROJECT))
    args = parser.parse_args()
    try:
        changed = configure_wda_project(args.project)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if changed:
        print("WDA 工程已写入当前 Mac 的动态 Bundle ID。")
    else:
        print("WDA 工程的动态 Bundle ID 已是当前值。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
