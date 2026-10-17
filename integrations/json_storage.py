"""Атомарные JSON, проверенные копии и отменобезопасный ввод-вывод."""

from __future__ import annotations

import asyncio
import copy
import errno
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from integrations.diagnostics import log_error


class JsonStorageError(RuntimeError):
    """Файл нельзя безопасно прочитать или записать."""


_RECOVERY_EVENTS: list[dict[str, str]] = []
_EVENT_LOCK = threading.Lock()
# Fixed-size stripes avoid retaining one lock for every temporary path forever.
_FILE_LOCKS = tuple(threading.RLock() for _ in range(64))
BACKUP_LIMIT = 10


def _file_lock(path: Path):
    return _FILE_LOCKS[hash(str(path.resolve())) % len(_FILE_LOCKS)]


def _backup_dir(path: Path) -> Path:
    return path.parent / ".json-backups"


def _backup_paths(path: Path) -> list[Path]:
    return sorted(_backup_dir(path).glob(f"{path.name}.*.bak"), reverse=True)


def _fsync_directory(folder: Path) -> None:
    descriptor = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
                raise
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = (path.stat().st_mode & 0o777) if path.exists() else 0o600
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
            tmp_name = tmp.name
            os.fchmod(tmp.fileno(), mode)
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        _fsync_directory(path.parent)
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)


def pop_recovery_events() -> list[dict[str, str]]:
    with _EVENT_LOCK:
        events = list(_RECOVERY_EVENTS)
        _RECOVERY_EVENTS.clear()
        return events


def load_json(path: Path, default: Any) -> Any:
    path = Path(path)
    with _file_lock(path):
        try:
            original = path.read_bytes()
        except FileNotFoundError:
            return copy.deepcopy(default)
        except OSError as exc:
            raise JsonStorageError(f"Не удалось прочитать JSON: {path}") from exc
        try:
            return json.loads(original)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            for candidate in _backup_paths(path):
                try:
                    payload = candidate.read_bytes()
                    data = json.loads(payload)
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    continue
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                corrupt = _backup_dir(path) / f"{path.name}.{stamp}.corrupt"
                try:
                    _atomic_write(corrupt, original, mode=0o600)
                    _atomic_write(path, payload)
                except OSError as exc:
                    raise JsonStorageError(f"Не удалось восстановить JSON: {path}") from exc
                event = {"file": str(path), "backup": str(candidate), "corrupt": str(corrupt)}
                with _EVENT_LOCK:
                    _RECOVERY_EVENTS.append(event)
                print(f"JSON RECOVERED: {event}")
                return data
            raise JsonStorageError(f"Не удалось прочитать JSON, исправной копии нет: {path}") from error


def save_json(path: Path, data: Any, *, trailing_newline: bool = False) -> None:
    path = Path(path)
    try:
        payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
        raw = (payload + ("\n" if trailing_newline else "")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise JsonStorageError(f"Не удалось сериализовать JSON: {path}") from exc
    with _file_lock(path):
        try:
            try:
                previous = path.read_bytes()
            except FileNotFoundError:
                previous = None
            if previous is not None:
                # Never overwrite corruption or silently skip a failed backup.
                json.loads(previous)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                _atomic_write(_backup_dir(path) / f"{path.name}.{stamp}.bak", previous, mode=0o600)
            _atomic_write(path, raw)
            # Rotation needs filenames only; do not parse ten full copies on each save.
            for old in _backup_paths(path)[BACKUP_LIMIT:]:
                try:
                    old.unlink(missing_ok=True)
                except OSError as exc:
                    log_error("storage_backup_rotation_failed", exc)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise JsonStorageError(f"Не удалось безопасно записать JSON: {path}") from exc


async def run_storage_io(operation: Callable[..., Any], *args, **kwargs) -> Any:
    """Keep the caller's transaction lock until the thread really finishes.

    Cancelling asyncio.to_thread alone does not stop disk I/O. Shield and drain
    it before propagating cancellation, including repeated shutdown requests.
    """
    return await finish_storage_operation(asyncio.to_thread(operation, *args, **kwargs))


async def finish_storage_operation(operation: Awaitable[Any]) -> Any:
    """Drain the whole operation before propagating caller cancellation.

    Also supports several awaited storage steps, such as redeeming a code
    followed by granting VIP. This does not make separate files crash-atomic.
    """
    task = asyncio.ensure_future(operation)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except Exception as exc:
            if cancelled:
                log_error("cancelled_storage_io_failed", exc)
                raise asyncio.CancelledError() from None
            raise
        else:
            if cancelled:
                raise asyncio.CancelledError()
            return result
