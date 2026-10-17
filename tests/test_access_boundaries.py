"""Regression checks using Telegram Update objects, without Telegram requests."""

import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from telegram import CallbackQuery, Message, Update

import bot
import ui
from handlers import vip as vip_handlers
from integrations import user_registry


def make_update(user_id=42, *, chat_type="private", callback=None, text="/vip"):
    chat = {"id": user_id if chat_type == "private" else -10042, "type": chat_type}
    user = {"id": user_id, "is_bot": False, "first_name": "Test"}
    message = {"message_id": 10, "date": 1700000000, "chat": chat, "from": user, "text": text}
    data = {"update_id": 1}
    if callback:
        message["from"] = {"id": 123456, "is_bot": True, "first_name": "Bot"}
        data["callback_query"] = {"id": "query", "chat_instance": "test", "from": user,
                                  "message": message, "data": callback}
    else:
        data["message"] = message
    return Update.de_json(data, None)


def current_user(**overrides):
    return dict({
        "id": 42, "vip": True,
        "policy_accepted_at": "2026-08-31T00:00:00Z",
        "policy_version": user_registry.PERSONAL_DATA_CONSENT_VERSION,
        "user_agreement_accepted_at": "2026-08-31T00:00:00Z",
        "user_agreement_version": user_registry.USER_AGREEMENT_VERSION,
    }, **overrides)


class AccessBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for attr, target, name in (("reply", Message, "reply_text"),
                                   ("answer", CallbackQuery, "answer"),
                                   ("section", bot.analytics, "log_section")):
            patcher = patch.object(target, name, new=AsyncMock())
            setattr(self, attr, patcher.start())
            self.addCleanup(patcher.stop)
        self.context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    async def test_old_vip_buttons_recheck_block_and_document_versions(self):
        for callback in ("vip:welcome", "vip:decks", "vip:deck:ten", "vip:sec:ten:intro", "vip:pdf:ten:dark"):
            for overrides in ({"admin_blocked": True}, {"policy_version": "old"},
                              {"user_agreement_version": "old"}):
                with self.subTest(callback=callback, overrides=overrides):
                    update = make_update(callback=callback)
                    with patch.object(bot, "_load_users", return_value={"42": current_user(**overrides)}), \
                         patch.object(vip_handlers, "vip_menu_callback", new=AsyncMock()) as content:
                        await bot.vip_callback(update, self.context)
                    content.assert_not_awaited()

    async def test_current_vip_reaches_content_handler(self):
        update = make_update(callback="vip:welcome")
        with patch.object(bot, "_load_users", return_value={"42": current_user()}), \
             patch.object(vip_handlers, "vip_menu_callback", new=AsyncMock()) as content:
            await bot.vip_callback(update, self.context)
        content.assert_awaited_once()
        self.section.assert_awaited_once_with(42, "vip")

    async def test_current_non_vip_cannot_read_materials(self):
        update = make_update(callback="vip:sec:ten:intro")
        with patch.object(bot, "_load_users", return_value={"42": current_user(vip=False)}), \
             patch.object(vip_handlers, "send_vip_html_parts", new=AsyncMock()) as send:
            await bot.vip_callback(update, self.context)
        send.assert_not_awaited()
        self.reply.assert_awaited_once()

    async def test_group_vip_callback_reveals_no_materials_or_profile(self):
        update = make_update(chat_type="supergroup", callback="vip:pdf:ten:dark")
        with patch.object(bot, "_load_users") as load, \
             patch.object(vip_handlers, "vip_menu_callback", new=AsyncMock()) as content:
            await bot.vip_callback(update, self.context)
        load.assert_not_called()
        content.assert_not_awaited()
        self.reply.assert_not_awaited()
        self.answer.assert_awaited_once_with(ui.MSG_PRIVATE_CHAT_ONLY, show_alert=True)

    async def test_group_vip_command_does_not_open_code_entry(self):
        update = make_update(chat_type="group")
        with patch.object(bot, "_require_access", new=AsyncMock()) as access:
            await bot.show_vip_home(update, self.context)
        access.assert_not_awaited()
        self.assertNotIn("awaiting_vip_code", self.context.user_data)

    async def test_vip_code_pending_in_private_chat_cannot_be_redeemed_in_group(self):
        update = make_update(chat_type="group", text="ONE-CODE")
        self.context.user_data["awaiting_vip_code"] = True
        with patch.object(bot, "_load_users", return_value={"42": current_user(vip=False)}), \
             patch.object(vip_handlers, "try_vip_code", new=AsyncMock()) as redeem:
            await bot.handle_text(update, self.context)
        redeem.assert_not_awaited()

    async def test_owner_cannot_export_users_to_group(self):
        update = make_update(next(iter(bot.SEED_ADMIN_IDS)), chat_type="supergroup", callback="admin:list:all")
        with patch.object(bot, "_load_users") as load, \
             patch.object(user_registry, "export_users_csv") as export:
            await bot.admin_callback(update, self.context)
        load.assert_not_called()
        export.assert_not_called()
        self.reply.assert_not_awaited()

    async def test_owner_god_command_in_group_does_not_write_profile(self):
        update = make_update(next(iter(bot.SEED_ADMIN_IDS)), chat_type="group", text="/god")
        with patch.object(bot, "ensure_user_saved", new=AsyncMock()) as save, \
             patch.object(bot, "add_admin", new=AsyncMock()) as add:
            await bot.god_cmd(update, self.context)
        save.assert_not_awaited()
        add.assert_not_awaited()

    async def test_owner_private_input_mode_cannot_leak_user_card_to_group(self):
        update = make_update(next(iter(bot.SEED_ADMIN_IDS)), chat_type="group", text="42")
        self.context.user_data["admin_mode"] = "user_lookup"
        with patch.object(bot, "add_admin", new=AsyncMock()) as add, \
             patch.object(bot, "_admin_find_user", new=AsyncMock()) as find:
            await bot.handle_text(update, self.context)
        add.assert_not_awaited()
        find.assert_not_awaited()

    async def test_vip_editor_matches_actual_sdk_signature(self):
        message = make_update(callback="vip:welcome").effective_message
        with patch.object(Message, "edit_text", autospec=True) as edit:
            await vip_handlers._vip_edit_or_reply(message, "VIP", None, {"protect_content": True})
        edit.assert_awaited_once()
        self.reply.assert_not_awaited()


class BotMembershipTests(unittest.IsolatedAsyncioTestCase):
    def make_membership(self, old="member", new="kicked", chat_type="private"):
        bot_user = {"id": 123456, "is_bot": True, "first_name": "Bot"}
        return Update.de_json({"update_id": 2, "my_chat_member": {
            "chat": {"id": 42, "type": chat_type},
            "from": {"id": 42, "is_bot": False, "first_name": "Test"},
            "date": 1700000000,
            "old_chat_member": {"user": bot_user, "status": old, "until_date": 0},
            "new_chat_member": {"user": bot_user, "status": new, "until_date": 0},
        }}, None)

    async def test_block_updates_chat_owner_instead_of_bot_profile(self):
        users = {"42": current_user(marketing_opt_in=True,
                                    marketing_consent_version=user_registry.MARKETING_CONSENT_VERSION)}
        self.assertTrue(user_registry.segment_filter("marketing_ready")(users["42"]))
        with patch.object(bot, "_load_users", return_value=users), patch.object(bot, "_save_users") as save:
            await bot.my_chat_member_handler(self.make_membership(), SimpleNamespace())
        self.assertEqual(users["42"]["bot_status"], "blocked")
        self.assertIn("blocked_at", users["42"])
        self.assertNotIn("123456", users)
        self.assertFalse(user_registry.segment_filter("marketing_ready")(users["42"]))
        save.assert_called_once_with(users)

    async def test_unblock_restores_existing_user_status(self):
        users = {"42": current_user(bot_status="blocked", unsubscribed_at="old")}
        with patch.object(bot, "_load_users", return_value=users), patch.object(bot, "_save_users") as save:
            await bot.my_chat_member_handler(self.make_membership(old="kicked", new="member"), SimpleNamespace())
        self.assertEqual(users["42"]["bot_status"], "active")
        self.assertNotIn("unsubscribed_at", users["42"])
        save.assert_called_once()

    async def test_unknown_user_is_not_created_by_membership_event(self):
        with patch.object(bot, "_load_users", return_value={}), patch.object(bot, "_save_users") as save:
            await bot.my_chat_member_handler(self.make_membership(), SimpleNamespace())
        save.assert_not_called()

    async def test_group_and_unchanged_status_do_not_write_users(self):
        for update in (self.make_membership(chat_type="supergroup"), self.make_membership(old="member", new="member")):
            with patch.object(bot, "_load_users") as load, patch.object(bot, "_save_users") as save:
                await bot.my_chat_member_handler(update, SimpleNamespace())
            load.assert_not_called()
            save.assert_not_called()
