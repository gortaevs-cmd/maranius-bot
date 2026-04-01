"""
Тексты команд, кнопок и сообщений бота — править здесь, чтобы не искать по коду.
Клавиатуры собираются функциями ниже; сравнение текста в хендлерах — через те же константы BTN_*.
"""

from __future__ import annotations

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# --- Подсказки команд (для /start и документации) ---
CMD_WEATHER_HELP = "/weather — погода по геолокации"
CMD_RATE_HELP = "/rate — курс валют"
CMD_MOON_HELP = "/moon — информация о луне"
CMD_ME_HELP = "/me — технические поля профиля Telegram"

START_MESSAGE = (
    "Привет! Я тестовый бот EL.\n\n"
    "Доступные команды:\n"
    f"{CMD_WEATHER_HELP}\n"
    f"{CMD_RATE_HELP}\n"
    f"{CMD_MOON_HELP}\n"
    f"{CMD_ME_HELP}\n\n"
    "Напиши команду или текст для меню."
)

# --- Кнопки главного админ-меню (Reply) ---
BTN_ADMIN_USERS = "Пользователи"
BTN_ADMIN_GROUPS = "Группы"
BTN_ADMIN_EVENTS = "События"
BTN_ADMIN_ZENCLASS = "Zenclass"

# --- Кнопки Zenclass (совпадают с текстом в handle_text в bot.py) ---
BTN_ZC_TEST = "🔍 Тест API"
BTN_ZC_STUDENTS = "👥 Студенты"
BTN_ZC_COURSES = "📚 Курсы"
BTN_ZC_CREATE = "➕ Создать студента"
BTN_ZC_BACK = "🔙 Назад"

# --- Погода: кнопки с геолокацией ---
BTN_WEATHER_REFRESH = "Обновить локацию"
BTN_WEATHER_SHARE_LOC = "Отправить мою геопозицию"

MSG_WEATHER_ASK_LOCATION = (
    "Нажми кнопку, чтобы отправить свою геопозицию, и я покажу погоду для твоего места."
)
MSG_WEATHER_FETCH_FAIL = "Не удалось получить погоду, попробуй позже."
MSG_WEATHER_GEO_FAIL = "Не удалось получить погоду по геолокации, попробуй позже."

# --- Курсы ---
RATE_CHOOSE_PROMPT = "Выбери валюту, для которой показать курс к {quote}:"
RATE_INLINE_METALS = "🥇🥈 Золото/Серебро"
MSG_RATE_METALS_FAIL = "Не удалось получить курс драгоценных металлов, попробуй позже."
MSG_RATE_CURRENCY_FAIL = "Не удалось получить курс, попробуй позже."

# --- Админ / режим бога ---
ADMIN_PANEL_TITLE = "Режим бога активирован.\nВыбери действие:"
ADMIN_ASK_CODE = (
    "Для доступа к режиму бога введи код доступа.\n"
    "Отправь код в следующем сообщении:"
)
ADMIN_CODE_OK = "Код верный! Режим бога активирован.\nВыбери действие:"
ADMIN_CODE_WRONG = (
    "Неверный код доступа. Попробуй еще раз или отправь /admin для повторной попытки."
)
ADMIN_BACK_MAIN = "Режим бога.\nВыбери действие:"

MSG_ADMIN_USER_COUNT = "Пользователей подписано: {count}"
MSG_ADMIN_NO_GROUPS = "Бот не состоит ни в одной группе."
MSG_ADMIN_GROUPS_LIST = "Группы ({count}): {ids}"

# --- События (статистика в группах) ---
EVENTS_TITLE_HTML = "📊 <b>События (Telegram)</b>"
EVENT_LABEL_SUBSCRIBE = "➕ Подписки"
EVENT_LABEL_UNSUBSCRIBE = "👋 Отписки"
EVENT_LABEL_REACTION = "❤️ Реакции"
EVENTS_RECENT_HEADER = "Последние события:"

# --- Zenclass: заголовки меню и сообщения (integrations/zenclass_handlers) ---
ZENCLASS_MENU_HEADER = "🔧 Zenclass API\n\nВыбери действие:"

ZC_MSG_TOKEN_MISSING = "❌ Zenclass API токен не настроен!"
ZC_MSG_TOKEN_MISSING_LONG = (
    "❌ Zenclass API токен не настроен!\n\n"
    "Добавь в .env файл:\n"
    "ZENCLASS_API_TOKEN=твой_токен\n"
    "ZENCLASS_API_BASE_URL=https://api.zenclass.net (опционально)"
)
ZC_MSG_CHECKING = "🔄 Проверяю подключение к Zenclass API..."
ZC_MSG_SCHOOL_OK_LONG = (
    "✅ Подключение успешно!\n\n"
    "📊 Информация о школе получена (слишком длинная для отображения)"
)
ZC_MSG_SCHOOL_OK_JSON = "✅ Подключение успешно!\n\n📊 Информация о школе:\n```json\n{json}\n```"
ZC_MSG_CONNECT_FAIL = (
    "❌ Не удалось подключиться к Zenclass API.\n\n"
    "Проверь:\n"
    "1. Правильность токена\n"
    "2. Разрешения токена (scopes)\n"
    "3. Базовый URL API"
)
ZC_MSG_LOADING_STUDENTS = "🔄 Загружаю список студентов..."
ZC_MSG_STUDENTS_HEADER = "👥 Студенты ({n}):\n\n"
ZC_MSG_STUDENTS_EMPTY = (
    "📋 Список студентов пуст или формат ответа неожиданный.\n\nПолный ответ:\n{payload}"
)
ZC_MSG_STUDENTS_FAIL = "❌ Не удалось получить список студентов."
ZC_MSG_LOADING_COURSES = "🔄 Загружаю список курсов..."
ZC_MSG_COURSES_HEADER = "📚 Курсы ({n}):\n\n"
ZC_MSG_COURSES_EMPTY = (
    "📋 Список курсов пуст или формат ответа неожиданный.\n\nПолный ответ:\n{payload}"
)
ZC_MSG_COURSES_FAIL = "❌ Не удалось получить список курсов."
ZC_MSG_CREATE_STUDENT_PROMPT = (
    "📝 Создание профиля студента в Zenclass.\n\n"
    "Введите email студента (например: student@example.com):"
)
ZC_MSG_MORE_STUDENTS = "... и еще {n} студентов"
ZC_MSG_MORE_COURSES = "... и еще {n} курсов"

# --- Создание студента (ответы в bot.py после email) ---
MSG_ZC_STUDENT_CREATED_OK = (
    "✅ Профиль студента создан в Zenclass и сохранён в базу platform_users."
)
MSG_ZC_STUDENT_CREATED_FAIL = "❌ Не удалось создать студента. Проверьте email и токен API."
MSG_ZC_EMAIL_INVALID = "❌ Неверный формат email. Отправьте email (например: user@example.com)"

# --- Прочее ---
UNKNOWN_COMMAND_HINT = "Неизвестная команда. Используй /start для списка команд."


def get_admin_keyboard() -> ReplyKeyboardMarkup:
    """Нижняя клавиатура режима администратора."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_ADMIN_USERS), KeyboardButton(BTN_ADMIN_GROUPS)],
            [KeyboardButton(BTN_ADMIN_EVENTS)],
            [KeyboardButton(BTN_ADMIN_ZENCLASS)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_zenclass_menu_keyboard() -> ReplyKeyboardMarkup:
    """Меню Zenclass (подменю админа)."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_ZC_TEST), KeyboardButton(BTN_ZC_STUDENTS)],
            [KeyboardButton(BTN_ZC_COURSES), KeyboardButton(BTN_ZC_CREATE)],
            [KeyboardButton(BTN_ZC_BACK)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_weather_refresh_keyboard() -> ReplyKeyboardMarkup:
    """Кнопка обновления геолокации для /weather."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_WEATHER_REFRESH, request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_weather_share_keyboard() -> ReplyKeyboardMarkup:
    """Первая отправка геолокации."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_WEATHER_SHARE_LOC, request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_rate_inline_keyboard(rate_base_codes: list[str]) -> InlineKeyboardMarkup:
    """Кнопки выбора валюты для /rate. Последний код в списке METALS — отдельная кнопка."""
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for code in rate_base_codes[:3]:
        row.append(InlineKeyboardButton(code, callback_data=f"rate:{code}"))
    if row:
        buttons.append(row)
    buttons.append(
        [InlineKeyboardButton(RATE_INLINE_METALS, callback_data="rate:METALS")]
    )
    return InlineKeyboardMarkup(buttons)


def rate_choose_prompt(quote: str) -> str:
    return RATE_CHOOSE_PROMPT.format(quote=quote)
