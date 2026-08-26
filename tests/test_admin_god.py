import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from integrations import admin_audit, user_registry, vip_codes


class AdminSegmentsTests(unittest.TestCase):
    def test_marketing_ready_requires_every_delivery_safeguard(self):
        predicate = user_registry.segment_filter("marketing_ready")
        ready = {
            "marketing_opt_in": True,
            "policy_accepted_at": "2026-08-01T00:00:00Z",
            "bot_status": "active",
            "admin_blocked": False,
            "is_internal": False,
        }
        self.assertTrue(predicate(ready))
        for field, value in (
            ("marketing_opt_in", False),
            ("policy_accepted_at", ""),
            ("bot_status", "blocked"),
            ("admin_blocked", True),
            ("is_internal", True),
        ):
            row = dict(ready)
            row[field] = value
            self.assertFalse(predicate(row), field)

    def test_legacy_segment_names_remain_compatible(self):
        row = {"bot_status": "active", "vip": True, "marketing_opt_in": True}
        self.assertTrue(user_registry.segment_filter("subscribed")(row))
        self.assertTrue(user_registry.segment_filter("vip")(row))
        self.assertTrue(user_registry.segment_filter("marketing")(row))


class AdminAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_records_deduplicated_targets_and_exports_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "admin_audit.json"
            entry = await admin_audit.append(
                path,
                actor_id=10,
                action="vip_import",
                target_ids=[22, 22, "23", 0],
                reason="  Подарок  за   курс ",
                meta={"granted": 2},
            )
            self.assertEqual(entry["target_ids"], [22, 23])
            self.assertEqual(entry["reason"], "Подарок за курс")
            recent = await admin_audit.recent(path)
            self.assertEqual(len(recent), 1)
            self.assertIn("vip_import", (await admin_audit.export_csv_bytes(path)).decode("utf-8-sig"))

    async def test_confirming_code_batch_writes_codes_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            message = SimpleNamespace(reply_text=AsyncMock())
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=10),
                effective_message=message,
            )
            context = SimpleNamespace(
                user_data={
                    "admin_batch": {
                        "kind": "vip_codes",
                        "raw": "ONE\nTWO",
                        "reason": "Коды для курса",
                    }
                }
            )
            with patch.object(vip_codes, "CODES_FILE", root / "codes.json"), patch.object(
                bot, "ADMIN_AUDIT_FILE", root / "admin_audit.json"
            ):
                await bot._admin_confirm_batch(update, context)
                self.assertEqual(await vip_codes.counts(), (2, 0))
                rows = await admin_audit.recent(root / "admin_audit.json")
            self.assertEqual(rows[0]["action"], "vip_codes_added")
            message.reply_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
