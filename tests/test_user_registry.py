import tempfile
import unittest
from pathlib import Path

from integrations import consent_log, user_registry


class MarketingOptInTests(unittest.TestCase):
    def test_opt_out_preserves_original_opt_in_timestamp(self):
        users = {
            "42": {
                "id": 42,
                "marketing_opt_in": True,
                "marketing_opt_in_at": "2026-08-01T10:00:00Z",
            }
        }
        user_registry.set_marketing_opt_in(users, 42, False)
        row = users["42"]
        self.assertFalse(row["marketing_opt_in"])
        self.assertEqual(row["marketing_opt_in_at"], "2026-08-01T10:00:00Z")
        self.assertIn("marketing_opt_out_at", row)

    def test_opt_in_clears_opt_out_timestamp(self):
        users = {
            "42": {
                "id": 42,
                "marketing_opt_in": False,
                "marketing_opt_out_at": "2026-08-02T10:00:00Z",
            }
        }
        user_registry.set_marketing_opt_in(users, 42, True)
        self.assertTrue(users["42"]["marketing_opt_in"])
        self.assertNotIn("marketing_opt_out_at", users["42"])


class PolicyVersionTests(unittest.TestCase):
    def test_has_current_policy_requires_matching_version(self):
        users = {
            "1": {"policy_accepted_at": "2026-01-01T00:00:00Z", "policy_version": "2024-08-03"},
            "2": {"policy_accepted_at": "2026-01-01T00:00:00Z", "policy_version": "2020-01-01"},
        }
        self.assertTrue(user_registry.has_current_policy(users, 1))
        self.assertFalse(user_registry.has_current_policy(users, 2))

    def test_no_policy_segment_includes_outdated_version(self):
        predicate = user_registry.segment_filter("no_policy")
        self.assertTrue(predicate({"policy_accepted_at": "", "policy_version": ""}))
        self.assertTrue(
            predicate({"policy_accepted_at": "2026-01-01T00:00:00Z", "policy_version": "old"})
        )
        self.assertFalse(
            predicate({"policy_accepted_at": "2026-01-01T00:00:00Z", "policy_version": "2024-08-03"})
        )


class StartParamTests(unittest.TestCase):
    def test_capture_start_param_only_once(self):
        users = {}
        self.assertTrue(user_registry.capture_start_param(users, 7, "  utm_campaign=test  "))
        self.assertEqual(users["7"]["start_param"], "utm_campaign=test")
        self.assertFalse(user_registry.capture_start_param(users, 7, "other"))


class ConsentLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "consent_log.json"
            first = await consent_log.append(
                path,
                user_id=42,
                event="policy_accepted",
                value=True,
                policy_version="2024-08-03",
                source="gate",
            )
            await consent_log.append(
                path,
                user_id=42,
                event="marketing_opt_in",
                value=True,
                source="marketing_offer",
            )
            rows = await consent_log.recent(path, user_id=42, limit=10)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["id"], first["id"])
            self.assertEqual(rows[0]["event"], "marketing_opt_in")


class ExportCsvTests(unittest.TestCase):
    def test_export_includes_marketing_timestamps(self):
        users = {
            "1": {
                "id": 1,
                "marketing_opt_in": True,
                "marketing_opt_in_at": "2026-08-01T10:00:00Z",
                "language_code": "ru",
                "timezone": "Europe/Moscow",
            }
        }
        text = user_registry.export_users_csv(users).decode("utf-8-sig")
        self.assertIn("marketing_opt_in_at", text)
        self.assertIn("language_code", text)
        self.assertIn("Europe/Moscow", text)


if __name__ == "__main__":
    unittest.main()
