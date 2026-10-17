import copy
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from integrations import admin_audit, consent_log, inbox, user_registry, vip_codes
from integrations.csv_export import SafeCsvWriter, safe_csv_cell
from integrations.json_storage import save_json


def read_rows(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))


class SafeCsvCellTests(unittest.TestCase):
    def test_formula_prefixes_and_control_characters_are_exported_as_text(self):
        for value in ("=1+1", "+SUM(1)", "-1+2", "@SUM(1)", "  =1", "\ufeff=1", "\tname", "\rname", "\nname", " \t@SUM(1)"):
            with self.subTest(value=repr(value)):
                self.assertEqual(safe_csv_cell(value), "'" + value)

    def test_ordinary_values_numbers_and_csv_quoting_are_preserved(self):
        values = [42, -42, None, "", "Имя; фамилия", '"Цитата"\nвторая строка', "already'safe", "https://maranius.ru/"]
        buf = io.StringIO()
        SafeCsvWriter(buf).writerow(values)
        row = next(csv.reader(io.StringIO(buf.getvalue()), delimiter=";"))
        self.assertEqual(row, ["42", "-42", "", ""] + values[4:])


class CsvExportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "log.json"

    async def test_users_export_neutralizes_profile_and_source_without_mutating_registry(self):
        users = {"42": {"id": 42, "username": "@SUM(1)", "first_name": " =1+1", "last_name": "Фамилия; имя",
                        "start_param": "\t=2", "vip_source": "+cmd", "vip": False}}
        before = copy.deepcopy(users)
        row = read_rows(user_registry.export_users_csv(users))[0]
        self.assertEqual(row["id"], "42")
        self.assertEqual(row["username"], "'@SUM(1)")
        self.assertEqual(row["first_name"], "' =1+1")
        self.assertEqual(row["start_param"], "'\t=2")
        self.assertEqual(row["vip_source"], "'+cmd")
        self.assertEqual(row["last_name"], "Фамилия; имя")
        self.assertEqual(users, before)

    async def test_inbox_export_keeps_original_text_and_export_timestamp(self):
        store = {"entries": [{"id": "test", "created_at": inbox._now_iso(), "user_id": 42,
                              "username": "=NAME()", "text": "-1+2", "meta": {"original": "=1"}}]}
        save_json(self.path, store)
        with patch.object(inbox, "INBOX_FILE", self.path):
            data, downloaded_at = await inbox.export_csv_bytes()
            stored = inbox._load_unlocked()["entries"][0]
        row = read_rows(data)[0]
        self.assertEqual(row["username"], "'=NAME()")
        self.assertEqual(row["text"], "'-1+2")
        self.assertEqual(json.loads(row["meta_json"]), {"original": "=1"})
        self.assertEqual(row["downloaded_at"], downloaded_at)
        self.assertEqual(stored["text"], "-1+2")
        self.assertEqual(stored["exported_at"], downloaded_at)

    async def test_vip_export_neutralizes_codes_and_usernames_without_changing_codes(self):
        store = {"active": ["=CODE()"], "used": [{"code": "+code", "user_id": 42, "username": "@user"}]}
        before = copy.deepcopy(store)
        with patch.object(vip_codes, "load_store", new=AsyncMock(return_value=store)):
            rows = read_rows(await vip_codes.export_csv_bytes())
        self.assertEqual(rows[0]["code"], "'=CODE()")
        self.assertEqual(rows[1]["code"], "'+code")
        self.assertEqual(rows[1]["username"], "'@user")
        self.assertEqual(store, before)

    async def test_consent_export_neutralizes_source_and_preserves_metadata(self):
        store = {"events": [{"id": "test", "user_id": 42, "source": "\ufeff=1", "meta": {"raw": "=1"}}]}
        save_json(self.path, store)
        before = self.path.read_bytes()
        row = read_rows(await consent_log.export_csv_bytes(self.path))[0]
        self.assertEqual(row["source"], "'\ufeff=1")
        self.assertEqual(json.loads(row["meta_json"]), {"raw": "=1"})
        self.assertEqual(self.path.read_bytes(), before)

    async def test_admin_audit_export_neutralizes_reason_without_mutating_history(self):
        store = {"events": [{"id": "test", "actor_id": 42, "target_ids": [1, 2], "reason": "\r=1", "meta": {"reason": "=1"}}]}
        save_json(self.path, store)
        before = self.path.read_bytes()
        row = read_rows(await admin_audit.export_csv_bytes(self.path))[0]
        self.assertEqual(row["reason"], "'\r=1")
        self.assertEqual(row["target_ids"], "1,2")
        self.assertEqual(json.loads(row["meta_json"]), {"reason": "=1"})
        self.assertEqual(self.path.read_bytes(), before)
