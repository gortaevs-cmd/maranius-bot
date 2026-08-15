#!/usr/bin/env python3
"""Прогреть Telegram file_id для VIP PDF (один раз после деплоя).

Отправляет обе книги в чат SEED_ADMIN и сохраняет file_id в data/vip/pdf_file_ids.json.
Запуск на сервере:
  cd /opt/apps/maranius && .venv/bin/python3 scripts/warm_pdf_file_ids.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from telegram import Bot, InputFile

from integrations import vip_content

load_dotenv(ROOT / ".env", override=True)

SEED_ADMIN_ID = int(os.environ.get("WARM_PDF_CHAT_ID", "186758977"))


def _upload_via_curl(
    token: str,
    chat_id: int,
    path: Path,
    label: str,
    proxy: str,
) -> str | None:
    """Fallback: curl стабильнее для ~40 MB через SOCKS (PTB иногда WriteTimeout)."""
    cmd = [
        "curl",
        "--max-time",
        "900",
        "-sS",
        "-F",
        f"chat_id={chat_id}",
        "-F",
        "protect_content=true",
        "-F",
        f"caption=Прогрев file_id — книга ({label})",
        "-F",
        f"document=@{path}",
        f"https://api.telegram.org/bot{token}/sendDocument",
    ]
    if proxy:
        cmd[1:1] = ["--socks5-hostname", proxy.replace("socks5h://", "").replace("socks5://", "")]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        data = json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        print(f"  curl fallback failed: {exc!r}")
        return None
    if not data.get("ok"):
        print(f"  curl fallback API error: {data}")
        return None
    doc = (data.get("result") or {}).get("document") or {}
    return doc.get("file_id")


async def main() -> int:
    from bot import resolve_bot_token

    token, profile = resolve_bot_token()
    if not token:
        print("FATAL: нет токена (resolve_bot_token)")
        return 1
    proxy = os.environ.get("TELEGRAM_PROXY_URL", "socks5h://127.0.0.1:1080")
    from telegram.request import HTTPXRequest

    request = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=180.0,
        write_timeout=600.0,
        media_write_timeout=600.0,
        proxy=proxy or None,
    )
    bot = Bot(token=token, request=request)
    me = await bot.get_me()
    print(f"Bot ({profile}): @{me.username} id={me.id} → chat {SEED_ADMIN_ID}")

    for variant, label in (("dark", "тёмная"), ("light", "светлая")):
        cached = vip_content.get_pdf_file_id(variant)
        if cached:
            print(f"  {variant}: уже есть file_id")
            continue
        path = vip_content.pdf_local_path(variant)
        if not path.is_file():
            print(f"  {variant}: FATAL нет файла {path}")
            return 1
        mb = path.stat().st_size // (1024 * 1024)
        print(f"  {variant}: upload {path.name} ({mb} MB)…")
        fid: str | None = None
        try:
            with path.open("rb") as fh:
                msg = await bot.send_document(
                    chat_id=SEED_ADMIN_ID,
                    document=InputFile(fh, filename=path.name),
                    caption=f"Прогрев file_id — книга ({label})",
                    protect_content=True,
                )
            fid = msg.document.file_id if msg.document else None
        except Exception as exc:
            print(f"  {variant}: PTB upload failed ({exc!r}), curl fallback…")
            fid = _upload_via_curl(token, SEED_ADMIN_ID, path, label, proxy)
        if not fid:
            print(f"  {variant}: FATAL нет file_id в ответе")
            return 1
        vip_content.save_pdf_file_id(variant, fid)
        print(f"  {variant}: OK file_id={fid[:28]}…")

    print("WARM PDF OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
