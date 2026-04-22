"""Configuration loader – reads settings from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


ENV_BASE_URL = "CONFLUENCE_BASE_URL"
ENV_EMAIL = "CONFLUENCE_EMAIL"
ENV_API_TOKEN = "CONFLUENCE_API_TOKEN"
ENV_TIMEOUT = "CONFLUENCE_TIMEOUT"
ENV_DEFAULT_DIR = "CONFLUENCE_MARKDOWN_DIR"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the Confluence client.

    All values are read from environment variables; see :func:`load_settings`.
    """

    base_url: str
    email: str
    api_token: str
    timeout: float = 30.0
    markdown_dir: Optional[str] = None

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                (ENV_BASE_URL, self.base_url),
                (ENV_EMAIL, self.email),
                (ENV_API_TOKEN, self.api_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Missing Confluence credentials. Please set the following "
                "environment variables: " + ", ".join(missing)
            )


def load_settings() -> Settings:
    """Load :class:`Settings` from the process environment."""

    try:
        timeout = float(os.getenv(ENV_TIMEOUT, "30").strip() or "30")
    except ValueError:
        timeout = 30.0

    settings = Settings(
        base_url=os.getenv(ENV_BASE_URL, "").strip().rstrip("/"),
        email=os.getenv(ENV_EMAIL, "").strip(),
        api_token=os.getenv(ENV_API_TOKEN, "").strip(),
        timeout=timeout,
        markdown_dir=(os.getenv(ENV_DEFAULT_DIR, "").strip() or None),
    )
    settings.validate()
    return settings
