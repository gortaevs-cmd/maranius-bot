import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot
import ui


class ErrorHandlingTests(unittest.TestCase):
    def test_unexpected_error_notifies_user_and_admin(self):
        async def scenario():
            message = SimpleNamespace(reply_text=AsyncMock())
            update = SimpleNamespace(effective_message=message)
            context = SimpleNamespace(
                error=RuntimeError("service <down>"),
                bot=SimpleNamespace(send_message=AsyncMock()),
            )

            await bot._on_error(update, context)

            message.reply_text.assert_awaited_once_with(
                ui.MSG_TECHNICAL_ERROR,
                reply_markup=ui.get_main_keyboard(),
            )
            self.assertEqual(context.bot.send_message.await_count, len(bot.SEED_ADMIN_IDS))
            alert = context.bot.send_message.await_args.args[1]
            self.assertIn("service &lt;down&gt;", alert)
            self.assertNotIn("service <down>", alert)

        asyncio.run(scenario())

    def test_failed_error_notifications_do_not_escape_handler(self):
        async def scenario():
            message = SimpleNamespace(reply_text=AsyncMock(side_effect=RuntimeError("Telegram down")))
            update = SimpleNamespace(effective_message=message)
            context = SimpleNamespace(
                error=RuntimeError("original"),
                bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("Telegram down"))),
            )
            await bot._on_error(update, context)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
