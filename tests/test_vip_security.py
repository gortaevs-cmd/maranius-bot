import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from handlers import vip as vip_handlers
from integrations import user_registry, vip_codes


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
