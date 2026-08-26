"""
Тексты команд, кнопок и сообщений бота — править здесь, чтобы не искать по коду.
Клавиатуры собираются функциями ниже; сравнение текста в хендлерах — через те же константы BTN_*.
"""

from __future__ import annotations

import html

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# --- Главное меню (reply, всегда внизу) ---
BTN_TODAY = "✨ Сегодня"
BTN_VIP = "🔑 Моё VIP"
BTN_STORE = "🛍 Лавка"
BTN_MORE = "⋯ Ещё"

# Внешние ссылки (синее «☰» и разделы)
URL_CATALOG = "https://maranius.ru/?utm_source=telegram&utm_medium=bot&utm_campaign=lavka"
URL_SCHOOL = "https://school.maranius.ru/"
URL_ANGELS_INSTRUCTION = "https://telegra.ph/Angelskie-znaki-08-03"
URL_ANGEL_COURSE = (
    "https://school.maranius.ru/public/product/"
    "e95de46b-60d7-48ec-a028-3861e91ca7e7/tariffs"
)
URL_KRYON_BOT = "https://t.me/KryonCrystalsBot"
URL_CARD_OF_DAY = "https://maranius.ru/karta-dnya"
URL_CARD_INSTRUCTION = "https://telegra.ph/Koloda-kart-Podskazki-Vselennoj-08-03"
URL_POLICY = "https://telegra.ph/Politika-obrabotki-personalnyh-dannyh-08-03"
URL_CONSENT = "https://telegra.ph/SOGLASIE-NA-OBRABOTKU-PERSONALNYH-DANNYH-05-31-2"
URL_AUTHOR = "https://t.me/maraniuss"
CONTACTS_HTML = f'<a href="{URL_AUTHOR}">@maraniuss</a>'

START_MESSAGE = (
    "Добро пожаловать в пространство Maranius.\n\n"
    "Нижнее меню — основной способ навигации:\n"
    f"• {BTN_TODAY} — ангелы, карта дня, кубик\n"
    f"• {BTN_VIP} — материалы после покупки\n"
    f"• {BTN_STORE} — каталог товаров\n"
    f"• {BTN_MORE} — курсы, услуги, лунный календарь, погода\n\n"
    "Выберите пункт ниже."
)

MSG_TODAY = (
    f"<b>{BTN_TODAY}</b>\n\n"
    "Ежедневные практики и знаки:\n"
    "• <b>Ангельские знаки</b> — инструкция; числа и время можно писать в любой момент\n"
    "• <b>Карта/Кристалл дня</b> — Подсказки Вселенной и кристалл Крайона на сайте\n"
    "• <b>Кубик</b> — подсказка на день, один бросок в сутки"
)

MSG_ANGEL_INSTRUCTION = (
    "<b>Ангельские знаки</b>\n\n"
    "Напиши в чат число (1–3 цифры) или время — например "
    "<code>11:11</code>, <code>14:30</code>, <code>777</code>.\n"
    "Бот ответит расшифровкой.\n\n"
    f'<a href="{URL_ANGELS_INSTRUCTION}">Подробная инструкция</a>'
)

MSG_ANGEL_NOT_FOUND = (
    "Не нашёл расшифровку для «{sign}».\n\n"
    "Проверь формат: число 1–3 цифры или время, например "
    "<code>11:11</code> или <code>777</code>."
)

MSG_ANGEL_FETCH_FAIL = (
    "Не удалось получить ангельский знак. Попробуй позже или проверь формат."
)


def format_angelic_sign(display_key: str, meaning: str) -> str:
    """HTML-сообщение расшифровки (Telegram HTML: tg-spoiler, не spoiler)."""
    safe_key = html.escape(display_key.strip())
    safe_meaning = html.escape(meaning.strip())
    return (
        f"💖 <b>Значение ангельского знака <u>{safe_key}</u></b> 💖\n\n"
        f"{safe_meaning}\n\n"
        f"<tg-spoiler>Тайны Вселенной: "
        f'<a href="{URL_ANGEL_COURSE}">«Путеводитель в мир Ангельской Нумерологии»</a>'
        f"</tg-spoiler>"
    )

MSG_CARD_HUB = (
    "<b>Карта/Кристалл дня</b>\n\n"
    "<b>Подсказки Вселенной</b> — метафорическая карта на день: нажми «Карта дня», "
    "Вселенная перемешает колоду и выдаст карту. Одна карта в сутки.\n\n"
    f'<a href="{URL_CARD_INSTRUCTION}">Инструкция к карте дня</a>\n\n'
    "<b>Кристалл дня</b> — знакомство с энергией кристаллов Атлантиды (Крайона): "
    "энергия сама выберет вас. Один кристалл в сутки."
)

MSG_CARD_SHUFFLE = "🃏 Перемешиваем колоду…"
MSG_CRYSTAL_SHUFFLE = "💎 Настраиваемся на кристалл…"
MSG_CARD_CATALOG_EMPTY = "Каталог карт временно недоступен. Попробуй позже."
MSG_CRYSTAL_CATALOG_EMPTY = "Каталог кристаллов временно недоступен. Попробуй позже."

MSG_DICE_INTRO = "🎲 Бросаем кубик на подсказку дня…"


def format_dice_already(dice_text: str) -> str:
    return (
        "Сегодня ты уже бросал(а) кубик. Новый бросок — после полуночи по твоему местному времени.\n\n"
        f"{dice_text}"
    )


def format_card_already(pull_title: str, pull_url: str) -> str:
    return (
        "Сегодня ты уже вытягивал(а) карту. Новая — после полуночи по твоему местному времени.\n\n"
        f"Твоя карта дня: <b>{html.escape(pull_title)}</b>\n"
        f'<a href="{html.escape(pull_url, quote=True)}">Открыть на сайте</a>'
    )


def format_card_success(pull_title: str, pull_url: str) -> str:
    return (
        "💖 <b>Твоя карта дня</b> 💖\n\n"
        f"<b>{html.escape(pull_title)}</b>\n\n"
        f'<a href="{html.escape(pull_url, quote=True)}">Открыть карту на сайте</a>'
    )


def format_crystal_already(pull_title: str, pull_url: str) -> str:
    return (
        "Сегодня ты уже получал(а) кристалл дня. Новый — после полуночи по твоему местному времени.\n\n"
        f"Твой кристалл: <b>{html.escape(pull_title)}</b>\n"
        f'<a href="{html.escape(pull_url, quote=True)}">Открыть на сайте</a>'
    )


def format_crystal_success(pull_title: str, pull_url: str) -> str:
    return (
        "💎 <b>Твой кристалл дня</b> 💎\n\n"
        f"<b>{html.escape(pull_title)}</b>\n\n"
        f'<a href="{html.escape(pull_url, quote=True)}">Открыть кристалл на сайте</a>'
    )

MSG_VIP_CODE_INVALID = (
    "Код не найден или уже использован.\n\n"
    f"Если код верный — свяжись со мной: {CONTACTS_HTML}"
)

MSG_VIP_CODE_OK = (
    "✅ Доступ открыт! Нажми «🔑 Моё VIP» ещё раз — откроется меню колод."
)

MSG_VIP_APPROVE_USER = (
    "✅ Доступ VIP открыт навсегда. Нажми «🔑 Моё VIP»."
)

MSG_VIP_REJECT_USER = (
    "Запрос на доступ отклонён. Если это ошибка — напиши "
    f"{CONTACTS_HTML}"
)

MSG_VIP_PDF_MISSING = (
    "PDF временно недоступен на сервере. Напиши "
    f"{CONTACTS_HTML} — пришлю файл."
)

MSG_TECHNICAL_ERROR = (
    "Не удалось выполнить действие из-за временного технического сбоя. "
    "Попробуй ещё раз через несколько минут. Ошибка зарегистрирована."
)

MSG_STORE_STUB = (
    f"<b>{BTN_STORE}</b>\n\n"
    "Здесь собраны товары и инструменты для духовных практик, "
    "самопознания и работы с энергиями:\n\n"
    "🃏 <b>Карты и колоды</b> — карты Таро, метафорические карты и кристаллы Крайона "
    "в виде карт.\n"
    "✨ <b>Авторские и программные свечи</b> — для духовных практик, работы с намерениями "
    "и энергиями.\n"
    "🎁 <b>Подарки</b> — осмысленные подарки для себя и близких.\n\n"
    "Перейдите в Лавку, чтобы посмотреть каталог, описания и выбрать подходящий товар 👇\n\n"
    f'<a href="{URL_CATALOG}">Открыть Лавку в браузере</a>'
)

BTN_STORE_OPEN = "🛍 Перейти в Лавку"

MSG_MORE = (
    f"<b>{BTN_MORE}</b>\n\n"
    "Здесь собраны полезные сервисы, материалы и информация:\n\n"
    "🌤 <b>Погода</b> — текущая погода и прогноз для вашей локации.\n"
    "🌙 <b>Луна</b> — фаза, лунные сутки и даты новолуния и полнолуния.\n"
    "🙌 <b>Услуги</b> — целительские практики, их описания и запись на сессию.\n"
    "🎓 <b>Курсы/Практики</b> — онлайн-программы и практики с кристаллами Крайона.\n"
    "ℹ️ <b>Инфо / FAQ</b> — как пользоваться ботом и ответы на частые вопросы.\n"
    "🛡 <b>Политика</b> — документы об обработке персональных данных.\n"
    "⚙️ <b>Настройки профиля</b> — статус подписок и управление рассылкой.\n\n"
    "Выберите нужный раздел ниже 👇"
)

MSG_SERVICES = (
    "<b>Целительские услуги</b>\n\n"
    "Приветствую тебя, душа моя! 😍\n\n"
    "Благодарю за интерес к моим практикам. "
    "Ниже — описания техник; запись и вопросы по услугам: "
    f"{CONTACTS_HTML}.\n\n"
    "С уважением,\nMaranius El Shaddai"
)

MSG_CONTACT = (
    "<b>Связь с автором</b>\n\n"
    "Если что-то непонятно или нужна помощь — нажми кнопку ниже "
    "или напиши @maraniuss.\n\n"
    "<b>С чем можно обратиться:</b>\n"
    "• товары и заказы — Лавка на maranius.ru\n"
    "• курсы и доступ к платформе — school.maranius.ru\n"
    "• VIP-код, колоды и материалы в боте\n"
    "• работа бота: ангелы, карта/кристалл дня, кубик\n\n"
    "Опиши вопрос одним сообщением — отвечу, как только смогу.\n\n"
    "<i>Целительские услуги и запись на сессии — в «Ещё → Услуги».</i>"
)

URL_SERVICE_LAN_TAROS = "https://telegra.ph/Celitelskie-uslugi-LAN-TAROS-08-04"
URL_SERVICE_NIA_TA_NE = "https://telegra.ph/Celitelskie-uslugi-Nia-Ta-Neh-08-04"

MSG_LEARNING = (
    "<b>Курсы и практики</b>\n\n"
    f'🎓 <a href="{URL_SCHOOL}">Онлайн-курсы</a> — полные программы на платформе.\n\n'
    "💎 <b>Путь Света через кристаллы Крайона</b>\n"
    "Практики 7 · 21 · 33 дня — ежедневные послания и глубокая работа с кристаллами.\n\n"
    f'Продолжить в боте → <a href="{URL_KRYON_BOT}">@KryonCrystalsBot</a>'
)

MSG_INFO_FAQ = (
    "<b>Инфо / FAQ</b>\n\n"
    "<b>1. Как пользоваться ботом</b>\n"
    "Внизу расположены 4 кнопки: «Сегодня», «VIP», «Лавка» и «Ещё». "
    "Основной раздел — «Сегодня».\n\n"
    "<b>2. Ангельские знаки</b>\n"
    "Напишите в чат число или время (11:11, 22:30, 7) — ответ придёт автоматически.\n\n"
    "<b>3. Карта/Кристалл дня</b>\n"
    "Получайте подсказку Вселенной или кристалл дня для решения повседневных задач.\n\n"
    "<b>4. Моё VIP</b>\n"
    "После покупки колоды открывается доступ к инструкциям для всех колод.\n\n"
    "<b>5. Лавка</b>\n"
    "Каталог товаров на maranius.ru.\n\n"
    "<b>6. Курсы и Крайон</b>\n"
    "Курсы — school.maranius.ru; практики с кристаллами Крайона на 7, 21 и 33 дня — @KryonCrystalsBot.\n\n"
    "<b>7. Услуги</b>\n"
    "Целительские практики — «Ещё → Услуги».\n\n"
    "<b>8. Связь с автором</b>\n"
    f"По вопросам о товарах, платформе или боте — /contact или {CONTACTS_HTML}.\n\n"
    "<b>9. Погода и Луна</b>\n"
    "Дополнительные сервисы в разделе «Ещё».\n\n"
    "<b>10. Кубик</b>\n"
    "Подсказка дня в разделе «Сегодня»."
)

MSG_POLICY_STUB = (
    f'<a href="{URL_POLICY}">Политика обработки персональных данных</a>\n'
    f'<a href="{URL_CONSENT}">Согласие на обработку персональных данных</a>'
)

MSG_POLICY_FULL = (
    "<b>Политика и согласие</b>\n\n"
    f'• <a href="{URL_POLICY}">Политика обработки персональных данных</a>\n'
    f'• <a href="{URL_CONSENT}">Согласие на обработку персональных данных</a>\n\n'
    "Маркетинговые рассылки — отдельно, только по вашему желанию."
)

MSG_POLICY_GATE = (
    "Перед использованием бота нужно принять политику обработки персональных данных.\n\n"
    f'• <a href="{URL_POLICY}">Политика</a>\n'
    f'• <a href="{URL_CONSENT}">Согласие</a>'
)

MSG_POLICY_ACCEPTED = "Спасибо. Политику приняли — можно пользоваться ботом."
MSG_POLICY_CONTINUE = "Продолжай с нужного раздела в меню."
MSG_ACCESS_RESTRICTED = "Доступ ограничен."

MSG_MARKETING_OFFER = (
    "Хотите получать новости и анонсы Maranius в Telegram?\n"
    "Это необязательно — сервисные сообщения (VIP, ответы) приходят без рассылки."
)
MSG_MARKETING_ON = "Вы подписаны на новости и анонсы."
MSG_MARKETING_OFF = "Рассылку отключили. Сервисные сообщения по-прежнему возможны."

MSG_PROFILE_MENU = (
    "<b>⚙️ Настройки профиля</b>\n\n"
    "Здесь можно посмотреть активные подписки и управлять рассылкой новостей."
)
MSG_PROFILE_SUBS_ON = (
    "<b>📬 Подписки</b>\n\n"
    "Вы подписаны на новости и анонсы Maranius в Telegram.\n"
    "Сервисные сообщения (VIP, ответы) приходят отдельно от рассылки."
)
MSG_PROFILE_SUBS_OFF = (
    "<b>📬 Подписки</b>\n\n"
    "Вы не подписаны на новости и анонсы.\n"
    "Сервисные сообщения (VIP, ответы) по-прежнему возможны без рассылки."
)

BTN_PROFILE_STATUS = "📋 Статус"
BTN_PROFILE_SUBS = "📬 Подписки"

CB_POLICY_ACCEPT = "consent:policy:accept"
CB_MARKETING_YES = "consent:marketing:yes"
CB_MARKETING_NO = "consent:marketing:no"
CB_MARKETING_TOGGLE_ON = "consent:marketing:on"
CB_MARKETING_TOGGLE_OFF = "consent:marketing:off"
CB_MARKETING_UNSUB = "consent:marketing:unsub"

BTN_POLICY_ACCEPT = "✅ Принимаю политику"
BTN_MARKETING_YES = "📬 Да, хочу новости"
BTN_MARKETING_NO = "Не сейчас"
BTN_MARKETING_ON = "📬 Подписаться на рассылку"
BTN_MARKETING_OFF = "🔕 Отключить рассылку"
BTN_MARKETING_UNSUB = "Не получать рассылки"

# Inline «Сегодня»
CB_TODAY_HOME = "today:home"
CB_TODAY_ANGEL = "today:angel"
CB_TODAY_CARD = "today:card"
CB_TODAY_DICE = "today:dice"
CB_CARD_BACK = "today:card:back"
CB_CARD_PULL = "today:card:pull"
CB_CRYSTAL_PULL = "today:card:crystal"

# Inline «Ещё»
CB_MORE_HOME = "more:home"
CB_MORE_PROFILE = "more:profile"
CB_PROFILE_STATUS = "more:profile:status"
CB_PROFILE_SUBS = "more:profile:subs"
CB_PROFILE_SUB_ON = "more:profile:sub:on"
CB_PROFILE_SUB_OFF = "more:profile:sub:off"
CB_WEATHER = "more:weather"
CB_MOON = "more:moon"
CB_SERVICES = "more:services"
CB_LEARNING = "more:learning"
CB_INFO = "more:info"
CB_POLICY = "more:policy"

# --- Погода (только Ещё → Погода; reply-клавиатура для геолокации) ---
BTN_WEATHER_SHARE_LOC = "📍 Отправить место"
BTN_WEATHER_NEW_LOC = "📍 Новое место"
# Legacy — старые клавиатуры в чатах
BTN_WEATHER_SHARE_LOC_LEGACY = "📍 Отправить геопозицию"
BTN_WEATHER_NEW_LOC_LEGACY = "📍 Новая геопозиция"

MSG_WEATHER_ASK_LOCATION = (
    "Нажми <b>📍 Отправить место</b> внизу, чтобы привязать локацию.\n"
    "На Mac удобнее отправить с телефона."
)
MSG_WEATHER_LOCATION_EXPIRED = (
    "Локация устарела. Нажми «📍 Отправить место», чтобы обновить."
)
MSG_WEATHER_CACHE_HINT = "\n\n<i>Сменить место: отправь «новое место».</i>"
MSG_WEATHER_FETCH_FAIL = "Не удалось получить погоду, попробуй позже."
MSG_WEATHER_GEO_FAIL = "Не удалось получить погоду по геолокации, попробуй позже."

# --- Режим бога (скрытый /god) ---
GOD_TEXT_TRIGGER = "god"

# Текстовые команды — запасной вход (legacy → inbox).
ADMIN_CMD_UNKNOWN_ANGELS = "неизвестные ангелы"
ADMIN_CMD_UNKNOWN_ANGELS_FILE = "файл неизвестных ангелов"

CB_ADMIN_HOME = "admin:home"
CB_ADMIN_MENU_INBOX = "admin:menu:inbox"
CB_ADMIN_MENU_LISTS = "admin:menu:lists"
CB_ADMIN_MENU_USERS = "admin:menu:users"
CB_ADMIN_MENU_BOT = "admin:menu:bot"
CB_ADMIN_MENU_VIP = "admin:menu:vip"
CB_ADMIN_MENU_AUDIT = "admin:menu:audit"
CB_ADMIN_INBOX = "admin:inbox"
CB_ADMIN_INBOX_CSV = "admin:inbox_csv"
CB_ADMIN_LIST_ALL = "admin:list:all"
CB_ADMIN_LIST_SEGMENT_PREFIX = "admin:list:"
CB_ADMIN_USERS = "admin:users"
CB_ADMIN_STATUS = "admin:status"
CB_ADMIN_VIP = "admin:vip"
CB_ADMIN_VIP_EXPORT = "admin:vip_export"
CB_ADMIN_VIP_ADD = "admin:vip_add"
CB_ADMIN_VIP_IMPORT = "admin:vip_import"
CB_ADMIN_VIP_CANCEL = "admin:vip_cancel"
CB_ADMIN_AUDIT = "admin:audit"
CB_ADMIN_AUDIT_CSV = "admin:audit_csv"
CB_ADMIN_USER_FIND = "admin:user:find"
CB_ADMIN_USER_BACK = "admin:user:back"
CB_ADMIN_USER_VIP_ON = "admin:user:vip:on"
CB_ADMIN_USER_VIP_OFF = "admin:user:vip:off"
CB_ADMIN_USER_BLOCK = "admin:user:block"
CB_ADMIN_USER_UNBLOCK = "admin:user:unblock"
CB_ADMIN_CONFIRM = "admin:confirm"
CB_ADMIN_CANCEL = "admin:cancel"
CB_ADMIN_BATCH_CONFIRM = "admin:batch:confirm"
CB_ADMIN_BATCH_CANCEL = "admin:batch:cancel"

# Legacy callbacks (redirect to inbox)
CB_ADMIN_MENU_ANGELS = "admin:menu:angels"
CB_ADMIN_UNKNOWN = "admin:unknown"
CB_ADMIN_UNKNOWN_CSV = "admin:unknown_csv"

ADMIN_BTN_HOME_INBOX = "📥 Inbox"
ADMIN_BTN_HOME_LISTS = "📋 Списки"
ADMIN_BTN_HOME_USERS = "👤 Пользователи"
ADMIN_BTN_HOME_BOT = "ℹ️ Бот"
ADMIN_BTN_HOME_VIP = "🔑 VIP"
ADMIN_BTN_HOME_AUDIT = "🧾 Журнал действий"
ADMIN_BTN_BACK = "◀️ Назад"
ADMIN_BTN_INBOX = "📥 Сводка Inbox"
ADMIN_BTN_INBOX_CSV = "📎 CSV Inbox"
ADMIN_BTN_LIST_ALL = "📎 Все пользователи (CSV)"
ADMIN_BTN_USERS = "📊 Статистика"
ADMIN_BTN_STATUS = "ℹ️ Статус бота"
ADMIN_BTN_VIP = "🔑 Сводка кодов"
ADMIN_BTN_VIP_EXPORT = "📎 Выгрузка кодов"
ADMIN_BTN_VIP_ADD = "➕ Добавить коды"
ADMIN_BTN_VIP_IMPORT = "👑 Импорт VIP"
ADMIN_BTN_VIP_CANCEL = "✖️ Отмена ввода"
ADMIN_BTN_AUDIT = "🧾 Последние действия"
ADMIN_BTN_AUDIT_CSV = "📎 CSV журнала"
ADMIN_BTN_USER_FIND = "🔎 Найти пользователя"
ADMIN_BTN_USER_VIP_ON = "🔑 Выдать VIP"
ADMIN_BTN_USER_VIP_OFF = "🔓 Снять VIP"
ADMIN_BTN_USER_BLOCK = "⛔ Ограничить доступ"
ADMIN_BTN_USER_UNBLOCK = "✅ Снять ограничение"

# Legacy labels
ADMIN_BTN_HOME_ANGELS = "👼 Ангелы"
ADMIN_BTN_UNKNOWN = "👼 Неизвестные знаки"
ADMIN_BTN_UNKNOWN_CSV = "📎 CSV неизвестных"

CB_VIP_APPROVE_PREFIX = "vip:approve:"
CB_VIP_REJECT_PREFIX = "vip:reject:"

ADMIN_VIP_SUMMARY = (
    "VIP-коды: <b>{active}</b> активных, <b>{used}</b> отработанных.\n\n"
    f"«{ADMIN_BTN_VIP_ADD}» — вставь списком одним сообщением (по строке).\n"
    f"«{ADMIN_BTN_VIP_EXPORT}» — один CSV-файл со всеми кодами.\n"
    f"«{ADMIN_BTN_VIP_IMPORT}» — список telegram_id для VIP (maranius + Kryon)."
)
ADMIN_VIP_ADD_PROMPT = (
    "Вставь коды <b>одним сообщением</b> — по одному на строку.\n"
    "Строки с # в начале игнорируются. Затем укажи основание и подтверди операцию."
)
ADMIN_VIP_ADD_RESULT = (
    "Готово: добавлено <b>{added}</b>, дублей <b>{dup}</b>, пустых <b>{skipped}</b>.\n"
    "Действие записано в журнал."
)
ADMIN_VIP_IMPORT_PROMPT = (
    "Вставь список <b>telegram_id</b> (по строке) или CSV с id в первой колонке.\n"
    "Бот покажет предпросмотр, запросит основание и подтверждение. Уведомления пользователям не отправляются."
)
ADMIN_VIP_IMPORT_RESULT = (
    "VIP импорт: выдано <b>{granted}</b>, уже были VIP <b>{skipped}</b>, "
    "дублей в списке <b>{duplicates}</b>, ошибок строк <b>{invalid}</b>.\n"
    "Действие записано в журнал; пользователям сообщения не отправлялись."
)
ADMIN_BATCH_REASON_PROMPT = "Укажи основание операции одним сообщением (от 3 до 200 символов)."
ADMIN_BATCH_CODE_PREVIEW = (
    "Коды: будет добавлено <b>{added}</b>, дублей <b>{dup}</b>, пустых <b>{skipped}</b>.\n"
    "Основание: <code>{reason}</code>\n\nПодтвердить?"
)
ADMIN_BATCH_IMPORT_PREVIEW = (
    "Импорт VIP-доступа: будет выдано <b>{granted}</b>, уже VIP <b>{skipped}</b>, "
    "дублей в списке <b>{duplicates}</b>, ошибок строк <b>{invalid}</b>.\n"
    "Основание: <code>{reason}</code>\n\nПодтвердить?"
)
ADMIN_VIP_WRONG_CODE = (
    "⚠️ Неверный VIP-код\n"
    "Пользователь: {user_link}\n"
    "Ввод: <code>{code}</code>"
)

ADMIN_STUB = (
    "<b>Режим бога</b> — выбери раздел.\n"
    "Вход: <code>/god</code> или текст <code>god</code>."
)
ADMIN_MENU_INBOX = (
    "<b>Inbox</b> — всё вне сценария: неизвестные ангелы, необработанный ввод.\n"
    "Хранение 90 дней. CSV — с датой скачивания в каждой строке."
)
ADMIN_MENU_LISTS = (
    "<b>Списки</b> — выгрузка сегментов пользователей или единый CSV."
)
ADMIN_MENU_USERS = (
    "<b>Пользователи</b> — найди человека по Telegram ID или @username и выбери действие.\n"
    "Текстовый формат тоже доступен: <code>vip 123456789 основание</code>, "
    "<code>block @nick основание</code>, <code>unvip id основание</code>, "
    "<code>unblock id основание</code>."
)
ADMIN_MENU_ANGELS = ADMIN_MENU_INBOX
ADMIN_MENU_BOT = (
    "<b>Бот</b> — статистика пользователей и служебная информация."
)
ADMIN_MENU_VIP = (
    "<b>VIP</b> — коды доступа, выгрузка и импорт пользователей."
)
ADMIN_MENU_AUDIT = (
    "<b>Журнал действий</b> — выдача и снятие VIP, ограничения, импорт и добавление кодов.\n"
    "Хранение: 365 дней, не более 20 000 записей."
)

ADMIN_INBOX_EMPTY = "Inbox пуст."
ADMIN_INBOX_SUMMARY = (
    "Inbox: <b>{total}</b> записей (90 дней), "
    "без уведомления админу: <b>{unnotified}</b>.\n\n"
    f"CSV: «{ADMIN_BTN_INBOX_CSV}»."
)
ADMIN_LISTS_HINT = (
    "Выбери сегмент для CSV. «Готовы к рассылке» исключает internal, "
    "заблокировавших бота и ручной стоп-лист."
)
ADMIN_USERS_SUMMARY = (
    "Пользователи: <b>{total}</b> всего (<b>{real}</b> без internal), "
    "<b>{week}</b> новых за 7 дней.\n"
    "VIP: <b>{vip}</b>, без ПДн: <b>{no_policy}</b>, "
    "рассылка: <b>{marketing}</b>, спящие (&gt;30д): <b>{sleeping}</b>."
)
ADMIN_USER_CMD_OK = "Готово: {action} для <code>{target}</code>."
ADMIN_USER_CMD_FAIL = "Не нашёл пользователя: <code>{target}</code>."
ADMIN_USER_PROTECTED = "Нельзя изменить доступ seed-администратора."
ADMIN_USER_CMD_REASON_REQUIRED = (
    "Добавь основание операции (от 3 символов), например: "
    "<code>vip 123456789 подарок за курс</code>."
)
ADMIN_USER_FIND_PROMPT = "Пришли Telegram ID или @username пользователя."
ADMIN_USER_CARD = (
    "<b>Пользователь</b>\n"
    "ID: <code>{user_id}</code>\n"
    "Username: {username}\n"
    "VIP-доступ: <b>{vip}</b> ({vip_source})\n"
    "Бот: <b>{bot_status}</b> · ручной стоп-лист: <b>{admin_blocked}</b>\n"
    "Согласие на рассылку: <b>{marketing}</b> · ПДн: <b>{policy}</b>\n"
    "Последняя активность: <code>{last_seen}</code>\n\n"
    "Выбери действие — затем потребуется основание и подтверждение."
)
ADMIN_CONFIRM_PROMPT = (
    "Подтвердить: <b>{action}</b> для <code>{target}</code>?\n"
    "Основание: <code>{reason}</code>"
)
ADMIN_AUDIT_EMPTY = "Журнал действий пока пуст."
ADMIN_AUDIT_SUMMARY = "<b>Последние действия</b>\n{rows}"
BTN_CONFIRM_YES = "✅ Да"
BTN_CONFIRM_NO = "❌ Отмена"
ADMIN_STATUS = (
    "<b>Статус бота</b>\n"
    "Профиль: <code>{profile}</code>\n"
    "Telegram: @{username}\n"
    "Команд в меню «☰»: {cmd_count}"
)
BTN_BACK_TODAY = "◀️ Назад"
BTN_BACK_MORE = "◀️ Назад"

ADMIN_DENIED = "Нет доступа."


def get_today_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BTN_BACK_TODAY, callback_data=CB_TODAY_HOME)]]
    )


def get_back_to_more_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BTN_BACK_MORE, callback_data=CB_MORE_HOME)]]
    )


def get_admin_vip_prompt_keyboard() -> InlineKeyboardMarkup:
    """VIP admin: отмена ввода кодов/id."""
    rows = list(get_admin_vip_keyboard().inline_keyboard)
    rows.append(
        [InlineKeyboardButton(ADMIN_BTN_VIP_CANCEL, callback_data=CB_ADMIN_VIP_CANCEL)]
    )
    return InlineKeyboardMarkup(rows)


def get_policy_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BTN_POLICY_ACCEPT, callback_data=CB_POLICY_ACCEPT)]]
    )


def get_marketing_offer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(BTN_MARKETING_YES, callback_data=CB_MARKETING_YES),
                InlineKeyboardButton(BTN_MARKETING_NO, callback_data=CB_MARKETING_NO),
            ]
        ]
    )


def get_policy_keyboard(*, with_marketing: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if with_marketing:
        rows.append(
            [
                InlineKeyboardButton(BTN_MARKETING_ON, callback_data=CB_MARKETING_TOGGLE_ON),
                InlineKeyboardButton(BTN_MARKETING_OFF, callback_data=CB_MARKETING_TOGGLE_OFF),
            ]
        )
    rows.append([InlineKeyboardButton(BTN_BACK_MORE, callback_data=CB_MORE_HOME)])
    return InlineKeyboardMarkup(rows)


def get_marketing_unsubscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BTN_MARKETING_UNSUB, callback_data=CB_MARKETING_UNSUB)]]
    )


def get_admin_home_keyboard() -> InlineKeyboardMarkup:
    """Главное меню режима бога (/god)."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(ADMIN_BTN_HOME_INBOX, callback_data=CB_ADMIN_MENU_INBOX)],
            [InlineKeyboardButton(ADMIN_BTN_HOME_LISTS, callback_data=CB_ADMIN_MENU_LISTS)],
            [
                InlineKeyboardButton(ADMIN_BTN_HOME_USERS, callback_data=CB_ADMIN_MENU_USERS),
                InlineKeyboardButton(ADMIN_BTN_HOME_BOT, callback_data=CB_ADMIN_MENU_BOT),
            ],
            [InlineKeyboardButton(ADMIN_BTN_HOME_VIP, callback_data=CB_ADMIN_MENU_VIP)],
            [InlineKeyboardButton(ADMIN_BTN_HOME_AUDIT, callback_data=CB_ADMIN_MENU_AUDIT)],
        ]
    )


def get_admin_inbox_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(ADMIN_BTN_INBOX, callback_data=CB_ADMIN_INBOX)],
            [InlineKeyboardButton(ADMIN_BTN_INBOX_CSV, callback_data=CB_ADMIN_INBOX_CSV)],
            [InlineKeyboardButton(ADMIN_BTN_BACK, callback_data=CB_ADMIN_HOME)],
        ]
    )


def get_admin_lists_keyboard() -> InlineKeyboardMarkup:
    segments = [
        ("Бот доступен", "available"),
        ("Заблокировали бота", "bot_blocked"),
        ("Без ПДн", "no_policy"),
        ("Есть согласие", "marketing_opt_in"),
        ("Готовы к рассылке", "marketing_ready"),
        ("VIP-доступ", "vip_access"),
        ("Активные 7д", "active_7"),
        ("Активные 30д", "active_30"),
        ("Спящие", "sleeping"),
        ("Чёрный список", "admin_blocked"),
    ]
    rows = [
        [InlineKeyboardButton(ADMIN_BTN_LIST_ALL, callback_data=CB_ADMIN_LIST_ALL)],
    ]
    row: list = []
    for label, seg in segments:
        row.append(
            InlineKeyboardButton(label, callback_data=f"{CB_ADMIN_LIST_SEGMENT_PREFIX}{seg}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(ADMIN_BTN_BACK, callback_data=CB_ADMIN_HOME)])
    return InlineKeyboardMarkup(rows)


def get_admin_users_manage_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(ADMIN_BTN_USER_FIND, callback_data=CB_ADMIN_USER_FIND)],
            [InlineKeyboardButton(ADMIN_BTN_BACK, callback_data=CB_ADMIN_HOME)],
        ]
    )


def get_admin_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(BTN_CONFIRM_YES, callback_data=CB_ADMIN_CONFIRM),
                InlineKeyboardButton(BTN_CONFIRM_NO, callback_data=CB_ADMIN_CANCEL),
            ]
        ]
    )


def get_admin_batch_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(BTN_CONFIRM_YES, callback_data=CB_ADMIN_BATCH_CONFIRM),
                InlineKeyboardButton(BTN_CONFIRM_NO, callback_data=CB_ADMIN_BATCH_CANCEL),
            ]
        ]
    )


def get_admin_user_card_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(ADMIN_BTN_USER_VIP_ON, callback_data=CB_ADMIN_USER_VIP_ON),
                InlineKeyboardButton(ADMIN_BTN_USER_VIP_OFF, callback_data=CB_ADMIN_USER_VIP_OFF),
            ],
            [
                InlineKeyboardButton(ADMIN_BTN_USER_BLOCK, callback_data=CB_ADMIN_USER_BLOCK),
                InlineKeyboardButton(ADMIN_BTN_USER_UNBLOCK, callback_data=CB_ADMIN_USER_UNBLOCK),
            ],
            [InlineKeyboardButton(ADMIN_BTN_BACK, callback_data=CB_ADMIN_USER_BACK)],
        ]
    )


def get_admin_angels_keyboard() -> InlineKeyboardMarkup:
    return get_admin_inbox_keyboard()


def get_admin_bot_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(ADMIN_BTN_USERS, callback_data=CB_ADMIN_USERS)],
            [InlineKeyboardButton(ADMIN_BTN_STATUS, callback_data=CB_ADMIN_STATUS)],
            [InlineKeyboardButton(ADMIN_BTN_BACK, callback_data=CB_ADMIN_HOME)],
        ]
    )


def get_admin_audit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(ADMIN_BTN_AUDIT, callback_data=CB_ADMIN_AUDIT)],
            [InlineKeyboardButton(ADMIN_BTN_AUDIT_CSV, callback_data=CB_ADMIN_AUDIT_CSV)],
            [InlineKeyboardButton(ADMIN_BTN_BACK, callback_data=CB_ADMIN_HOME)],
        ]
    )


def get_admin_vip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(ADMIN_BTN_VIP, callback_data=CB_ADMIN_VIP)],
            [
                InlineKeyboardButton(ADMIN_BTN_VIP_ADD, callback_data=CB_ADMIN_VIP_ADD),
                InlineKeyboardButton(ADMIN_BTN_VIP_EXPORT, callback_data=CB_ADMIN_VIP_EXPORT),
            ],
            [InlineKeyboardButton(ADMIN_BTN_VIP_IMPORT, callback_data=CB_ADMIN_VIP_IMPORT)],
            [InlineKeyboardButton(ADMIN_BTN_BACK, callback_data=CB_ADMIN_HOME)],
        ]
    )


def get_admin_inline_keyboard() -> InlineKeyboardMarkup:
    """Совместимость: корень режима бога."""
    return get_admin_home_keyboard()


def get_contact_inline_keyboard(*, with_back: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("💬 Написать автору", url=URL_AUTHOR)],
    ]
    if with_back:
        rows.append([InlineKeyboardButton(BTN_BACK_MORE, callback_data=CB_MORE_HOME)])
    return InlineKeyboardMarkup(rows)


def get_store_inline_keyboard() -> InlineKeyboardMarkup:
    """Ссылка на каталог с UTM-метками для Яндекс Метрики."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BTN_STORE_OPEN, url=URL_CATALOG)]]
    )


def get_services_inline_keyboard(*, with_back: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                'Целительская энергия — "ЛАН ТАРОС"',
                url=URL_SERVICE_LAN_TAROS,
            )
        ],
        [
            InlineKeyboardButton(
                'Энергетическая техника — "НИА ТА НЭ"',
                url=URL_SERVICE_NIA_TA_NE,
            )
        ],
        [
            InlineKeyboardButton(
                "Записаться / Задать вопрос",
                url=URL_AUTHOR,
            )
        ],
    ]
    if with_back:
        rows.append([InlineKeyboardButton(BTN_BACK_MORE, callback_data=CB_MORE_HOME)])
    return InlineKeyboardMarkup(rows)

UNKNOWN_COMMAND_HINT = (
    "<b>Не понял запрос.</b>\n\n"
    "Попробуй так:\n"
    f"• {BTN_TODAY} — карта дня, ангельские знаки, кубик\n"
    "• Напиши число или время: <code>7</code>, <code>777</code>, "
    "<code>11:11</code>\n"
    f"• {BTN_VIP} — доступ к колодам\n"
    f"• {BTN_STORE} — каталог\n"
    f"• {BTN_MORE} — погода, луна, услуги, FAQ"
)


def get_bot_commands() -> list[BotCommand]:
    """Синее «☰»: внешние ссылки + старт + политика (без дублей reply)."""
    return [
        BotCommand("start", "Главное меню"),
        BotCommand("store", "Каталог Maranius"),
        BotCommand("learning", "Курсы/Практики"),
        BotCommand("contact", "Связь с автором"),
        BotCommand("policy", "Политика конфиденциальности"),
    ]


def get_main_keyboard(*, show_marketing_subscribe: bool = False) -> ReplyKeyboardMarkup:
    """Постоянная нижняя навигация стенда Maranius."""
    rows = [
        [KeyboardButton(BTN_TODAY), KeyboardButton(BTN_VIP)],
        [KeyboardButton(BTN_STORE), KeyboardButton(BTN_MORE)],
    ]
    if show_marketing_subscribe:
        rows.append([KeyboardButton(BTN_MARKETING_ON)])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_today_inline_keyboard() -> InlineKeyboardMarkup:
    """Экран «Сегодня»: ангел, карта, кубик."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👼 Ангельские знаки", callback_data=CB_TODAY_ANGEL)],
            [InlineKeyboardButton("🃏💎 Карта/Кристалл дня", callback_data=CB_TODAY_CARD)],
            [InlineKeyboardButton("🎲 Бросить кубик", callback_data=CB_TODAY_DICE)],
        ]
    )


def get_card_hub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🃏 Карта дня", callback_data=CB_CARD_PULL)],
            [InlineKeyboardButton("💎 Кристалл дня", callback_data=CB_CRYSTAL_PULL)],
            [InlineKeyboardButton(BTN_BACK_TODAY, callback_data=CB_TODAY_HOME)],
        ]
    )


def get_card_hub_back_keyboard() -> InlineKeyboardMarkup:
    """После выдачи карты/кристалла — вернуться к выбору."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BTN_BACK_TODAY, callback_data=CB_CARD_BACK)]]
    )


def get_more_inline_keyboard() -> InlineKeyboardMarkup:
    """Подменю «Ещё» — без смены нижней reply-клавиатуры."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌤 Погода", callback_data=CB_WEATHER),
                InlineKeyboardButton("🌙 Луна", callback_data=CB_MOON),
            ],
            [
                InlineKeyboardButton("Услуги", callback_data=CB_SERVICES),
                InlineKeyboardButton("Курсы/Практики", callback_data=CB_LEARNING),
            ],
            [
                InlineKeyboardButton("Инфо / FAQ", callback_data=CB_INFO),
                InlineKeyboardButton("Политика", callback_data=CB_POLICY),
            ],
            [InlineKeyboardButton("⚙️ Настройки профиля", callback_data=CB_MORE_PROFILE)],
        ]
    )


def get_profile_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(BTN_PROFILE_STATUS, callback_data=CB_PROFILE_STATUS)],
            [InlineKeyboardButton(BTN_PROFILE_SUBS, callback_data=CB_PROFILE_SUBS)],
            [InlineKeyboardButton(BTN_BACK_MORE, callback_data=CB_MORE_HOME)],
        ]
    )


def get_profile_subs_keyboard(*, marketing_opt_in: bool) -> InlineKeyboardMarkup:
    if marketing_opt_in:
        toggle = InlineKeyboardButton(BTN_MARKETING_OFF, callback_data=CB_PROFILE_SUB_OFF)
    else:
        toggle = InlineKeyboardButton(BTN_MARKETING_ON, callback_data=CB_PROFILE_SUB_ON)
    return InlineKeyboardMarkup(
        [
            [toggle],
            [InlineKeyboardButton(BTN_BACK_MORE, callback_data=CB_MORE_PROFILE)],
        ]
    )


def get_profile_status_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BTN_BACK_MORE, callback_data=CB_MORE_PROFILE)]]
    )


def get_weather_share_keyboard() -> ReplyKeyboardMarkup:
    """Запрос GPS — временная reply-клавиатура."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_WEATHER_SHARE_LOC, request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
