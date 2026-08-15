import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from integrations import vip_codes


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


class AdminGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_user_cannot_use_admin_callback(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=999999),
            effective_message=message,
            callback_query=SimpleNamespace(from_user=SimpleNamespace(id=999999)),
        )
        self.assertFalse(await bot._admin_guard(update))
        message.reply_text.assert_awaited_once()

    async def test_seed_admin_passes_admin_guard(self):
        admin_id = next(iter(bot.SEED_ADMIN_IDS))
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=admin_id),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
            callback_query=None,
        )
        self.assertTrue(await bot._admin_guard(update))


if __name__ == "__main__":
    unittest.main()
