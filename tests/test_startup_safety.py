import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot


class StartupSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_init_preserves_pending_updates(self):
        application = SimpleNamespace(
            bot=SimpleNamespace(delete_webhook=AsyncMock(), get_me=AsyncMock(
                return_value=SimpleNamespace(username="TestBot", id=123456))),
            bot_data={}, job_queue=None,
        )
        with patch.object(bot, "ensure_seed_admins"), patch.object(bot, "ensure_seed_vip"), \
             patch.object(bot, "_apply_pending_legacy_migration", return_value=None), \
             patch.object(bot.inbox_mod, "migrate_legacy_unknown_csv", new=AsyncMock(return_value=0)), \
             patch.object(bot, "sync_bot_commands", new=AsyncMock(return_value=5)), \
             patch.object(bot, "_schedule_reports"), \
             patch.object(bot, "heartbeat_loop", new=AsyncMock()), \
             patch.object(bot, "_notify_storage_recoveries", new=AsyncMock()):
            try:
                await bot._maranius_post_init(application)
                application.bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
            finally:
                await bot._stop_technical_heartbeat(application)


class PollingSafetyTests(unittest.TestCase):
    def test_application_polling_preserves_pending_updates(self):
        builder = Mock()
        for name in ("token", "connect_timeout", "read_timeout", "write_timeout", "media_write_timeout",
                     "post_init", "post_stop"):
            getattr(builder, name).return_value = builder
        application = builder.build.return_value
        with patch.object(bot, "load_dotenv"), \
             patch.object(bot, "resolve_bot_token", return_value=("123456:TEST_ONLY", "test")), \
             patch.object(bot, "ensure_seed_admins"), patch.object(bot, "ensure_seed_vip"), \
             patch.object(bot.events_storage, "init_storage"), \
             patch.object(bot, "TELEGRAM_PROXY_URL", ""), \
             patch.object(bot.Application, "builder", return_value=builder), \
             patch.object(bot, "BOT_TOKEN", None), patch.object(bot, "BOT_PROFILE_ACTIVE", "test"):
            bot.main()
        application.run_polling.assert_called_once()
        self.assertIs(application.run_polling.call_args.kwargs["drop_pending_updates"], False)
