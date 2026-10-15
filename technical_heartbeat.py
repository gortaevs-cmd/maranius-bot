"""Daily owner-only process heartbeat at 10:00 Moscow time."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram.error import BadRequest, Forbidden, RetryAfter

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3), name="MSK")


def next_heartbeat(now: datetime) -> datetime:
    """Return the next 10:00 MSK, independently of the server timezone."""
    local = now.astimezone(MSK)
    scheduled = local.replace(hour=10, minute=0, second=0, microsecond=0)
    if scheduled < local:
        scheduled += timedelta(days=1)
    return scheduled


async def send_heartbeat(bot, chat_ids, bot_name: str) -> bool:
    """Send only to owners, retrying temporary failures without logging secrets."""
    delivered = True
    for chat_id in sorted(set(chat_ids)):
        if chat_id <= 0:
            continue
        for attempt in range(3):
            now = datetime.now(MSK)
            text = (
                f"✅ {bot_name} работает.\n"
                "Технический сигнал: процесс запущен, отправка в Telegram доступна.\n"
                f"{now:%d.%m.%Y %H:%M:%S} МСК"
            )
            try:
                await bot.send_message(chat_id=chat_id, text=text)
            except (BadRequest, Forbidden) as exc:
                logger.error(
                    "Heartbeat rejected: bot=%s error_type=%s", bot_name, type(exc).__name__
                )
                delivered = False
                break
            except Exception as exc:
                logger.warning(
                    "Heartbeat failed: bot=%s attempt=%s error_type=%s",
                    bot_name,
                    attempt + 1,
                    type(exc).__name__,
                )
                delay = 60
                if isinstance(exc, RetryAfter):
                    retry = exc.retry_after
                    delay = retry.total_seconds() if isinstance(retry, timedelta) else float(retry)
                    delay = max(1, delay)
                if attempt == 2 or delay > 600:
                    delivered = False
                    break
                # A network timeout may mean delivery succeeded; Telegram has no
                # idempotency key for sendMessage, so a retry can duplicate it.
                await asyncio.sleep(delay)
            else:
                logger.info("Heartbeat delivered: bot=%s", bot_name)
                break
    return delivered


async def heartbeat_loop(bot, chat_ids, bot_name: str) -> None:
    """Run inside the bot; restarts after 10:00 wait until the next day."""
    owners = tuple(sorted({chat_id for chat_id in chat_ids if chat_id > 0}))
    if not owners:
        logger.error("Heartbeat disabled: no owner configured for %s", bot_name)
        return
    while True:
        scheduled = next_heartbeat(datetime.now(MSK))
        logger.info("Heartbeat scheduled: bot=%s at=%s", bot_name, scheduled.isoformat())
        while True:
            delay = (scheduled - datetime.now(MSK)).total_seconds()
            if delay <= 0:
                break
            # Recheck wall time so clock adjustments do not shift the schedule.
            await asyncio.sleep(min(delay, 60))
        if datetime.now(MSK) - scheduled < timedelta(minutes=10):
            await send_heartbeat(bot, owners, bot_name)
        # Ensure the next iteration cannot reuse the same scheduled instant.
        await asyncio.sleep(1)
