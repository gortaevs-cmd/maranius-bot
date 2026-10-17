"""Недельные и календарные месячные отчёты и их расписание."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dt_time
from typing import Callable, Dict, Set

from telegram.ext import Application, ContextTypes

from integrations.json_storage import run_storage_io
from integrations import admin_alerts, analytics, inbox as inbox_mod, user_registry

def _format_report_text(*, title: str, st: Dict[str, int], inbox_total: int) -> str:
    top = analytics.top_sections(7)
    top_lines = ", ".join(f"{k}: {v}" for k, v in top) if top else "—"
    return (
        f"<b>{title}</b>\n"
        f"Пользователи (real): {st['real']} (+{st['new_7d']} за 7д)\n"
        f"Активные 7/30д: {st['active_7']} / {st['active_30']}\n"
        f"Спящие (&gt;30д): {st['sleeping']}\n"
        f"VIP: {st['vip']} · без ПДн: {st['no_policy']} · рассылка: {st['marketing']}\n"
        f"Inbox (90д): {inbox_total}\n"
        f"Топ разделов 7д: {top_lines}"
    )


@dataclass
class ReportHandlers:
    user_stats: Callable[[], Dict[str, int]]
    seed_admin_ids: Set[int]

    async def _send_weekly_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        st = await run_storage_io(self.user_stats)
        inbox_total, _ = await inbox_mod.stats()
        await analytics.rollup_weekly()
        text = await run_storage_io(_format_report_text, title="📊 Отчёт за неделю", st=st, inbox_total=inbox_total)
        await admin_alerts.notify_seed_admins(context.bot, self.seed_admin_ids, text)

    async def _send_monthly_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        st = await run_storage_io(self.user_stats)
        inbox_total, _ = await inbox_mod.stats()
        now_msk = datetime.now(user_registry.MSK)
        prev = (now_msk.replace(day=1) - timedelta(days=1))
        entry = await analytics.rollup_monthly(prev.year, prev.month)
        monthly = entry["stats_month"]
        top = monthly["top_sections"]
        top_lines = ", ".join(f"{html.escape(k)}: {v}" for k, v in top) if top else "—"
        text = (
            f"<b>📅 Итог месяца {prev.strftime('%m.%Y')}</b>\n"
            "Активность за календарный месяц (МСК, по журналу):\n"
            f"Событий: {monthly['events']} · пользователей: {monthly['unique_users']}\n"
            f"Топ разделов месяца: {top_lines}\n\n"
            "<b>Текущая сводка базы:</b>\n"
            f"Пользователи (real): {st['real']}\n"
            f"Активные 7/30д: {st['active_7']} / {st['active_30']}\n"
            f"Спящие (&gt;30д): {st['sleeping']}\n"
            f"VIP: {st['vip']} · без ПДн: {st['no_policy']} · рассылка: {st['marketing']}\n"
            f"Inbox (90д): {inbox_total}"
        )
        await admin_alerts.notify_seed_admins(context.bot, self.seed_admin_ids, text)

    def _schedule_reports(self, application: Application) -> None:
        if not application.job_queue:
            print("JobQueue недоступен — отчёты не запланированы (установите python-telegram-bot[job-queue])")
            return
        report_time = dt_time(hour=12, minute=0, tzinfo=user_registry.MSK)
        application.job_queue.run_daily(
            self._send_weekly_report,
            time=report_time,
            # python-telegram-bot 20+: 0 = воскресенье, 6 = суббота.
            days=(0,),
            name="weekly_report",
        )

        async def monthly_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
            now_msk = datetime.now(user_registry.MSK)
            if now_msk.day == 1:
                await self._send_monthly_report(ctx)

        application.job_queue.run_daily(
            monthly_job,
            time=report_time,
            name="monthly_report",
        )
        print("Отчёты: воскресенье и 1-е число, 12:00 MSK")
