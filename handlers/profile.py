"""Настройки профиля: статус подписок и управление рассылкой."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from integrations import user_registry
import ui


def _fmt_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return html.escape(str(iso))


def _course_lines(courses: List[Dict[str, Any]]) -> List[str]:
    active = [
        c
        for c in courses
        if isinstance(c, dict) and str(c.get("status") or "").casefold() in ("active", "enrolled", "")
    ]
    if not active and courses:
        active = [c for c in courses if isinstance(c, dict)]
    if not active:
        return ["• нет активных зачислений"]
    lines: List[str] = []
    for item in active[:10]:
        name = html.escape(str(item.get("course_name") or item.get("course_id") or "курс"))
        enrolled = _fmt_date(str(item.get("enrolled_at") or ""))
        status = html.escape(str(item.get("status") or "активен"))
        lines.append(f"• {name} — {status} (с {enrolled})")
    return lines


def format_status_html(
    row: Dict[str, Any],
    *,
    is_vip: bool,
    courses: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Текст экрана «Статус» на русском."""
    courses = courses or []

    if is_vip:
        granted = _fmt_date(str(row.get("vip_granted_at") or ""))
        source = user_registry.vip_source_label(str(row.get("vip_source") or ""))
        vip_block = f"• активен (с {granted}, {source})"
    else:
        vip_block = "• не активен"

    if row.get("marketing_opt_in"):
        mkt_date = _fmt_date(str(row.get("marketing_opt_in_at") or ""))
        marketing_block = f"• подписаны (с {mkt_date})"
    else:
        marketing_block = "• не подписаны"

    if row.get("policy_accepted_at"):
        policy_date = _fmt_date(str(row.get("policy_accepted_at") or ""))
        policy_block = f"• принято (с {policy_date})"
    else:
        policy_block = "• не принято"

    course_lines = _course_lines(courses)
    courses_text = "\n".join(course_lines)

    return (
        "<b>📋 Статус подписок</b>\n\n"
        f"<b>VIP-доступ</b> (раздел «{ui.BTN_VIP}»):\n"
        f"{vip_block}\n\n"
        "<b>Рассылка новостей</b> в Telegram:\n"
        f"{marketing_block}\n\n"
        "<b>Обработка персональных данных</b>:\n"
        f"{policy_block}\n\n"
        "<b>Курсы и практики</b>:\n"
        f"{courses_text}"
    )


def subscriptions_message(*, marketing_opt_in: bool) -> str:
    return ui.MSG_PROFILE_SUBS_ON if marketing_opt_in else ui.MSG_PROFILE_SUBS_OFF


async def set_marketing_opt_in(
    *,
    users_lock,
    load_users: Callable[[], Dict[str, Any]],
    save_users: Callable[[Dict[str, Any]], None],
    user_id: int,
    value: bool,
) -> None:
    async with users_lock:
        users = load_users()
        user_registry.set_marketing_opt_in(users, user_id, value)
        save_users(users)
