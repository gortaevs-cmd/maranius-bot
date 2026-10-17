import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations import analytics, json_storage as storage, platform_db


class StorageFailureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'users.json'
        storage.pop_recovery_events()

    def test_backup_failure_preserves_original(self):
        storage.save_json(self.path, {'version': 1})
        original = storage._atomic_write
        def fail_backup(path, data, **kwargs):
            if path.suffix == '.bak': raise OSError('backup failed')
            return original(path, data, **kwargs)
        with patch.object(storage, '_atomic_write', side_effect=fail_backup):
            with self.assertRaises(storage.JsonStorageError): storage.save_json(self.path, {'version': 2})
        self.assertEqual(storage.load_json(self.path, {}), {'version': 1})

    def test_save_cannot_overwrite_corruption(self):
        self.path.write_bytes(b'{broken')
        with self.assertRaises(storage.JsonStorageError): storage.save_json(self.path, {'new': 1})
        self.assertEqual(self.path.read_bytes(), b'{broken')

    def test_invalid_utf8_recovers_atomically_and_keeps_original(self):
        storage.save_json(self.path, {'version': 1})
        storage.save_json(self.path, {'version': 2})
        self.path.write_bytes(b'\xff\xfeinvalid')
        with patch.object(storage.os, 'replace', wraps=storage.os.replace) as replace:
            self.assertEqual(storage.load_json(self.path, {}), {'version': 1})
        self.assertTrue(any(Path(call.args[1]) == self.path for call in replace.call_args_list))
        event = storage.pop_recovery_events()[0]
        self.assertEqual(Path(event['corrupt']).read_bytes(), b'\xff\xfeinvalid')
        self.assertEqual(Path(event['corrupt']).stat().st_mode & 0o777, 0o600)

    def test_restore_failure_keeps_corruption_and_original_backup(self):
        storage.save_json(self.path, {'version': 1})
        storage.save_json(self.path, {'version': 2})
        self.path.write_text('{broken')
        original = storage._atomic_write
        def fail_restore(path, data, **kwargs):
            if path == self.path: raise OSError('restore failed')
            return original(path, data, **kwargs)
        with patch.object(storage, '_atomic_write', side_effect=fail_restore):
            with self.assertRaises(storage.JsonStorageError): storage.load_json(self.path, {})
        self.assertEqual(self.path.read_text(), '{broken')
        self.assertTrue(list((self.path.parent / '.json-backups').glob('*.bak')))

    def test_backup_rotation_does_not_parse_all_previous_backups(self):
        for i in range(15): storage.save_json(self.path, {'version': i})
        with patch.object(storage.json, 'loads', wraps=json.loads) as parse:
            storage.save_json(self.path, {'version': 16})
        self.assertEqual(parse.call_count, 1)
        copies = list((self.path.parent / '.json-backups').glob('*.bak'))
        self.assertEqual(len(copies), storage.BACKUP_LIMIT)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in copies))

    def test_directory_is_an_error_instead_of_missing_file_default(self):
        self.path.mkdir()
        with self.assertRaises(storage.JsonStorageError): storage.load_json(self.path, {})
        with self.assertRaises(storage.JsonStorageError): storage.save_json(self.path, {})

    def test_non_finite_numbers_are_not_written(self):
        for value in (float('nan'), float('inf')):
            with self.assertRaises(storage.JsonStorageError): storage.save_json(self.path, {'value': value})
        self.assertFalse(self.path.exists())

    def test_failed_fsync_leaves_old_json_and_no_temporary_file(self):
        storage.save_json(self.path, {'version': 1})
        with patch.object(storage.os, 'fsync', side_effect=OSError('disk')):
            with self.assertRaises(storage.JsonStorageError): storage.save_json(self.path, {'version': 2})
        self.assertEqual(storage.load_json(self.path, {}), {'version': 1})
        self.assertEqual(list(self.path.parent.rglob('*.tmp')), [])


class AsyncStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancellation_keeps_transaction_lock_until_disk_completes(self):
        entered, release = threading.Event(), threading.Event()
        lock = asyncio.Lock()
        def disk():
            entered.set()
            assert release.wait(3)
        async def transaction():
            async with lock: await storage.run_storage_io(disk)
        task = asyncio.create_task(transaction())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel(); await asyncio.sleep(0)
            task.cancel(); await asyncio.sleep(0)
            self.assertTrue(lock.locked())
            self.assertFalse(task.done())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(lock.locked())

    async def test_disk_errors_propagate(self):
        def disk(): raise storage.JsonStorageError('disk')
        with self.assertRaises(storage.JsonStorageError): await storage.run_storage_io(disk)

    async def test_concurrent_analytics_updates_do_not_lose_events(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(analytics, '_LOCK', asyncio.Lock()), patch.object(analytics, 'EVENTS_FILE', Path(temp)/'events.json'):
            await asyncio.gather(*(analytics.log_section(i, 'weather') for i in range(12)))
            events = storage.load_json(analytics.EVENTS_FILE, {})['events']
            self.assertEqual({e['user_id'] for e in events}, set(range(12)))

    async def test_concurrent_platform_creation_uses_one_user(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(platform_db, '_platform_db_lock', asyncio.Lock()), patch.object(platform_db, 'PLATFORM_USERS_FILE', str(Path(temp)/'platform.json')):
            rows = await asyncio.gather(*(platform_db.get_or_create_user(telegram_id=42) for _ in range(12)))
            self.assertEqual(len({r['id'] for r in rows}), 1)
            self.assertEqual(len(storage.load_json(Path(platform_db.PLATFORM_USERS_FILE), {})['users']), 1)
