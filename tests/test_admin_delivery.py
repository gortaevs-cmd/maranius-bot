from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import Forbidden

from integrations import admin_alerts
from integrations import inbox, notification_outbox
import bot


class AdminDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        queue = patch.object(notification_outbox, "OUTBOX_FILE", Path(temp.name) / "outbox.json")
        queue.start(); self.addCleanup(queue.stop)
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

    async def test_enqueue_does_not_claim_delivery_or_wait_for_network(self):
        self.assertFalse(await self.notify())
        self.bot.send_message.assert_not_awaited()
        await notification_outbox.deliver_due(self.bot)
        self.bot.send_message.assert_awaited_once()

    async def test_empty_or_group_recipients_are_not_reported_as_delivered(self):
        self.assertFalse(await admin_alerts.notify_seed_admins(self.bot, set(), "request"))
        self.assertFalse(await admin_alerts.notify_seed_admins(self.bot, {0, -42}, "request"))
        self.bot.send_message.assert_not_awaited()

    async def test_rate_limit_does_not_enqueue_excess_entries(self):
        with patch.object(admin_alerts, "_minute_bucket", return_value=10):
            for _ in range(4): self.assertFalse(await self.notify())
        self.assertEqual((await notification_outbox.stats())["pending"], 3)

    async def test_rate_cache_releases_previous_minute_users(self):
        with patch.object(admin_alerts, "_minute_bucket", return_value=10):
            await admin_alerts._can_notify_inbox(100)
        with patch.object(admin_alerts, "_minute_bucket", return_value=11):
            await admin_alerts._can_notify_inbox(200)
        self.assertNotIn(100, admin_alerts._inbox_rate)
        self.assertIn(200, admin_alerts._inbox_rate)


class InboxDeliveryStateTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_entry(self, *, delivered, angel):
        user = SimpleNamespace(id=42, username="tester")
        message = SimpleNamespace(text="unhandled request", reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_message=message, message=message)
        send = AsyncMock(side_effect=None if delivered else Forbidden("blocked"))
        context = SimpleNamespace(user_data={}, bot=SimpleNamespace(send_message=send))
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(inbox, "INBOX_FILE", Path(temp) / "inbox.json"), \
                 patch.object(notification_outbox, "OUTBOX_FILE", Path(temp) / "outbox.json"), \
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
                self.assertFalse((await inbox.load_store())["entries"][0]["admin_notified_at"])
                await notification_outbox.deliver_due(context.bot)
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
