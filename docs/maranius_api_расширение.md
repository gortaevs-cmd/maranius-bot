# Maranius API — расширение функций

**Ключевые слова:** API, HTTP, эндпоинт, FastAPI, SmartBotPro, интеграция, расширение функций, деплой, обновление

**Теги:** #maranius #api #http #fastapi #smartbotpro #интеграция #деплой

**Связанные файлы:**
- [bot.py](../bot.py) — основная логика бота
- [api.py](../api.py) — HTTP API эндпоинты
- [requirements.txt](../requirements.txt) — зависимости Python
- [мои_боты_статус.md](../../мои_боты_статус.md) — статус деплоя ботов

---

## Быстрый старт

Чеклист добавления новой функции в API:

1. ✅ Добавить/использовать функцию логики в `bot.py` (если нужна новая)
2. ✅ Добавить эндпоинт в `api.py`
3. ✅ Проверить локально через браузер/curl
4. ✅ Закоммитить и запушить в Git
5. ✅ На сервере: `git pull`
6. ✅ Перезапустить API: `systemctl restart maranius-api`
7. ✅ Проверить на сервере
8. ✅ Обновить SmartBotPro — добавить HTTP-запрос в сценарий

[Подробный процесс →](#процесс-добавления-функции)

---

## Процесс добавления функции

### Шаг 1: Добавить/использовать функцию логики в bot.py

Если функция уже есть в `bot.py` — используй её. Если нужна новая — добавь.

**Примеры существующих функций:**
- `_weather_at_coords(lat, lon, ...)` — погода
- `_get_moon_data(date)` — данные о луне
- `_fetch_rate(currency, quote)` — курс валют

**Если нужна новая функция:**

```python
# В bot.py
async def _fetch_crypto_rate(coin: str) -> Optional[float]:
    """Получить курс криптовалюты."""
    url = f"https://api.example.com/crypto/{coin}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return float(data.get("rate"))
    except Exception:
        return None
```

---

### Шаг 2: Добавить эндпоинт в api.py

Открой `api.py` и добавь новый эндпоинт:

```python
from fastapi import FastAPI
from bot import _weather_at_coords, _get_moon_data, _fetch_rate, _fetch_crypto_rate

app = FastAPI()

# Существующие эндпоинты
@app.get("/api/weather")
async def weather(lat: float, lon: float):
    result = await _weather_at_coords(lat, lon, "Локация")
    return {"text": result} if result else {"error": "Не удалось получить погоду"}

@app.get("/api/moon")
async def moon():
    today = date.today()
    data = _get_moon_data(today)
    phase_name = _moon_phase_from_data(today, data)
    lunar_day = _lunar_day_from_data(today, data)
    return {
        "phase": phase_name,
        "lunar_day": lunar_day,
        "illumination": int(round(data["illumination"] * 100)),
        "prev_new_moon": str(data["prev_new_moon"]),
        "next_new_moon": str(data["next_new_moon"]),
        "full_moon": str(data["full_moon_this_cycle"])
    }

@app.get("/api/rate")
async def rate(currency: str):
    rate_value = await _fetch_rate(currency, "RUB")
    if rate_value is None:
        return {"error": "Не удалось получить курс"}
    return {"currency": currency, "rate": rate_value, "quote": "RUB"}

# НОВЫЙ эндпоинт
@app.get("/api/crypto")
async def crypto(coin: str):
    rate_value = await _fetch_crypto_rate(coin)
    if rate_value is None:
        return {"error": "Не удалось получить курс криптовалюты"}
    return {"coin": coin, "rate": rate_value}
```

---

### Шаг 3: Проверка локально

Запусти API локально:

```bash
cd "БИЗНЕС/AIF5/06_IT_разработка_и_внедрение/Боты_ИИ_автоматизация/maranius"
source .venv/bin/activate  # или создай venv, если нет
pip install fastapi uvicorn  # если ещё не установлено
uvicorn api:app --host 127.0.0.1 --port 8000
```

Проверь в браузере или через curl:

```bash
# Луна
curl http://127.0.0.1:8000/api/moon

# Погода
curl "http://127.0.0.1:8000/api/weather?lat=55.75&lon=37.62"

# Курс
curl "http://127.0.0.1:8000/api/rate?currency=USD"

# Новый эндпоинт
curl "http://127.0.0.1:8000/api/crypto?coin=BTC"
```

---

### Шаг 4: Деплой на сервер

**На локальном компьютере:**

```bash
cd "БИЗНЕС/AIF5/06_IT_разработка_и_внедрение/Боты_ИИ_автоматизация/maranius"
git add api.py bot.py  # если менял bot.py
git commit -m "Добавлен эндпоинт /api/crypto"
git push
```

**На сервере:**

```bash
ssh root@ТВОЙ_IP
cd /opt/bots/maranius
git pull

# Если добавлял зависимости в requirements.txt:
pip install -r requirements.txt

# Перезапустить API
systemctl restart maranius-api

# Проверить статус
systemctl status maranius-api
```

**Проверка на сервере:**

```bash
curl http://ТВОЙ_IP:8000/api/crypto?coin=BTC
```

---

### Шаг 5: Обновление SmartBotPro

В сценарии SmartBotPro:

1. Добавь блок **«HTTP-запрос»** (Интеграции → HTTP-запрос)
2. Настрой:
   - **Метод:** GET
   - **URL:** `http://ТВОЙ_IP:8000/api/crypto?coin=BTC`
   - **Переменная ответа:** `%crypto_resp%` (тело ответа)
3. В сообщении пользователю используй:
   - `{{ %crypto_resp%.coin }}` — название монеты
   - `{{ %crypto_resp%.rate }}` — курс
   - Или `{{ %crypto_resp% | pretty }}` — весь JSON

---

## Примеры кода

### Пример 1: Эндпоинт погоды

```python
@app.get("/api/weather")
async def weather(lat: float, lon: float):
    """Получить погоду по координатам."""
    result = await _weather_at_coords(lat, lon, "Локация")
    if result:
        return {"text": result, "status": "ok"}
    return {"error": "Не удалось получить погоду", "status": "error"}
```

### Пример 2: Эндпоинт луны

```python
@app.get("/api/moon")
async def moon():
    """Получить информацию о луне."""
    from datetime import date
    from bot import _get_moon_data, _moon_phase_from_data, _lunar_day_from_data
    
    today = date.today()
    data = _get_moon_data(today)
    phase_name = _moon_phase_from_data(today, data)
    lunar_day = _lunar_day_from_data(today, data)
    
    return {
        "date": str(today),
        "phase": phase_name,
        "lunar_day": lunar_day,
        "illumination_percent": int(round(data["illumination"] * 100)),
        "prev_new_moon": str(data["prev_new_moon"]),
        "next_new_moon": str(data["next_new_moon"]),
        "full_moon": str(data["full_moon_this_cycle"])
    }
```

### Пример 3: Эндпоинт курса валют

```python
@app.get("/api/rate")
async def rate(currency: str):
    """Получить курс валюты."""
    from bot import _fetch_rate
    
    rate_value = await _fetch_rate(currency, "RUB")
    if rate_value is None:
        return {"error": f"Не удалось получить курс {currency}", "status": "error"}
    
    return {
        "currency": currency,
        "rate": rate_value,
        "quote": "RUB",
        "status": "ok"
    }
```

### Пример 4: Новый эндпоинт (криптовалюты)

```python
@app.get("/api/crypto")
async def crypto(coin: str):
    """Получить курс криптовалюты."""
    from bot import _fetch_crypto_rate
    
    rate_value = await _fetch_crypto_rate(coin.upper())
    if rate_value is None:
        return {"error": f"Не удалось получить курс {coin}", "status": "error"}
    
    return {
        "coin": coin.upper(),
        "rate": rate_value,
        "status": "ok"
    }
```

---

## Чеклист действий

| # | Действие | Где выполнять | Команда/Файл |
|---|----------|---------------|--------------|
| 1 | Добавить функцию логики | `bot.py` | Новый `async def _function_name()` |
| 2 | Добавить эндпоинт | `api.py` | `@app.get("/api/endpoint")` |
| 3 | Проверить локально | Локальный терминал | `uvicorn api:app --host 127.0.0.1 --port 8000` |
| 4 | Закоммитить | Локальный Git | `git add api.py bot.py && git commit -m "..."` |
| 5 | Запушить | Локальный Git | `git push` |
| 6 | Обновить на сервере | SSH на сервер | `cd /opt/bots/maranius && git pull` |
| 7 | Установить зависимости (если новые) | SSH на сервер | `pip install -r requirements.txt` |
| 8 | Перезапустить API | SSH на сервер | `systemctl restart maranius-api` |
| 9 | Проверить на сервере | SSH на сервер | `curl http://IP:8000/api/endpoint` |
| 10 | Обновить SmartBotPro | SmartBotPro интерфейс | Добавить блок «HTTP-запрос» |

---

## Частые вопросы

### Что делать при изменении только api.py?

1. Закоммитить и запушить `api.py`
2. На сервере: `git pull`
3. Перезапустить API: `systemctl restart maranius-api`

**Не нужно** перезапускать Telegram-бота.

---

### Что делать при изменении bot.py?

1. Закоммитить и запушить `bot.py` и `api.py` (если менял)
2. На сервере: `git pull`
3. Перезапустить **оба** процесса:
   - `systemctl restart maranius-api` (API)
   - `systemctl restart maranius-bot` (Telegram-бот, если есть отдельный сервис)

---

### Что делать при добавлении зависимостей в requirements.txt?

1. Добавить строку в `requirements.txt` (например, `requests==2.31.0`)
2. Закоммитить и запушить `requirements.txt`
3. На сервере:
   ```bash
   git pull
   pip install -r requirements.txt
   systemctl restart maranius-api
   ```

---

### Как проверить, что API работает?

**Локально:**
```bash
curl http://127.0.0.1:8000/api/moon
```

**На сервере:**
```bash
curl http://ТВОЙ_IP:8000/api/moon
```

**Через браузер:**
Открой `http://ТВОЙ_IP:8000/api/moon` в браузере.

---

### Как посмотреть логи API?

```bash
# Если через systemd:
journalctl -u maranius-api -f

# Если запускаешь вручную:
# Логи будут в терминале, где запущен uvicorn
```

---

### Как добавить автозапуск API через systemd?

Создай файл `/etc/systemd/system/maranius-api.service`:

```ini
[Unit]
Description=Maranius HTTP API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bots/maranius
ExecStart=/opt/bots/maranius/.venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Затем:

```bash
systemctl daemon-reload
systemctl enable maranius-api
systemctl start maranius-api
```

---

## См. также

- [мои_боты_статус.md](../../мои_боты_статус.md) — статус деплоя ботов
- [ИНСТРУКЦИЯ_ДЕПЛОЙ_БОТА.md](../../ИНСТРУКЦИЯ_ДЕПЛОЙ_БОТА.md) — инструкция по деплою
- [SmartQuery/справочники/03_интеграции.md](../../../../../база знаний/SmartQuery/справочники/03_интеграции.md) — интеграции SmartBotPro
