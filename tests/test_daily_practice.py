import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from integrations import daily_practice


class DailyPracticeStateTests(unittest.TestCase):
    def test_moscow_date_uses_moscow_timezone(self):
        utc = datetime(2026, 8, 15, 21, 30, tzinfo=timezone.utc)
        self.assertEqual(daily_practice.msk_date_str(now=utc), "2026-08-16")

    def test_next_moscow_day_resets_all_daily_results(self):
        old = {
            "date_msk": "2026-08-15",
            "card": {"slug": "a", "title": "A", "url": "https://x/a"},
            "crystal": {"slug": "b", "title": "B", "url": "https://x/b"},
            "dice": {"value": 6},
        }
        state = daily_practice.normalize_practice(old, today_msk="2026-08-16")
        self.assertEqual(state, {"date_local": "2026-08-16", "card": None, "crystal": None, "dice": None})

    def test_same_moment_can_be_different_days_for_users(self):
        utc = datetime(2026, 8, 15, 15, 30, tzinfo=timezone.utc)
        self.assertEqual(daily_practice.local_date_str(timezone_str="Europe/London", now=utc), "2026-08-15")
        self.assertEqual(daily_practice.local_date_str(timezone_str="Asia/Vladivostok", now=utc), "2026-08-16")

    def test_unknown_timezone_falls_back_to_moscow(self):
        utc = datetime(2026, 8, 15, 21, 30, tzinfo=timezone.utc)
        self.assertEqual(daily_practice.local_date_str(timezone_str="Invalid/Zone", now=utc), "2026-08-16")

    def test_card_crystal_and_dice_do_not_overwrite_each_other(self):
        state = daily_practice.normalize_practice(None, today_msk="2026-08-15")
        state = daily_practice.apply_card_pull(state, {"slug": "c", "title": "Card", "url": "u1"})
        state = daily_practice.apply_crystal_pull(state, {"slug": "k", "title": "Crystal", "url": "u2"})
        state = daily_practice.apply_dice_roll(state, 5)
        self.assertEqual(state["card"]["slug"], "c")
        self.assertEqual(state["crystal"]["slug"], "k")
        self.assertEqual(state["dice"]["value"], 5)

    def test_card_pull_opens_a_published_site_page(self):
        with patch.object(
            daily_practice.secrets,
            "choice",
            return_value={"slug": "42", "title": "Карта № 42"},
        ):
            pull = daily_practice.pick_random_card()

        self.assertEqual(pull, {
            "slug": "42",
            "title": "Карта № 42",
            "url": "https://maranius.ru/practice/podskazki/42",
        })

    def test_crystal_pull_opens_its_anchor_on_the_published_page(self):
        with patch.object(
            daily_practice.secrets,
            "choice",
            return_value={"slug": "elixir", "title": "ЭЛЕКСИР"},
        ):
            pull = daily_practice.pick_random_crystal()

        self.assertEqual(pull, {
            "slug": "elixir",
            "title": "ЭЛЕКСИР",
            "url": "https://maranius.ru/themes/kristally#elixir",
        })

    def test_existing_crystal_pull_migrates_the_legacy_link(self):
        state = daily_practice.normalize_practice(
            {
                "date_local": "2026-08-26",
                "card": None,
                "crystal": {
                    "slug": "danas",
                    "title": "ДАНАС",
                    "url": "https://maranius.ru/practice/podskazki?crystal=danas#kristall",
                },
                "dice": None,
            },
            today_local="2026-08-26",
        )

        self.assertEqual(
            state["crystal"]["url"],
            "https://maranius.ru/themes/kristally#danas",
        )


class DailyPracticeConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_card_clicks_persist_only_one_result(self):
        original_sleep = asyncio.sleep

        async def yield_once(_seconds):
            await original_sleep(0)

        users = {"42": {"id": 42}}
        saves = []
        message = SimpleNamespace(reply_text=AsyncMock(), edit_text=AsyncMock())
        pulls = [
            {"slug": "first", "title": "First", "url": "u1"},
            {"slug": "second", "title": "Second", "url": "u2"},
        ]

        def save(value):
            saves.append(value["42"]["daily_practice"]["card"]["slug"])

        with (
            patch.object(bot, "_load_users", side_effect=lambda: users),
            patch.object(bot, "_save_users", side_effect=save),
            patch.object(bot.daily_practice, "pick_random_card", side_effect=pulls),
            patch.object(bot.asyncio, "sleep", new=AsyncMock(side_effect=yield_once)),
            patch.object(bot, "_edit_or_reply", new=AsyncMock()),
        ):
            await asyncio.gather(bot._handle_card_pull(message, 42), bot._handle_card_pull(message, 42))

        self.assertEqual(len(saves), 1)
        self.assertEqual(users["42"]["daily_practice"]["card"]["slug"], "first")


if __name__ == "__main__":
    unittest.main()
