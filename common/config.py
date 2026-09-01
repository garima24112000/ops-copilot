"""Environment loading. Never read .env directly — this loads it into os.environ once,
then everything else reads os.environ, per CLAUDE.md rule 8."""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv


@lru_cache(maxsize=1)
def _load() -> None:
    load_dotenv()


def env(key: str, default: str | None = None) -> str | None:
    _load()
    return os.environ.get(key, default)


def require_env(key: str) -> str:
    value = env(key)
    if not value:
        raise RuntimeError(f"required environment variable {key} is not set (check .env)")
    return value
