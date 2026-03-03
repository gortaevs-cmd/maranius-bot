"""Обработчики команд Telegram для интеграции с Zenclass API."""
import json
import re
from typing import TYPE_CHECKING

from telegram import ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes

from integrations.zenclass_api import (
    ZENCLASS_API_TOKEN,
    zenclass_get_school_info,
    zenclass_get_students,
    zenclass_get_courses,
    zenclass_create_student,
)
from integrations import platform_db

if TYPE_CHECKING:
    from telegram import Update


async def zenclass_test(update: "Update", context: ContextTypes.DEFAULT_TYPE) -> None:
    """Тестирование подключения к Zenclass API."""
    if not ZENCLASS_API_TOKEN:
        await update.message.reply_text(
            "❌ Zenclass API токен не настроен!\n\n"
            "Добавь в .env файл:\n"
            "ZENCLASS_API_TOKEN=твой_токен\n"
            "ZENCLASS_API_BASE_URL=https://api.zenclass.net (опционально)"
        )
        return

    await update.message.reply_text("🔄 Проверяю подключение к Zenclass API...")

    school_info = await zenclass_get_school_info()
    if school_info:
        info_text = json.dumps(school_info, ensure_ascii=False, indent=2)
        # Разбиваем на части если слишком длинное
        if len(info_text) > 4000:
            await update.message.reply_text(
                "✅ Подключение успешно!\n\n"
                "📊 Информация о школе получена (слишком длинная для отображения)"
            )
        else:
            await update.message.reply_text(
                f"✅ Подключение успешно!\n\n📊 Информация о школе:\n```json\n{info_text}\n```",
                parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(
            "❌ Не удалось подключиться к Zenclass API.\n\n"
            "Проверь:\n"
            "1. Правильность токена\n"
            "2. Разрешения токена (scopes)\n"
            "3. Базовый URL API"
        )


async def zenclass_students(update: "Update", context: ContextTypes.DEFAULT_TYPE) -> None:
    """Получить список студентов."""
    if not ZENCLASS_API_TOKEN:
        await update.message.reply_text("❌ Zenclass API токен не настроен!")
        return

    await update.message.reply_text("🔄 Загружаю список студентов...")

    students = await zenclass_get_students()
    if students:
        students_list = students.get("data", []) if isinstance(students, dict) else students
        if isinstance(students_list, list) and len(students_list) > 0:
            text = f"👥 Студенты ({len(students_list)}):\n\n"
            for i, student in enumerate(students_list[:10], 1):
                student_id = student.get("id", "N/A")
                email = student.get("email", "N/A")
                name = student.get("name", student.get("first_name", "N/A"))
                text += f"{i}. {name} ({email})\nID: {student_id}\n\n"

            if len(students_list) > 10:
                text += f"... и еще {len(students_list) - 10} студентов"

            await update.message.reply_text(text)
        else:
            await update.message.reply_text(
                "📋 Список студентов пуст или формат ответа неожиданный.\n\n"
                "Полный ответ:\n" + json.dumps(students, ensure_ascii=False, indent=2)
            )
    else:
        await update.message.reply_text("❌ Не удалось получить список студентов.")


async def zenclass_courses(update: "Update", context: ContextTypes.DEFAULT_TYPE) -> None:
    """Получить список курсов."""
    if not ZENCLASS_API_TOKEN:
        await update.message.reply_text("❌ Zenclass API токен не настроен!")
        return

    await update.message.reply_text("🔄 Загружаю список курсов...")

    courses = await zenclass_get_courses()
    if courses:
        courses_list = courses.get("data", []) if isinstance(courses, dict) else courses
        if isinstance(courses_list, list) and len(courses_list) > 0:
            text = f"📚 Курсы ({len(courses_list)}):\n\n"
            for i, course in enumerate(courses_list[:10], 1):
                course_id = course.get("id", "N/A")
                name = course.get("name", "N/A")
                text += f"{i}. {name}\nID: {course_id}\n\n"

            if len(courses_list) > 10:
                text += f"... и еще {len(courses_list) - 10} курсов"

            await update.message.reply_text(text)
        else:
            await update.message.reply_text(
                "📋 Список курсов пуст или формат ответа неожиданный.\n\n"
                "Полный ответ:\n" + json.dumps(courses, ensure_ascii=False, indent=2)
            )
    else:
        await update.message.reply_text("❌ Не удалось получить список курсов.")


async def zenclass_create_student_handler(update: "Update", context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запросить email для создания студента."""
    if not ZENCLASS_API_TOKEN:
        await update.message.reply_text("❌ Zenclass API токен не настроен!")
        return
    context.user_data["awaiting_zenclass_email"] = True
    await update.message.reply_text(
        "📝 Создание профиля студента в Zenclass.\n\n"
        "Введите email студента (например: student@example.com):"
    )


async def zenclass_create_student_with_email(
    update: "Update", context: ContextTypes.DEFAULT_TYPE, email: str
) -> bool:
    """Создать студента по email и сохранить в platform_db. Возвращает True если успешно."""
    if not ZENCLASS_API_TOKEN:
        return False
    user = update.effective_user
    first_name = user.first_name if user else None
    last_name = user.last_name if user else None

    result = await zenclass_create_student(
        email=email.strip(),
        first_name=first_name,
        last_name=last_name,
    )
    if not result or not result.get("status"):
        return False

    data = result.get("data", {})
    zenclass_user_id = data.get("user_id")
    if not zenclass_user_id:
        return False

    await platform_db.get_or_create_user(
        email=email.strip(),
        zenclass_user_id=zenclass_user_id,
        name=(first_name or "") + (" " + (last_name or "") if last_name else "").strip() or None,
    )
    return True


def is_valid_email(text: str) -> bool:
    """Проверить, похоже ли сообщение на email."""
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", text.strip()))


def get_zenclass_menu_keyboard() -> ReplyKeyboardMarkup:
    """Получить клавиатуру меню Zenclass."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🔍 Тест API"), KeyboardButton("👥 Студенты")],
            [KeyboardButton("📚 Курсы"), KeyboardButton("➕ Создать студента")],
            [KeyboardButton("🔙 Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
