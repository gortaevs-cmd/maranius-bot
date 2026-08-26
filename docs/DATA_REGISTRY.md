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
| `policy_accepted_at`, `policy_version` | ISO, `"2024-08-03"` | реализовано |
| `marketing_opt_in`, `marketing_opt_in_at` | bool, ISO | реализовано |
| `marketing_offer_shown_at` | ISO — предложение рассылки после политики | реализовано |
| `bot_status` | `active` \| `blocked` | реализовано (`my_chat_member`) |
| `blocked_at`, `unsubscribed_at`, `resubscribed_at` | ISO | реализовано |
| `admin_blocked`, `admin_blocked_at` | bool, ISO | реализовано |
| `is_internal` | bool — тест/seed, исключать из рассылок и «чистой» статистики | реализовано |
| `tags` | list[str] | идея |

**Политика (URL):** общая для ботов Maranius — [Политика](https://telegra.ph/Politika-obrabotki-personalnyh-dannyh-08-03), [Согласие](https://telegra.ph/SOGLASIE-NA-OBRABOTKU-PERSONALNYH-DANNYH-05-31-2).

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
| `no_policy` | нет `policy_accepted_at` |
| `marketing_opt_in` | есть согласие на маркетинговые сообщения |
| `marketing_ready` | согласие и политика есть, бот доступен, нет ручного стоп-листа и `is_internal` |
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
| 2026-08-26 | безопасный сегмент `marketing_ready`, аудит `/god`, подтверждение пакетных VIP-операций |
