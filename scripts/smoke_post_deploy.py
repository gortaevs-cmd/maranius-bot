#!/usr/bin/env python3
"""Smoke после деплоя: синтаксис уже проверен; здесь — getMe через Telegram API."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)


def _env_strip(name: str) -> str | None:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip().strip("'\"")
    return v if v else None


def _resolve_token() -> str | None:
    raw = (_env_strip("BOT_PROFILE") or "prod").lower()
    if raw in ("test", "dev", "local"):
        return _env_strip("BOT_TOKEN_TEST")
    return _env_strip("BOT_TOKEN_PROD") or _env_strip("BOT_TOKEN")


def main() -> int:
    token = _resolve_token()
    if not token:
        print("SMOKE FAIL: нет токена в .env (BOT_TOKEN_TEST / BOT_TOKEN_PROD)", file=sys.stderr)
        return 1

    proxy = _env_strip("TELEGRAM_PROXY_URL")
    kwargs: dict = {"timeout": 30.0}
    if proxy:
        kwargs["proxy"] = proxy

    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{token}/getMe",
            **kwargs,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        print(f"SMOKE FAIL: getMe — {exc!r}", file=sys.stderr)
        return 1

    if not data.get("ok"):
        print(f"SMOKE FAIL: getMe ответ — {data!r}", file=sys.stderr)
        return 1

    user = data.get("result") or {}
    username = user.get("username") or "?"
    print(f"SMOKE OK: @{username} id={user.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
