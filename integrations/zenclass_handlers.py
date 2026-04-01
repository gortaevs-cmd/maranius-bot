"""Обработчики команд Telegram для интеграции с Zenclass API."""
import json
import re
from typing import TYPE_CHECKING

from telegram.ext import ContextTypes

import ui

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
        await update.message.reply_text(ui.ZC_MSG_TOKEN_MISSING_LONG)
        return

    await update.message.reply_text(ui.ZC_MSG_CHECKING)

    school_info = await zenclass_get_school_info()
    if school_info:
        info_text = json.dumps(school_info, ensure_ascii=False, indent=2)
        # Разбиваем на части если слишком длинное
        if len(info_text) > 4000:
            await update.message.reply_text(ui.ZC_MSG_SCHOOL_OK_LONG)
        else:
            await update.message.reply_text(
                ui.ZC_MSG_SCHOOL_OK_JSON.format(json=info_text),
                parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(ui.ZC_MSG_CONNECT_FAIL)


async def zenclass_students(update: "Update", context: ContextTypes.DEFAULT_TYPE) -> None:
    """Получить список студентов."""
    if not ZENCLASS_API_TOKEN:
        await update.message.reply_text(ui.ZC_MSG_TOKEN_MISSING)
        return

    await update.message.reply_text(ui.ZC_MSG_LOADING_STUDENTS)

    students = await zenclass_get_students()
    if students:
        students_list = students.get("data", []) if isinstance(students, dict) else students
        if isinstance(students_list, list) and len(students_list) > 0:
            text = ui.ZC_MSG_STUDENTS_HEADER.format(n=len(students_list))
            for i, student in enumerate(students_list[:10], 1):
                student_id = student.get("id", "N/A")
                email = student.get("email", "N/A")
                name = student.get("name", student.get("first_name", "N/A"))
                text += f"{i}. {name} ({email})\nID: {student_id}\n\n"

            if len(students_list) > 10:
                text += ui.ZC_MSG_MORE_STUDENTS.format(n=len(students_list) - 10)

            await update.message.reply_text(text)
        else:
            await update.message.reply_text(
                ui.ZC_MSG_STUDENTS_EMPTY.format(
                    payload=json.dumps(students, ensure_ascii=False, indent=2)
                )
            )
    else:
        await update.message.reply_text(ui.ZC_MSG_STUDENTS_FAIL)


async def zenclass_courses(update: "Update", context: ContextTypes.DEFAULT_TYPE) -> None:
    """Получить список курсов."""
    if not ZENCLASS_API_TOKEN:
        await update.message.reply_text(ui.ZC_MSG_TOKEN_MISSING)
        return

    await update.message.reply_text(ui.ZC_MSG_LOADING_COURSES)

    courses = await zenclass_get_courses()
    if courses:
        courses_list = courses.get("data", []) if isinstance(courses, dict) else courses
        if isinstance(courses_list, list) and len(courses_list) > 0:
            text = ui.ZC_MSG_COURSES_HEADER.format(n=len(courses_list))
            for i, course in enumerate(courses_list[:10], 1):
                course_id = course.get("id", "N/A")
                name = course.get("name", "N/A")
                text += f"{i}. {name}\nID: {course_id}\n\n"

            if len(courses_list) > 10:
                text += ui.ZC_MSG_MORE_COURSES.format(n=len(courses_list) - 10)

            await update.message.reply_text(text)
        else:
            await update.message.reply_text(
                ui.ZC_MSG_COURSES_EMPTY.format(
                    payload=json.dumps(courses, ensure_ascii=False, indent=2)
                )
            )
    else:
        await update.message.reply_text(ui.ZC_MSG_COURSES_FAIL)


async def zenclass_create_student_handler(update: "Update", context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запросить email для создания студента."""
    if not ZENCLASS_API_TOKEN:
        await update.message.reply_text(ui.ZC_MSG_TOKEN_MISSING)
        return
    context.user_data["awaiting_zenclass_email"] = True
    await update.message.reply_text(ui.ZC_MSG_CREATE_STUDENT_PROMPT)


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
