"""Remove credential-like variables before starting diagnostic subprocesses."""

from __future__ import annotations

import os
from collections.abc import Mapping


_SENSITIVE_NAME_PARTS = (
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTHORIZATION",
    "COOKIE",
)


def is_sensitive_env_name(name: str) -> bool:
    """Return whether an environment variable may carry reusable credentials."""
    upper_name = name.upper()
    return any(part in upper_name for part in _SENSITIVE_NAME_PARTS)


def sanitized_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy an environment while removing credential-like entries."""
    env = dict(os.environ if source is None else source)
    return {name: value for name, value in env.items() if not is_sensitive_env_name(name)}
