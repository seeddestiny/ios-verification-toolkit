#!/usr/bin/env python3
"""Resolve Apple Development signing metadata without storing personal values."""
from __future__ import annotations

import argparse
import plistlib
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

try:
    from .env_sanitizer import sanitized_env
except ImportError:
    from env_sanitizer import sanitized_env


_TEAM_ID_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
_IDENTITY_PATTERN = re.compile(
    r'^\s*\d+\)\s+[0-9A-Fa-f]+\s+"'
    r'(?P<name>(?:Apple Development|iPhone Developer): .+? '
    r'\((?P<team_id>[A-Z0-9]{10})\))"\s*$',
    re.MULTILINE,
)


@dataclass(frozen=True)
class SigningIdentity:
    common_name: str
    team_id: str


def parse_signing_identities(output: str) -> list[SigningIdentity]:
    """Parse development identities from ``security find-identity`` output."""
    return [
        SigningIdentity(match.group("name"), match.group("team_id"))
        for match in _IDENTITY_PATTERN.finditer(output)
    ]


def discover_signing_identities(
    source: Mapping[str, str] | None = None,
) -> list[SigningIdentity]:
    environment = os.environ if source is None else source
    try:
        result = subprocess.run(
            ["security", "find-identity", "-v", "-p", "codesigning"],
            capture_output=True,
            text=True,
            timeout=10,
            env=sanitized_env(environment),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"无法读取本机代码签名身份: {exc}") from exc
    if result.returncode != 0:
        raise ValueError("无法读取本机代码签名身份，请检查 macOS Keychain")
    return parse_signing_identities(result.stdout)


def discover_existing_wda_team_ids(
    source: Mapping[str, str] | None = None,
) -> list[str]:
    """Read team identifiers from locally signed WDA build products."""
    environment = os.environ if source is None else source
    derived_data = os.path.expanduser(
        environment.get(
            "IOS_MCP_WDA_DERIVED_DATA_GLOB",
            "~/Library/Developer/Xcode/DerivedData/"
            "WebDriverAgent-*/Build/Products/*iphoneos/*.app",
        )
    )
    from glob import glob

    team_ids: set[str] = set()
    for app_path in glob(derived_data):
        try:
            result = subprocess.run(
                ["codesign", "-d", "--entitlements", ":-", app_path],
                capture_output=True,
                timeout=10,
                env=sanitized_env(environment),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        data = result.stdout or result.stderr
        plist_start = data.find(b"<?xml")
        if plist_start < 0:
            continue
        try:
            entitlements = plistlib.loads(data[plist_start:])
        except (plistlib.InvalidFileException, ValueError):
            continue
        team_id = str(entitlements.get("com.apple.developer.team-identifier") or "")
        if _TEAM_ID_PATTERN.fullmatch(team_id):
            team_ids.add(team_id)
    return sorted(team_ids)


def _validate_team_id(value: str) -> str:
    if not _TEAM_ID_PATTERN.fullmatch(value):
        raise ValueError("IOS_MCP_TEAM_ID 必须是 10 位大写字母或数字")
    return value


def resolve_team_id(
    source: Mapping[str, str] | None = None,
    *,
    identities: Sequence[SigningIdentity] | None = None,
    wda_team_ids: Sequence[str] | None = None,
) -> str:
    """Return an explicit team ID or safely infer one unique local team."""
    environment = os.environ if source is None else source
    explicit = environment.get("IOS_MCP_TEAM_ID", "").strip()
    if explicit:
        return _validate_team_id(explicit)

    available = list(identities) if identities is not None else discover_signing_identities(environment)
    team_ids = sorted({identity.team_id for identity in available})
    if wda_team_ids is not None:
        existing_wda_teams = list(wda_team_ids)
    elif identities is not None:
        existing_wda_teams = []
    else:
        existing_wda_teams = discover_existing_wda_team_ids(environment)
    reusable_teams = sorted(set(team_ids).intersection(existing_wda_teams))
    if len(reusable_teams) == 1:
        return reusable_teams[0]
    if not team_ids:
        raise ValueError("未找到 Apple Development 签名身份，请先在 Xcode 配置开发证书")
    if len(team_ids) > 1:
        raise ValueError(
            f"检测到 {len(team_ids)} 个可用开发团队；为避免选错，请显式设置 IOS_MCP_TEAM_ID"
        )
    return team_ids[0]


def resolve_certificate_common_name(
    source: Mapping[str, str] | None = None,
    *,
    identities: Sequence[SigningIdentity] | None = None,
) -> str:
    """Return the local certificate name for the resolved team without logging it."""
    environment = os.environ if source is None else source
    if identities is None:
        available = discover_signing_identities(environment)
        wda_team_ids = discover_existing_wda_team_ids(environment)
    else:
        available = list(identities)
        wda_team_ids = []
    team_id = resolve_team_id(
        environment,
        identities=available,
        wda_team_ids=wda_team_ids,
    )
    matches = [identity.common_name for identity in available if identity.team_id == team_id]
    if not matches:
        raise ValueError("未找到 IOS_MCP_TEAM_ID 对应的 Apple Development 证书")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "field",
        nargs="?",
        choices=("team-id", "certificate-name"),
        default="team-id",
    )
    args = parser.parse_args()
    try:
        value = (
            resolve_team_id()
            if args.field == "team-id"
            else resolve_certificate_common_name()
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
