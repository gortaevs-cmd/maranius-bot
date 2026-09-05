import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from handlers import vip as vip_handlers
from integrations import admin_alerts, user_registry, vip_codes
import ui


class VipCodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(vip_codes, "CODES_FILE", Path(self.tmp.name) / "codes.json")
        self.path_patch.start()

    async def asyncTearDown(self):
        self.path_patch.stop()
        self.tmp.cleanup()

    async def test_one_code_can_only_be_redeemed_once_concurrently(self):
        await vip_codes.add_codes_bulk("ONE-CODE")
        results = await asyncio.gather(
            vip_codes.redeem_code("ONE-CODE", user_id=1),
            vip_codes.redeem_code("ONE-CODE", user_id=2),
        )
        self.assertEqual(sum(1 for ok, _ in results if ok), 1)
        self.assertEqual(await vip_codes.counts(), (0, 1))

    async def test_failed_vip_grant_can_return_code_to_active_pool(self):
        await vip_codes.add_codes_bulk("SAFE-CODE")
        ok, _ = await vip_codes.redeem_code("SAFE-CODE", user_id=7)
        self.assertTrue(ok)
        self.assertTrue(await vip_codes.rollback_redemption("SAFE-CODE", user_id=7))
        self.assertEqual(await vip_codes.counts(), (1, 0))

    async def test_preview_counts_new_duplicates_and_blank_lines(self):
        await vip_codes.add_codes_bulk("USED")
        preview = await vip_codes.preview_codes_bulk("NEW\nUSED\n\n# comment\nNEW")
        self.assertEqual(preview, (1, 2, 1))

    async def test_admin_approved_invalid_code_is_recorded_as_used_once(self):
        self.assertTrue(await vip_codes.mark_code_used("TDO070726R", user_id=1835235749))
        self.assertFalse(await vip_codes.mark_code_used("tdo070726r", user_id=1835235749))
        store = await vip_codes.load_store()
        self.assertEqual(store["used"][0]["code"], "tdo070726r")
        self.assertEqual(store["used"][0]["user_id"], 1835235749)
        self.assertEqual(store["used"][0]["source"], "admin_approved_invalid_code")

    async def test_vip_approval_uses_code_from_its_alert(self):
        message = SimpleNamespace(
            text="⚠️ Неверный VIP-код\nПользователь: Екатерина\nUser: 1835235749\nВвод: TDO070726R",
            reply_text=AsyncMock(),
        )
        query = SimpleNamespace(
            data="vip:approve:1835235749",
            message=message,
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        grant = AsyncMock()
        mark_used = AsyncMock()
        audit = AsyncMock()

        await vip_handlers.vip_approve_callback(
            update,
            context,
            admin_guard=AsyncMock(return_value=True),
            grant_vip=grant,
            mark_code_used=mark_used,
            audit_action=audit,
        )

        grant.assert_awaited_once_with(1835235749)
        mark_used.assert_awaited_once_with("TDO070726R", 1835235749)
        audit.assert_awaited_once_with("vip_grant_from_alert", 1835235749, "TDO070726R")

    async def test_admin_notifications_make_text_copyable_and_returned_name_clickable(self):
        bot_mock = SimpleNamespace(send_message=AsyncMock())
        await admin_alerts.notify_inbox_entry(
            bot_mock,
            {1},
            user_id=1835235749,
            username=None,
            entry_type="unknown_command",
            text="TDO070726R",
        )
        inbox_text = bot_mock.send_message.await_args.kwargs["text"]
        self.assertIn("<code>TDO070726R</code>", inbox_text)

        bot_mock.send_message.reset_mock()
        await admin_alerts.notify_legacy_contact_return(
            bot_mock,
            {1},
            user_id=1835235749,
            username=None,
            first_name="Екатерина",
            source="legacy_broadcast_delivery_2026-09-01",
        )
        return_text = bot_mock.send_message.await_args.kwargs["text"]
        self.assertIn('href="tg://user?id=1835235749"', return_text)

    def test_wrong_vip_alert_includes_copyable_user_id(self):
        text = ui.ADMIN_VIP_WRONG_CODE.format(
            user_link="Екатерина", user_id=1835235749, code="TDO070726R"
        )
        self.assertIn("User: <code>1835235749</code>", text)

    async def test_successful_redemption_notifies_admin_with_entered_code(self):
        await vip_codes.add_codes_bulk("VIP-CODE")
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7, username="vip_user", first_name="Вера"),
            message=message,
        )
        context = SimpleNamespace(user_data={"awaiting_vip_code": True})
        grant_vip = AsyncMock()
        show_vip_home = AsyncMock()
        notify_success = AsyncMock()

        handled = await vip_handlers.try_vip_code(
            update,
            context,
            "  Vip-Code  ",
            is_vip=lambda _: False,
            grant_vip=grant_vip,
            show_vip_home=show_vip_home,
            notify_wrong=AsyncMock(),
            protect_kwargs={},
            notify_success=notify_success,
        )

        self.assertTrue(handled)
        grant_vip.assert_awaited_once_with(7)
        notify_success.assert_awaited_once_with(
            context,
            user_id=7,
            username="vip_user",
            first_name="Вера",
            code="  Vip-Code  ",
        )
        self.assertNotIn("awaiting_vip_code", context.user_data)


class AdminGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_user_god_command_does_not_save_profile(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=999999),
            effective_message=message,
        )
        with patch.object(bot, "ensure_user_saved", new=AsyncMock()) as ensure_saved:
            await bot.god_cmd(update, SimpleNamespace())
        ensure_saved.assert_not_awaited()
        message.reply_text.assert_awaited_once()

    async def test_regular_user_cannot_use_admin_callback(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=999999),
            effective_message=message,
            callback_query=SimpleNamespace(from_user=SimpleNamespace(id=999999)),
        )
        self.assertFalse(await bot._admin_guard(update))
        message.reply_text.assert_awaited_once()

    async def test_seed_admin_passes_admin_guard_after_required_acceptances(self):
        admin_id = next(iter(bot.SEED_ADMIN_IDS))
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=admin_id),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
            callback_query=None,
        )
        users = {
            str(admin_id): {
                "id": admin_id,
                "policy_accepted_at": "2026-08-31T00:00:00Z",
                "policy_version": user_registry.PERSONAL_DATA_CONSENT_VERSION,
                "user_agreement_accepted_at": "2026-08-31T00:00:00Z",
                "user_agreement_version": user_registry.USER_AGREEMENT_VERSION,
            }
        }
        with patch.object(bot, "_load_users", return_value=users):
            self.assertTrue(await bot._admin_guard(update))


if __name__ == "__main__":
    unittest.main()
