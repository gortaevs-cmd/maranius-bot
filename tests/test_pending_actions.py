import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

from telegram import CallbackQuery, Message, Update

import bot
import ui
from integrations import user_registry


def callback_update(data):
    return Update.de_json({
        "update_id": 1,
        "callback_query": {
            "id": "query", "chat_instance": "test", "data": data,
            "from": {"id": 42, "is_bot": False, "first_name": "Человек", "username": "human"},
            "message": {
                "message_id": 10, "date": 1700000000, "chat": {"id": 42, "type": "private"},
                "from": {"id": 999, "is_bot": True, "first_name": "Bot"}, "text": "Согласия",
            },
        },
    }, None)


class PendingActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.reply = AsyncMock()
        for target, name, value in (
            (user_registry, "USERS_FILE", Path(temp.name) / "users.json"),
            (Message, "reply_text", self.reply),
            (CallbackQuery, "answer", AsyncMock()),
            (bot.consent_handlers.consent_log, "_append_sync", Mock()),
            (bot.legacy_contacts, "_claim_returning_inactive_user_sync", Mock(return_value=None)),
            (bot.platform_db, "get_or_create_user", AsyncMock()),
            (bot.admin_alerts, "_notify_new_subscriber_sync", Mock()),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = SimpleNamespace(user_data={"pending_action": "angel", "pending_angel_key": "11:11"}, bot=SimpleNamespace())

    async def test_real_consent_callbacks_resume_original_angel_request_for_human(self):
        user_registry.save_users({})
        with patch.object(bot.ewasml_services, "lookup_angelic_sign", return_value=("Послание", "11:11")) as lookup:
            for data in (ui.CB_POLICY_ACCEPT, ui.CB_USER_AGREEMENT_ACCEPT, ui.CB_MARKETING_NO):
                update = callback_update(data)
                self.assertIsNone(update.message)
                await bot.consent_callback(update, self.context)
        lookup.assert_called_once_with("11:11")
        self.assertEqual(self.reply.await_args.args[0], ui.format_angelic_sign("11:11", "Послание"))
        self.assertTrue(self.reply.await_args.kwargs["protect_content"])
        users = user_registry.load_users()
        self.assertEqual(set(users), {"42"})
        self.assertEqual(users["42"]["first_name"], "Человек")
        self.assertFalse(users["42"]["marketing_opt_in"])
        self.assertNotIn("pending_action", self.context.user_data)
        self.assertNotIn("pending_angel_key", self.context.user_data)

    async def test_resumed_angel_rechecks_block_and_consent(self):
        for row in ({"id": 42}, {"id": 42, "admin_blocked": True}):
            with self.subTest(row=row):
                user_registry.save_users({"42": row})
                self.context.user_data["pending_angel_key"] = "11:11"
                with patch.object(bot.ewasml_services, "lookup_angelic_sign") as lookup:
                    await bot._continue_pending_action(callback_update(ui.CB_MARKETING_NO), self.context, "angel")
                lookup.assert_not_called()
                self.assertEqual(self.context.user_data["pending_angel_key"], "11:11")

    async def test_missing_navigation_routes_resume_the_requested_screen(self):
        update = callback_update(ui.CB_MARKETING_NO)
        users = {}
        user_registry.accept_policy(users, 42)
        user_registry.accept_user_agreement(users, 42)
        user_registry.save_users(users)
        for action, text in (("contact", ui.MSG_CONTACT), ("services", ui.MSG_SERVICES),
                             ("learning", ui.MSG_LEARNING), ("info", ui.MSG_INFO_FAQ)):
            with self.subTest(action=action):
                self.context.user_data["pending_action"] = action
                self.reply.reset_mock()
                await bot.consent_callback(update, self.context)
                self.reply.assert_awaited_once()
                self.assertEqual(self.reply.await_args.args[0], text)
