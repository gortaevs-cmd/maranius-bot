"""Модуль для работы с кросс-сервисной базой пользователей платформы."""
import asyncio
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from integrations.json_storage import load_json, save_json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
PLATFORM_USERS_FILE = os.path.join(DATA_DIR, "platform_users.json")
USER_COURSES_FILE = os.path.join(DATA_DIR, "user_courses.json")

_platform_db_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_platform_users() -> Dict[str, Any]:
    """Загрузить platform_users.json."""
    return load_json(PLATFORM_USERS_FILE, {"users": {}, "by_email": {}, "by_telegram": {}})


def _save_platform_users(data: Dict[str, Any]) -> None:
    """Сохранить platform_users.json."""
    save_json(PLATFORM_USERS_FILE, data, trailing_newline=True)


def _load_user_courses() -> Dict[str, Any]:
    """Загрузить user_courses.json."""
    return load_json(USER_COURSES_FILE, {"enrollments": []})


def _save_user_courses(data: Dict[str, Any]) -> None:
    """Сохранить user_courses.json."""
    save_json(USER_COURSES_FILE, data, trailing_newline=True)


def _rebuild_indices(data: Dict[str, Any]) -> None:
    """Пересобрать индексы by_email, by_telegram."""
    by_email: Dict[str, str] = {}
    by_telegram: Dict[str, str] = {}
    for uid, user in data.get("users", {}).items():
        if user.get("email"):
            by_email[user["email"].lower().strip()] = uid
        if user.get("telegram_id") is not None:
            by_telegram[str(user["telegram_id"])] = uid
    data["by_email"] = by_email
    data["by_telegram"] = by_telegram


async def get_or_create_user(
    email: Optional[str] = None,
    telegram_id: Optional[int] = None,
    name: Optional[str] = None,
    phone: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Найти или создать пользователя. Возвращает запись пользователя или None."""
    async with _platform_db_lock:
        data = _load_platform_users()
        users = data.setdefault("users", {})
        by_email = data.setdefault("by_email", {})
        by_telegram = data.setdefault("by_telegram", {})

        now = _now_iso()
        user_id: Optional[str] = None

        # Поиск по имеющимся идентификаторам
        if email:
            user_id = by_email.get(email.lower().strip())
        if not user_id and telegram_id is not None:
            user_id = by_telegram.get(str(telegram_id))

        if user_id and user_id in users:
            user = users[user_id].copy()
            updated = False
            if email and not user.get("email"):
                user["email"] = email
                updated = True
            if telegram_id is not None and user.get("telegram_id") != telegram_id:
                user["telegram_id"] = telegram_id
                updated = True
            if name and not user.get("name"):
                user["name"] = name
                updated = True
            if phone is not None and user.get("phone") != phone:
                user["phone"] = phone
                updated = True
            if updated:
                user["updated_at"] = now
                sources = set(user.get("sources", []))
                if telegram_id is not None:
                    sources.add("telegram")
                user["sources"] = list(sources)
                users[user_id] = user
                _rebuild_indices(data)
                _save_platform_users(data)
            return user

        # Создание нового пользователя
        if not email and telegram_id is None:
            return None

        user_id = str(uuid.uuid4())
        sources: List[str] = []
        if telegram_id is not None:
            sources.append("telegram")

        user = {
            "id": user_id,
            "email": email or None,
            "telegram_id": telegram_id,
            "name": name or None,
            "phone": phone or None,
            "created_at": now,
            "updated_at": now,
            "sources": sources,
            "metadata": {},
        }
        users[user_id] = user
        _rebuild_indices(data)
        _save_platform_users(data)
        return user


async def find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Поиск пользователя по email."""
    async with _platform_db_lock:
        data = _load_platform_users()
        user_id = data.get("by_email", {}).get(email.lower().strip())
        if user_id:
            return data.get("users", {}).get(user_id, {}).copy()
    return None


async def find_user_by_telegram(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Поиск пользователя по Telegram ID."""
    async with _platform_db_lock:
        data = _load_platform_users()
        user_id = data.get("by_telegram", {}).get(str(telegram_id))
        if user_id:
            return data.get("users", {}).get(user_id, {}).copy()
    return None


async def link_telegram(user_id: str, telegram_id: int) -> bool:
    """Связать запись пользователя с Telegram."""
    async with _platform_db_lock:
        data = _load_platform_users()
        users = data.get("users", {})
        if user_id not in users:
            return False
        users[user_id]["telegram_id"] = telegram_id
        users[user_id]["updated_at"] = _now_iso()
        if "telegram" not in users[user_id].get("sources", []):
            users[user_id]["sources"] = users[user_id].get("sources", []) + ["telegram"]
        _rebuild_indices(data)
        _save_platform_users(data)
        return True


async def add_user_course(
    user_id: str,
    course_id: str,
    course_name: Optional[str] = None,
    status: str = "active",
    source: str = "api",
) -> None:
    """Добавить запись о курсе пользователя."""
    async with _platform_db_lock:
        data = _load_user_courses()
        enrollments = data.setdefault("enrollments", [])
        enrollments.append({
            "user_id": user_id,
            "course_id": course_id,
            "course_name": course_name or "",
            "enrolled_at": _now_iso(),
            "status": status,
            "source": source,
        })
        _save_user_courses(data)


async def get_user_courses(user_id: str) -> List[Dict[str, Any]]:
    """Получить курсы пользователя."""
    async with _platform_db_lock:
        data = _load_user_courses()
        enrollments = data.get("enrollments", [])
        return [e for e in enrollments if e.get("user_id") == user_id]
