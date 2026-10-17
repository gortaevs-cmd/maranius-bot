"""Атомарные JSON, проверенные копии и отменобезопасный ввод-вывод."""

from __future__ import annotations

import asyncio
import copy
import errno
import hashlib
import inspect
import json
import logging
import os
import tempfile
import threading
from functools import wraps
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
_BASE = Path(__file__).resolve().parent.parent
_RUNTIME = Path(os.getenv("MARANIUS_RUNTIME_DIR") or _BASE)
TRANSACTION_FILE = _RUNTIME / "storage_transaction.json"
TRANSACTION_ROOTS = (_BASE, _RUNTIME)
_IO_LOCK = threading.RLock()
_LOCAL = threading.local()
_CLEANUP_PENDING = False


def _serialized(operation):
    @wraps(operation)
    def call(*args, **kwargs):
        with _IO_LOCK:
            if getattr(_LOCAL, "staged", None) is None:
                recover_transaction()
            return operation(*args, **kwargs)
    return call


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


@_serialized
def load_json(path: Path, default: Any) -> Any:
    path = Path(path)
    staged = getattr(_LOCAL, "staged", None)
    if staged is not None and str(path.resolve()) in staged:
        return copy.deepcopy(staged[str(path.resolve())]["data"])
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


@_serialized
def save_json(path: Path, data: Any, *, trailing_newline: bool = False) -> None:
    path = Path(path)
    staged = getattr(_LOCAL, "staged", None)
    if staged is not None:
        _validate_target(path)
        # Validate and detach now: later mutations must not change a staged write.
        payload = _encode(data, trailing_newline)
        staged[str(path.resolve())] = {
            "path": str(path.resolve()), "data": json.loads(payload),
            "trailing_newline": trailing_newline,
        }
        return
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
    return await finish_storage_operation(asyncio.to_thread(_serialized(operation), *args, **kwargs))


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


def _encode(data, trailing_newline=False) -> bytes:
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
        return (text + ("\n" if trailing_newline else "")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise JsonStorageError("Invalid transaction JSON") from exc


def _validate_target(path: Path) -> None:
    resolved = path.resolve()
    roots = [Path(root).resolve() for root in TRANSACTION_ROOTS]
    if (resolved == TRANSACTION_FILE.resolve() or resolved.suffix != ".json"
            or any(part in {".git", ".json-backups", ".runtime-backups"} for part in resolved.parts)
            or not any(resolved.is_relative_to(root) for root in roots)):
        raise JsonStorageError("Invalid transaction target")


def _clear_transaction() -> None:
    global _CLEANUP_PENDING
    _CLEANUP_PENDING = True
    TRANSACTION_FILE.unlink(missing_ok=True)
    _fsync_directory(TRANSACTION_FILE.parent)
    _CLEANUP_PENDING = False


def recover_transaction(*, report: bool = True) -> bool:
    """Replay a durable decision before any new storage operation; fail closed."""
    global _CLEANUP_PENDING
    with _IO_LOCK:
        if _CLEANUP_PENDING:
            try:
                _fsync_directory(TRANSACTION_FILE.parent)
            except OSError as exc:
                raise JsonStorageError("Cannot finish storage transaction cleanup") from exc
            _CLEANUP_PENDING = False
        try:
            raw = TRANSACTION_FILE.read_bytes()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise JsonStorageError("Cannot read storage transaction") from exc
        try:
            record = json.loads(raw)
            writes = record["writes"]
            valid = (type(record.get("version")) is int and record["version"] == 1
                     and isinstance(writes, list) and 1 <= len(writes) <= 16
                     and record["sha256"] == hashlib.sha256(_encode(writes)).hexdigest())
            if not valid:
                raise ValueError("invalid transaction record")
            targets = set()
            prepared = []
            for entry in writes:
                path = Path(entry["path"])
                _validate_target(path)
                if str(path.resolve()) in targets or type(entry["trailing_newline"]) is not bool:
                    raise ValueError("invalid transaction entry")
                targets.add(str(path.resolve()))
                prepared.append((path, _encode(entry["data"], entry["trailing_newline"])))
            for path, payload in prepared:
                _atomic_write(path, payload)
            _clear_transaction()
            if report:
                logging.getLogger(__name__).warning("storage_transaction_recovered files=%s", len(prepared))
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise JsonStorageError("Cannot recover storage transaction") from exc
        return True


def _call_sync(operation, *args, **kwargs):
    result = operation(*args, **kwargs)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError("Storage transaction callback must be synchronous")
    return result


def atomic_json(operation: Callable[..., Any], *args, **kwargs) -> Any:
    """Single-process, crash-recoverable transaction; callback must be synchronous.

    A failed callback/preflight writes no source data. Once the journal has been
    persisted the decision is committed: interrupted application is replayed,
    never rolled back. No readers/writers can pass an outstanding journal.
    """
    with _IO_LOCK:
        if getattr(_LOCAL, "staged", None) is not None:
            try:
                return _call_sync(operation, *args, **kwargs)
            except BaseException:
                _LOCAL.rollback_only = True
                raise
        recover_transaction()
        staged = {}
        _LOCAL.staged = staged
        _LOCAL.rollback_only = False
        try:
            result = _call_sync(operation, *args, **kwargs)
            if _LOCAL.rollback_only:
                raise JsonStorageError("Nested storage transaction was aborted")
        finally:
            _LOCAL.staged = None
        if not staged:
            return result
        if len(staged) > 16:
            raise JsonStorageError("Too many transaction targets")
        writes = list(staged.values())
        record = {"version": 1, "writes": writes,
                  "sha256": hashlib.sha256(_encode(writes)).hexdigest()}
        try:
            # Mandatory valid backups precede the commit decision. A failed
            # backup cannot turn a rejected operation into a committed one.
            for entry in writes:
                path = Path(entry["path"])
                try:
                    previous = path.read_bytes()
                except FileNotFoundError:
                    continue
                json.loads(previous)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                _atomic_write(_backup_dir(path) / f"{path.name}.{stamp}.bak", previous, mode=0o600)
            _atomic_write(TRANSACTION_FILE, _encode(record), mode=0o600)
            recover_transaction(report=False)
        except (OSError, ValueError, UnicodeError) as exc:
            raise JsonStorageError("Cannot commit storage transaction") from exc
        for entry in writes:
            for old in _backup_paths(Path(entry["path"]))[BACKUP_LIMIT:]:
                try:
                    old.unlink(missing_ok=True)
                except OSError as exc:
                    log_error("storage_backup_rotation_failed", exc)
        return result
