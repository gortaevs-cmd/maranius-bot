import asyncio
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from integrations import admin_alerts
from integrations import inbox
import bot


class AdminDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        rate = patch.dict(admin_alerts._inbox_rate, {}, clear=True)
        rate.start()
        self.addCleanup(rate.stop)
        minute = patch.object(admin_alerts, "_rate_minute", None)
        minute.start()
        self.addCleanup(minute.stop)
        self.bot = SimpleNamespace(send_message=AsyncMock())

    async def notify(self):
        return await admin_alerts.notify_inbox_entry(
            self.bot, {1}, user_id=42, username=None, entry_type="unknown_command", text="request",
        )

    async def test_delivery_is_true_only_after_success(self):
        self.assertTrue(await self.notify())
        self.bot.send_message.assert_awaited_once()

    async def test_rejected_delivery_is_false_and_is_not_retried(self):
        for error in (Forbidden("blocked"), BadRequest("bad request"), RuntimeError("unexpected")):
            with self.subTest(error=type(error).__name__), patch.object(admin_alerts.asyncio, "sleep", new=AsyncMock()) as sleep:
                self.bot.send_message.reset_mock()
                self.bot.send_message.side_effect = error
                self.assertFalse(await self.notify())
                self.bot.send_message.assert_awaited_once()
                sleep.assert_not_awaited()

    async def test_temporary_network_failure_is_retried_until_success(self):
        self.bot.send_message.side_effect = [TimedOut(), NetworkError("offline"), None]
        with patch.object(admin_alerts.asyncio, "sleep", new=AsyncMock()) as sleep:
            self.assertTrue(await self.notify())
        self.assertEqual(self.bot.send_message.await_count, 3)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [1, 2])

    async def test_network_retries_are_bounded(self):
        self.bot.send_message.side_effect = NetworkError("offline")
        with patch.object(admin_alerts.asyncio, "sleep", new=AsyncMock()) as sleep:
            self.assertFalse(await self.notify())
        self.assertEqual(self.bot.send_message.await_count, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_short_telegram_cooldown_is_respected(self):
        self.bot.send_message.side_effect = [RetryAfter(3), None]
        with patch.object(admin_alerts.asyncio, "sleep", new=AsyncMock()) as sleep:
            self.assertTrue(await self.notify())
        sleep.assert_awaited_once_with(3)

    async def test_long_telegram_cooldown_does_not_hold_user_handler(self):
        self.bot.send_message.side_effect = RetryAfter(60)
        with patch.object(admin_alerts.asyncio, "sleep", new=AsyncMock()) as sleep:
            self.assertFalse(await self.notify())
        self.bot.send_message.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_empty_or_group_recipients_are_not_reported_as_delivered(self):
        self.assertFalse(await admin_alerts.notify_seed_admins(self.bot, set(), "request"))
        self.assertFalse(await admin_alerts.notify_seed_admins(self.bot, {0, -42}, "request"))
        self.bot.send_message.assert_not_awaited()

    async def test_partial_recipient_failure_is_not_complete_delivery(self):
        self.bot.send_message.side_effect = [Forbidden("blocked"), None]
        self.assertFalse(await admin_alerts.notify_seed_admins(self.bot, {1, 2}, "request"))
        self.assertEqual([call.kwargs["chat_id"] for call in self.bot.send_message.await_args_list], [1, 2])

    async def test_rate_limit_does_not_claim_delivery(self):
        with patch.object(admin_alerts, "_minute_bucket", return_value=10):
            for _ in range(3):
                self.assertTrue(await self.notify())
            self.assertFalse(await self.notify())
        self.assertEqual(self.bot.send_message.await_count, 3)

    async def test_rate_cache_releases_previous_minute_users(self):
        with patch.object(admin_alerts, "_minute_bucket", return_value=10):
            await admin_alerts._can_notify_inbox(100)
        with patch.object(admin_alerts, "_minute_bucket", return_value=11):
            await admin_alerts._can_notify_inbox(200)
        self.assertNotIn(100, admin_alerts._inbox_rate)
        self.assertIn(200, admin_alerts._inbox_rate)

    async def test_shutdown_is_not_swallowed_or_retried(self):
        self.bot.send_message.side_effect = asyncio.CancelledError()
        with patch.object(admin_alerts.asyncio, "sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await self.notify()
        self.bot.send_message.assert_awaited_once()
        sleep.assert_not_awaited()


class InboxDeliveryStateTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_entry(self, *, delivered, angel):
        user = SimpleNamespace(id=42, username="tester")
        message = SimpleNamespace(text="unhandled request", reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_message=message, message=message)
        send = AsyncMock(side_effect=None if delivered else Forbidden("blocked"))
        context = SimpleNamespace(user_data={}, bot=SimpleNamespace(send_message=send))
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(inbox, "INBOX_FILE", Path(temp) / "inbox.json"), \
                 patch.object(bot, "_load_users", return_value={}), \
                 patch.object(bot, "_require_access", new=AsyncMock(return_value=True)), \
                 patch.object(bot, "ensure_user_saved", new=AsyncMock()), \
                 patch.object(bot.vip_handlers, "try_vip_code", new=AsyncMock(return_value=False)), \
                 patch.object(admin_alerts, "_can_notify_inbox", new=AsyncMock(return_value=True)), \
                 patch.object(bot.ewasml_services, "lookup_angelic_sign", return_value=(None, "99:99")):
                if angel:
                    await bot.reply_angelic_sign(update, context, "9999")
                else:
                    await bot.handle_text(update, context)
                entries = (await inbox.load_store())["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(bool(entries[0]["admin_notified_at"]), delivered)
        message.reply_text.assert_awaited_once()

    async def test_failed_delivery_keeps_both_entry_types_unnotified(self):
        for angel in (False, True):
            with self.subTest(angel=angel):
                await self.exercise_entry(delivered=False, angel=angel)

    async def test_successful_delivery_marks_both_entry_types_notified(self):
        for angel in (False, True):
            with self.subTest(angel=angel):
                await self.exercise_entry(delivered=True, angel=angel)
