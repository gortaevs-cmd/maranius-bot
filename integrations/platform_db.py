"""Модуль для работы с кросс-сервисной базой пользователей платформы."""
import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
PLATFORM_USERS_FILE = os.path.join(DATA_DIR, "platform_users.json")
USER_COURSES_FILE = os.path.join(DATA_DIR, "user_courses.json")

_platform_db_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_platform_users() -> Dict[str, Any]:
    """Загрузить platform_users.json."""
    if not os.path.exists(PLATFORM_USERS_FILE):
        return {"users": {}, "by_email": {}, "by_telegram": {}, "by_zenclass": {}}
    try:
        with open(PLATFORM_USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"users": {}, "by_email": {}, "by_telegram": {}, "by_zenclass": {}}


def _save_platform_users(data: Dict[str, Any]) -> None:
    """Сохранить platform_users.json."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PLATFORM_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_user_courses() -> Dict[str, Any]:
    """Загрузить user_courses.json."""
    if not os.path.exists(USER_COURSES_FILE):
        return {"enrollments": []}
    try:
        with open(USER_COURSES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"enrollments": []}


def _save_user_courses(data: Dict[str, Any]) -> None:
    """Сохранить user_courses.json."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(USER_COURSES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _rebuild_indices(data: Dict[str, Any]) -> None:
    """Пересобрать индексы by_email, by_telegram, by_zenclass."""
    by_email: Dict[str, str] = {}
    by_telegram: Dict[str, str] = {}
    by_zenclass: Dict[str, str] = {}
    for uid, user in data.get("users", {}).items():
        if user.get("email"):
            by_email[user["email"].lower().strip()] = uid
        if user.get("telegram_id") is not None:
            by_telegram[str(user["telegram_id"])] = uid
        if user.get("zenclass_user_id"):
            by_zenclass[user["zenclass_user_id"]] = uid
    data["by_email"] = by_email
    data["by_telegram"] = by_telegram
    data["by_zenclass"] = by_zenclass


async def get_or_create_user(
    email: Optional[str] = None,
    telegram_id: Optional[int] = None,
    zenclass_user_id: Optional[str] = None,
    name: Optional[str] = None,
    phone: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Найти или создать пользователя. Возвращает запись пользователя или None."""
    async with _platform_db_lock:
        data = _load_platform_users()
        users = data.setdefault("users", {})
        by_email = data.setdefault("by_email", {})
        by_telegram = data.setdefault("by_telegram", {})
        by_zenclass = data.setdefault("by_zenclass", {})

        now = _now_iso()
        user_id: Optional[str] = None

        # Поиск по имеющимся идентификаторам
        if email:
            user_id = by_email.get(email.lower().strip())
        if not user_id and telegram_id is not None:
            user_id = by_telegram.get(str(telegram_id))
        if not user_id and zenclass_user_id:
            user_id = by_zenclass.get(zenclass_user_id)

        if user_id and user_id in users:
            user = users[user_id].copy()
            updated = False
            if email and not user.get("email"):
                user["email"] = email
                updated = True
            if telegram_id is not None and user.get("telegram_id") != telegram_id:
                user["telegram_id"] = telegram_id
                updated = True
            if zenclass_user_id and not user.get("zenclass_user_id"):
                user["zenclass_user_id"] = zenclass_user_id
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
                if zenclass_user_id:
                    sources.add("zenclass")
                user["sources"] = list(sources)
                users[user_id] = user
                _rebuild_indices(data)
                _save_platform_users(data)
            return user

        # Создание нового пользователя
        if not email and telegram_id is None and not zenclass_user_id:
            return None

        user_id = str(uuid.uuid4())
        sources: List[str] = []
        if telegram_id is not None:
            sources.append("telegram")
        if zenclass_user_id:
            sources.append("zenclass")

        user = {
            "id": user_id,
            "email": email or None,
            "telegram_id": telegram_id,
            "zenclass_user_id": zenclass_user_id or None,
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


async def find_user_by_zenclass(zenclass_user_id: str) -> Optional[Dict[str, Any]]:
    """Поиск пользователя по Zenclass user_id."""
    async with _platform_db_lock:
        data = _load_platform_users()
        user_id = data.get("by_zenclass", {}).get(zenclass_user_id)
        if user_id:
            return data.get("users", {}).get(user_id, {}).copy()
    return None


async def link_zenclass(user_id: str, zenclass_user_id: str) -> bool:
    """Связать запись пользователя с Zenclass."""
    async with _platform_db_lock:
        data = _load_platform_users()
        users = data.get("users", {})
        if user_id not in users:
            return False
        users[user_id]["zenclass_user_id"] = zenclass_user_id
        users[user_id]["updated_at"] = _now_iso()
        if "zenclass" not in users[user_id].get("sources", []):
            users[user_id]["sources"] = users[user_id].get("sources", []) + ["zenclass"]
        _rebuild_indices(data)
        _save_platform_users(data)
        return True


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
    zenclass_course_id: str,
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
            "zenclass_course_id": zenclass_course_id,
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
