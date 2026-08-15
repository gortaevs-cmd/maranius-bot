# Памятка: EWASMLBOT (Smartbot → Maranius)

- **Бот Telegram:** `@EWASMLBOT`
- **Кабинет:** `karmannyj_marketolo.smartbotpro.ru`
- **project_id:** `644d1628a1ec0b5089449c8e`
- **scenario_id:** `644e3e0d349912cf2abfea20`
- **Выгрузка JSON:** [`EWASMLBOT_blocks.json`](./EWASMLBOT_blocks.json) (`/api/blocks/list`)
- **Блоков:** 119
- **Уникальных кнопок (текст + тип + цель):** 63
- **Дата фиксации:** 2026-08-08

---

## 0. Карта разделов (человекочитаемо)

```
/start → приветствие → notify менеджеру
/learning → курсы / кристаллы / карты / кубик
/store → лавка (сайт + shopback в сценарий магазина)
/services → услуги + запись @maraniuss
/info → ангелы-инструкция, соцсети, FAQ, отзывы, рефералка
/vip → проверка %VIP_клиент% → инструкции колод / ритуалы
/policy → политика ПДн
/refstat → только тест (whitelist user_id)

Без команды:
  • числа/время → Ангельский помощник (HTTP)
  • VIP-коды → доступ VIP
  • карта дня (колоды Подсказки / Мудрость рода)
```

## 1. Слэш-команды

| Команда | Имя input-блока | Первый block_id после |
|---------|-----------------|------------------------|
| `/info` | info - Информация | `adb054f52b17a49148b425f2` |
| `/learning` | learning - Практики и курсы | `ac4469e4a1fd5c5b20656eec` |
| `/policy` | policy - Политика обработки персональных данных | `3d01f95ec4bfd6015a65af40` |
| `/refstat` | — | `287dfd6cb308a9d66c1d1dc7` |
| `/services` | services - Услуги Maranius | `a84861063b1d8e4a6e318de9` |
| `/store` | store - Магическая лавка | `d70bb7e0f3327f1a7052ac08` |
| `/vip` | vip - Для владельцев колод | `86332218b105c7ef7c9fb893` |

## 2. Типы блоков в выгрузке

| type/kind | Кол-во |
|-----------|--------|
| `action/add_row_google_sheets` | 2 |
| `action/add_to_list` | 1 |
| `action/ask_gpt` | 1 |
| `action/communicate_ai` | 2 |
| `action/exec_sq` | 4 |
| `action/http_request` | 3 |
| `action/notify_managers` | 4 |
| `action/portal_out` | 1 |
| `action/send_message` | 65 |
| `action/set_vars` | 1 |
| `action/switch_context` | 2 |
| `action/tg_send_dice` | 1 |
| `action/user_input` | 6 |
| `condition/basic` | 7 |
| `condition/is_group_member` | 1 |
| `input/chat_message` | 16 |
| `input/default_answer` | 1 |
| `input/first_message` | 1 |

## 3. HTTP и внешние интеграции

- **Ангельские знаки (обработка):** `https://ewasml.maranius.ru/EWASML/ANGELIC_SIGNS/bot.php?key=%key%`
- **5547793eac1fee4632eb68aa:** `https://ewasml.maranius.ru/EWASML/cardsBotUniwerse/cardsBotUniwerse.php`
- **b21230531b5f0345d2c9761c:** `https://ewasml.maranius.ru/EWASML/cardsBotmudrostRoda/cardsBotmudrostRoda.php`

## 4. Порталы (другие сценарии)

- **Предварительный заказ** → сценарий `🚚 Магазин в Telegram с доставкой (MagicLawka)` (`681ca563c5b5a528f8b9002f`)

## 5. Кнопки и цели

Для `transition` — целевой блок. Полные тексты и HTML — в JSON.

| Кнопка | Тип | Цель | Результат |
|--------|-----|------|-----------|
| "Искры Женской Божественности" | `transition` | `31446dd83e6c885f56c7511f` | **Инструкция колоды: "Искры Женской Божественности"** (`send_message`) |
| "Кристаллы Атлантиды (Крайона)" | `transition` | `67769abac84186e2aaa1d58c` | **Колода: Кристаллы Атлантиды (Крайона)** (`send_message`) |
| "Тень души" | `transition` | `67609305971928389c4e4e0f` | **Инструкция колоды: "Тень души"** (`send_message`) |
| FAQ | `transition` | `c7a997f2698caec041bba938` | `send_message` / `action` |
| Instagram* | `open_url` | `https://www.instagram.com/esotericsworlds/` | Открыть URL: `https://www.instagram.com/esotericsworlds/` |
| Shop Magiclawka | `open_url` | `https://magiclawka.com/` | Открыть URL: `https://magiclawka.com/` |
| Shop Maranius | `open_url` | `https://maranius.ru/rituals/` | Открыть URL: `https://maranius.ru/rituals/` |
| TenChat | `open_url` | `https://tenchat.ru/maranius?utm_source=72c1b215-114b-4c1f-b8` | Открыть URL: `https://tenchat.ru/maranius?utm_source=72c1b215-114b-4c1f-b841-2702bef6b81b` |
| Threads* | `open_url` | `https://www.threads.net/@maraniuss` | Открыть URL: `https://www.threads.net/@maraniuss` |
| Ангельский помощник | `open_url` | `https://telegra.ph/Angelskie-znaki-08-03` | Открыть URL: `https://telegra.ph/Angelskie-znaki-08-03` |
| Бросить кубик на удачу | `transition` | `49725e2dd779fbdf06d643ad` | **Бросаем кубик** (`tg_send_dice`) |
| В раздел для VIP | `transition` | `cf05be2e78c8ba34c2957b68` | **Добро пожаловать в раздел для VIP!** (`send_message`) |
| Варианты раскладов | `transition` | `980ce170c3ffe79c2c3e20d2` | **Варианты раскладов "Тень души"** (`send_message`) |
| ВКонтакте | `open_url` | `https://vk.com/maranius` | Открыть URL: `https://vk.com/maranius` |
| Все карты | `transition` | `66b1d0748c4ad2733331c002` | **Искры Женской Божественности - от А до Э** (`send_message`) |
| Все карты | `transition` | `6760968e93a6c3a6530552b4` | **Инструкция колоды: "Тень души" все карты** (`send_message`) |
| Вытащить карту | `transition` | `dc44301cd8633fa4e10eb840` | `send_message` / `action` |
| Вытащить карту | `transition` | `964e59de2094bd5968259c66` | `send_message` / `action` |
| ГОТОВО | `transition` | `d53375351bd3d91aff4f1edd` | `is_group_member` / `condition` |
| Записаться/Задать вопрос | `transition` | `818b938c96eb9bf0d5bb930d` | **Записаться/Задать вопрос** (`send_message`) |
| Заработать/Потратить баллы | `transition` | `—` | — |
| Инструкция | `open_url` | `https://telegra.ph/Angelskie-znaki-08-03` | Открыть URL: `https://telegra.ph/Angelskie-znaki-08-03` |
| Карты от А до И | `transition` | `56def9147a96c97f825fe613` | **Искры Женской Божественности - от А до И** (`send_message`) |
| Карты от К до П | `transition` | `66b0e7284f31ff3ddaaaeab7` | **Искры Женской Божественности - от К до П** (`send_message`) |
| Карты от Р до Э | `transition` | `66b0e6f58c4ad2733331ab08` | **Искры Женской Божественности - от Р до Э** (`send_message`) |
| Колода "Кристаллы" | `transition` | `66584b476ace4da341e7831a` | **Колода "Кристаллы"** (`send_message`) |
| Колода "Мудрость рода" | `transition` | `66584aa36ace4da341e782e0` | **Колода "Мудрость рода"** (`send_message`) |
| Колода "Подсказки Вселенной" | `transition` | `665849ca6ace4da341e782ba` | **Колода "Подсказки Вселенной"** (`send_message`) |
| Младшие арканы | `transition` | `6760987d7eff4be4b23bd314` | **Инструкция колоды: "Тень души" Младшие арканы** (`send_message`) |
| Моя статистика + ссылка реферера | `transition` | `5c1187ac2dca2b7b6eeaddc3` | `send_message` / `action` |
| Обратная связь | `transition` | `c3bff28ae9745324a28b2e90` | **Обратная связь** (`send_message`) |
| Онлайн-курсы | `open_url` | `https://school.maranius.ru/` | Открыть URL: `https://school.maranius.ru/` |
| Поддержать проект | `transition` | `16018a122dcba0687d121cf0` | `send_message` / `action` |
| Политика обработки персональных данных | `open_url` | `https://telegra.ph/Politika-obrabotki-personalnyh-dannyh-08-` | Открыть URL: `https://telegra.ph/Politika-obrabotki-personalnyh-dannyh-08-03` |
| ПОЛОЖЕНИЕ/ПРАВИЛА | `transition` | `a5d52335b3be57bafbbc2488` | `send_message` / `action` |
| Получить ссылку для приглашения | `transition` | `e0702c0150f0bd283d34879c` | `send_message` / `action` |
| Посмотреть каталог в ТГ | `shopback` | `bb35ee0dd631b115cb3148e4` | **shopback** → **Предварительный заказ** (`portal_out`) |
| Путь света через кристаллы Крайона | `transition` | `9da0d097615695f2020d7df1` | `send_message` / `action` |
| Работа с картами | `transition` | `873dfc29172a3e16e99cfadf` | **Список колод** (`send_message`) |
| Расклады | `open_url` | `https://telegra.ph/Koloda-kart-Mudrost-Roda-08-03` | Открыть URL: `https://telegra.ph/Koloda-kart-Mudrost-Roda-08-03` |
| Расклады | `open_url` | `https://telegra.ph/Koloda-kart-Podskazki-Vselennoj-08-03` | Открыть URL: `https://telegra.ph/Koloda-kart-Podskazki-Vselennoj-08-03` |
| Реферальная программа | `transition` | `a8981778adb67e6fc7be1f51` | `send_message` / `action` |
| СОГЛАСИЕ НА ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ | `open_url` | `https://telegra.ph/SOGLASIE-NA-OBRABOTKU-PERSONALNYH-DANNYH-` | Открыть URL: `https://telegra.ph/SOGLASIE-NA-OBRABOTKU-PERSONALNYH-DANNYH-05-31-2` |
| Соцсети | `transition` | `e6fc35c26eae789f4cbe70c4` | **Социальные сети — Esoterics Worlds** (`send_message`) |
| Старшие арканы | `transition` | `67609809971928389c4e4e8a` | **Инструкция колоды: "Тень души" Старшие арканы** (`send_message`) |
| Телеграм Канал | `open_url` | `https://t.me/+o9krj4BOENM0NGNi` | Открыть URL: `https://t.me/+o9krj4BOENM0NGNi` |
| Целительская энергия - "ЛАН ТАРОС" | `open_url` | `https://telegra.ph/Celitelskie-uslugi-LAN-TAROS-08-04` | Открыть URL: `https://telegra.ph/Celitelskie-uslugi-LAN-TAROS-08-04` |
| Энергетическая техника - "НИА ТА НЭ" | `open_url` | `https://telegra.ph/Celitelskie-uslugi-Nia-Ta-Neh-08-04` | Открыть URL: `https://telegra.ph/Celitelskie-uslugi-Nia-Ta-Neh-08-04` |
| Яндекс Дзен | `open_url` | `https://dzen.ru/maranius?share_to=link` | Открыть URL: `https://dzen.ru/maranius?share_to=link` |
| ⚔️Мечи | `transition` | `67609ccb7eff4be4b23bd387` | **Инструкция колоды: "Тень души" Мечи** (`send_message`) |
| ⚕️Жезлы | `transition` | `67609d0d7eff4be4b23bd392` | **Инструкция колоды: "Тень души" Жезлы** (`send_message`) |
| ⬅️ Назад | `transition` | `31446dd83e6c885f56c7511f` | **Инструкция колоды: "Искры Женской Божественности"** (`send_message`) |
| ⬅️ Назад | `transition` | `cf05be2e78c8ba34c2957b68` | **Добро пожаловать в раздел для VIP!** (`send_message`) |
| ⬅️ Назад | `transition` | `67609305971928389c4e4e0f` | **Инструкция колоды: "Тень души"** (`send_message`) |
| ⬅️ Назад | `transition` | `6760987d7eff4be4b23bd314` | **Инструкция колоды: "Тень души" Младшие арканы** (`send_message`) |
| ⬅️ Назад | `transition` | `873dfc29172a3e16e99cfadf` | **Список колод** (`send_message`) |
| ⬅️ Назад | `transition` | `41178fc382a3d3badbeaa917` | `send_message` / `action` |
| 🃏 ИНСТРУКЦИИ ДЛЯ КОЛОД | `transition` | `41178fc382a3d3badbeaa917` | `send_message` / `action` |
| 🏆Кубки | `transition` | `67609c6993a6c3a653055331` | **Инструкция колоды: "Тень души" Кубки** (`send_message`) |
| 🏆Кубки | `transition` | `—` | — |
| 🔮ИНСТРУКЦИИ ДЛЯ РИТУАЛОВ | `transition` | `453a7b6fcfdc62f2094e98c8` | `send_message` / `action` |
| 🟢 Книга в PDF | `transition` | `417916241e41a1de45e9dc49` | **Книжечка Тень Души** (`send_message`) |
| 🪙Пентакли | `transition` | `67609c8f7eff4be4b23bd381` | **Инструкция колоды: "Тень души" Пентакли** (`send_message`) |

## 6. Переменные `%…%` в выгрузке

`%accepted_input_codes%`, `%birthdate%`, `%card%`, `%channel_kind%`, `%chat_title%`, `%code_index%`, `%codes_available%`, `%codes_used%`, `%date%`, `%datetime%`, `%first_name%`, `%global_ref_bot_subscribed%`, `%global_ref_users%`, `%IJB091125%`, `%input_codes_used%`, `%is_digits%`, `%is_num%`, `%is_time%`, `%key%`, `%last_name%`, `%len%`, `%liked_type%`, `%max_input_confidence%`, `%message_text%`, `%messages_received%`, `%my_code%`, `%points_balance%`, `%points_spent%`, `%points_total%`, `%raw%`, `%realm%`, `%ref%`, `%ref_subscribed_bot%`, `%referral_balance%`, `%referral_locked%`, `%referral_points%`, `%referral_registered_at%`, `%referral_spent%`, `%referrals_total%`, `%referred_by%`, `%referrer_active%`, `%reward_bot_given%`, `%reward_bot_points%`, `%sex%`, `%SHD091125%`, `%shopback_text%`, `%shopback_total_str%`, `%SOUL311225%`, `%t1%`, `%t2%`, `%TDO301025%`, `%tg_send_dice_value%`, `%time%`, `%user_code%`, `%user_id%`, `%user_oid%`, `%username%`, `%value%`, `%VIP_клиент%`, `%weekday%`, `%ИЖБ%`, `%ИскрыЖБ%`, `%Тень_Души%`

## 7. Замечания / поломки

- Кнопка «🏆Кубки» в блоке «Инструкция колоды: "Тень души" Пентакли»: `block_id` = null
- Кнопка «Заработать/Потратить баллы» в блоке «90df7816788aab2989e89512»: `block_id` = null

## 8. Заметки для переноса в Maranius

- Внутренние `_id` Smartbot в новом боте не переносятся — свои состояния / `callback_data`.
- Telegraph-ссылки в VIP и картах — контент страниц не в JSON, только URL.
- Сценарий магазина (`portal_out`) нужно выгружать отдельно.
- В JSON есть VIP-коды и whitelist user_id — не коммитить в публичный репозиторий без нужды; файл локальный/приватный.
- Старая памятка: [`../SMARTBOT_SCENARIO_ПАМЯТКА.md`](../SMARTBOT_SCENARIO_ПАМЯТКА.md) (по устаревшему пути Inbox).
