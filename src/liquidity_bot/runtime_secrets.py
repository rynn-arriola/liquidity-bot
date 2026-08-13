"""Credential handoff without copying or executing maker-lab code.

Environment variables take precedence. When they are absent, the loader reads
only four literal string assignments from the sibling maker-lab
``credentials.py`` file. The file is parsed as data with :mod:`ast`; it is
never imported or executed.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


KEY_ENV = "POLYMARKET_US_KEY_ID"
SECRET_ENV = "POLYMARKET_US_SECRET_KEY"
DISCORD_ENV = "DISCORD_WEBHOOK_URL"
DISCORD_ERRORS_ENV = "DISCORD_ERRORS_WEBHOOK_URL"
CREDENTIALS_FILE_ENV = "LIQUIDITY_BOT_SHARED_CREDENTIALS_FILE"

_FILE_KEYS = {
    "KEY_ID",
    "SECRET_KEY",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_ERRORS_WEBHOOK_URL",
}


class RuntimeSecretError(RuntimeError):
    """Required runtime secrets are missing or unsafe."""


@dataclass(frozen=True)
class RuntimeSecrets:
    # repr=False prevents accidental disclosure in logs and test failures.
    key_id: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    discord_webhook_url: str = field(default="", repr=False)
    discord_errors_webhook_url: str = field(default="", repr=False)
    source: str = "none"

    @property
    def polymarket_configured(self) -> bool:
        return bool(self.key_id and self.secret_key)

    @property
    def discord_configured(self) -> bool:
        return bool(self.discord_webhook_url)

    @property
    def discord_errors_configured(self) -> bool:
        return bool(self.discord_errors_webhook_url
                    or self.discord_webhook_url)


def default_shared_credentials_file() -> Path:
    """Return the sibling maker-lab secret file for local/server layouts."""

    project_root = Path(__file__).resolve().parents[2]
    return project_root.parent / "maker-lab" / "credentials.py"


def _literal_assignments(path: Path) -> dict[str, str]:
    try:
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise RuntimeSecretError(
            "shared credential file could not be parsed") from exc

    result: dict[str, str] = {}
    for node in tree.body:
        name: str | None = None
        value_node: ast.expr | None = None
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            name = node.targets[0].id
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value_node = node.value
        if name not in _FILE_KEYS or value_node is None:
            continue
        try:
            value = ast.literal_eval(value_node)
        except (ValueError, TypeError):
            raise RuntimeSecretError(
                f"shared credential {name} must be a literal string") from None
        if not isinstance(value, str):
            raise RuntimeSecretError(
                f"shared credential {name} must be a literal string")
        result[name] = value.strip()
    return result


def _validate_discord_url(value: str, field: str) -> None:
    if not value:
        return
    parts = urlsplit(value)
    if (parts.scheme != "https"
            or parts.hostname not in {"discord.com", "discordapp.com"}
            or not parts.path.startswith("/api/webhooks/")
            or parts.username or parts.password):
        raise RuntimeSecretError(f"{field} is not an approved Discord webhook URL")


def load_runtime_secrets(
    *,
    credentials_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    require_polymarket: bool = False,
) -> RuntimeSecrets:
    """Load environment overrides and then the shared maker-lab secret file."""

    environment = os.environ if environ is None else environ
    configured_path = str(environment.get(CREDENTIALS_FILE_ENV, "")).strip()
    path = Path(credentials_file or configured_path
                or default_shared_credentials_file()).expanduser().resolve()
    file_values = _literal_assignments(path) if path.is_file() else {}

    def value(env_name: str, file_name: str) -> str:
        override = str(environment.get(env_name, "")).strip()
        return override or file_values.get(file_name, "")

    key_id = value(KEY_ENV, "KEY_ID")
    secret_key = value(SECRET_ENV, "SECRET_KEY")
    discord = value(DISCORD_ENV, "DISCORD_WEBHOOK_URL")
    discord_errors = value(
        DISCORD_ERRORS_ENV, "DISCORD_ERRORS_WEBHOOK_URL")
    if bool(key_id) != bool(secret_key):
        raise RuntimeSecretError(
            "Polymarket key ID and secret key must be configured together")
    if require_polymarket and not (key_id and secret_key):
        raise RuntimeSecretError(
            "Polymarket credentials are not configured in the environment or "
            "shared maker-lab credential file")
    _validate_discord_url(discord, DISCORD_ENV)
    _validate_discord_url(discord_errors, DISCORD_ERRORS_ENV)
    source = "environment" if any(str(environment.get(name, "")).strip() for name in (
        KEY_ENV, SECRET_ENV, DISCORD_ENV, DISCORD_ERRORS_ENV)) else (
            "shared_file" if file_values else "none")
    return RuntimeSecrets(
        key_id=key_id,
        secret_key=secret_key,
        discord_webhook_url=discord,
        discord_errors_webhook_url=discord_errors,
        source=source,
    )
