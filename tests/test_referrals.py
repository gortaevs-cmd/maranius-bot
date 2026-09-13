import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import ui
from integrations import referrals


def _users():
    return {
        "100": {"id": 100, "bot_status": "active"},
        "200": {"id": 200, "bot_status": "active"},
    }


class ReferralProgramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_file = referrals.REFERRALS_FILE
        referrals.REFERRALS_FILE = Path(self.tmp.name) / "referrals.json"

    async def asyncTearDown(self):
        referrals.REFERRALS_FILE = self.old_file
        self.tmp.cleanup()

    async def test_code_is_stable_and_payload_is_strict(self):
        first = await referrals.get_or_create_code(100)
        second = await referrals.get_or_create_code(100)
        self.assertEqual(first, second)
        self.assertEqual(referrals.parse_referral_payload(f"ref_{first.lower()}"), first)
        self.assertIsNone(referrals.parse_referral_payload("ref_short"))
        self.assertIsNone(referrals.parse_referral_payload("utm_source=telegram"))

    async def test_referral_qualifies_once_after_two_days(self):
        users = _users()
        code = await referrals.get_or_create_code(100)
        start = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        self.assertEqual(
            await referrals.register_referral(200, f"ref_{code}", users, now=start),
            "registered",
        )
        self.assertEqual(
            await referrals.record_activity(200, users, now=start), "pending"
        )
        self.assertEqual(
            await referrals.record_activity(
                200, users, now=start + timedelta(hours=24, minutes=1)
            ),
            "qualified",
        )
        dashboard = await referrals.get_dashboard(100, users, "MaraniusBOT")
        self.assertEqual(dashboard["balance"], referrals.ACTIVATION_REWARD_POINTS)
        self.assertEqual(dashboard["qualified"], 1)

        # Повторный callback/визит не даёт второго начисления.
        self.assertIsNone(
            await referrals.record_activity(
                200, users, now=start + timedelta(days=2)
            )
        )
        dashboard = await referrals.get_dashboard(100, users, "MaraniusBOT")
        self.assertEqual(dashboard["balance"], referrals.ACTIVATION_REWARD_POINTS)

    async def test_self_referral_and_blocked_referrer_are_rejected(self):
        users = _users()
        code = await referrals.get_or_create_code(100)
        self.assertEqual(
            await referrals.register_referral(100, f"ref_{code}", users), "rejected"
        )
        users["100"]["admin_blocked"] = True
        self.assertEqual(
            await referrals.register_referral(200, f"ref_{code}", users), "rejected"
        )

    async def test_redeem_is_atomic_and_requires_balance(self):
        users = _users()
        code = await referrals.get_or_create_code(100)
        start = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        await referrals.register_referral(200, f"ref_{code}", users, now=start)
        await referrals.record_activity(200, users, now=start)
        await referrals.record_activity(200, users, now=start + timedelta(days=1))
        insufficient = await referrals.redeem_reward(100, "coupon10", now=start)
        self.assertFalse(insufficient["ok"])
        self.assertEqual(insufficient["reason"], "insufficient")

        # Два квалифицированных реферала дают 200, всё ещё меньше 300.
        users["300"] = {"id": 300, "bot_status": "active"}
        code = await referrals.get_or_create_code(100)
        await referrals.register_referral(300, f"ref_{code}", users, now=start)
        await referrals.record_activity(300, users, now=start)
        await referrals.record_activity(300, users, now=start + timedelta(days=1))
        insufficient = await referrals.redeem_reward(100, "coupon10", now=start)
        self.assertFalse(insufficient["ok"])

        users["400"] = {"id": 400, "bot_status": "active"}
        await referrals.register_referral(400, f"ref_{code}", users, now=start)
        await referrals.record_activity(400, users, now=start)
        await referrals.record_activity(400, users, now=start + timedelta(days=1))
        redeemed = await referrals.redeem_reward(100, "coupon10", now=start)
        self.assertTrue(redeemed["ok"])
        self.assertEqual(redeemed["balance"], 0)
        retry = await referrals.redeem_reward(100, "coupon10", now=start)
        self.assertFalse(retry["ok"])
        self.assertEqual(retry["reason"], "insufficient")


class ReferralUiTests(unittest.TestCase):
    def test_profile_contains_referral_entry_and_registered_callback(self):
        buttons = [
            button
            for row in ui.get_profile_menu_keyboard().inline_keyboard
            for button in row
        ]
        referral = next(button for button in buttons if button.callback_data == ui.CB_PROFILE_REFERRAL)
        self.assertEqual(referral.text, ui.BTN_PROFILE_REFERRAL)
        self.assertIn("в работе", ui.MSG_REFERRAL_IN_PROGRESS.casefold())


class ReferralUiCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_referral_button_shows_in_progress_message_without_dashboard(self):
        message = SimpleNamespace()
        query = SimpleNamespace(
            message=message,
            from_user=SimpleNamespace(id=100),
            data=ui.CB_PROFILE_REFERRAL,
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query, update_id=1)
        context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

        with patch.object(bot, "_require_access", new=AsyncMock(return_value=True)), patch.object(
            bot, "_update_from_callback", return_value=None
        ), patch.object(bot, "_edit_or_reply", new=AsyncMock()) as render, patch.object(
            bot, "_render_referral_dashboard", new=AsyncMock()
        ) as dashboard:
            await bot.more_callback(update, context)

        query.answer.assert_awaited_once()
        self.assertEqual(render.await_args.args[:2], (message, ui.MSG_REFERRAL_IN_PROGRESS))
        dashboard.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
