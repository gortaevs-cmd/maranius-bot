"""Модуль для работы с Zenclass API."""
import os
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

# Настройки Zenclass API (docs: https://docs.zenclass.ru, spec: openapi-spec.json)
ZENCLASS_API_TOKEN = os.getenv("ZENCLASS_API_TOKEN")
ZENCLASS_API_BASE_URL = os.getenv("ZENCLASS_API_BASE_URL", "https://api.zenclass.net")
ZENCLASS_DEBUG = os.getenv("ZENCLASS_DEBUG", "").lower() in ("1", "true", "yes")


async def zenclass_api_request(
    method: str, endpoint: str, data: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Выполнить запрос к Zenclass API.
    
    Args:
        method: HTTP метод (GET, POST, DELETE)
        endpoint: Endpoint API (например, "/school", "/students")
        data: Данные для POST запросов (опционально)
    
    Returns:
        JSON ответ от API или None в случае ошибки
    """
    if not ZENCLASS_API_TOKEN:
        return None

    url = f"{ZENCLASS_API_BASE_URL}/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {ZENCLASS_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers)
            elif method.upper() == "POST":
                response = await client.post(url, headers=headers, json=data)
            elif method.upper() == "DELETE":
                response = await client.delete(url, headers=headers)
            else:
                return None

            response.raise_for_status()
            return response.json()
    except Exception as e:
        if ZENCLASS_DEBUG:
            print(f"Ошибка Zenclass API {method} {endpoint}: {e!r}")
        return None


async def zenclass_create_student(
    email: str,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Создать профиль студента (POST /api/v1/student). Возвращает ответ с user_id."""
    data_inner: Dict[str, Any] = {"email": email}
    if first_name:
        data_inner["first_name"] = first_name
    if last_name:
        data_inner["second_name"] = last_name
    for k, v in kwargs.items():
        if v is not None and k in (
            "country", "city", "phone", "bio", "gender", "birthday",
            "timezone", "send_email", "marketing_data",
        ):
            data_inner[k] = v
    return await zenclass_api_request("POST", "/api/v1/student", {"data": data_inner})


async def zenclass_get_school_info() -> Optional[Dict[str, Any]]:
    """Получить информацию о школе (GET /api/v1/school)."""
    return await zenclass_api_request("GET", "/api/v1/school")


async def zenclass_get_students() -> Optional[Dict[str, Any]]:
    """Получить список студентов. В OpenAPI spec эндпоинта нет — может не работать."""
    return await zenclass_api_request("GET", "/api/v1/students")


async def zenclass_get_student(student_id: str) -> Optional[Dict[str, Any]]:
    """Получить информацию о студенте по ID или email."""
    return await zenclass_api_request("GET", f"/api/v1/student/{student_id}")


async def zenclass_get_courses() -> Optional[Dict[str, Any]]:
    """Получить список курсов. В OpenAPI spec эндпоинта нет — может не работать."""
    return await zenclass_api_request("GET", "/api/v1/courses")


async def zenclass_get_student_courses(student_id: str) -> Optional[Dict[str, Any]]:
    """Получить курсы студента."""
    return await zenclass_api_request("GET", f"/api/v1/student/{student_id}/courses")


async def zenclass_remove_student_from_course(
    student_id: str, course_id: str
) -> Optional[Dict[str, Any]]:
    """Исключить студента из курса (POST /api/v1/student/course/expel)."""
    body: Dict[str, Any] = {"course_id": course_id}
    body["email" if "@" in student_id else "id"] = student_id
    return await zenclass_api_request(
        "POST", "/api/v1/student/course/expel", body
    )
