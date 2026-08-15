import json
import tempfile
import unittest
from pathlib import Path

from integrations.json_storage import JsonStorageError, load_json, save_json


class JsonStorageTests(unittest.TestCase):
    def test_missing_file_returns_independent_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = {"items": []}
            loaded = load_json(Path(tmp) / "missing.json", default)
            loaded["items"].append("changed")
            self.assertEqual(default, {"items": []})

    def test_atomic_save_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            save_json(path, {"ok": True, "text": "привет"})
            self.assertEqual(json.loads(path.read_text()), {"ok": True, "text": "привет"})
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_corrupt_existing_file_raises_instead_of_returning_empty_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            path.write_text('{"broken":', encoding="utf-8")
            with self.assertRaises(JsonStorageError):
                load_json(path, {})
            self.assertEqual(path.read_text(encoding="utf-8"), '{"broken":')

    def test_serialization_error_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            path.write_text('{"safe": true}', encoding="utf-8")
            with self.assertRaises(JsonStorageError):
                save_json(path, {"bad": {1, 2, 3}})
            self.assertEqual(path.read_text(encoding="utf-8"), '{"safe": true}')

    def test_corrupt_file_is_restored_from_latest_valid_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            save_json(path, {"version": 1})
            save_json(path, {"version": 2})
            path.write_text('{"broken":', encoding="utf-8")
            recovered = load_json(path, {})
            self.assertEqual(recovered, {"version": 1})
            self.assertEqual(json.loads(path.read_text()), {"version": 1})
            self.assertTrue(list((path.parent / ".json-backups").glob("*.corrupt")))


if __name__ == "__main__":
    unittest.main()
