"""Durable owner notifications for the single polling process.

Disk transactions run outside the event loop. Telegram sends never hold a disk
lock. Recipient progress and a short lease survive restarts; Telegram itself
cannot guarantee exactly-once delivery after a lost response.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

from integrations.diagnostics import log_error
from integrations.json_storage import JsonStorageError, load_json, run_storage_io, save_json

_BASE = Path(__file__).resolve().parent.parent
OUTBOX_FILE = Path(os.getenv("MARANIUS_RUNTIME_DIR") or _BASE) / "notification_outbox.json"
MAX_PENDING = 1000
MAX_HISTORY = 2000
MAX_ATTEMPTS = 20
RETENTION_SECONDS = 7 * 86400
LEASE_SECONDS = 120
POLL_SECONDS = 5
_BATCH_SIZE = 10
_DISK_LOCK = threading.RLock()
_WORKER_LOCK = asyncio.Lock()


def _now() -> float:
    return time.time()


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _load() -> dict:
    store = load_json(OUTBOX_FILE, {"version": 1, "items": [], "next_allowed_at": 0})
    valid = isinstance(store, dict) and type(store.get("version")) is int and store['version'] == 1 and isinstance(store.get("items"), list)
    if not valid or not _number(store.get("next_allowed_at")):
        raise JsonStorageError("Invalid notification outbox schema")
    ids, keys = set(), set()
    for item in store["items"]:
        valid = isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("key"), str)
        if not valid:
            raise JsonStorageError("Invalid notification outbox item")
        if item['id'] in ids or item['key'] in keys:
            raise JsonStorageError("Duplicate notification outbox identity")
        ids.add(item['id'])
        keys.add(item['key'])
        valid = (
            item.get("state") in ("pending", "sent", "failed", "expired")
            and _number(item.get("created_at")) and isinstance(item.get("payload_hash"), str)
            and isinstance(item.get("recipients"), list) and bool(item["recipients"])
            and (item.get("inbox_id") is None or isinstance(item["inbox_id"], str))
            and _number(item.get("ack_next_at", 0))
            and (item.get("parse_mode") is None or isinstance(item["parse_mode"], str))
            and (item["state"] == "pending" or _number(item.get("completed_at")))
        )
        if not valid or (item["state"] == "pending" and not isinstance(item.get("text"), str)):
            raise JsonStorageError("Invalid notification outbox payload")
        target_ids = set()
        for recipient in item["recipients"]:
            if not (isinstance(recipient, dict) and type(recipient.get("id")) is int and recipient['id'] > 0
                    and recipient.get("state") in ("pending", "sent", "failed")
                    and type(recipient.get("attempts")) is int and recipient['attempts'] >= 0
                    and _number(recipient.get("next_at"))):
                raise JsonStorageError("Invalid notification outbox recipient")
            if recipient['id'] in target_ids:
                raise JsonStorageError("Duplicate notification recipient")
            target_ids.add(recipient['id'])
    return store


def _save(store: dict) -> None:
    save_json(OUTBOX_FILE, store, trailing_newline=True)


def _transaction(operation: Callable[[dict], Any]) -> Any:
    with _DISK_LOCK:
        return operation(_load())


def _finish_item(item: dict, state: str, now: float) -> None:
    item.update(state=state, completed_at=now, text=None)


def _prune(store: dict, now: float) -> bool:
    changed = False
    for item in store["items"]:
        if item["state"] == "pending" and not all(r["state"] == "sent" for r in item["recipients"]):
            if now - item["created_at"] >= RETENTION_SECONDS:
                _finish_item(item, "expired", now)
                changed = True
    history = [i for i in store["items"] if i["state"] != "pending" and now-i.get("completed_at", i["created_at"]) < RETENTION_SECONDS]
    keep_ids = {i["id"] for i in history[-MAX_HISTORY:]}
    kept = [i for i in store["items"] if i["state"] == "pending" or i["id"] in keep_ids]
    if len(kept) != len(store["items"]):
        changed = True
    store["items"] = kept
    return changed


async def enqueue(recipients, text: str, *, parse_mode: str = "HTML",
                  key: Optional[str] = None, inbox_id: Optional[str] = None) -> bool:
    """Persist before sending. True only for a previously completed same event."""
    return await run_storage_io(_enqueue_sync, recipients, text, parse_mode=parse_mode, key=key, inbox_id=inbox_id)


def _enqueue_sync(recipients, text: str, *, parse_mode: str = "HTML",
                  key: Optional[str] = None, inbox_id: Optional[str] = None) -> bool:
    """Persist before sending. True only for a previously completed same event."""
    targets = sorted({uid for uid in recipients if type(uid) is int and uid > 0})
    if not targets:
        return False
    if not isinstance(text, str) or not text or len(text.encode("utf-16-le")) // 2 > 4096:
        raise ValueError("Notification text must fit one message")
    if parse_mode is not None and not isinstance(parse_mode, str):
        raise ValueError("Invalid notification parse mode")
    if inbox_id is not None and (not isinstance(inbox_id, str) or not 1 <= len(inbox_id) <= 200):
        raise ValueError("Invalid notification source ID")
    event_key = uuid.uuid4().hex if key is None else key
    if not isinstance(event_key, str) or not event_key or len(event_key) > 200:
        raise ValueError("Invalid notification key")
    payload_hash = hashlib.sha256(json.dumps([targets, text, parse_mode, inbox_id], ensure_ascii=False).encode()).hexdigest()
    now = _now()

    def add(store):
        changed = _prune(store, now)
        for item in store["items"]:
            if item["key"] == event_key:
                if item["payload_hash"] != payload_hash:
                    raise JsonStorageError("Notification key reused with another payload")
                if changed:
                    _save(store)
                return item["state"] == "sent"
        if sum(i["state"] == "pending" for i in store["items"]) >= MAX_PENDING:
            raise JsonStorageError("Notification outbox is full")
        store["items"].append({
            "id": uuid.uuid4().hex, "key": event_key, "payload_hash": payload_hash,
            "created_at": now, "state": "pending", "text": text, "parse_mode": parse_mode,
            "inbox_id": inbox_id,
            "recipients": [{"id": uid, "state": "pending", "attempts": 0, "next_at": now} for uid in targets],
        })
        _save(store)
        return False
    return _transaction(add)


def _claim(store: dict):
    now = _now()
    changed = _prune(store, now)
    for item in store["items"]:
        if item["state"] != "pending":
            continue
        if all(r["state"] == "sent" for r in item["recipients"]):
            if item.get("ack_next_at", 0) > now:
                continue
            item['ack_next_at'] = now + LEASE_SECONDS
            _save(store)
            return {"kind": "ack", "id": item["id"], "inbox_id": item["inbox_id"]}
        for recipient in item["recipients"]:
            if recipient["state"] == "pending" and recipient["attempts"] >= MAX_ATTEMPTS and recipient["next_at"] <= now:
                recipient["state"] = "failed"
                changed = True
        if all(r["state"] != "pending" for r in item["recipients"]):
            _finish_item(item, "failed", now)
            changed = True
            continue
        if store["next_allowed_at"] > now:
            continue
        for recipient in item["recipients"]:
            if recipient["state"] != "pending" or recipient["next_at"] > now:
                continue
            recipient["attempts"] += 1
            recipient["next_at"] = now + LEASE_SECONDS
            _save(store)
            return {"kind": "send", "id": item["id"], "recipient": recipient["id"],
                    "text": item["text"], "parse_mode": item["parse_mode"], "attempt": recipient["attempts"]}
    if changed:
        _save(store)
    return None


def _record(store: dict, action: dict, state: str, *, delay: float = 0, error_type=None, pause=False) -> None:
    now = _now()
    for item in store["items"]:
        if item["id"] != action["id"]:
            continue
        if action["kind"] == "ack":
            if state == "sent":
                _finish_item(item, "sent", now)
            else:
                item['ack_next_at'] = now + delay
        else:
            recipient = next(r for r in item["recipients"] if r['id'] == action['recipient'])
            recipient.update(state=state, next_at=now+delay, error_type=error_type)
            if pause:
                store["next_allowed_at"] = max(store["next_allowed_at"], now+delay)
        _save(store)
        return
    raise JsonStorageError("Notification disappeared during delivery")


async def deliver_due(bot, *, limit: int = _BATCH_SIZE, allowed_recipients=None) -> int:
    """One bounded pass. Other callers never send the same item concurrently."""
    if _WORKER_LOCK.locked():
        return 0
    handled = 0
    async with _WORKER_LOCK:
        for _ in range(limit):
            action = await run_storage_io(_transaction, _claim)
            if action is None:
                break
            if action["kind"] == "ack":
                try:
                    if action["inbox_id"]:
                        from integrations import inbox
                        await inbox.mark_notified(action["inbox_id"])
                except Exception as exc:
                    log_error("outbox_source_ack_failed", exc)
                    await run_storage_io(_transaction, lambda store: _record(store, action, "pending", delay=30))
                else:
                    await run_storage_io(_transaction, lambda store: _record(store, action, "sent"))
                continue
            state, delay, error_type, pause = "sent", 0, None, False
            if allowed_recipients is not None and action['recipient'] not in allowed_recipients:
                await run_storage_io(_transaction, lambda store: _record(
                    store, action, "failed", error_type="RecipientRemoved",
                ))
                handled += 1
                continue
            try:
                await bot.send_message(
                    chat_id=action["recipient"], text=action["text"], parse_mode=action["parse_mode"],
                    connect_timeout=5, read_timeout=10, write_timeout=10, pool_timeout=5,
                )
            except (BadRequest, Forbidden) as exc:
                state, error_type = "failed", type(exc).__name__
                log_error("outbox_delivery_rejected", exc, attempt=action["attempt"])
            except RetryAfter as exc:
                retry = exc.retry_after
                delay = max(1, retry.total_seconds() if hasattr(retry, "total_seconds") else float(retry))
                state, error_type, pause = "pending", type(exc).__name__, True
                log_error("outbox_rate_limited", exc, attempt=action["attempt"])
            except NetworkError as exc:
                state, error_type = "pending", type(exc).__name__
                delay = min(3600, 30 * 2 ** min(action["attempt"] - 1, 7))
                log_error("outbox_network_error", exc, attempt=action["attempt"])
            except Exception as exc:
                state, error_type = "failed", type(exc).__name__
                log_error("outbox_delivery_failed", exc, attempt=action["attempt"])
            await run_storage_io(_transaction, lambda store: _record(
                store, action, state, delay=delay, error_type=error_type, pause=pause,
            ))
            handled += 1
    return handled


async def stats() -> dict[str, int]:
    def count(store):
        if _prune(store, _now()):
            _save(store)
        return {state: sum(i['state'] == state for i in store['items']) for state in ('pending','sent','failed','expired')}
    return await run_storage_io(_transaction, count)


async def worker_loop(bot, seed_admin_ids) -> None:
    allowed = frozenset(uid for uid in seed_admin_ids if type(uid) is int and uid > 0)
    while True:
        try:
            await deliver_due(bot, allowed_recipients=allowed)
        except Exception as exc:
            # A broken queue must not start a recursive alert-enqueue loop.
            log_error("outbox_worker_failed", exc)
        await asyncio.sleep(POLL_SECONDS)
