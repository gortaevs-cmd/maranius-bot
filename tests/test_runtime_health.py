import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import NetworkError, RetryAfter
from telegram.ext import JobQueue

from integrations import runtime_health as health, json_storage as storage, notification_outbox as outbox
import technical_heartbeat


class HealthFixture:
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.now = 1000.0
        for target, key, value in (
            (health, 'time', SimpleNamespace(monotonic=lambda: self.now)),
            (outbox, 'OUTBOX_FILE', self.root / 'queue.json'),
            (outbox, '_now', lambda: self.now),
            (storage, 'TRANSACTION_FILE', self.root / 'transaction.json'),
            (storage, 'TRANSACTION_ROOTS', (self.root,)),
            (storage, '_CLEANUP_PENDING', False),
        ):
            stack.enter_context(patch.object(target, key, value))
        self.monitor = health.RuntimeHealth(path=self.root / 'health.json', identity='123')
        self.app = SimpleNamespace(
            running=True, updater=SimpleNamespace(running=True),
            bot_data={key: SimpleNamespace(done=lambda: False) for key in health.TASKS.values()},
            job_queue=SimpleNamespace(scheduler=SimpleNamespace(running=True), jobs=lambda: [
                SimpleNamespace(name=name, next_t=object()) for name in health.REQUIRED_JOBS]),
        )
        self.app.bot_data['runtime_health'] = self.monitor
        for progress in self.monitor.progress.values():
            progress.beat()
        self.monitor.storage_ok = True

    def check(self):
        read_bytes = Path.read_bytes
        def read(path):
            return b'python\0bot.py\0' if path.name == 'cmdline' else read_bytes(path)
        with patch.object(health, 'process_start', return_value='123'), patch.object(Path, 'read_bytes', read):
            return health.check_snapshot(self.monitor.path)

    def publish(self):
        health.write_snapshot(self.monitor.snapshot(self.app), self.monitor.path)


class HealthSnapshotTests(HealthFixture, unittest.TestCase):
    def test_ready_requires_all_components_and_snapshot_is_private(self):
        self.publish()
        self.assertEqual(self.check(), (True, 'ok'))
        self.assertEqual(self.monitor.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.root.glob('.maranius-health-*')), [])

    def test_missing_stale_future_invalid_and_foreign_snapshot_fail_closed(self):
        self.assertFalse(self.check()[0])
        self.publish()
        self.now += health.MAX_AGE + 1
        self.assertEqual(self.check(), (False, 'stale_snapshot'))
        self.now = 999
        self.assertEqual(self.check(), (False, 'stale_snapshot'))
        self.now = 1000
        snapshot = json.loads(self.monitor.path.read_text())
        for field, value in (('version', True), ('process_start', 'old-process'), ('pid', -1), ('pid', True),
                             ('updated_monotonic', float('nan')), ('ready', 'true')):
            with self.subTest(field=field, value=value):
                self.monitor.path.write_text(json.dumps({**snapshot, field: value}))
                self.assertFalse(self.check()[0])
        for invalid in ('{', '[]', '{}', 'null'):
            self.monitor.path.write_text(invalid)
            self.assertFalse(self.check()[0])

    def test_done_or_missing_worker_is_unhealthy_even_with_live_loop(self):
        for key in health.TASKS.values():
            with self.subTest(key=key):
                old = self.app.bot_data.pop(key)
                self.assertFalse(self.monitor.snapshot(self.app)['ready'])
                self.app.bot_data[key] = SimpleNamespace(done=lambda: True)
                self.assertFalse(self.monitor.snapshot(self.app)['ready'])
                self.app.bot_data[key] = old

    def test_no_progress_detects_hung_worker_and_scheduler(self):
        for name in self.monitor.progress:
            with self.subTest(name=name):
                self.monitor.progress[name].deadline = 1
                self.assertIn(name + '_stalled', self.monitor.snapshot(self.app)['reasons'])
                self.monitor.progress[name].beat()

    def test_failed_worker_and_stopping_are_unhealthy(self):
        self.monitor.progress['outbox'].failed = True
        self.assertIn('outbox_failed', self.monitor.snapshot(self.app)['reasons'])
        self.monitor.progress['outbox'].failed = False
        self.monitor.stopping = True
        self.assertIn('stopping', self.monitor.snapshot(self.app)['reasons'])

    def test_polling_scheduler_and_required_jobs_must_be_running(self):
        self.app.updater.running = False
        self.assertIn('polling_not_running', self.monitor.snapshot(self.app)['reasons'])
        self.app.updater.running = True
        self.app.job_queue.scheduler.running = False
        self.assertIn('scheduler_not_running', self.monitor.snapshot(self.app)['reasons'])
        self.app.job_queue.scheduler.running = True
        self.app.job_queue.jobs = lambda: []
        self.assertIn('scheduled_job_missing', self.monitor.snapshot(self.app)['reasons'])
        self.app.job_queue.jobs = lambda: [SimpleNamespace(name=n, next_t=None) for n in health.REQUIRED_JOBS]
        self.assertIn('scheduled_job_missing', self.monitor.snapshot(self.app)['reasons'])
        self.app.job_queue = None
        self.assertIn('scheduler_not_running', self.monitor.snapshot(self.app)['reasons'])

    def test_readonly_checker_never_imports_bot_or_storage(self):
        code = "from integrations.runtime_health import check_snapshot; import sys; check_snapshot(); assert 'bot' not in sys.modules; assert 'integrations.json_storage' not in sys.modules; assert 'integrations.notification_outbox' not in sys.modules"
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_replacing_snapshot_fails_without_truncating_last_status(self):
        self.publish()
        before = self.monitor.path.read_bytes()
        with patch.object(health.os, 'replace', side_effect=OSError('disk')):
            with self.assertRaises(OSError): self.publish()
        self.assertEqual(self.monitor.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.maranius-health-*')), [])

    @unittest.skipUnless(sys.platform == 'linux', 'Docker uses Linux /proc')
    def test_real_process_identity_and_exit_in_fresh_process(self):
        import select
        # A fake bot argument only; no application imports or network calls.
        script = "import os,time; from pathlib import Path; from integrations.runtime_health import write_snapshot,process_start; import sys; write_snapshot({'version':1,'pid':os.getpid(),'process_start':process_start(os.getpid()),'updated_monotonic':time.monotonic(),'ready':True},Path(sys.argv[1])); print('ready',flush=True); sys.stdin.read()"
        child = subprocess.Popen([sys.executable, '-B', '-c', script, str(self.monitor.path), 'bot.py'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([child.stdout], [], [], 5)[0], 'child did not publish a status')
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            with patch.object(health.time, 'monotonic', lambda: json.loads(self.monitor.path.read_text())['updated_monotonic']):
                self.assertEqual(health.check_snapshot(self.monitor.path), (True, 'ok'))
                child.communicate('', timeout=5)
                self.assertFalse(health.check_snapshot(self.monitor.path)[0])
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()


class HealthRuntimeTests(HealthFixture, unittest.IsolatedAsyncioTestCase):
    async def test_actual_ptb_jobqueue_reports_scheduled_and_paused_jobs(self):
        queue = JobQueue()
        self.app.job_queue = queue
        jobs = [queue.run_repeating(AsyncMock(), interval=600, first=600, name=name)
                for name in health.REQUIRED_JOBS]
        await queue.start()
        try:
            self.assertTrue(self.monitor.snapshot(self.app)['ready'])
            jobs[0].job.pause()
            self.assertIn('scheduled_job_missing', self.monitor.snapshot(self.app)['reasons'])
            jobs[0].job.resume()
            self.assertTrue(self.monitor.snapshot(self.app)['ready'])
        finally:
            await queue.stop()

    async def run_once(self):
        with patch.object(health.asyncio, 'sleep', new=AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.monitor.run(self.app)

    async def test_quiet_queue_is_healthy_without_creating_working_json(self):
        await self.run_once()
        self.assertEqual(self.check(), (True, 'ok'))
        self.assertEqual(self.monitor.queue['due'], 0)
        self.assertFalse(outbox.OUTBOX_FILE.exists())
        self.assertFalse(storage.TRANSACTION_FILE.exists())

    async def test_broken_queue_is_unhealthy_and_can_recover(self):
        outbox.OUTBOX_FILE.write_text('{secret payload')
        with self.assertLogs('integrations.diagnostics', level='ERROR') as log:
            await self.run_once()
        self.assertNotIn('secret payload', '\n'.join(log.output))
        self.assertIn('storage_unavailable', json.loads(self.monitor.path.read_text())['reasons'])
        self.assertEqual(outbox.OUTBOX_FILE.read_text(), '{secret payload')
        outbox.OUTBOX_FILE.write_text('{"version":1,"items":[],"next_allowed_at":0}')
        await self.run_once()
        self.assertTrue(self.check()[0])

    async def test_invalid_wal_blocks_health_without_overwriting_it(self):
        storage.TRANSACTION_FILE.write_text('{broken journal')
        with self.assertLogs('integrations.diagnostics', level='ERROR'):
            await self.run_once()
        self.assertFalse(self.check()[0])
        self.assertEqual(storage.TRANSACTION_FILE.read_text(), '{broken journal')

    async def test_pending_valid_transaction_is_replayed_by_polling_process_probe(self):
        first, second = self.root / 'a.json', self.root / 'b.json'
        write = storage._atomic_write
        def fail_second(path, *args, **kwargs):
            if path == second: raise OSError('disk')
            return write(path, *args, **kwargs)
        def commit():
            storage.save_json(first, {'vip': True})
            storage.save_json(second, {'queued': True})
        with patch.object(storage, '_atomic_write', side_effect=fail_second):
            with self.assertRaises(storage.JsonStorageError): storage.atomic_json(commit)
        self.assertTrue(storage.TRANSACTION_FILE.exists())
        await self.run_once()
        self.assertTrue(self.check()[0])
        self.assertEqual(json.loads(second.read_text()), {'queued': True})
        self.assertFalse(storage.TRANSACTION_FILE.exists())

    async def test_hung_storage_probe_cannot_refresh_snapshot_and_cancellation_drains_thread(self):
        entered, release = threading.Event(), threading.Event()
        self.publish()
        def blocked():
            entered.set()
            release.wait(5)
            return {'pending': 0}
        with patch.object(health, 'storage_probe', side_effect=blocked):
            task = asyncio.create_task(self.monitor.run(self.app))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.now += health.MAX_AGE + 1
                self.assertEqual(self.check(), (False, 'stale_snapshot'))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError): await task

    async def test_transaction_replay_failure_keeps_health_unready_and_journal_intact(self):
        target = self.root / 'business.json'
        def commit(): storage.save_json(target, {'vip': True})
        write = storage._atomic_write
        def fail_target(path, *args, **kwargs):
            if path == target: raise OSError('disk')
            return write(path, *args, **kwargs)
        with patch.object(storage, '_atomic_write', side_effect=fail_target):
            with self.assertRaises(storage.JsonStorageError): storage.atomic_json(commit)
            journal = storage.TRANSACTION_FILE.read_bytes()
            with self.assertLogs('integrations.diagnostics', level='ERROR'):
                await self.run_once()
            self.assertFalse(self.check()[0])
            self.assertEqual(storage.TRANSACTION_FILE.read_bytes(), journal)
            self.assertFalse(target.exists())
        await self.run_once()
        self.assertTrue(self.check()[0])

    async def test_hung_send_stalls_progress_even_while_task_is_alive(self):
        await outbox.enqueue({42}, 'private text')
        entered = asyncio.Event()
        async def send(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(outbox.deliver_due(
            SimpleNamespace(send_message=send), progress=self.monitor.progress['outbox'].beat))
        self.app.bot_data['notification_outbox_task'] = task
        try:
            await entered.wait()
            self.now += health.MAX_AGE + 1
            self.assertFalse(task.done())
            self.assertIn('outbox_stalled', self.monitor.snapshot(self.app)['reasons'])
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task

    async def test_scheduler_tick_is_actual_job_execution(self):
        self.monitor.progress['scheduler'].deadline = 1
        await health.scheduler_tick(SimpleNamespace(application=self.app))
        self.assertTrue(self.monitor.snapshot(self.app)['ready'])

    async def test_queue_pause_backoff_and_lease_are_expected_waits(self):
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=RetryAfter(600)))
        await outbox.enqueue({42}, 'private text')
        await outbox.deliver_due(bot)
        summary = await storage.run_storage_io(health.storage_probe)
        self.assertEqual(summary['pending'], 1)
        self.assertEqual(summary['due'], 0)
        self.assertEqual(summary['oldest_due_seconds'], 0)
        self.assertNotIn('private text', json.dumps(summary))
        self.now += 601
        bot.send_message.side_effect = NetworkError('offline')
        await outbox.deliver_due(bot)
        self.assertEqual((await storage.run_storage_io(health.storage_probe))['due'], 0)
        self.now += 100
        bot.send_message.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError): await outbox.deliver_due(bot)
        self.assertEqual((await storage.run_storage_io(health.storage_probe))['due'], 0)
        self.now += outbox.LEASE_SECONDS + 1
        self.assertEqual((await storage.run_storage_io(health.storage_probe))['due'], 1)

    async def test_slow_batch_reports_each_action_instead_of_entire_batch(self):
        for _ in range(5): await outbox.enqueue({42}, 'private text')
        watchdog = self.monitor.progress['outbox']
        async def send(**kwargs):
            self.now += 30
            self.assertFalse(watchdog.stalled())
        await outbox.deliver_due(SimpleNamespace(send_message=send), progress=watchdog.beat)
        self.assertGreater(self.now, 1000 + health.MAX_AGE)
        self.assertFalse(watchdog.stalled())

    async def test_worker_errors_mark_unhealthy_until_successful_pass(self):
        watchdog = self.monitor.progress['outbox']
        observations = []
        async def sleep(_):
            observations.append(watchdog.failed)
            if len(observations) == 2: raise asyncio.CancelledError()
        with patch.object(outbox, 'deliver_due', new=AsyncMock(side_effect=[OSError('disk'), 0])), \
             patch.object(outbox.asyncio, 'sleep', side_effect=sleep), \
             self.assertLogs('integrations.diagnostics', level='ERROR'):
            with self.assertRaises(asyncio.CancelledError): await outbox.worker_loop(None, {42}, watchdog=watchdog)
        self.assertEqual(observations, [True, False])

    async def test_heartbeat_retry_wait_extends_deadline_without_false_alarm(self):
        watchdog = self.monitor.progress['heartbeat']
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[RetryAfter(600), None]))
        async def sleep(delay):
            self.now += delay
            self.assertFalse(watchdog.stalled())
        with patch.object(technical_heartbeat.asyncio, 'sleep', side_effect=sleep):
            self.assertTrue(await technical_heartbeat.send_heartbeat(bot, {42}, 'test', progress=watchdog.beat))

