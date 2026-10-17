import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from integrations import analytics


class CalendarMonthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for name, filename in (("EVENTS_FILE", "events.json"), ("AGGREGATES_FILE", "aggregates.json")):
            patcher = patch.object(analytics, name, Path(temp.name) / filename)
            patcher.start()
            self.addCleanup(patcher.stop)

    def save_events(self, events):
        analytics._save_json(analytics.EVENTS_FILE, {"events": events})

    async def test_month_bounds_use_moscow_midnight_with_year_rollover(self):
        start, end = analytics.month_bounds(2026, 12)
        self.assertEqual(start, datetime(2026, 11, 30, 21, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 12, 31, 21, tzinfo=timezone.utc))

    async def test_february_bounds_include_leap_day(self):
        start, end = analytics.month_bounds(2024, 2)
        self.assertEqual((end - start).days, 29)
        self.assertEqual(end, datetime(2024, 2, 29, 21, tzinfo=timezone.utc))

    async def test_month_stats_include_start_exclude_end_and_ignore_invalid_events(self):
        self.save_events([
            {"ts": "2024-01-31T20:59:59Z", "user_id": 1, "section": "before"},
            {"ts": "2024-01-31T21:00:00Z", "user_id": 2, "section": "start"},
            {"ts": "2024-02-29T23:59:59+03:00", "user_id": 2, "section": "leap"},
            {"ts": "2024-02-29T21:00:00Z", "user_id": 3, "section": "after"},
            {"ts": "2024-02-10T12:00:00", "user_id": 4, "section": "legacy_utc"},
            {"ts": "bad", "user_id": 5, "section": "invalid"}, None,
        ])
        stats = analytics.monthly_stats(2024, 2)
        self.assertEqual(stats["events"], 3)
        self.assertEqual(stats["unique_users"], 2)
        self.assertEqual(dict(stats["top_sections"]), {"start": 1, "leap": 1, "legacy_utc": 1})

    async def test_rollup_uses_requested_month_and_keeps_other_history(self):
        self.save_events([{"ts": "2024-02-01T00:00:00Z", "user_id": 2, "section": "angel"}])
        old = {"period": "2024-01", "stats_30d": {"events": 17}}
        analytics._save_json(analytics.AGGREGATES_FILE, {"monthly": [old], "weekly": [{"period": "week"}]})
        first = await analytics.rollup_monthly(2024, 2)
        second = await analytics.rollup_monthly(2024, 2)
        stored = analytics._load_json(analytics.AGGREGATES_FILE, {})
        self.assertEqual(first["stats_month"]["events"], 1)
        self.assertEqual(first["start_inclusive"], "2024-01-31T21:00:00Z")
        self.assertEqual(first["end_exclusive"], "2024-02-29T21:00:00Z")
        self.assertNotIn("stats_30d", first)
        self.assertEqual([m["period"] for m in stored["monthly"]], ["2024-01", "2024-02"])
        self.assertEqual(stored["monthly"][0], old)
        self.assertEqual(stored["monthly"][1]["stats_month"]["events"], second["stats_month"]["events"])
        self.assertEqual(stored["weekly"], [{"period": "week"}])

    async def test_monthly_report_uses_previous_calendar_month_and_separate_current_snapshot(self):
        self.save_events([
            {"ts": "2026-02-02T00:00:00Z", "user_id": 2, "section": "<angel>"},
            {"ts": "2026-03-01T08:00:00Z", "user_id": 3, "section": "next_month"},
        ])
        now = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
        st = {"real": 20, "new_7d": 8, "active_7": 2, "active_30": 3, "sleeping": 17,
              "vip": 5, "no_policy": 4, "marketing": 1}
        with patch.object(bot, "datetime") as clock, \
             patch.object(bot, "_user_stats", return_value=st), \
             patch.object(bot.inbox_mod, "stats", new=AsyncMock(return_value=(4, 2))), \
             patch.object(bot.admin_alerts, "notify_seed_admins", new=AsyncMock()) as notify:
            clock.now.return_value = now
            await bot._send_monthly_report(SimpleNamespace(bot="fake"))
        text = notify.await_args.args[2]
        self.assertIn("02.2026", text)
        self.assertIn("Событий: 1 · пользователей: 1", text)
        self.assertIn("&lt;angel&gt;: 1", text)
        self.assertNotIn("next_month", text)
        self.assertNotIn("Топ разделов 7д", text)
        self.assertNotIn("+8 за 7д", text)
        self.assertIn("Текущая сводка базы:", text)
        self.assertIn("Пользователи (real): 20", text)

    async def test_rolling_stats_exclude_future_events(self):
        self.save_events([
            {"ts": "2026-10-16T00:00:00Z", "user_id": 2, "section": "weather"},
            {"ts": "2026-10-18T00:00:00Z", "user_id": 3, "section": "future"},
        ])
        with patch.object(analytics, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 17, tzinfo=timezone.utc)
            self.assertEqual(analytics.period_stats(7)["events"], 1)
