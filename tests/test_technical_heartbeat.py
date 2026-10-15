import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import Forbidden

import technical_heartbeat as heartbeat


class HeartbeatScheduleTests(unittest.TestCase):
    def test_utc_schedule_is_ten_moscow(self):
        now = datetime(2026, 10, 15, 6, 59, tzinfo=timezone.utc)
        self.assertEqual(
            heartbeat.next_heartbeat(now),
            datetime(2026, 10, 15, 7, 0, tzinfo=timezone.utc),
        )

    def test_restart_after_ten_waits_until_tomorrow(self):
        now = datetime(2026, 12, 31, 10, 1, tzinfo=heartbeat.MSK)
        self.assertEqual(
            heartbeat.next_heartbeat(now),
            datetime(2027, 1, 1, 10, 0, tzinfo=heartbeat.MSK),
        )

    def test_exactly_ten_is_due_today(self):
        now = datetime(2026, 10, 15, 10, 0, tzinfo=heartbeat.MSK)
        self.assertEqual(heartbeat.next_heartbeat(now), now)


class HeartbeatDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_only_and_no_duplicate_recipients(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        self.assertTrue(await heartbeat.send_heartbeat(bot, [42, 42, 0, -1], "TestBot"))
        bot.send_message.assert_awaited_once()
        message = bot.send_message.await_args.kwargs
        self.assertEqual(message["chat_id"], 42)
        self.assertIn("TestBot работает", message["text"])
        self.assertIn("МСК", message["text"])

    async def test_transient_error_retries_then_stops_after_success(self):
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[OSError(), None]))
        with patch.object(heartbeat.asyncio, "sleep", new=AsyncMock()) as sleep:
            self.assertTrue(await heartbeat.send_heartbeat(bot, [42], "TestBot"))
        self.assertEqual(bot.send_message.await_count, 2)
        sleep.assert_awaited_once_with(60)

    async def test_forbidden_recipient_is_not_retried(self):
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=Forbidden("blocked")))
        self.assertFalse(await heartbeat.send_heartbeat(bot, [42], "TestBot"))
        self.assertEqual(bot.send_message.await_count, 1)

    async def test_shutdown_cancels_delivery(self):
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=asyncio.CancelledError()))
        with self.assertRaises(asyncio.CancelledError):
            await heartbeat.send_heartbeat(bot, [42], "TestBot")


if __name__ == "__main__":
    unittest.main()
