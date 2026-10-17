"""Runtime storage boundaries: responsiveness, cancellation and real transactions."""
import asyncio
import copy
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from events import storage as event_storage
from handlers import profile
from handlers import vip as vip_handlers
from handlers.admin import AdminHandlers
from handlers.weather import WeatherHandlers
from integrations import admin_audit, consent_log, legacy_contacts, user_registry, vip_codes, vip_content
from integrations.json_storage import JsonStorageError, load_json, run_storage_io


class RuntimeStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.users_lock = asyncio.Lock()
        for target, name, value in (
            (bot, '_users_lock', self.users_lock),
            (user_registry, 'USERS_FILE', self.root / 'users.json'),
            (admin_audit, '_LOCK', asyncio.Lock()),
            (consent_log, '_LOCK', asyncio.Lock()),
            (consent_log, 'CONSENT_LOG_FILE', self.root / 'consent.json'),
            (vip_codes, '_lock', asyncio.Lock()),
            (vip_handlers, '_CODE_ACTIVATION_LOCK', asyncio.Lock()),
            (vip_codes, 'CODES_FILE', self.root / 'codes.json'),
            (legacy_contacts, '_lock', asyncio.Lock()),
            (event_storage, '_events_lock', asyncio.Lock()),
            (event_storage, '_events_file', str(self.root / 'events.json')),
        ):
            self.patches.enter_context(patch.object(target, name, value))

    async def assert_disk_is_responsive(self, operation, target, loader_name, result):
        entered, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        worker_threads = []

        def blocked_load(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test did not release storage')
            return copy.deepcopy(result)

        with patch.object(target, loader_name, side_effect=blocked_load):
            task = asyncio.create_task(operation())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                # The loader is still blocked; unrelated loop work must execute.
                marker = asyncio.Event()
                asyncio.get_running_loop().call_soon(marker.set)
                await asyncio.wait_for(marker.wait(), 1)
                self.assertFalse(task.done())
                self.assertTrue(all(t != loop_thread for t in worker_threads))
            finally:
                release.set()
                await task

    async def test_policy_storage_does_not_block_loop(self):
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
        )
        async def show_policy():
            await bot.show_policy(update, SimpleNamespace())
        await self.assert_disk_is_responsive(show_policy, bot, '_load_users', {})

    async def test_keyboard_storage_does_not_block_loop(self):
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
        )
        await self.assert_disk_is_responsive(
            lambda: bot._reply_screen(update, 'test screen'), bot, '_load_users', {},
        )

    async def test_consent_journal_does_not_block_loop(self):
        await self.assert_disk_is_responsive(
            lambda: consent_log.append(user_id=42, event='policy_accepted'),
            consent_log, 'load_json', {'events': []},
        )

    async def test_admin_journal_does_not_block_loop(self):
        await self.assert_disk_is_responsive(
            lambda: admin_audit.append(self.root / 'audit.json', actor_id=1, action='vip_grant', reason='test'),
            admin_audit, 'load_json', {'events': []},
        )

    async def test_vip_code_storage_does_not_block_loop(self):
        await self.assert_disk_is_responsive(
            lambda: vip_codes.redeem_code('test', user_id=42),
            vip_codes, 'load_json', {'active': ['test'], 'used': []},
        )

    async def test_group_events_do_not_block_loop(self):
        await self.assert_disk_is_responsive(
            lambda: event_storage.save_event('reaction', {'id': -100}, {'id': 42}),
            event_storage, 'load_json', {'events': [], 'schema_version': 1},
        )

    async def test_legacy_claim_does_not_block_loop(self):
        await self.assert_disk_is_responsive(
            lambda: legacy_contacts.claim_returning_inactive_user(42),
            legacy_contacts, 'load_inactive_store', {'records': {}},
        )

    async def test_parallel_profile_vip_consent_and_location_keep_all_fields(self):
        users = {'42': {'id': 42, 'vip_revoked_at': 'old-revocation'}}
        user_registry.accept_policy(users, 42)
        user_registry.accept_user_agreement(users, 42)
        user_registry.save_users(users)
        user = SimpleNamespace(id=42, username='client', first_name='Client', last_name='', language_code='ru')
        update = SimpleNamespace(effective_user=user, effective_chat=None)
        weather = WeatherHandlers(
            users_lock=self.users_lock, load_users=user_registry.load_users, save_users=user_registry.save_users,
            require_access=AsyncMock(), ensure_user_saved=AsyncMock(),
            main_keyboard_for=lambda uid: None, edit_or_reply=AsyncMock(),
        )
        with patch.object(bot.platform_db, 'get_or_create_user', new=AsyncMock()), \
             patch('handlers.weather._get_timezone_by_coords', return_value='Europe/Moscow'):
            results = await asyncio.gather(
                bot.ensure_user_saved(update), bot.ensure_user_saved(update),
                bot._grant_vip_user(42),
                profile.set_marketing_opt_in(
                    users_lock=self.users_lock, load_users=user_registry.load_users,
                    save_users=user_registry.save_users, user_id=42, value=True,
                ),
                weather._save_user_location('42', 55.75, 37.62),
            )
        self.assertEqual(sorted(results[:2]), [False, True])
        saved = user_registry.load_users()
        row = saved['42']
        self.assertTrue(row['vip'])
        self.assertTrue(user_registry.has_current_access(saved, 42))
        self.assertTrue(user_registry.has_current_marketing_consent(saved, 42))
        self.assertEqual(row['vip_revoked_at'], 'old-revocation')
        self.assertEqual(row['last_location']['lat'], 55.75)
        self.assertEqual(row['username'], 'client')
        self.assertTrue(row['first_seen'])
        self.assertEqual(len(await consent_log.recent()), 1)

    async def test_cancelled_save_retains_users_lock_and_next_change(self):
        user_registry.save_users({'42': {'id': 42}})
        entered, release = threading.Event(), threading.Event()
        original_save = bot._save_users
        calls = []

        def blocked_save(users):
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('test did not release storage')
            original_save(users)

        with patch.object(bot, '_save_users', side_effect=blocked_save):
            first = asyncio.create_task(bot._grant_vip_user(42))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                first.cancel()
                await asyncio.sleep(0)
                first.cancel()
                second = asyncio.create_task(profile.set_marketing_opt_in(
                    users_lock=self.users_lock, load_users=user_registry.load_users,
                    save_users=bot._save_users, user_id=42, value=True,
                ))
                await asyncio.sleep(0)
                self.assertTrue(self.users_lock.locked())
                self.assertFalse(first.done())
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                if second is not None:
                    await second
        row = user_registry.load_users()['42']
        self.assertTrue(row['vip'])
        self.assertTrue(row['marketing_opt_in'])

    async def test_parallel_journal_appends_keep_every_event(self):
        operations = []
        for uid in range(12):
            operations.extend((
                consent_log.append(user_id=uid, event='policy_accepted'),
                admin_audit.append(self.root / 'audit.json', actor_id=uid, action='test', reason='test'),
                event_storage.save_event('reaction', {'id': -100}, {'id': uid}),
            ))
        await asyncio.gather(*operations)
        for filename, field in (('consent.json', 'user_id'), ('audit.json', 'actor_id')):
            events = load_json(self.root / filename, {})['events']
            self.assertEqual({e[field] for e in events}, set(range(12)))
            self.assertEqual(len({e['id'] for e in events}), 12)
        events = load_json(self.root / 'events.json', {})['events']
        self.assertEqual({e['user']['id'] for e in events}, set(range(12)))

    async def test_cancelled_code_redemption_finishes_vip_grant(self):
        await vip_codes.add_codes_bulk('test-code')
        user_registry.save_users({'42': {'id': 42}})
        entered, release = threading.Event(), threading.Event()
        original_save = vip_codes._save_unlocked

        def blocked_save(data):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test did not release storage')
            original_save(data)

        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42, username='client', first_name='Client'),
            message=message,
        )
        with patch.object(vip_codes, '_save_unlocked', side_effect=blocked_save):
            task = asyncio.create_task(vip_handlers.try_vip_code(
                update, SimpleNamespace(user_data={'awaiting_vip_code': True}), 'test-code',
                is_vip=lambda uid: False, grant_vip=bot._grant_vip_user,
                show_vip_home=AsyncMock(), notify_wrong=AsyncMock(), protect_kwargs={},
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
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(await vip_codes.counts(), (0, 1))
        self.assertTrue(user_registry.load_users()['42']['vip'])
        message.reply_text.assert_not_awaited()

    async def test_failed_grant_returns_consumed_code(self):
        await vip_codes.add_codes_bulk('test-code')
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42, username='client', first_name='Client'),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        with self.assertRaises(JsonStorageError):
            await vip_handlers.try_vip_code(
                update, SimpleNamespace(user_data={'awaiting_vip_code': True}), 'test-code',
                is_vip=lambda uid: False, grant_vip=AsyncMock(side_effect=JsonStorageError('disk unavailable')),
                show_vip_home=AsyncMock(), notify_wrong=AsyncMock(), protect_kwargs={},
            )
        self.assertEqual(await vip_codes.counts(), (1, 0))
        update.message.reply_text.assert_not_awaited()

    async def test_parallel_codes_for_same_user_consume_only_one(self):
        await vip_codes.add_codes_bulk('first-code\nsecond-code')
        user_registry.save_users({'42': {'id': 42}})
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42, username='client', first_name='Client'),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        initial_checks = 0
        counter_lock = threading.Lock()
        both_started = threading.Barrier(2)

        def is_vip(uid):
            nonlocal initial_checks
            with counter_lock:
                initial_checks += 1
                initial = initial_checks <= 2
            if initial:
                both_started.wait(2)
                return False
            return bot._user_is_vip(uid)

        async def activate(code):
            return await vip_handlers.try_vip_code(
                update, SimpleNamespace(user_data={'awaiting_vip_code': True}), code,
                is_vip=is_vip, grant_vip=bot._grant_vip_user,
                show_vip_home=AsyncMock(), notify_wrong=AsyncMock(), protect_kwargs={},
            )
        results = await asyncio.gather(activate('first-code'), activate('second-code'))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(await vip_codes.counts(), (1, 1))
        self.assertTrue(user_registry.load_users()['42']['vip'])

    async def test_write_failure_propagates_and_does_not_report_consent_success(self):
        user_registry.save_users({'42': {'id': 42, 'marketing_opt_in': False}})
        audit = AsyncMock()
        with patch.object(profile.consent_log, 'append', audit), \
             patch.object(user_registry, 'save_json', side_effect=JsonStorageError('disk unavailable')):
            with self.assertRaises(JsonStorageError):
                await profile.set_marketing_opt_in(
                    users_lock=self.users_lock, load_users=user_registry.load_users,
                    save_users=user_registry.save_users, user_id=42, value=True,
                )
        audit.assert_not_awaited()
        self.assertFalse(user_registry.load_users()['42']['marketing_opt_in'])
        self.assertFalse(self.users_lock.locked())

    async def test_parallel_pdf_cache_updates_keep_both_file_ids(self):
        with patch.object(vip_content, 'PDF_FILE_IDS_FILE', self.root / 'pdf_ids.json'):
            await asyncio.gather(
                run_storage_io(vip_content.save_pdf_file_id, 'dark', 'dark-file-id'),
                run_storage_io(vip_content.save_pdf_file_id, 'light', 'light-file-id'),
            )
            self.assertEqual(load_json(vip_content.PDF_FILE_IDS_FILE, {}), {
                'dark': 'dark-file-id', 'light': 'light-file-id',
            })

    async def test_admin_user_card_uses_one_snapshot_without_reading_disk(self):
        handlers = AdminHandlers(
            seed_admin_ids=set(), users_lock=self.users_lock,
            load_users=lambda: self.fail('card must use its row snapshot'), save_users=lambda users: None,
            require_private_chat=AsyncMock(), ensure_user_saved=AsyncMock(), add_admin=AsyncMock(),
            main_keyboard_for=lambda uid: None, audit_file=self.root / 'audit.json',
            consent_file=self.root / 'consent.json', bot_profile=lambda: 'test',
        )
        users = {'42': {'id': 42}}
        user_registry.accept_policy(users, 42)
        user_registry.accept_user_agreement(users, 42)
        text = handlers._admin_user_card_text(42, users['42'])
        self.assertIn('42', text)
