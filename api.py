"""
HTTP API для интеграции с SmartBotPro и другими сервисами.

Эндпоинты:
- GET /api/weather?lat=55.75&lon=37.62 - погода по координатам
- GET /api/moon - информация о луне
- GET /api/rate?currency=USD - курс валют
"""

from fastapi import FastAPI, HTTPException
from datetime import date
from typing import Optional
import sys
import os

# Добавляем текущую директорию в путь для импорта bot
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Импорты из bot.py
from bot import (
    _weather_at_coords,
    _get_moon_data,
    _moon_phase_from_data,
    _lunar_day_from_data,
    get_moon_emoji,
    _fetch_rate,
    _get_city_from_coords,
    RATE_QUOTE_CURRENCY,
)

app = FastAPI(
    title="Maranius API",
    description="HTTP API для получения данных о погоде, луне и курсе валют",
    version="1.0.0"
)


@app.get("/")
async def root():
    """Корневой эндпоинт с информацией об API."""
    return {
        "service": "Maranius API",
        "version": "1.0.0",
        "endpoints": {
            "weather": "/api/weather?lat=55.75&lon=37.62",
            "moon": "/api/moon",
            "rate": "/api/rate?currency=USD"
        }
    }


@app.get("/api/weather")
async def weather(lat: float, lon: float):
    """
    Получить погоду по координатам.
    
    Параметры:
    - lat: широта (float)
    - lon: долгота (float)
    
    Возвращает JSON с текстом погоды или ошибкой.
    """
    try:
        # Получаем название места по координатам
        place_name = await _get_city_from_coords(lat, lon)
        if not place_name or place_name == "Локация":
            place_name = f"({lat:.4f}, {lon:.4f})"
        
        # Получаем погоду
        weather_text = await _weather_at_coords(lat, lon, place_name)
        
        if weather_text is None:
            raise HTTPException(
                status_code=500,
                detail="Не удалось получить данные о погоде"
            )
        
        return {
            "status": "ok",
            "text": weather_text,
            "location": {
                "lat": lat,
                "lon": lon,
                "name": place_name
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении погоды: {str(e)}"
        )


@app.get("/api/moon")
async def moon():
    """
    Получить информацию о луне на сегодня.
    
    Возвращает JSON с данными о фазе луны, лунных сутках и датах фаз.
    """
    try:
        today = date.today()
        moon_data = _get_moon_data(today)
        phase_name = _moon_phase_from_data(today, moon_data)
        lunar_day = _lunar_day_from_data(today, moon_data)
        emoji = get_moon_emoji(phase_name)
        illumination_percent = int(round(moon_data["illumination"] * 100))
        
        # Вычисляем дни до следующего новолуния и полнолуния
        days_to_new = (moon_data["next_new_moon"] - today).days
        
        import ephem
        obs = ephem.Observer()
        obs.date = today
        next_full_date = ephem.Date(ephem.next_full_moon(obs.date)).datetime().date()
        days_to_full = (next_full_date - today).days
        
        if days_to_full == 0:
            obs.date = moon_data["next_new_moon"]
            next_full_date = ephem.Date(ephem.next_full_moon(obs.date)).datetime().date()
            days_to_full = (next_full_date - today).days
        
        return {
            "status": "ok",
            "date": str(today),
            "phase": phase_name,
            "emoji": emoji,
            "lunar_day": lunar_day,
            "illumination_percent": illumination_percent,
            "phases": {
                "prev_new_moon": str(moon_data["prev_new_moon"]),
                "next_new_moon": str(moon_data["next_new_moon"]),
                "full_moon": str(moon_data["full_moon_this_cycle"])
            },
            "days_until": {
                "new_moon": days_to_new if days_to_new > 0 else 0,
                "full_moon": days_to_full if days_to_full > 0 else 0
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении данных о луне: {str(e)}"
        )


@app.get("/api/rate")
async def rate(currency: str):
    """
    Получить курс валюты.
    
    Параметры:
    - currency: код валюты (USD, EUR, CNY, XAU для золота, XAG для серебра)
    
    Возвращает JSON с курсом валюты к рублю.
    """
    try:
        currency_upper = currency.upper()
        
        # Получаем курс
        rate_value = await _fetch_rate(currency_upper, RATE_QUOTE_CURRENCY)
        
        if rate_value is None:
            raise HTTPException(
                status_code=404,
                detail=f"Не удалось получить курс для валюты {currency_upper}"
            )
        
        return {
            "status": "ok",
            "currency": currency_upper,
            "rate": rate_value,
            "quote": RATE_QUOTE_CURRENCY,
            "description": f"1 {currency_upper} = {rate_value:.2f} {RATE_QUOTE_CURRENCY}"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении курса: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
