import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import ui
from integrations import user_registry


def stamp(hours=0):
    return (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


class WeatherCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for target, name, value in (
            (user_registry, "USERS_FILE", Path(temp.name) / "users.json"),
            (bot.weather_handlers, "_get_timezone_by_coords", lambda *args: "Europe/Moscow"),
            (bot, "_require_access", AsyncMock(return_value=True)),
            (bot, "ensure_user_saved", AsyncMock()),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.loc = {"lat": 55.75, "lon": 37.62, "updated_at": stamp()}
        self.row = {"id": 42, "last_location": self.loc, "timezone": "Europe/Moscow"}
        user_registry.save_users({"42": self.row})
        self.message = SimpleNamespace(
            reply_text=AsyncMock(), location=SimpleNamespace(latitude=59.94, longitude=30.32)
        )
        self.update = SimpleNamespace(
            message=self.message, effective_message=self.message,
            effective_user=SimpleNamespace(id=42),
        )
        self.context = SimpleNamespace(bot=SimpleNamespace(), user_data={})

    async def seed_cache(self):
        self.assertTrue(await bot._weather_handlers._save_weather_cache("42", "Москва", location=self.loc))

    async def test_new_location_invalidates_all_old_cache_fields(self):
        await self.seed_cache()
        row = await bot._weather_handlers._save_user_location("42", 59.94, 30.32)
        self.assertEqual(row, user_registry.load_users()["42"])
        self.assertEqual(row["last_location"]["lat"], 59.94)
        for key in ("last_weather_text", "last_weather_location", "last_weather_fetched_at"):
            self.assertNotIn(key, row)

    async def test_unknown_timezone_does_not_keep_previous_place_timezone(self):
        with patch.object(bot.weather_handlers, "_get_timezone_by_coords", return_value=None):
            await bot._weather_handlers._save_user_location("42", 59.94, 30.32)
        self.assertNotIn("timezone", user_registry.load_users()["42"])

    async def test_cache_freshness_requires_matching_place_and_recent_fetch(self):
        await self.seed_cache()
        row = user_registry.load_users()["42"]
        self.assertTrue(bot.weather_handlers._weather_cache_is_fresh(row))
        for changes in (
            {"last_weather_fetched_at": stamp(5)},
            {"last_weather_fetched_at": stamp(-1)},
            {"last_weather_fetched_at": "invalid"},
            {"last_weather_location": dict(self.loc, lon=1)},
            {"last_weather_text": ""},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(bot.weather_handlers._weather_cache_is_fresh(dict(row, **changes)))

    async def test_legacy_cache_without_metadata_is_refetched(self):
        user_registry.save_users({"42": dict(self.row, last_weather_text="Старый прогноз")})
        with patch.object(bot._weather_handlers, "_weather_fetch_and_send", new=AsyncMock()) as fetch:
            await bot.weather(self.update, self.context)
        fetch.assert_awaited_once()
        self.message.reply_text.assert_not_awaited()

    async def test_matching_cache_is_served_without_api_call(self):
        await self.seed_cache()
        with patch.object(bot._weather_handlers, "_weather_fetch_and_send", new=AsyncMock()) as fetch:
            await bot.weather(self.update, self.context)
        fetch.assert_not_awaited()
        self.assertEqual(self.message.reply_text.await_args.args[0], "Москва" + ui.MSG_WEATHER_CACHE_HINT)

    async def test_old_request_cannot_overwrite_new_place_cache(self):
        new = await bot._weather_handlers._save_user_location("42", 59.94, 30.32)
        await bot._weather_handlers._save_weather_cache("42", "Петербург", location=new["last_location"])
        self.assertFalse(await bot._weather_handlers._save_weather_cache("42", "Москва", location=self.loc))
        self.assertEqual(user_registry.load_users()["42"]["last_weather_text"], "Петербург")
        self.assertFalse(await bot._weather_handlers._save_weather_cache("99", "Москва", location=self.loc))
        self.assertNotIn("99", user_registry.load_users())

    async def test_delayed_saved_place_response_is_not_sent(self):
        async def delayed_forecast(*args, **kwargs):
            await bot._weather_handlers._save_user_location("42", 59.94, 30.32)
            return "Устаревшая Москва"

        with patch.object(bot.weather_handlers, "_get_city_from_coords", new=AsyncMock(return_value="Москва")), \
             patch.object(bot.weather_handlers, "_weather_at_coords", new=delayed_forecast):
            await bot._weather_handlers._weather_fetch_and_send(self.update, self.row)
        self.assertEqual(self.message.reply_text.await_args.args[0], ui.MSG_WEATHER_LOCATION_CHANGED)
        self.assertNotIn("last_weather_text", user_registry.load_users()["42"])

    async def test_delayed_geo_response_is_not_sent_or_cached(self):
        async def delayed_forecast(*args, **kwargs):
            await bot._weather_handlers._save_user_location("42", 0, 0)
            return "Устаревший Петербург"

        with patch.object(bot.weather_handlers, "_get_city_from_coords", new=AsyncMock(return_value="Петербург")), \
             patch.object(bot.weather_handlers, "_weather_at_coords", new=delayed_forecast):
            await bot.weather_by_location(self.update, self.context)
        self.assertEqual(self.message.reply_text.await_args.args[0], ui.MSG_WEATHER_LOCATION_CHANGED)
        self.assertEqual(user_registry.load_users()["42"]["last_location"]["lat"], 0)

    async def test_failed_geo_fetch_cannot_reuse_old_place_forecast(self):
        await self.seed_cache()
        with patch.object(bot.weather_handlers, "_get_city_from_coords", new=AsyncMock(return_value="Петербург")), \
             patch.object(bot.weather_handlers, "_weather_at_coords", new=AsyncMock(return_value=None)):
            await bot.weather_by_location(self.update, self.context)
        self.assertEqual(self.message.reply_text.await_args.args[0], ui.MSG_WEATHER_GEO_FAIL)
        self.assertNotIn("last_weather_text", user_registry.load_users()["42"])

    async def test_new_geo_forecast_is_saved_with_its_coordinates(self):
        await self.seed_cache()
        with patch.object(bot.weather_handlers, "_get_city_from_coords", new=AsyncMock(return_value="Петербург")), \
             patch.object(bot.weather_handlers, "_weather_at_coords", new=AsyncMock(return_value="Новый прогноз")):
            await bot.weather_by_location(self.update, self.context)
        row = user_registry.load_users()["42"]
        self.assertTrue(bot.weather_handlers._weather_cache_is_fresh(row))
        self.assertEqual(row["last_weather_location"]["lat"], 59.94)
        self.assertEqual(row["last_weather_text"], "Новый прогноз")
        self.assertEqual(self.message.reply_text.await_args.args[0], "Новый прогноз")

    async def test_future_or_expired_location_is_not_fresh(self):
        for hours in (-1, 5):
            self.assertFalse(bot.weather_handlers._location_is_fresh({"last_location": dict(self.loc, updated_at=stamp(hours))}))

    async def test_moon_callback_edits_existing_message_through_shared_renderer(self):
        self.update.callback_query = SimpleNamespace()
        with patch.object(bot.weather_handlers, "_format_moon_text", return_value="Луна"), \
             patch.object(bot, "_edit_or_reply", new=AsyncMock()) as edit:
            await bot.moon_cmd(self.update, self.context)
        edit.assert_awaited_once()
        self.assertIs(edit.await_args.args[0], self.message)
        self.assertEqual(edit.await_args.args[1], "Луна")
        self.message.reply_text.assert_not_awaited()

    async def test_moon_command_sends_new_message(self):
        self.update.callback_query = None
        with patch.object(bot.weather_handlers, "_format_moon_text", return_value="Луна"):
            await bot.moon_cmd(self.update, self.context)
        self.assertEqual(self.message.reply_text.await_args.args[0], "Луна")

    async def test_weather_and_moon_denied_access_do_not_fetch_or_save_profile(self):
        with patch.object(bot, "_require_access", new=AsyncMock(return_value=False)), \
             patch.object(bot, "ensure_user_saved", new=AsyncMock()) as save, \
             patch.object(bot.weather_handlers, "_format_moon_text") as moon, \
             patch.object(bot._weather_handlers, "_weather_fetch_and_send", new=AsyncMock()) as weather:
            await bot.weather(self.update, self.context)
            await bot.weather_by_location(self.update, self.context)
            await bot.moon_cmd(self.update, self.context)
        save.assert_not_awaited()
        moon.assert_not_called()
        weather.assert_not_awaited()
        self.message.reply_text.assert_not_awaited()

    async def test_profile_refresh_preserves_cache_metadata_and_vip_revocation(self):
        await self.seed_cache()
        users = user_registry.load_users()
        users["42"]["vip"] = True
        user_registry.accept_policy(users, 42)
        user_registry.accept_user_agreement(users, 42)
        user_registry.revoke_vip(users, 42)
        old = dict(users["42"])
        row, is_new = user_registry.merge_telegram_profile(
            users, user_id=42, username="test", first_name="Тест", last_name="",
            language_code="ru", is_premium=False, seed_admin_ids=set(),
        )
        self.assertFalse(is_new)
        for key in ("vip_revoked_at", "last_weather_location", "last_weather_fetched_at"):
            self.assertEqual(row[key], old[key])
        self.assertFalse(user_registry.is_vip(users, 42, seed_admin_ids=set()))
        self.assertTrue(user_registry.has_current_access(users, 42))
