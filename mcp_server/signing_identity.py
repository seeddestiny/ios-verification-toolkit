#!/usr/bin/env python3
"""Resolve Apple Development signing metadata without storing personal values."""
from __future__ import annotations

import argparse
import json
import plistlib
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

try:
    from .env_sanitizer import sanitized_env
    from .local_config import load_local_config
    from .runtime_paths import ensure_runtime_paths, runtime_paths
except ImportError:
    from env_sanitizer import sanitized_env
    from local_config import load_local_config
    from runtime_paths import ensure_runtime_paths, runtime_paths


_TEAM_ID_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
_TEAM_STATE_FILE = "signing-team.json"
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
        raise ValueError("签名 Team ID 必须是 10 位大写字母或数字")
    return value


def load_cached_team_id(source: Mapping[str, str] | None = None) -> str:
    """Load the last successful team from private, Git-ignored runtime state."""
    path = runtime_paths(source).state / _TEAM_STATE_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return ""
    value = str(payload.get("team_id") or "").strip()
    return value if _TEAM_ID_PATTERN.fullmatch(value) else ""


def remember_team_id(
    value: str,
    source: Mapping[str, str] | None = None,
) -> None:
    """Persist a successful team locally with user-only permissions."""
    team_id = _validate_team_id(value)
    state = ensure_runtime_paths(source).state
    path = state / _TEAM_STATE_FILE
    temporary = state / f".{_TEAM_STATE_FILE}.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"team_id": team_id}, stream, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def candidate_team_ids(
    source: Mapping[str, str] | None = None,
    *,
    identities: Sequence[SigningIdentity] | None = None,
    wda_team_ids: Sequence[str] | None = None,
    cached_team_id: str | None = None,
) -> list[str]:
    """Return deterministic candidates without exposing certificate names."""
    environment = os.environ if source is None else source
    explicit = environment.get("IOS_MCP_TEAM_ID", "").strip()
    if explicit:
        return [_validate_team_id(explicit)]
    configured = ""
    if source is None:
        configured = str(load_local_config().get("signing_team_id") or "").strip()
    if configured:
        return [_validate_team_id(configured)]

    available = list(identities) if identities is not None else discover_signing_identities(environment)
    # 保留 Keychain 的本机发现顺序；不要用 Team ID 字典序制造伪优先级。
    team_ids = list(dict.fromkeys(identity.team_id for identity in available))
    if not team_ids:
        raise ValueError("未找到 Apple Development 签名身份，请先在 Xcode 配置开发证书")

    if cached_team_id is None:
        cached = "" if identities is not None else load_cached_team_id(environment)
    else:
        cached = cached_team_id
    if wda_team_ids is not None:
        existing_wda_teams = list(wda_team_ids)
    elif identities is not None:
        existing_wda_teams = []
    else:
        existing_wda_teams = discover_existing_wda_team_ids(environment)

    ordered: list[str] = []
    for candidate in (cached, *sorted(set(existing_wda_teams)), *team_ids):
        if candidate in team_ids and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def resolve_team_id(
    source: Mapping[str, str] | None = None,
    *,
    identities: Sequence[SigningIdentity] | None = None,
    wda_team_ids: Sequence[str] | None = None,
    cached_team_id: str | None = None,
) -> str:
    """Return the highest-priority local team; run_all validates it by building."""
    return candidate_team_ids(
        source,
        identities=identities,
        wda_team_ids=wda_team_ids,
        cached_team_id=cached_team_id,
    )[0]


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
        raise ValueError("未找到本机所选 Team ID 对应的 Apple Development 证书")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "field",
        nargs="?",
        choices=("team-id", "team-candidates", "certificate-name", "remember-team"),
        default="team-id",
    )
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    try:
        if args.field == "team-id":
            value = resolve_team_id()
        elif args.field == "team-candidates":
            value = "\n".join(candidate_team_ids())
        elif args.field == "certificate-name":
            value = resolve_certificate_common_name()
        else:
            if not args.value:
                raise ValueError("remember-team 需要 Team ID")
            remember_team_id(args.value)
            value = ""
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if value:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
