import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot
import ui
from handlers import consent as consent_handlers


class AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class StartConsentFlowTests(unittest.IsolatedAsyncioTestCase):
    def make_update(self, user_id: int = 42):
        user = SimpleNamespace(id=user_id, username=None, is_bot=False)
        message = SimpleNamespace(reply_text=AsyncMock(), text="")
        chat = SimpleNamespace(id=user_id)
        return SimpleNamespace(
            effective_user=user,
            effective_message=message,
            message=message,
            effective_chat=chat,
        )

    async def test_new_user_sees_policy_before_profile_is_saved(self):
        update = self.make_update()
        context = SimpleNamespace(
            user_data={},
            args=[],
            bot=SimpleNamespace(set_chat_menu_button=AsyncMock()),
        )

        with (
            patch.object(bot, "_load_users", return_value={}),
            patch.object(bot, "ensure_user_saved", new=AsyncMock()) as save_profile,
            patch.object(
                bot.consent_handlers,
                "show_policy_gate",
                new=AsyncMock(),
            ) as show_gate,
        ):
            await bot.start(update, context)

        save_profile.assert_not_awaited()
        show_gate.assert_awaited_once_with(update, context)
        self.assertEqual(context.user_data["pending_action"], "start")
        update.effective_message.reply_text.assert_not_awaited()

    async def test_user_with_policy_gets_normal_start(self):
        update = self.make_update()
        context = SimpleNamespace(
            user_data={},
            args=[],
            bot=SimpleNamespace(set_chat_menu_button=AsyncMock()),
        )
        users = {"42": {"id": 42, "policy_accepted_at": "2026-08-15T00:00:00Z", "policy_version": "2024-08-03"}}

        with (
            patch.object(bot, "_load_users", return_value=users),
            patch.object(bot, "ensure_user_saved", new=AsyncMock()) as save_profile,
            patch.object(bot, "_save_users", new=MagicMock()),
        ):
            await bot.start(update, context)

        save_profile.assert_awaited_once_with(update, bot=context.bot, force=False)
        update.effective_message.reply_text.assert_awaited_once()
        context.bot.set_chat_menu_button.assert_awaited_once()

    async def test_pending_start_continues_after_consent(self):
        update = self.make_update()
        context = SimpleNamespace(user_data={})

        with patch.object(bot, "start", new=AsyncMock()) as start_handler:
            await bot._continue_pending_action(update, context, "start")

        start_handler.assert_awaited_once_with(update, context)

    async def test_policy_acceptance_waits_for_marketing_choice_before_start(self):
        users = {}
        saved = {}
        query = SimpleNamespace(
            data=ui.CB_POLICY_ACCEPT,
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={"pending_action": "start"})

        def save_users(value):
            saved.clear()
            saved.update(value)

        pending = await consent_handlers.consent_callback(
            update,
            context,
            users_lock=AsyncLock(),
            load_users=lambda: users,
            save_users=save_users,
        )

        self.assertIsNone(pending)
        self.assertEqual(context.user_data["pending_action"], "start")
        self.assertEqual(saved["42"]["id"], 42)
        self.assertIn("policy_accepted_at", saved["42"])
        self.assertNotIn("username", saved["42"])
        self.assertNotIn("first_name", saved["42"])

    async def test_marketing_choice_continues_pending_start(self):
        users = {"42": {"id": 42, "policy_accepted_at": "2026-08-15T00:00:00Z", "policy_version": "2024-08-03"}}
        query = SimpleNamespace(
            data=ui.CB_MARKETING_YES,
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace(user_data={"pending_action": "start"})

        pending = await consent_handlers.consent_callback(
            update=SimpleNamespace(callback_query=query),
            context=context,
            users_lock=AsyncLock(),
            load_users=lambda: users,
            save_users=lambda _value: None,
        )

        self.assertEqual(pending, "start")
        self.assertNotIn("pending_action", context.user_data)
        self.assertTrue(users["42"]["marketing_opt_in"])
        query.message.reply_text.assert_not_awaited()

    async def test_marketing_refusal_does_not_remove_policy_access(self):
        users = {"42": {"id": 42, "policy_accepted_at": "2026-08-15T00:00:00Z", "policy_version": "2024-08-03"}}
        query = SimpleNamespace(data=ui.CB_MARKETING_NO, from_user=SimpleNamespace(id=42), answer=AsyncMock(), message=SimpleNamespace(reply_text=AsyncMock()))
        context = SimpleNamespace(user_data={})
        await consent_handlers.consent_callback(update=SimpleNamespace(callback_query=query), context=context, users_lock=AsyncLock(), load_users=lambda: users, save_users=lambda _v: None)
        self.assertFalse(users["42"]["marketing_opt_in"])
        self.assertIn("policy_accepted_at", users["42"])

    async def test_handle_text_does_not_save_profile_without_policy(self):
        update = self.make_update()
        update.message.text = "привет"
        context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

        with (
            patch.object(bot, "_load_users", return_value={}),
            patch.object(bot, "ensure_user_saved", new=AsyncMock()) as save_profile,
            patch.object(bot, "_require_access", new=AsyncMock(return_value=False)),
        ):
            await bot.handle_text(update, context)

        save_profile.assert_not_awaited()

    async def test_pending_angel_is_continued_after_policy(self):
        update = self.make_update()
        context = SimpleNamespace(user_data={"pending_angel_key": "11:11"})
        with patch.object(bot, "reply_angelic_sign", new=AsyncMock()) as reply:
            await bot._continue_pending_action(update, context, "angel")
        reply.assert_awaited_once_with(update, context, "11:11")


if __name__ == "__main__":
    unittest.main()
