import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot
from integrations import legacy_contacts


class LegacyMigrationTests(unittest.TestCase):
    def test_migration_separates_inactive_and_preserves_consent_boundary(self):
        users = {
            "30": {
                "id": 30,
                "first_seen": "2026-08-30T00:00:00Z",
                "policy_accepted_at": "2026-08-30T00:00:00Z",
            }
        }
        inactive_store = legacy_contacts.empty_inactive_store()

        result = legacy_contacts.apply_legacy_migration(
            users,
            inactive_store,
            active_user_ids=[10, 20],
            vip_user_ids=[20],
            inactive_user_ids=[30, 40],
            source="legacy_broadcast_delivery_2026-09-01",
            imported_at="2026-09-01T00:00:00Z",
        )

        self.assertEqual(result["active_created"], 2)
        self.assertEqual(result["vip_granted"], 1)
        self.assertEqual(result["inactive_stored"], 1)
        self.assertEqual(result["inactive_existing_active"], 1)
        self.assertEqual(users["10"]["bot_status"], "active")
        self.assertNotIn("policy_accepted_at", users["10"])
        self.assertTrue(users["20"]["vip"])
        self.assertEqual(users["20"]["vip_source"], "import")
        self.assertNotIn("30", inactive_store["records"])
        self.assertEqual(inactive_store["records"]["40"]["id"], 40)

    def test_migration_rejects_vip_outside_active_set(self):
        with self.assertRaisesRegex(ValueError, "Every VIP ID"):
            legacy_contacts.apply_legacy_migration(
                {},
                legacy_contacts.empty_inactive_store(),
                active_user_ids=[10],
                vip_user_ids=[20],
                inactive_user_ids=[30],
                source="legacy_broadcast_delivery_2026-09-01",
            )

    def test_migration_allows_an_empty_vip_set(self):
        users = {}
        inactive_store = legacy_contacts.empty_inactive_store()
        result = legacy_contacts.apply_legacy_migration(
            users,
            inactive_store,
            active_user_ids=[10],
            vip_user_ids=[],
            inactive_user_ids=[20],
            source="legacy_broadcast_delivery_2026-09-01",
        )
        self.assertEqual(result["vip_granted"], 0)
        self.assertFalse(users["10"].get("vip"))


class LegacyReactivationTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_returning_user_removes_record_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy_inactive_users.json"
            legacy_contacts.save_inactive_store(
                {
                    "schema_version": 1,
                    "records": {
                        "42": {
                            "id": 42,
                            "source": "legacy_broadcast_delivery_2026-09-01",
                        }
                    },
                },
                path,
            )
            with patch.object(legacy_contacts, "INACTIVE_USERS_FILE", path):
                record = await legacy_contacts.claim_returning_inactive_user(42)
                repeated = await legacy_contacts.claim_returning_inactive_user(42)

            self.assertEqual(record["id"], 42)
            self.assertIsNone(repeated)
            self.assertNotIn("42", legacy_contacts.load_inactive_store(path)["records"])

    async def test_return_notification_is_sent_only_after_access_is_current(self):
        user = SimpleNamespace(
            id=42,
            username="returned_user",
            first_name="Return",
            last_name="User",
            language_code="ru",
            is_premium=False,
        )
        update = SimpleNamespace(
            effective_user=user,
            effective_chat=SimpleNamespace(type="private"),
        )
        users = {
            "42": {
                "id": 42,
                "policy_accepted_at": "2026-09-01T00:00:00Z",
                "policy_version": bot.user_registry.PERSONAL_DATA_CONSENT_VERSION,
                "user_agreement_accepted_at": "2026-09-01T00:00:00Z",
                "user_agreement_version": bot.user_registry.USER_AGREEMENT_VERSION,
            }
        }
        claimed = AsyncMock(
            return_value={"source": "legacy_broadcast_delivery_2026-09-01"}
        )
        notify = AsyncMock()
        platform = AsyncMock()

        with (
            patch.object(bot, "_load_users", side_effect=lambda: users),
            patch.object(bot, "_save_users", new=MagicMock()),
            patch.object(
                bot.legacy_contacts, "claim_returning_inactive_user", new=claimed
            ),
            patch.object(
                bot.admin_alerts, "notify_legacy_contact_return", new=notify
            ),
            patch.object(bot.platform_db, "get_or_create_user", new=platform),
        ):
            await bot.ensure_user_saved(update, bot=SimpleNamespace())

        claimed.assert_awaited_once_with(42)
        notify.assert_awaited_once()
        self.assertIn("first_seen", users["42"])

    async def test_return_is_not_claimed_before_required_documents(self):
        user = SimpleNamespace(id=42)
        update = SimpleNamespace(effective_user=user)
        claimed = AsyncMock()

        with (
            patch.object(bot, "_load_users", return_value={}),
            patch.object(
                bot.legacy_contacts, "claim_returning_inactive_user", new=claimed
            ),
        ):
            saved = await bot.ensure_user_saved(update, bot=SimpleNamespace())

        self.assertFalse(saved)
        claimed.assert_not_awaited()


class PendingMigrationTests(unittest.TestCase):
    def test_pending_migration_is_consumed_before_polling_and_keeps_no_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = root / "legacy_migration_pending.json"
            last = root / "legacy_migration_last_result.json"
            inactive = root / "legacy_inactive_users.json"
            pending.write_text(
                '{"source":"legacy_broadcast_delivery_2026-09-01",'
                '"active_user_ids":[10,20],"vip_user_ids":[20],'
                '"inactive_user_ids":[30]}',
                encoding="utf-8",
            )
            users = {}
            with (
                patch.object(legacy_contacts, "PENDING_MIGRATION_FILE", pending),
                patch.object(legacy_contacts, "LAST_MIGRATION_FILE", last),
                patch.object(legacy_contacts, "INACTIVE_USERS_FILE", inactive),
                patch.object(bot, "_load_users", return_value=users),
                patch.object(bot, "_save_users", new=MagicMock()),
            ):
                completed = bot._apply_pending_legacy_migration()

            self.assertIsNotNone(completed)
            self.assertFalse(pending.exists())
            self.assertTrue(last.is_file())
            self.assertTrue(users["20"]["vip"])
            self.assertNotIn("policy_accepted_at", users["10"])
            self.assertEqual(
                legacy_contacts.load_inactive_store(inactive)["records"]["30"]["id"],
                30,
            )


if __name__ == "__main__":
    unittest.main()
