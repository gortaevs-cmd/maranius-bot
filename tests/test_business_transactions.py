"""Crash decisions, failure before commit and source/outbox consistency."""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import ui
from handlers.admin import AdminHandlers
from handlers.consent import update_marketing
from integrations import admin_alerts, admin_audit, analytics, business_events, consent_log
from integrations import inbox, json_storage as storage, legacy_contacts, notification_outbox, user_registry, vip_codes


class TransactionFixture:
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patches = ExitStack()
        self.addCleanup(patches.close)
        for target, name, value in (
            (storage, 'TRANSACTION_FILE', self.root / 'storage_transaction.json'),
            (storage, 'TRANSACTION_ROOTS', (self.root,)),
            (storage, '_CLEANUP_PENDING', False),
            (user_registry, 'USERS_FILE', self.root / 'users.json'),
            (vip_codes, 'CODES_FILE', self.root / 'codes.json'),
            (notification_outbox, 'OUTBOX_FILE', self.root / 'outbox.json'),
            (inbox, 'INBOX_FILE', self.root / 'inbox.json'),
            (consent_log, 'CONSENT_LOG_FILE', self.root / 'consent.json'),
            (legacy_contacts, 'INACTIVE_USERS_FILE', self.root / 'legacy.json'),
            (analytics, 'AGGREGATES_FILE', self.root / 'aggregates.json'),
        ):
            patches.enter_context(patch.object(target, name, value))
        self.user = SimpleNamespace(id=42, username='client', first_name='Client', last_name='', language_code='ru')

    def seed_user(self):
        users = {'42': {'id': 42, 'vip': False}}
        user_registry.accept_policy(users, 42)
        user_registry.accept_user_agreement(users, 42)
        user_registry.save_users(users)
        return users

    def activate(self):
        return business_events.activate_vip_code(
            'one-code', self.user, load_users=user_registry.load_users,
            save_users=user_registry.save_users, seed_admin_ids={1},
        )

    def queue(self):
        return storage.load_json(notification_outbox.OUTBOX_FILE, {'items': []})['items']

    def prepare_pair(self):
        self.first, self.second = self.root / 'a.json', self.root / 'b.json'
        storage.save_json(self.first, {'value': 0})
        storage.save_json(self.second, {'value': 0})

    def change_pair(self):
        storage.save_json(self.first, {'value': 1})
        storage.save_json(self.second, {'value': 2})


class TransactionRecoveryTests(TransactionFixture, unittest.TestCase):
    def test_callback_failure_discards_all_staged_changes(self):
        self.prepare_pair()
        def fail():
            self.change_pair()
            raise ValueError('rejected')
        with self.assertRaises(ValueError): storage.atomic_json(fail)
        self.assertEqual(storage.load_json(self.first, {}), {'value': 0})
        self.assertEqual(storage.load_json(self.second, {}), {'value': 0})
        self.assertFalse(storage.TRANSACTION_FILE.exists())

    def test_journal_write_failure_does_not_apply_any_source(self):
        self.prepare_pair()
        original = storage._atomic_write
        def fail(path, *args, **kwargs):
            if path == storage.TRANSACTION_FILE: raise OSError('journal unavailable')
            return original(path, *args, **kwargs)
        with patch.object(storage, '_atomic_write', side_effect=fail):
            with self.assertRaises(storage.JsonStorageError): storage.atomic_json(self.change_pair)
        self.assertEqual(storage.load_json(self.first, {}), {'value': 0})
        self.assertFalse(storage.TRANSACTION_FILE.exists())

    def test_caught_nested_failure_cannot_commit_a_partial_operation(self):
        self.prepare_pair()
        def inner():
            self.change_pair()
            raise ValueError('rejected')
        def outer():
            try: storage.atomic_json(inner)
            except ValueError: pass
        with self.assertRaises(storage.JsonStorageError): storage.atomic_json(outer)
        self.assertEqual(storage.load_json(self.first, {}), {'value': 0})
        self.assertFalse(storage.TRANSACTION_FILE.exists())

    def test_backup_failure_is_before_commit(self):
        self.prepare_pair()
        original = storage._atomic_write
        def fail(path, *args, **kwargs):
            if path.suffix == '.bak': raise OSError('backup unavailable')
            return original(path, *args, **kwargs)
        with patch.object(storage, '_atomic_write', side_effect=fail):
            with self.assertRaises(storage.JsonStorageError): storage.atomic_json(self.change_pair)
        self.assertEqual(storage.load_json(self.first, {}), {'value': 0})
        self.assertFalse(storage.TRANSACTION_FILE.exists())

    def test_partial_application_blocks_unrelated_reads_and_writes_until_recovery(self):
        self.prepare_pair()
        other = self.root / 'other.json'
        original = storage._atomic_write
        def fail(path, *args, **kwargs):
            if path.resolve() == self.second.resolve(): raise OSError('disk unavailable')
            return original(path, *args, **kwargs)
        with patch.object(storage, '_atomic_write', side_effect=fail):
            with self.assertRaises(storage.JsonStorageError): storage.atomic_json(self.change_pair)
            self.assertEqual(json.loads(self.first.read_text()), {'value': 1})
            self.assertTrue(storage.TRANSACTION_FILE.exists())
            self.assertEqual(storage.TRANSACTION_FILE.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(storage.JsonStorageError): storage.load_json(other, {})
            with self.assertRaises(storage.JsonStorageError): storage.save_json(other, {'unexpected': 1})
            self.assertFalse(other.exists())
        self.assertEqual(storage.load_json(self.second, {}), {'value': 2})
        self.assertFalse(storage.TRANSACTION_FILE.exists())
        # Replay did not back up a partially applied image over the original.
        self.assertEqual(json.loads(storage._backup_paths(self.first)[0].read_text()), {'value': 0})

    def test_journal_cleanup_failure_is_recoverable(self):
        self.prepare_pair()
        original = Path.unlink
        def fail(path, *args, **kwargs):
            if path == storage.TRANSACTION_FILE: raise OSError('cleanup unavailable')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', new=fail):
            with self.assertRaises(storage.JsonStorageError): storage.atomic_json(self.change_pair)
        self.assertTrue(storage.recover_transaction())
        self.assertEqual(storage.load_json(self.second, {}), {'value': 2})
        self.assertFalse(storage.TRANSACTION_FILE.exists())

    def test_failed_cleanup_directory_fsync_blocks_following_operations(self):
        self.prepare_pair()
        original = storage._fsync_directory
        def fail(folder):
            if storage._CLEANUP_PENDING: raise OSError('directory sync unavailable')
            return original(folder)
        with patch.object(storage, '_fsync_directory', side_effect=fail):
            with self.assertRaises(storage.JsonStorageError): storage.atomic_json(self.change_pair)
            self.assertTrue(storage._CLEANUP_PENDING)
            with self.assertRaises(storage.JsonStorageError): storage.load_json(self.first, {})
        self.assertEqual(storage.load_json(self.first, {}), {'value': 1})
        self.assertFalse(storage._CLEANUP_PENDING)

    def test_corrupt_journal_fails_closed_without_using_old_backup(self):
        self.prepare_pair()
        storage.TRANSACTION_FILE.write_bytes(b'{broken journal')
        with self.assertRaises(storage.JsonStorageError): storage.load_json(self.first, {})
        self.assertEqual(storage.TRANSACTION_FILE.read_bytes(), b'{broken journal')
        self.assertEqual(json.loads(self.first.read_text()), {'value': 0})

    def test_journal_checksum_failure_never_applies_payload(self):
        self.prepare_pair()
        record = {'version': 1, 'writes': [{'path': str(self.first), 'data': {'value': 9}, 'trailing_newline': False}], 'sha256': 'incorrect'}
        storage.TRANSACTION_FILE.write_text(json.dumps(record))
        with self.assertRaises(storage.JsonStorageError): storage.recover_transaction()
        self.assertEqual(json.loads(self.first.read_text()), {'value': 0})

    def test_journal_rejects_paths_outside_roots_before_applying_any_file(self):
        self.prepare_pair()
        outside = self.root.parent / (self.root.name + '-outside.json')
        writes = [{'path': str(p), 'data': {'value': 9}, 'trailing_newline': False} for p in (self.first, outside)]
        record = {'version': 1, 'writes': writes, 'sha256': hashlib.sha256(storage._encode(writes)).hexdigest()}
        storage.TRANSACTION_FILE.write_text(json.dumps(record))
        with self.assertRaises(storage.JsonStorageError): storage.recover_transaction()
        self.assertFalse(outside.exists())
        self.assertEqual(json.loads(self.first.read_text()), {'value': 0})

    def test_staged_reads_and_nested_transactions_use_detached_snapshot(self):
        self.prepare_pair()
        value = {'value': 7}
        def commit():
            storage.save_json(self.first, value)
            value['value'] = 999
            self.assertEqual(storage.load_json(self.first, {}), {'value': 7})
            storage.atomic_json(storage.save_json, self.second, {'value': 8})
        storage.atomic_json(commit)
        self.assertEqual(storage.load_json(self.first, {}), {'value': 7})
        self.assertEqual(storage.load_json(self.second, {}), {'value': 8})

    def test_async_callback_is_rejected_without_commit(self):
        async def invalid(): pass
        with self.assertRaises(TypeError): storage.atomic_json(invalid)
        self.assertFalse(storage.TRANSACTION_FILE.exists())

    def test_process_crash_after_vip_write_is_recovered_in_fresh_process(self):
        self.seed_user()
        storage.save_json(vip_codes.CODES_FILE, {'active': ['one-code'], 'used': []})
        setup = f'''
import json, os, socket
from pathlib import Path
from types import SimpleNamespace
from integrations import json_storage as storage, user_registry, vip_codes, notification_outbox, business_events
def deny(*args, **kwargs): raise AssertionError('network is disabled')
socket.socket.connect = deny
root = Path({str(self.root)!r})
storage.TRANSACTION_FILE = root / 'storage_transaction.json'
storage.TRANSACTION_ROOTS = (root,)
user_registry.USERS_FILE = root / 'users.json'
vip_codes.CODES_FILE = root / 'codes.json'
notification_outbox.OUTBOX_FILE = root / 'outbox.json'
'''
        crash = setup + '''
original = storage._atomic_write
def die(path, *args, **kwargs):
    if path.resolve() == notification_outbox.OUTBOX_FILE.resolve(): os._exit(71)
    return original(path, *args, **kwargs)
storage._atomic_write = die
business_events.activate_vip_code('one-code', SimpleNamespace(id=42, username='client', first_name='Client'), load_users=user_registry.load_users, save_users=user_registry.save_users, seed_admin_ids={1})
'''
        first = subprocess.run([sys.executable, '-B', '-c', crash], capture_output=True, text=True)
        self.assertEqual(first.returncode, 71, first.stderr)
        self.assertTrue(storage.TRANSACTION_FILE.exists())
        self.assertTrue(json.loads(user_registry.USERS_FILE.read_text())['42']['vip'])
        self.assertFalse(notification_outbox.OUTBOX_FILE.exists())
        restore = setup + '''
users = user_registry.load_users()
items = notification_outbox._load()['items']
print(json.dumps({'vip': users['42']['vip'], 'access': user_registry.has_current_access(users,42), 'queue':len(items), 'state':items[0]['state'], 'used':len(vip_codes._load_unlocked()['used']), 'journal':storage.TRANSACTION_FILE.exists()}))
'''
        second = subprocess.run([sys.executable, '-B', '-c', restore], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(second.stdout), {'vip': True, 'access': True, 'queue': 1, 'state': 'pending', 'used': 1, 'journal': False})
        self.assertEqual(self.activate(), (False, 'already_vip'))
        self.assertEqual(len(self.queue()), 1)


class BusinessConsistencyTests(TransactionFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user_lock = asyncio.Lock()

    async def test_full_queue_cannot_leave_an_orphan_inbox_record(self):
        with patch.object(admin_alerts, '_can_notify_inbox', new=AsyncMock(return_value=True)), \
             patch.object(notification_outbox, 'MAX_PENDING', 0):
            with self.assertRaises(storage.JsonStorageError):
                await business_events.add_inbox_entry({1}, entry_type='unknown_command', user_id=42, username=None, text='request')
        self.assertFalse(inbox.INBOX_FILE.exists())
        self.assertEqual(self.queue(), [])
        self.assertFalse(storage.TRANSACTION_FILE.exists())

    async def test_cancelled_committed_inbox_operation_finishes_both_records(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        original = storage._atomic_write
        def block(path, *args, **kwargs):
            original(path, *args, **kwargs)
            if path == storage.TRANSACTION_FILE:
                entered.set()
                if not release.wait(5): raise TimeoutError('test did not release journal')
        with patch.object(admin_alerts, '_can_notify_inbox', new=AsyncMock(return_value=True)), \
             patch.object(storage, '_atomic_write', side_effect=block):
            task = asyncio.create_task(business_events.add_inbox_entry(
                {1}, entry_type='unknown_command', user_id=42, username=None, text='request',
            ))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(len((await inbox.load_store())['entries']), 1)
        self.assertEqual(len(self.queue()), 1)
        self.assertFalse(storage.TRANSACTION_FILE.exists())

    async def test_manual_vip_alert_audit_failure_discards_user_and_code(self):
        self.seed_user()
        storage.save_json(vip_codes.CODES_FILE, {'active': [], 'used': []})
        message = SimpleNamespace(reply_text=AsyncMock(), text=ui.ADMIN_VIP_WRONG_CODE.format(
            user_link='Client', user_id=42, code='manual-code',
        ))
        query = SimpleNamespace(data=ui.CB_VIP_APPROVE_PREFIX+'42', answer=AsyncMock(), message=message)
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=10), effective_message=message)
        sender = AsyncMock()
        with patch.object(bot, '_admin_guard', new=AsyncMock(return_value=True)), \
             patch.object(bot, '_users_lock', self.user_lock), \
             patch.object(bot._admin_handlers, 'audit_file', self.root / 'audit.json'), \
             patch.object(admin_audit, '_append_sync', side_effect=OSError('audit unavailable')):
            with self.assertRaises(OSError):
                await bot.vip_approve_callback(update, SimpleNamespace(bot=SimpleNamespace(send_message=sender)))
        self.assertFalse(user_registry.load_users()['42']['vip'])
        self.assertEqual(await vip_codes.counts(), (0, 0))
        sender.assert_not_awaited()

    async def test_rate_suppression_still_records_inbox_without_enqueuing(self):
        with patch.object(admin_alerts, '_can_notify_inbox', new=AsyncMock(return_value=False)):
            await business_events.add_inbox_entry({1}, entry_type='unknown_command', user_id=42, username=None, text='request')
        self.assertEqual(len((await inbox.load_store())['entries']), 1)
        self.assertEqual(self.queue(), [])

    async def test_profile_and_first_visit_notification_are_committed_once(self):
        self.seed_user()
        def visit():
            return business_events.profile_visit(self.user, load_users=user_registry.load_users, save_users=user_registry.save_users, seed_admin_ids={1}, notify=True)
        results = await asyncio.gather(*(storage.run_storage_io(visit) for _ in range(6)))
        self.assertEqual(sum(is_new for allowed, is_new in results), 1)
        self.assertEqual(len(self.queue()), 1)
        self.assertTrue(user_registry.load_users()['42']['first_seen'])

    async def test_legacy_return_queue_failure_preserves_roster_and_profile(self):
        self.seed_user()
        legacy_contacts.save_inactive_store({'schema_version': 1, 'records': {'42': {'id': 42, 'source': 'legacy'}}})
        with patch.object(notification_outbox, 'MAX_PENDING', 0):
            with self.assertRaises(storage.JsonStorageError):
                await storage.run_storage_io(business_events.profile_visit, self.user, load_users=user_registry.load_users, save_users=user_registry.save_users, seed_admin_ids={1}, notify=True)
        self.assertIn('42', legacy_contacts.load_inactive_store()['records'])
        self.assertNotIn('first_seen', user_registry.load_users()['42'])

    async def test_legacy_return_has_one_notification_and_no_new_subscriber_duplicate(self):
        self.seed_user()
        legacy_contacts.save_inactive_store({'schema_version': 1, 'records': {'42': {'id': 42, 'source': 'legacy'}}})
        for _ in range(2):
            await storage.run_storage_io(business_events.profile_visit, self.user, load_users=user_registry.load_users, save_users=user_registry.save_users, seed_admin_ids={1}, notify=True)
        self.assertEqual(len(self.queue()), 1)
        self.assertIn('Вернулся', self.queue()[0]['text'])
        self.assertNotIn('42', legacy_contacts.load_inactive_store()['records'])

    async def test_vip_queue_failure_leaves_code_available_and_access_unchanged(self):
        self.seed_user()
        storage.save_json(vip_codes.CODES_FILE, {'active': ['one-code'], 'used': []})
        with patch.object(notification_outbox, 'MAX_PENDING', 0):
            with self.assertRaises(storage.JsonStorageError): await storage.run_storage_io(self.activate)
        self.assertFalse(user_registry.load_users()['42']['vip'])
        self.assertEqual(await vip_codes.counts(), (1, 0))
        self.assertEqual(await storage.run_storage_io(self.activate), (True, 'ok'))
        self.assertEqual(len(self.queue()), 1)

    async def test_concurrent_vip_requests_consume_one_code_and_one_notification(self):
        self.seed_user()
        storage.save_json(vip_codes.CODES_FILE, {'active': ['one-code'], 'used': []})
        results = await asyncio.gather(*(storage.run_storage_io(self.activate) for _ in range(6)))
        self.assertEqual(sum(ok for ok, reason in results), 1)
        self.assertEqual(await vip_codes.counts(), (0, 1))
        self.assertEqual(len(self.queue()), 1)

    async def test_consent_log_failure_discards_marketing_change(self):
        self.seed_user()
        with patch.object(consent_log, '_append_sync', side_effect=OSError('log unavailable')):
            with self.assertRaises(OSError):
                await storage.run_storage_io(update_marketing, user_registry.load_users, user_registry.save_users, 42, True, source='profile', action='test')
        self.assertFalse(user_registry.load_users()['42'].get('marketing_opt_in'))
        self.assertFalse(consent_log.CONSENT_LOG_FILE.exists())

    async def test_unsubscribe_without_current_documents_is_recorded_atomically(self):
        user_registry.save_users({'42': {'id': 42, 'marketing_opt_in': True}})
        await storage.run_storage_io(update_marketing, user_registry.load_users, user_registry.save_users, 42, False, source='unsub_button', action='test', require_access=False)
        self.assertFalse(user_registry.load_users()['42']['marketing_opt_in'])
        self.assertEqual((await consent_log.recent())[0]['event'], 'marketing_opt_out')

    async def test_audit_failure_discards_admin_vip_change_before_sending_success(self):
        self.seed_user()
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=10), effective_message=message)
        handlers = AdminHandlers(
            seed_admin_ids={10}, users_lock=self.user_lock, load_users=user_registry.load_users,
            save_users=user_registry.save_users, require_private_chat=AsyncMock(),
            ensure_user_saved=AsyncMock(), add_admin=AsyncMock(), main_keyboard_for=lambda uid: None,
            audit_file=self.root / 'audit.json', consent_file=self.root / 'consent.json', bot_profile=lambda: 'test',
        )
        sender = AsyncMock()
        with patch.object(admin_audit, '_append_sync', side_effect=OSError('audit unavailable')):
            with self.assertRaises(OSError):
                await handlers._admin_exec_user_cmd(update, SimpleNamespace(bot=SimpleNamespace(send_message=sender)), 'vip', 42, 'test')
        self.assertFalse(user_registry.load_users()['42']['vip'])
        sender.assert_not_awaited()
        message.reply_text.assert_not_awaited()

    async def test_failed_report_enqueue_discards_aggregate(self):
        from handlers.reports import ReportHandlers
        self.seed_user()
        st = user_registry.stats_summary(user_registry.load_users())
        with patch.object(notification_outbox, 'MAX_PENDING', 0):
            with self.assertRaises(storage.JsonStorageError):
                await ReportHandlers(lambda: st, {1})._send_weekly_report(SimpleNamespace(bot=None))
        self.assertFalse(analytics.AGGREGATES_FILE.exists())

    async def test_repeated_migration_after_commit_does_not_repeat_notification(self):
        pending, last = self.root / 'pending.json', self.root / 'last.json'
        storage.save_json(pending, {'source': 'legacy', 'active_user_ids': [42], 'vip_user_ids': [42], 'inactive_user_ids': [43]})
        original = Path.unlink
        def fail(path, *args, **kwargs):
            if path == pending: raise OSError('cleanup unavailable')
            return original(path, *args, **kwargs)
        with patch.object(legacy_contacts, 'PENDING_MIGRATION_FILE', pending), \
             patch.object(legacy_contacts, 'LAST_MIGRATION_FILE', last):
            with patch.object(Path, 'unlink', new=fail):
                with self.assertRaises(OSError): await storage.run_storage_io(bot._apply_pending_legacy_migration)
            self.assertEqual(len(self.queue()), 1)
            self.assertIsNone(await storage.run_storage_io(bot._apply_pending_legacy_migration))
        self.assertFalse(pending.exists())
        self.assertEqual(len(self.queue()), 1)
        self.assertTrue(user_registry.load_users()['42']['vip'])
