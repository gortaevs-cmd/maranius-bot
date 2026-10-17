import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot
import ui
from integrations import admin_audit, consent_log, inbox as inbox_mod, user_registry, vip_codes


class AdminSegmentsTests(unittest.TestCase):
    def test_marketing_ready_requires_every_delivery_safeguard(self):
        predicate = user_registry.segment_filter("marketing_ready")
        ready = {
            "marketing_opt_in": True,
            "marketing_consent_version": user_registry.MARKETING_CONSENT_VERSION,
            "policy_accepted_at": "2026-08-01T00:00:00Z",
            "policy_version": user_registry.PERSONAL_DATA_CONSENT_VERSION,
            "user_agreement_accepted_at": "2026-08-01T00:00:00Z",
            "user_agreement_version": user_registry.USER_AGREEMENT_VERSION,
            "bot_status": "active",
            "admin_blocked": False,
            "is_internal": False,
        }
        self.assertTrue(predicate(ready))
        for field, value in (
            ("marketing_opt_in", False),
            ("policy_accepted_at", ""),
            ("user_agreement_accepted_at", ""),
            ("bot_status", "blocked"),
            ("admin_blocked", True),
            ("is_internal", True),
        ):
            row = dict(ready)
            row[field] = value
            self.assertFalse(predicate(row), field)

    def test_legacy_segment_names_remain_compatible(self):
        row = {
            "bot_status": "active",
            "vip": True,
            "marketing_opt_in": True,
            "marketing_consent_version": user_registry.MARKETING_CONSENT_VERSION,
        }
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

    async def test_consent_log_exports_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "consent_log.json"
            await consent_log.append(
                path,
                user_id=42,
                event="policy_accepted",
                value=True,
                policy_version=user_registry.PERSONAL_DATA_CONSENT_VERSION,
                purpose="personal_data_processing",
                document="personal-data-consent",
                document_url="https://maranius.ru/legal/personal-data-consent/",
                action="consent:policy:accept",
                source="gate",
            )
            csv_text = (await consent_log.export_csv_bytes(path)).decode("utf-8-sig")
            self.assertIn("policy_accepted", csv_text)
            self.assertIn("42", csv_text)

    async def test_inbox_export_serializes_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox_path = Path(tmp) / "inbox.json"
            with patch.object(inbox_mod, "INBOX_FILE", inbox_path):
                await inbox_mod.add_entry(
                    entry_type="unknown_command",
                    user_id=42,
                    username="tester",
                    text="test",
                    meta={"source": "god"},
                )
                data, _ = await inbox_mod.export_csv_bytes()

            rows = list(
                csv.DictReader(io.StringIO(data.decode("utf-8-sig")), delimiter=";")
            )
            self.assertEqual(json.loads(rows[0]["meta_json"]), {"source": "god"})

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
                    }
                }
            )
            with patch.object(vip_codes, "CODES_FILE", root / "codes.json"), patch.object(
                bot._admin_handlers, "audit_file", root / "admin_audit.json"
            ):
                await bot._admin_handlers._admin_confirm_batch(update, context)
                self.assertEqual(await vip_codes.counts(), (2, 0))
                rows = await admin_audit.recent(root / "admin_audit.json")
            self.assertEqual(rows[0]["action"], "vip_codes_added")
            self.assertEqual(rows[0]["reason"], bot.admin_handlers.VIP_CODES_AUDIT_REASON)
            message.reply_text.assert_awaited_once()

    async def test_adding_codes_moves_straight_to_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            message = SimpleNamespace(reply_text=AsyncMock())
            update = SimpleNamespace(effective_message=message)
            context = SimpleNamespace(user_data={"admin_mode": "vip_add"})
            with patch.object(vip_codes, "CODES_FILE", Path(tmp) / "codes.json"):
                await bot._admin_handlers._admin_prepare_vip_codes(update, context, "ONE\nTWO")

            self.assertNotIn("admin_mode", context.user_data)
            batch = context.user_data["admin_batch"]
            self.assertEqual(batch["reason"], bot.admin_handlers.VIP_CODES_AUDIT_REASON)
            self.assertEqual(batch["preview"], {"added": 2, "duplicates": 0, "skipped": 0})
            self.assertEqual(
                message.reply_text.call_args.args[0],
                ui.ADMIN_BATCH_CODE_PREVIEW.format(added=2, dup=0, skipped=0),
            )
            self.assertEqual(message.reply_text.call_args.kwargs["parse_mode"], "HTML")
            self.assertIsNotNone(message.reply_text.call_args.kwargs["reply_markup"])


class ReportScheduleTests(unittest.TestCase):
    def test_weekly_report_runs_on_sunday_at_noon_moscow(self):
        run_daily = Mock()
        application = SimpleNamespace(job_queue=SimpleNamespace(run_daily=run_daily))

        bot._schedule_reports(application)

        weekly_call = next(
            call for call in run_daily.call_args_list if call.kwargs["name"] == "weekly_report"
        )
        report_time = weekly_call.kwargs["time"]
        self.assertEqual(weekly_call.kwargs["days"], (0,))
        self.assertEqual((report_time.hour, report_time.minute), (12, 0))
        self.assertEqual(str(report_time.tzinfo), "Europe/Moscow")


if __name__ == "__main__":
    unittest.main()
