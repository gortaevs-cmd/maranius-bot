"""In-process watchdog and a read-only, stdlib-only Docker health probe.

The probe never imports bot.py, opens working JSON or contacts Telegram. Only
the polling process probes storage (including WAL recovery), under its lock.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import time
from pathlib import Path

SNAPSHOT_FILE = Path("/tmp/maranius-health.json")
INTERVAL = 10
MAX_AGE = 90
REQUIRED_JOBS = frozenset({"weekly_report", "monthly_report", "storage_recovery_alerts", "runtime_health_tick"})
TASKS = {"heartbeat": "technical_heartbeat_task", "outbox": "notification_outbox_task"}


class Progress:
    """Event-loop-owned progress; deadlines include deliberate long sleeps."""

    def __init__(self):
        self.deadline = 0.0  # No success has been observed yet.
        self.failed = False

    def beat(self, *, wait: float = MAX_AGE) -> None:
        self.deadline = time.monotonic() + wait

    def stalled(self) -> bool:
        return time.monotonic() > self.deadline


def process_start(pid: int) -> str:
    # comm may contain spaces and parentheses. Field 22 is starttime in ticks.
    return (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]


def write_snapshot(snapshot: dict, path: Path = SNAPSHOT_FILE) -> None:
    """Atomic ephemeral status, without business backups or durable writes."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".maranius-health-", delete=False) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(snapshot, allow_nan=False).encode())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def check_snapshot(path: Path = SNAPSHOT_FILE) -> tuple[bool, str]:
    """Fail closed on stale, foreign-process, malformed or unready status."""
    try:
        snapshot = json.loads(path.read_bytes())
        if not isinstance(snapshot, dict) or type(snapshot.get("version")) is not int or snapshot["version"] != 1:
            return False, "invalid_snapshot"
        stamp, pid = snapshot["updated_monotonic"], snapshot["pid"]
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            return False, "invalid_snapshot"
        if not 0 <= time.monotonic() - stamp <= MAX_AGE:
            return False, "stale_snapshot"
        if type(pid) is not int or pid <= 0 or process_start(pid) != snapshot["process_start"]:
            return False, "different_process"
        args = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        if not any(arg.rsplit(b"/", 1)[-1] == b"bot.py" for arg in args):
            return False, "different_process"
        return (True, "ok") if snapshot.get("ready") is True else (False, "components_unready")
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return False, "snapshot_unavailable"


def storage_probe() -> dict:
    # Imports stay out of the standalone probe. run_storage_io recovers the WAL
    # before calling us; invalid/unreplayable WAL blocks this successful result.
    from integrations import notification_outbox
    return notification_outbox._transaction(notification_outbox._health_summary)


class RuntimeHealth:
    def __init__(self, *, path: Path = SNAPSHOT_FILE, identity: str | None = None):
        self.path = path
        self.pid = os.getpid()
        self.identity = process_start(self.pid) if identity is None else identity
        self.progress = {name: Progress() for name in (*TASKS, "scheduler")}
        self.storage_ok = False
        self.queue = None
        self.stopping = False

    def snapshot(self, application) -> dict:
        reasons = []
        if self.stopping:
            reasons.append("stopping")
        if not application.running or not application.updater or not application.updater.running:
            reasons.append("polling_not_running")
        for name, key in TASKS.items():
            task = application.bot_data.get(key)
            if task is None or task.done():
                reasons.append(name + "_stopped")
            elif self.progress[name].failed:
                reasons.append(name + "_failed")
            elif self.progress[name].stalled():
                reasons.append(name + "_stalled")
        queue = application.job_queue
        if queue is None or not queue.scheduler.running:
            reasons.append("scheduler_not_running")
        # PTB's enabled flag starts False even for scheduled jobs. The actual
        # APScheduler next time also detects a directly paused APS job.
        elif not REQUIRED_JOBS.issubset({job.name for job in queue.jobs() if job.next_t is not None}):
            reasons.append("scheduled_job_missing")
        if self.progress["scheduler"].stalled():
            reasons.append("scheduler_stalled")
        if not self.storage_ok:
            reasons.append("storage_unavailable")
        return {
            "version": 1, "pid": self.pid, "process_start": self.identity,
            "updated_monotonic": time.monotonic(), "ready": not reasons,
            "reasons": reasons, "queue": self.queue,
        }

    async def run(self, application) -> None:
        from integrations.diagnostics import log_error
        from integrations.json_storage import finish_storage_operation, run_storage_io
        while True:
            try:
                self.queue = await run_storage_io(storage_probe)
            except Exception as exc:
                self.storage_ok = False
                self.queue = None
                log_error("runtime_health_storage_failed", exc)
            else:
                self.storage_ok = True
            try:
                snapshot = self.snapshot(application)
                await finish_storage_operation(asyncio.to_thread(write_snapshot, snapshot, self.path))
            except Exception as exc:
                # The last successful snapshot becomes stale even if disk fails.
                log_error("runtime_health_snapshot_failed", exc)
            await asyncio.sleep(INTERVAL)


async def scheduler_tick(context) -> None:
    context.application.bot_data["runtime_health"].progress["scheduler"].beat()
