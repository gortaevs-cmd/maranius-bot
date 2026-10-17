import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

from integrations import inbox, notification_outbox as outbox
from integrations.json_storage import JsonStorageError, load_json, save_json


class OutboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'outbox.json'
        self.now = 1000.0
        for target, name, value in (
            (outbox, 'OUTBOX_FILE', self.path),
            (inbox, 'INBOX_FILE', Path(temp.name) / 'inbox.json'),
            (outbox, '_now', lambda: self.now),
        ):
            patcher = patch.object(target, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        self.bot = SimpleNamespace(send_message=AsyncMock())

    def store(self):
        return load_json(self.path, {})

    async def enqueue(self, **kwargs):
        return await outbox.enqueue({1}, 'request', **kwargs)

    async def test_enqueue_is_durable_without_network_and_payload_removed_after_delivery(self):
        self.assertFalse(await self.enqueue(key='event'))
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store()['items'][0]['text'], 'request')
        await outbox.deliver_due(self.bot)
        self.bot.send_message.assert_awaited_once()
        self.assertIsNone(self.store()['items'][0]['text'])
        self.assertTrue(await self.enqueue(key='event'))
        await outbox.deliver_due(self.bot)
        self.bot.send_message.assert_awaited_once()

    async def test_recipient_success_is_not_replayed_after_partial_failure(self):
        await outbox.enqueue({1,2}, 'request')
        self.bot.send_message.side_effect = [None, NetworkError('offline'), None]
        await outbox.deliver_due(self.bot)
        self.now += 31
        await outbox.deliver_due(self.bot)
        self.assertEqual([c.kwargs['chat_id'] for c in self.bot.send_message.await_args_list], [1,2,2])
        self.assertEqual((await outbox.stats())['sent'], 1)

    async def test_permanent_and_unexpected_failures_do_not_loop(self):
        for error in (Forbidden('blocked'), BadRequest('bad'), RuntimeError('unexpected')):
            await self.enqueue()
            self.bot.send_message.side_effect = error
            before = self.bot.send_message.await_count
            await outbox.deliver_due(self.bot)
            self.now += 4000
            await outbox.deliver_due(self.bot)
            self.assertEqual(self.bot.send_message.await_count, before+1)
        self.assertEqual((await outbox.stats())['failed'], 3)

    async def test_network_backoff_is_persisted_and_bounded(self):
        await self.enqueue()
        self.bot.send_message.side_effect = NetworkError('private error')
        with patch.object(outbox, 'MAX_ATTEMPTS', 3):
            await outbox.deliver_due(self.bot)
            self.assertEqual(self.store()['items'][0]['recipients'][0]['next_at'], 1030)
            await outbox.deliver_due(self.bot)
            self.assertEqual(self.bot.send_message.await_count, 1)
            self.now += 31
            await outbox.deliver_due(self.bot)
            self.now += 61
            await outbox.deliver_due(self.bot)
            self.now += 121
            await outbox.deliver_due(self.bot)
        self.assertEqual(self.bot.send_message.await_count, 3)
        self.assertEqual((await outbox.stats())['failed'], 1)
        self.assertNotIn('private error', self.path.read_text())

    async def test_retry_after_pauses_all_sends_and_survives_another_pass(self):
        await self.enqueue()
        await self.enqueue()
        self.bot.send_message.side_effect = [RetryAfter(60), None, None]
        await outbox.deliver_due(self.bot)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(self.store()['next_allowed_at'], 1060)
        self.now += 59
        await outbox.deliver_due(self.bot)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.now += 2
        await outbox.deliver_due(self.bot)
        self.assertEqual((await outbox.stats())['sent'], 2)

    async def test_cancelled_send_is_recovered_after_lease(self):
        await self.enqueue()
        self.bot.send_message.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await outbox.deliver_due(self.bot)
        self.bot.send_message.side_effect = None
        await outbox.deliver_due(self.bot)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.now += outbox.LEASE_SECONDS+1
        await outbox.deliver_due(self.bot)
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.assertEqual((await outbox.stats())['sent'], 1)

    async def test_concurrent_workers_send_once(self):
        await self.enqueue()
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(**kwargs):
            entered.set(); await release.wait()
        self.bot.send_message.side_effect = send
        first = asyncio.create_task(outbox.deliver_due(self.bot))
        await entered.wait()
        self.assertEqual(await outbox.deliver_due(self.bot), 0)
        release.set(); await first
        self.bot.send_message.assert_awaited_once()

    async def test_concurrent_enqueue_keeps_all_items_and_deduplicates_keys(self):
        await asyncio.gather(*(outbox.enqueue({1}, 'request', key=f'event-{i%5}') for i in range(20)))
        self.assertEqual(len(self.store()['items']), 5)

    async def test_same_key_with_different_payload_is_rejected(self):
        await self.enqueue(key='event')
        with self.assertRaises(JsonStorageError):
            await outbox.enqueue({1}, 'different', key='event')
        self.assertEqual(len(self.store()['items']), 1)

    async def test_full_queue_preserves_existing_pending_events(self):
        with patch.object(outbox, 'MAX_PENDING', 1):
            await self.enqueue()
            with self.assertRaises(JsonStorageError): await self.enqueue()
        self.assertEqual(len(self.store()['items']), 1)
        self.bot.send_message.assert_not_awaited()

    async def test_expired_pending_message_is_not_sent(self):
        await self.enqueue()
        self.now += outbox.RETENTION_SECONDS+1
        await outbox.deliver_due(self.bot)
        self.bot.send_message.assert_not_awaited()
        self.assertEqual((await outbox.stats())['expired'], 1)
        self.assertIsNone(self.store()['items'][0]['text'])

    async def test_old_history_is_pruned(self):
        await self.enqueue()
        await outbox.deliver_due(self.bot)
        self.now += outbox.RETENTION_SECONDS+1
        await self.enqueue()
        self.assertEqual(len(self.store()['items']), 1)

    async def test_batch_limit_does_not_drain_entire_backlog(self):
        for _ in range(3): await self.enqueue()
        await outbox.deliver_due(self.bot, limit=1)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual((await outbox.stats())['pending'], 3)  # first still needs source acknowledgment

    async def test_inbox_ack_retries_without_resending_telegram(self):
        entry = await inbox.add_entry(entry_type='unknown_command', user_id=42, username=None, text='request')
        await self.enqueue(inbox_id=entry['id'], key='inbox:'+entry['id'])
        with patch.object(inbox, 'mark_notified', new=AsyncMock(side_effect=OSError('disk'))):
            await outbox.deliver_due(self.bot)
        self.assertFalse((await inbox.load_store())['entries'][0]['admin_notified_at'])
        self.now += 31
        await outbox.deliver_due(self.bot)
        self.bot.send_message.assert_awaited_once()
        self.assertTrue((await inbox.load_store())['entries'][0]['admin_notified_at'])
        self.assertEqual((await outbox.stats())['sent'], 1)

    async def test_old_inbox_is_never_scanned_or_sent(self):
        await inbox.add_entry(entry_type='unknown_command', user_id=42, username=None, text='old request')
        await outbox.deliver_due(self.bot)
        self.bot.send_message.assert_not_awaited()
        self.assertFalse((await inbox.load_store())['entries'][0]['admin_notified_at'])

    async def test_failed_source_ack_does_not_block_other_notifications(self):
        await self.enqueue(inbox_id='missing-source')
        await outbox.enqueue({2}, 'another notification')
        with patch.object(inbox, 'mark_notified', new=AsyncMock(side_effect=OSError('disk'))):
            await outbox.deliver_due(self.bot)
        self.assertEqual([c.kwargs['chat_id'] for c in self.bot.send_message.await_args_list], [1,2])
        self.assertEqual((await outbox.stats())['sent'], 1)
        self.assertEqual((await outbox.stats())['pending'], 1)

    async def test_duplicate_recipient_schema_is_not_accepted(self):
        await self.enqueue()
        store = self.store()
        store['items'][0]['recipients'] *= 2
        save_json(self.path, store)
        with self.assertRaises(JsonStorageError): await outbox.deliver_due(self.bot)
        self.bot.send_message.assert_not_awaited()

    async def test_worker_recovers_from_storage_error_without_logging_payload(self):
        with patch.object(outbox, 'deliver_due', new=AsyncMock(side_effect=[JsonStorageError('secret-payload'), None])) as deliver, \
             patch.object(outbox.asyncio, 'sleep', new=AsyncMock(side_effect=[None, asyncio.CancelledError()])), \
             self.assertLogs('integrations.diagnostics', level='ERROR') as captured:
            with self.assertRaises(asyncio.CancelledError): await outbox.worker_loop(self.bot, {1})
        self.assertEqual(deliver.await_count, 2)
        self.assertNotIn('secret-payload', '\n'.join(captured.output))

    async def test_oversize_notification_is_not_silently_truncated(self):
        with self.assertRaises(ValueError): await outbox.enqueue({1}, '🙂' * 3000)
        self.assertFalse(self.path.exists())
        for fields in ({'key': ''}, {'inbox_id': 42}, {'parse_mode': {}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                await self.enqueue(**fields)
        self.assertFalse(self.path.exists())

    async def test_removed_owner_does_not_receive_queued_message(self):
        await outbox.enqueue({1,2}, 'request')
        await outbox.deliver_due(self.bot, allowed_recipients={2})
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.kwargs['chat_id'], 2)
        self.assertEqual((await outbox.stats())['failed'], 1)

    async def test_empty_group_targets_do_not_create_queue(self):
        self.assertFalse(await outbox.enqueue({0,-1}, 'request'))
        self.assertFalse(self.path.exists())

    async def test_bad_schema_is_not_overwritten(self):
        save_json(self.path, {'version': 999, 'items': []})
        before = self.path.read_bytes()
        with self.assertRaises(JsonStorageError): await self.enqueue()
        self.assertEqual(self.path.read_bytes(), before)

    async def test_failed_enqueue_never_sends(self):
        with patch.object(outbox, '_save', side_effect=JsonStorageError('disk')):
            with self.assertRaises(JsonStorageError): await self.enqueue()
        self.bot.send_message.assert_not_awaited()

    async def test_restart_in_fresh_python_process_delivers_saved_event(self):
        await outbox.enqueue({1,2}, 'request')
        await outbox.deliver_due(self.bot, limit=1)
        self.assertEqual(self.bot.send_message.await_args.kwargs['chat_id'], 1)
        script = '''import asyncio,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from integrations import notification_outbox as o
o.OUTBOX_FILE=Path(sys.argv[1])
o._now=lambda:1001.0
async def main():
 b=SimpleNamespace(send_message=AsyncMock())
 await o.deliver_due(b)
 print([c.kwargs['chat_id'] for c in b.send_message.await_args_list])
asyncio.run(main())
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-B', '-c', script, str(self.path)], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '[2]')
        self.assertEqual((await outbox.stats())['sent'], 1)
