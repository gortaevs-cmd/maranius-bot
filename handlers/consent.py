"""Согласие ПДн и opt-in рассылки."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, Set

from telegram import Update
from telegram.ext import ContextTypes

from integrations import analytics, consent_log, user_registry
import ui

AccessFn = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


def _main_kb(users: dict, user_id: int, seed_admin_ids: Optional[Set[int]]):
    if not seed_admin_ids:
        return ui.get_main_keyboard()
    return user_registry.main_reply_keyboard(users, user_id, seed_admin_ids=seed_admin_ids)


def is_restricted(update: Update, users: dict) -> bool:
    user = update.effective_user
    if not user:
        return False
    return user_registry.is_admin_blocked(users, user.id)


async def reply_restricted(update: Update) -> None:
    message = update.effective_message
    if message:
        await message.reply_text(ui.MSG_ACCESS_RESTRICTED, parse_mode="HTML")


async def show_policy_screen(update: Update, *, with_marketing_controls: bool = False) -> None:
    message = update.effective_message
    if not message:
        return
    kb = ui.get_policy_keyboard(with_marketing=with_marketing_controls)
    await message.reply_text(
        ui.MSG_POLICY_FULL,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=kb,
    )


async def show_policy_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        ui.MSG_POLICY_GATE,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=ui.get_policy_gate_keyboard(),
    )


async def show_marketing_offer(update: Update) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        ui.MSG_MARKETING_OFFER,
        parse_mode="HTML",
        reply_markup=ui.get_marketing_offer_keyboard(),
    )


async def _log_policy(user_id: int, *, source: str, action: str) -> None:
    await consent_log.append(
        user_id=user_id,
        event="policy_accepted",
        value=True,
        purpose="personal_data_processing",
        document="personal-data-consent",
        document_url=ui.URL_PERSONAL_DATA_CONSENT,
        policy_version=user_registry.PERSONAL_DATA_CONSENT_VERSION,
        action=action,
        source=source,
        meta={
            "privacy_policy_version": user_registry.PRIVACY_POLICY_VERSION,
            "user_agreement_version": user_registry.USER_AGREEMENT_VERSION,
        },
    )


async def _log_marketing(
    user_id: int, value: bool, *, source: str, action: str
) -> None:
    await consent_log.append(
        user_id=user_id,
        event="marketing_opt_in" if value else "marketing_opt_out",
        value=value,
        purpose="telegram_marketing",
        document="marketing-consent",
        document_url=ui.URL_MARKETING_CONSENT,
        policy_version=user_registry.MARKETING_CONSENT_VERSION,
        action=action,
        source=source,
        meta={
            "privacy_policy_version": user_registry.PRIVACY_POLICY_VERSION,
            "user_agreement_version": user_registry.USER_AGREEMENT_VERSION,
        },
    )


async def after_policy_accepted(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    users_lock,
    load_users: Callable[[], dict],
    save_users: Callable[[dict], None],
) -> None:
    user = update.effective_user
    if not user:
        return
    async with users_lock:
        users = load_users()
        user_registry.accept_policy(users, user.id, action=ui.CB_POLICY_ACCEPT)
        save_users(users)
        shown = user_registry.marketing_offer_was_shown(users, user.id)
        if not shown:
            user_registry.mark_marketing_offer_shown(users, user.id)
            save_users(users)
    await _log_policy(user.id, source="gate", action=ui.CB_POLICY_ACCEPT)
    if not shown:
        await show_marketing_offer(update)
        return
    pending = context.user_data.pop("pending_action", None)
    if pending:
        await message_reply_continue(update)


async def message_reply_continue(
    update: Update,
    *,
    users: dict,
    user_id: int,
    seed_admin_ids: set[int],
) -> None:
    message = update.effective_message
    if message:
        await message.reply_text(
            ui.MSG_POLICY_CONTINUE,
            reply_markup=user_registry.main_reply_keyboard(
                users, user_id, seed_admin_ids=seed_admin_ids
            ),
        )


async def consent_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    users_lock,
    load_users: Callable[[], dict],
    save_users: Callable[[dict], None],
    seed_admin_ids: Optional[Set[int]] = None,
) -> Optional[str]:
    """
    Обработка callback согласий. Returns pending_action если нужно продолжить действие.
    """
    query = update.callback_query
    if not query or not query.data:
        return None
    await query.answer()
    user = query.from_user
    if not user:
        return None
    data = query.data

    if data == ui.CB_POLICY_ACCEPT:
        async with users_lock:
            users = load_users()
            if user_registry.is_admin_blocked(users, user.id):
                await query.message.reply_text(ui.MSG_ACCESS_RESTRICTED, parse_mode="HTML")
                return None
            user_registry.accept_policy(users, user.id, action=data)
            shown = user_registry.marketing_offer_was_shown(users, user.id)
            if not shown:
                user_registry.mark_marketing_offer_shown(users, user.id)
            save_users(users)
        await _log_policy(user.id, source="gate", action=data)
        if not shown:
            await query.message.reply_text(
                ui.MSG_MARKETING_OFFER,
                parse_mode="HTML",
                reply_markup=ui.get_marketing_offer_keyboard(),
            )
            # Продолжаем start/другое действие только после
            # ответа на вопрос о рассылке, чтобы не слать два экрана сразу.
            return None
        await query.message.reply_text(
            ui.MSG_POLICY_ACCEPTED,
            parse_mode="HTML",
            reply_markup=_main_kb(users, user.id, seed_admin_ids),
        )
        return context.user_data.pop("pending_action", None)

    if data in (ui.CB_MARKETING_YES, ui.CB_MARKETING_NO):
        opt_in = data == ui.CB_MARKETING_YES
        async with users_lock:
            users = load_users()
            user_registry.set_marketing_opt_in(users, user.id, opt_in, action=data)
            save_users(users)
        await _log_marketing(
            user.id, opt_in, source="marketing_offer", action=data
        )
        pending = context.user_data.pop("pending_action", None)
        if pending:
            return pending
        text = ui.MSG_MARKETING_ON if opt_in else ui.MSG_MARKETING_OFF
        await query.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=_main_kb(users, user.id, seed_admin_ids),
        )
        return None

    if data == ui.CB_MARKETING_TOGGLE_ON:
        async with users_lock:
            users = load_users()
            user_registry.set_marketing_opt_in(users, user.id, True, action=data)
            save_users(users)
        await _log_marketing(user.id, True, source="policy_screen", action=data)
        await query.message.reply_text(
            ui.MSG_MARKETING_ON,
            parse_mode="HTML",
            reply_markup=_main_kb(users, user.id, seed_admin_ids),
        )
        return None

    if data == ui.CB_MARKETING_TOGGLE_OFF:
        async with users_lock:
            users = load_users()
            user_registry.set_marketing_opt_in(users, user.id, False, action=data)
            save_users(users)
        await _log_marketing(user.id, False, source="policy_screen", action=data)
        await query.message.reply_text(
            ui.MSG_MARKETING_OFF,
            parse_mode="HTML",
            reply_markup=_main_kb(users, user.id, seed_admin_ids),
        )
        return None

    return None


async def require_user_access(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    users_lock,
    load_users: Callable[[], dict],
    action_key: str,
    seed_admin_ids: Set[int],
) -> bool:
    """
    True — можно выполнять действие.
    False — показали gate / restricted.
    """
    user = update.effective_user
    if not user:
        return False
    if user.id in seed_admin_ids:
        return True

    async with users_lock:
        users = load_users()
        if user_registry.is_admin_blocked(users, user.id):
            await reply_restricted(update)
            return False
        if not user_registry.has_current_policy(users, user.id):
            context.user_data["pending_action"] = action_key
            await show_policy_gate(update, context)
            return False
    await analytics.log_section(user.id, action_key)
    return True
