"""Безопасное чтение и атомарная запись рабочих JSON-файлов."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonStorageError(RuntimeError):
    """Файл есть, но его нельзя безопасно прочитать или записать."""


_RECOVERY_EVENTS: list[dict[str, str]] = []
BACKUP_LIMIT = 10


def _backup_dir(path: Path) -> Path:
    return path.parent / ".json-backups"


def _valid_backups(path: Path) -> list[Path]:
    folder = _backup_dir(path)
    out = []
    for candidate in sorted(folder.glob(f"{path.name}.*.bak"), reverse=True):
        try:
            json.loads(candidate.read_text(encoding="utf-8"))
            out.append(candidate)
        except (json.JSONDecodeError, OSError):
            continue
    return out


def pop_recovery_events() -> list[dict[str, str]]:
    events = list(_RECOVERY_EVENTS)
    _RECOVERY_EVENTS.clear()
    return events


def load_json(path: Path, default: Any) -> Any:
    path = Path(path)
    if not path.is_file():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        backups = _valid_backups(path)
        if not backups:
            raise JsonStorageError(f"Не удалось прочитать JSON, исправной копии нет: {path}") from exc
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        corrupt = _backup_dir(path) / f"{path.name}.{stamp}.corrupt"
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, corrupt)
        shutil.copy2(backups[0], path)
        data = json.loads(path.read_text(encoding="utf-8"))
        event = {"file": str(path), "backup": str(backups[0]), "corrupt": str(corrupt)}
        _RECOVERY_EVENTS.append(event)
        print(f"JSON RECOVERED: {event}")
        return data


def save_json(path: Path, data: Any, *, trailing_newline: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as exc:
        raise JsonStorageError(f"Не удалось сериализовать JSON: {path}") from exc
    if trailing_newline:
        payload += "\n"

    tmp_name = ""
    try:
        if path.is_file():
            try:
                json.loads(path.read_text(encoding="utf-8"))
                folder = _backup_dir(path)
                folder.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                shutil.copy2(path, folder / f"{path.name}.{stamp}.bak")
                for old in _valid_backups(path)[BACKUP_LIMIT:]:
                    old.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError):
                pass
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_name = tmp.name
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.chmod(tmp_name, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(tmp_name, path)
    except OSError as exc:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise JsonStorageError(f"Не удалось атомарно записать JSON: {path}") from exc
