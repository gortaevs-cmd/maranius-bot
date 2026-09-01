# Реестр данных Maranius (поля и сущности)

Дополнение к [REGISTRY.md](./REGISTRY.md). Описывает **что уже пишется в файлы** и **что планируется** — без привязки к конкретным функциям (обновляйте при изменении моделей).

**Индекс документации:** [README.md](./README.md) (карта модулей и ссылок).

---

## Легенда

- **Реализовано** — код уже заполняет эти ключи.
- **План** — согласовано или предложено; записи в файлах может ещё не быть.
- **Идея** — на рассмотрении.

---

## `users.json` (запись на одного пользователя Telegram)

**Статус сущности:** реализовано (`integrations/user_registry.py`, `bot.ensure_user_saved`).

| Поле | Тип / примечание | Статус |
|------|------------------|--------|
| `id` | int, Telegram user id | реализовано |
| `username` | str \| null | реализовано |
| `first_name`, `last_name` | str | реализовано |
| `language_code` | str | реализовано |
| `is_premium` | bool | реализовано |
| `first_seen`, `last_seen` | ISO UTC строка | реализовано |
| `last_location` | `{ lat, lon, updated_at }` | реализовано (при геосообщении) |
| `timezone` | str (IANA), из координат | реализовано (при геосообщении) |
| `daily_practice` | `{ date_local, card?, crystal?, dice? }` | реализовано; дата по `timezone` пользователя, fallback `Europe/Moscow`; старое `date_msk` читается при миграции |
| `vip`, `vip_granted_at` | bool, ISO | реализовано |
| `vip_source` | `code` \| `admin_grant` \| `import` \| `seed_admin` | реализовано |
| `vip_revoked_at` | ISO | реализовано (при снятии VIP) |
| `policy_accepted_at`, `policy_version`, `personal_data_consent_action` | ISO, версия согласия на ПДн `2026-08-31-v1.0`, callback активного действия | реализовано |
| `user_agreement_accepted_at`, `user_agreement_version`, `user_agreement_accept_action` | ISO, версия пользовательского соглашения `2026-08-31-v1.0`, отдельный callback акцепта | реализовано |
| `marketing_opt_in`, `marketing_opt_in_at` | bool, ISO — последнее положительное маркетинговое согласие | реализовано |
| `marketing_consent_version`, `marketing_consent_action` | версия `2026-08-31-v1.0`, callback активного opt-in | реализовано |
| `marketing_offer_shown_at`, `marketing_offer_version` | ISO, версия показанного предложения рассылки | реализовано |
| `bot_status` | `active` \| `blocked` | реализовано (`my_chat_member`) |
| `blocked_at`, `unsubscribed_at`, `resubscribed_at` | ISO | реализовано |
| `admin_blocked`, `admin_blocked_at` | bool, ISO | реализовано |
| `is_internal` | bool — тест/seed, исключать из рассылок и «чистой» статистики | реализовано |
| `marketing_opt_out_at`, `marketing_opt_out_action` | ISO, callback — отказ от рассылки (дата подписки не перезаписывается) | реализовано |
| `start_param`, `start_param_at` | str, ISO — deep-link `/start`, пишется один раз после согласия на ПДн | реализовано |
| `legacy_import_source`, `legacy_imported_at` | источник и дата технического переноса старого контакта; не являются согласием | реализовано |

**Актуальные документы `@MaraniusBOT`:** [Политика конфиденциальности](https://maranius.ru/legal/privacy-policy/), [согласие на обработку персональных данных](https://maranius.ru/legal/personal-data-consent/), [согласие на рекламные сообщения](https://maranius.ru/legal/marketing-consent/) и [Пользовательское соглашение](https://maranius.ru/legal/user-agreement/). Согласия старых версий не переводятся на новую версию задним числом.

---

## `legacy_inactive_users.json`

**Статус:** реализовано (`integrations/legacy_contacts.py`); в production лежит в `/app/.runtime` и исключён из Git.

Отдельный минимальный список старых контактов, которых нет в верифицированной выгрузке доставки и которые не имеют VIP. Запись: `id`, `source`, `listed_at`. Эти записи не становятся пользователями Maranius и не входят в маркетинговые сегменты. После возвращения в бот и принятия актуальных согласия на ПДн и пользовательского соглашения ID атомарно удаляется из этого списка, профиль сохраняется активным в `users.json`, а seed-админу уходит одно уведомление.

Перед production-деплоем в том же runtime можно положить только-ID манифест `legacy_migration_pending.json`. Новый процесс бота применяет его до запуска polling, пишет результат вместе с манифестом в `legacy_migration_last_result.json` и удаляет pending-файл. Это исключает гонку с обычными обновлениями `users.json`.

---

## `consent_log.json`

**Статус:** реализовано (`integrations/consent_log.py`); в production лежит в `/app/.runtime`.

Append-only журнал согласий и акцептов. Запись: `id`, `created_at`, `user_id`, `event`
(`policy_accepted`, `user_agreement_accepted`, `marketing_opt_in`, `marketing_opt_out`), `value`, `purpose`,
`document`, `policy_version`, `document_url`, `action`, `source`, `meta`. Так фиксируются
назначение, редакция документа, URL и конкретное активное действие пользователя.

Retention: **365 дней**, максимум **50 000** записей. Текущие поля в `users.json` — кэш
для сегментов; юридически значимая история — в этом журнале. В `/god → Журнал действий`:
последние 10 записей и CSV-выгрузка.

---

## `data/inbox.json`

**Статус:** реализовано (`integrations/inbox.py`).

Запись в `entries[]`:

| Поле | Назначение |
|------|------------|
| `id` | uuid (12 hex) |
| `created_at` | ISO UTC |
| `type` | `unknown_angel`, `unknown_command`, `duplicate_vip_code`, … |
| `user_id`, `username`, `text` | кто и что |
| `meta` | объект (normalized, original_user_id, …) |
| `exported_at` | ISO — последняя выгрузка CSV |
| `admin_notified_at` | ISO — уведомление seed-admin |

Retention: **90 дней**. Миграция из `data/angelic/unknown_angelic.csv` при старте бота.

---

## `data/activity_events.json` / `data/activity_aggregates.json`

**Статус:** реализовано (`integrations/analytics.py`).

- Сырые события: `{ ts, user_id, section }`, retention 90 дней.
- Агрегаты: weekly/monthly сводки для отчётов.

---

## `admins.json`

**Статус:** реализовано.

```json
{ "admins": [123456789] }
```

**/god и алерты:** только `SEED_ADMIN_IDS` (186758977).

---
## `admin_audit.json`

**Статус:** реализовано (`integrations/admin_audit.py`); в production лежит в `/app/.runtime`.

Каждая запись в `events[]` содержит `id`, `created_at`, `actor_id`, `action`,
`target_ids`, обязательное `reason` (до 200 символов) и безопасный `meta` с итогами
пакетной операции. Фиксируются выдача/снятие VIP, ручное ограничение, решения по
VIP-алертам, импорт VIP и добавление кодов.

Retention: **365 дней**, максимум **20 000 записей**. Доступны последние действия и
контролируемая CSV-выгрузка только в `/god` для seed-админа.

---
## `data/platform_users.json`

**Статус:** реализовано (`integrations/platform_db.py`).

Корень:

- `users` — объект `uuid -> запись`
- `by_email`, `by_telegram` — индексы

Запись пользователя (`users[id]`):

| Поле | Статус |
|------|--------|
| `id`, `email`, `telegram_id` | реализовано |
| `name`, `phone` | реализовано |
| `created_at`, `updated_at` | реализовано |
| `sources` | реализовано (`telegram`, …) |
| `metadata` | реализовано (объект, по умолчанию `{}`) |

---
## `data/user_courses.json`

**Статус:** реализовано.

Элементы массива `enrollments`:

| Поле | Статус |
|------|--------|
| `user_id`, `course_id`, `course_name`, `status`, `source`, `enrolled_at` | реализовано |

---

## `events.json`

**Статус:** реализовано (`events/storage.py`) — события **групп/каналов**.

Событие: `id`, `type`, `timestamp`, `chat`, `user`, `meta`.

---

## Сегменты для CSV (`/god → Списки`)

| Сегмент | Критерий |
|---------|----------|
| `available` | бот не заблокирован пользователем (`bot_status != blocked`) |
| `bot_blocked` | пользователь заблокировал бот (`bot_status == blocked`) |
| `no_policy` | нет `policy_accepted_at` или устаревший `policy_version` |
| `with_policy` | актуальная политика принята |
| `marketing_opt_in` | есть отдельное согласие на маркетинговые сообщения текущей версии |
| `marketing_ready` | отдельное маркетинговое согласие, согласие на ПДн и акцепт пользовательского соглашения текущих версий; бот доступен, нет ручного стоп-листа и `is_internal` |
| `vip_access` | `vip == true`, то есть доступ, а не подтверждённая покупка |
| `active_7` / `active_30` | `last_seen` ≤ 7 / 30 дней |
| `sleeping` | `last_seen` > 30 дней |
| `admin_blocked` | `admin_blocked` |

Старые технические имена `subscribed`, `unsubscribed`, `marketing` и `vip` сохранены
для совместимости, но в интерфейсе больше не показываются.

---

## Журнал DATA_REGISTRY

| Дата | Изменение |
|------|-----------|
| 2026-04-03 | Первичное описание существующих JSON и плановых полей |
| 2026-08-15 | users: согласия, bot_status, inbox, analytics, сегменты, zenclass stub |
| 2026-08-15 | Убраны Zenclass-поля и индексы; API-эндпоинты SmartBotPro удалены; fastapi/uvicorn убраны из зависимостей |
| 2026-08-26 | consent_log, marketing_opt_out_at, start_param, re-consent по policy_version, расширенный CSV; tags убран |
| 2026-08-31 | согласия и ссылки переведены на опубликованные документы maranius.ru; разделены версии ПДн и маркетинга, журнал дополнен документом и активным действием |
| 2026-08-31 | доступ требует раздельных: акцепта пользовательского соглашения и согласия на ПДн; акцепт версионируется и журналируется отдельно |
| 2026-09-01 | Контролируемая миграция старых контактов: активный пул, сохранение VIP, отдельный список неактивных и уведомление о возвращении после gate |
