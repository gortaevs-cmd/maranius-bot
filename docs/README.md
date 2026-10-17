# Документация Maranius

Точка входа для навигации по проекту и документам.

---

## Код и модули

| Модуль | Путь | Назначение |
|--------|------|------------|
| Точка входа бота | [`../bot.py`](../bot.py) | Telegram long polling, регистрация маршрутов, сборка зависимостей и общие пользовательские сценарии |
| Административная панель | [`../handlers/admin.py`](../handlers/admin.py) | `/god`, admin callback, ввод причин, подтверждения, CSV и журнал действий |
| Погода и луна | [`../handlers/weather.py`](../handlers/weather.py) | Геолокация, прогноз, кэш и лунный блок |
| Отчёты | [`../handlers/reports.py`](../handlers/reports.py) | Недельная/месячная сводка и расписание |
| Тексты и клавиатуры | [`../ui.py`](../ui.py) | Константы `BTN_*`, сообщения, сборка клавиатур |
| Платформа пользователей | [`../integrations/platform_db.py`](../integrations/platform_db.py) | `data/platform_users.json`, курсы |
| Хранение JSON | [`../integrations/json_storage.py`](../integrations/json_storage.py) | Атомарная запись/восстановление, обязательные backup, отменобезопасный ввод-вывод |
| Очередь уведомлений | [`../integrations/notification_outbox.py`](../integrations/notification_outbox.py) | Сохранение уведомлений владельцам, отложенные повторы и отметка доставки Inbox |
| Реферальная программа | [`../integrations/referrals.py`](../integrations/referrals.py) | Коды, qualification, ledger и заявки на награды |
| События чатов | [`../events/`](../events/) | Подписки, реакции, `events.json` |

---

## Документы в этой папке

| Файл | Содержание |
|------|------------|
| [REGISTRY.md](./REGISTRY.md) | Реестр команд, кнопок, callback, переменных `.env`, файлов данных, статусы план/готово |
| [DATA_REGISTRY.md](./DATA_REGISTRY.md) | Поля JSON (`users`, `platform_users`, планируемые заказы/заявки) |
| [AUDIT_IMPROVEMENTS.md](./AUDIT_IMPROVEMENTS.md) | Блоки технического аудита, проверенные публикации и оставшиеся работы |
| [REFERRAL_PROGRAM.md](./REFERRAL_PROGRAM.md) | Механика рефералов, антифрод, начисления и списание баллов |
| [CRM_АНАЛИТИКА_И_РАССЫЛКИ.md](./CRM_АНАЛИТИКА_И_РАССЫЛКИ.md) | Целевая база клиентов, VIP-сегментация, кампании, Twenty/n8n и backup-политика |
| [ИНТЕРФЕЙС_МЕНЮ_И_КОМАНДЫ.md](./ИНТЕРФЕЙС_МЕНЮ_И_КОМАНДЫ.md) | Меню Telegram и команды |
| [smartbot/](./smartbot/) | Исторические выгрузки SmartBot Pro: EWASMLBOT, KryonCrystalsBot (JSON + памятки) |
| [SMARTBOT_SCENARIO_ПАМЯТКА.md](./SMARTBOT_SCENARIO_ПАМЯТКА.md) | Старая памятка EWASML; актуальная — [smartbot/EWASMLBOT_ПАМЯТКА.md](./smartbot/EWASMLBOT_ПАМЯТКА.md) |

---

## Документы рядом с проектом

Папка на два уровня выше от этого файла: `Боты_ИИ_автоматизация/` (родитель каталога `maranius/`).

| Файл | Ссылка |
|------|--------|
| Деплой бота (git pull, сервер) | [ИНСТРУКЦИЯ_ДЕПЛОЙ_БОТА.md](../../ИНСТРУКЦИЯ_ДЕПЛОЙ_БОТА.md) |
| Статус ботов | [мои_боты_статус.md](../../мои_боты_статус.md) |

Корень репозитория Maranius — каталог `maranius/` (где лежат `bot.py`, `.env.example`, `requirements.txt`).

---

## Быстрые ссылки из корня репозитория

- [README.md](../README.md) — установка, запуск, `.env`
- [`.env.example`](../.env.example) — шаблон переменных окружения
- [`.github/workflows/tests.yml`](../.github/workflows/tests.yml) — автоматические тесты GitHub Actions
