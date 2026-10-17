import asyncio
import csv
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import CallbackQuery, Message, Update
from telegram.error import BadRequest

import ui
from handlers.admin import AdminHandlers
from integrations import admin_audit, user_registry, vip_codes


def make_update(*, user_id=10, callback=None, text="", chat_type="private"):
    user = {"id": user_id, "is_bot": False, "first_name": "Администратор"}
    message = {
        "message_id": 1, "date": 1700000000, "text": text,
        "chat": {"id": user_id if chat_type == "private" else -100, "type": chat_type},
        "from": user,
    }
    data = {"update_id": 1}
    if callback:
        message["from"] = {"id": 999, "is_bot": True, "first_name": "Bot"}
        data["callback_query"] = {
            "id": "callback", "chat_instance": "test", "from": user,
            "message": message, "data": callback,
        }
    else:
        data["message"] = message
    return Update.de_json(data, None)


class AdminHandlerFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.reply = AsyncMock()
        self.edit = AsyncMock()
        self.document = AsyncMock()
        for target, name, value in (
            (user_registry, "USERS_FILE", root / "users.json"),
            (vip_codes, "CODES_FILE", root / "codes.json"),
            (Message, "reply_text", self.reply),
            (Message, "edit_text", self.edit),
            (Message, "reply_document", self.document),
            (CallbackQuery, "answer", AsyncMock()),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.handlers = AdminHandlers(
            seed_admin_ids={10}, users_lock=asyncio.Lock(),
            load_users=user_registry.load_users, save_users=user_registry.save_users,
            require_private_chat=AsyncMock(side_effect=lambda update: update.effective_chat.type == "private"),
            ensure_user_saved=AsyncMock(), add_admin=AsyncMock(),
            main_keyboard_for=lambda uid: ui.get_main_keyboard(),
            audit_file=root / "audit.json", consent_file=root / "consent.json",
            bot_profile=lambda: "test",
        )
        self.context = SimpleNamespace(user_data={}, bot=SimpleNamespace(send_message=AsyncMock()))
        users = {"10": {"id": 10}, "42": {"id": 42, "username": "client", "vip": False}}
        user_registry.accept_policy(users, 10)
        user_registry.accept_user_agreement(users, 10)
        user_registry.save_users(users)

    async def callback(self, data, **kwargs):
        await self.handlers.admin_callback(make_update(callback=data, **kwargs), self.context)

    async def text(self, value, **kwargs):
        return await self.handlers.handle_text(make_update(text=value, **kwargs), self.context, value)

    async def test_search_reason_confirmation_and_duplicate_callback_produce_one_mutation(self):
        await self.callback(ui.CB_ADMIN_USER_FIND)
        self.assertTrue(await self.text("@client"))
        self.assertEqual(self.context.user_data["admin_selected_user"], 42)
        await self.callback(ui.CB_ADMIN_USER_BLOCK)
        self.assertTrue(await self.text("  Проверка   <доступа>  "))
        self.assertFalse(user_registry.is_admin_blocked(user_registry.load_users(), 42))
        self.assertIn("&lt;доступа&gt;", self.reply.await_args.args[0])
        await self.callback(ui.CB_ADMIN_CONFIRM)
        await self.callback(ui.CB_ADMIN_CONFIRM)
        self.assertTrue(user_registry.is_admin_blocked(user_registry.load_users(), 42))
        entries = await admin_audit.recent(self.handlers.audit_file)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["actor_id"], 10)
        self.assertEqual(entries[0]["target_ids"], [42])
        self.assertEqual(entries[0]["action"], "user_block")
        self.assertEqual(entries[0]["reason"], "Проверка <доступа>")
        self.assertNotIn("admin_confirm", self.context.user_data)

    async def test_vip_import_retains_preview_reason_and_audit_after_confirmation(self):
        await self.callback(ui.CB_ADMIN_VIP_IMPORT)
        self.assertTrue(await self.text("42\n43\n42\nbad"))
        self.assertFalse(user_registry.load_users()["42"]["vip"])
        self.assertTrue(await self.text("Участники курса"))
        await self.callback(ui.CB_ADMIN_BATCH_CONFIRM)
        await self.callback(ui.CB_ADMIN_BATCH_CONFIRM)
        users = user_registry.load_users()
        self.assertTrue(users["42"]["vip"])
        self.assertTrue(users["43"]["vip"])
        entries = await admin_audit.recent(self.handlers.audit_file)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["target_ids"], [42, 43])
        self.assertEqual(entries[0]["reason"], "Участники курса")
        self.assertEqual(entries[0]["meta"], {"granted": 2, "already_vip": 0, "duplicates": 1, "invalid": 1})

    async def test_vip_codes_remain_unchanged_until_confirmation(self):
        await self.callback(ui.CB_ADMIN_VIP_ADD)
        self.assertTrue(await self.text("ONE\nTWO\nONE"))
        self.assertEqual(await vip_codes.counts(), (0, 0))
        await self.callback(ui.CB_ADMIN_BATCH_CONFIRM)
        await self.callback(ui.CB_ADMIN_BATCH_CONFIRM)
        self.assertEqual(await vip_codes.counts(), (2, 0))
        entries = await admin_audit.recent(self.handlers.audit_file)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "vip_codes_added")

    async def test_cancel_prevents_pending_user_change(self):
        self.assertTrue(await self.text("vip 42 Подарок за обучение"))
        await self.callback(ui.CB_ADMIN_CANCEL)
        await self.callback(ui.CB_ADMIN_CONFIRM)
        self.assertFalse(user_registry.load_users()["42"]["vip"])
        self.assertEqual(await admin_audit.recent(self.handlers.audit_file), [])

    async def test_home_clears_all_admin_input_and_confirmation_state(self):
        self.context.user_data.update({key: "pending" for key in (
            "admin_mode", "admin_batch", "admin_user_action", "admin_confirm", "admin_selected_user",
        )})
        await self.callback(ui.CB_ADMIN_HOME)
        self.assertEqual(self.context.user_data, {})
        self.edit.assert_awaited_once()

    async def test_denied_callback_cannot_consume_pending_change_or_export(self):
        pending = {"cmd": "block", "target": 42, "reason": "Проверка"}
        for options in ({"user_id": 20}, {"chat_type": "supergroup"}):
            for data in (ui.CB_ADMIN_CONFIRM, ui.CB_ADMIN_LIST_ALL):
                with self.subTest(options=options, data=data):
                    self.context.user_data["admin_confirm"] = dict(pending)
                    await self.callback(data, **options)
                    self.assertEqual(self.context.user_data["admin_confirm"], pending)
        self.document.assert_not_awaited()
        self.assertFalse(user_registry.is_admin_blocked(user_registry.load_users(), 42))
        self.assertEqual(await admin_audit.recent(self.handlers.audit_file), [])

    async def test_outdated_consent_blocks_changes_and_export(self):
        users = user_registry.load_users()
        users["10"]["policy_version"] = "old"
        user_registry.save_users(users)
        self.context.user_data["admin_confirm"] = {"cmd": "block", "target": 42, "reason": "Проверка"}
        await self.callback(ui.CB_ADMIN_CONFIRM)
        await self.callback(ui.CB_ADMIN_LIST_ALL)
        self.document.assert_not_awaited()
        self.assertIn("admin_confirm", self.context.user_data)
        self.assertFalse(user_registry.is_admin_blocked(user_registry.load_users(), 42))

    async def test_seed_admin_cannot_be_changed_by_text_confirmation(self):
        self.assertTrue(await self.text("block 10 Проверка защиты"))
        await self.callback(ui.CB_ADMIN_CONFIRM)
        self.assertFalse(user_registry.is_admin_blocked(user_registry.load_users(), 10))
        self.assertEqual(await admin_audit.recent(self.handlers.audit_file), [])
        self.assertEqual(self.reply.await_args.args[0], ui.ADMIN_USER_PROTECTED)

    async def test_csv_callback_sends_safe_export_from_injected_registry(self):
        users = user_registry.load_users()
        users["42"]["first_name"] = "=1+1"
        user_registry.save_users(users)
        await self.callback(ui.CB_ADMIN_LIST_ALL)
        document = self.document.await_args.kwargs["document"]
        self.assertEqual(document.filename, "users_all.csv")
        rows = list(csv.DictReader(io.StringIO(document.input_file_content.decode("utf-8-sig")), delimiter=";"))
        client = next(row for row in rows if row["id"] == "42")
        self.assertEqual(client["first_name"], "'=1+1")

    async def test_edit_failure_falls_back_to_new_panel_message(self):
        self.edit.side_effect = BadRequest("message cannot be edited")
        await self.callback(ui.CB_ADMIN_HOME)
        self.assertEqual(self.reply.await_args.args[0], ui.ADMIN_STUB)

    async def test_regular_text_remains_available_for_normal_bot_routing(self):
        self.assertFalse(await self.text("11:11", user_id=42))
        self.assertFalse(await self.text("Погода"))
        self.reply.assert_not_awaited()
