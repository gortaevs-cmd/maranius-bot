"""Административные команды, callback и ввод /god без зависимости от bot.py."""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from telegram import InputFile, Update
from telegram.ext import ContextTypes

import ui
from handlers import vip as vip_handlers
from integrations.json_storage import run_storage_io
from integrations import admin_audit, consent_log, inbox as inbox_mod, user_registry, vip_codes, notification_outbox
from integrations.diagnostics import log_error

VIP_CODES_AUDIT_REASON = "Добавление VIP-кодов через админ-панель"

_ADMIN_ACTIONS = {
    "vip": "VIP выдать",
    "grant_vip": "VIP выдать",
    "unvip": "VIP снять",
    "revoke_vip": "VIP снять",
    "block": "ограничить доступ",
    "unblock": "снять ограничение",
}


async def _admin_edit_panel(message, text: str, keyboard) -> None:
    """Обновить сообщение панели или отправить новое, если edit недоступен."""
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


def _clear_admin_input_mode(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("admin_mode", None)
    context.user_data.pop("admin_batch", None)
    context.user_data.pop("admin_user_action", None)
    context.user_data.pop("admin_confirm", None)


def _reset_admin_navigation(context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_admin_input_mode(context)
    context.user_data.pop("admin_selected_user", None)


def _is_admin_unknown_angels_cmd(text: str) -> Optional[str]:
    """Legacy: перенаправление на inbox."""
    raw = (text or "").strip().casefold()
    if raw == ui.ADMIN_CMD_UNKNOWN_ANGELS.casefold():
        return "summary"
    if raw == ui.ADMIN_CMD_UNKNOWN_ANGELS_FILE.casefold():
        return "file"
    return None


def _admin_reason(raw: str) -> Optional[str]:
    reason = " ".join((raw or "").split())
    if len(reason) < 3:
        return None
    return reason[:200]


def _admin_audit_row(entry: Dict[str, Any]) -> str:
    actions = {
        "vip_grant": "выдача VIP",
        "vip_grant_from_alert": "выдача VIP по алерту",
        "vip_reject_from_alert": "отклонение VIP по алерту",
        "vip_revoke": "снятие VIP",
        "user_block": "ограничение доступа",
        "user_unblock": "снятие ограничения",
        "vip_import": "импорт VIP",
        "vip_codes_added": "добавление кодов",
    }
    targets = [str(value) for value in entry.get("target_ids", [])]
    target_text = ", ".join(targets[:3]) or "—"
    if len(targets) > 3:
        target_text += f" +{len(targets) - 3}"
    return (
        f"• <code>{html.escape(str(entry.get('created_at') or ''))}</code> — "
        f"{html.escape(actions.get(str(entry.get('action')), str(entry.get('action') or 'действие')))}; "
        f"цели: <code>{html.escape(target_text)}</code>; "
        f"{html.escape(str(entry.get('reason') or '—'))}"
    )


def _admin_consent_row(entry: Dict[str, Any]) -> str:
    events = {
        "policy_accepted": "ПДн принято",
        "marketing_opt_in": "рассылка: да",
        "marketing_opt_out": "рассылка: нет",
    }
    event = events.get(str(entry.get("event")), str(entry.get("event") or "согласие"))
    source = str(entry.get("source") or "—")
    version = str(entry.get("policy_version") or "—")
    document = str(entry.get("document") or "—")
    action = str(entry.get("action") or "—")
    return (
        f"• <code>{html.escape(str(entry.get('created_at') or ''))}</code> — "
        f"user <code>{html.escape(str(entry.get('user_id') or ''))}</code>: "
        f"{html.escape(event)}; источник: {html.escape(source)}; "
        f"документ: {html.escape(document)}; версия: {html.escape(version)}; "
        f"действие: {html.escape(action)}"
    )


@dataclass
class AdminHandlers:
    seed_admin_ids: Set[int]
    users_lock: Any
    load_users: Callable[[], Dict[str, Any]]
    save_users: Callable[[Dict[str, Any]], None]
    require_private_chat: Callable[[Update], Awaitable[bool]]
    ensure_user_saved: Callable[..., Awaitable[bool]]
    add_admin: Callable[[int], Awaitable[None]]
    main_keyboard_for: Callable[[int], Any]
    audit_file: Path
    consent_file: Path
    bot_profile: Callable[[], str]

    async def _open_god_panel(self, update: Update) -> None:
        """Открыть корень режима бога (/god или текст god). Только seed-админы."""
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return
        if user.id not in self.seed_admin_ids:
            await message.reply_text(ui.ADMIN_DENIED)
            return
        if not await self.require_private_chat(update):
            return
        await self.add_admin(user.id)
        await message.reply_text(
            ui.ADMIN_STUB,
            parse_mode="HTML",
            reply_markup=ui.get_admin_home_keyboard(),
        )

    async def god_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Скрытый /god: только для seed/admins, без запроса кода."""
        user = update.effective_user
        if not user or user.id not in self.seed_admin_ids:
            await self._open_god_panel(update)
            return
        if not await self.require_private_chat(update):
            return
        await self.ensure_user_saved(update, force=True)
        await self._open_god_panel(update)

    async def _admin_guard(self, update: Update) -> bool:
        """Guard для /god: seed-админы с принятыми обязательными документами."""
        user = update.effective_user
        if not user and update.callback_query:
            user = update.callback_query.from_user
        if user and user.id in self.seed_admin_ids:
            if not await self.require_private_chat(update):
                return False
            users = await run_storage_io(self.load_users)
            if user_registry.has_current_access(users, user.id):
                return True
            message = update.effective_message
            if message:
                await message.reply_text(
                    ui.MSG_POLICY_GATE,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=ui.get_policy_gate_keyboard(
                        user_agreement_accepted=user_registry.has_current_user_agreement(
                            users, user.id
                        ),
                        personal_data_consent_accepted=user_registry.has_current_policy(
                            users, user.id
                        ),
                    ),
                )
            return False
        message = update.effective_message
        if message:
            await message.reply_text(ui.ADMIN_DENIED)
        return False

    async def admin_inbox(self, update: Update, mode: str) -> None:
        """Сводка или CSV inbox (только seed-admin)."""
        message = update.effective_message
        if not message or not await self._admin_guard(update):
            return

        total, unnotified = await inbox_mod.stats()
        if total == 0:
            await message.reply_text(
                ui.ADMIN_INBOX_EMPTY,
                reply_markup=ui.get_admin_inbox_keyboard(),
            )
            return

        if mode == "summary":
            await message.reply_text(
                ui.ADMIN_INBOX_SUMMARY.format(total=total, unnotified=unnotified),
                parse_mode="HTML",
                reply_markup=ui.get_admin_inbox_keyboard(),
            )
            return

        csv_bytes, ts = await inbox_mod.export_csv_bytes()
        import io

        await message.reply_document(
            document=InputFile(io.BytesIO(csv_bytes), filename="inbox.csv"),
            caption=f"Inbox: {total} записей, скачано {ts}",
            reply_markup=ui.get_admin_inbox_keyboard(),
        )

    async def admin_unknown_angels(self, update: Update, mode: str) -> None:
        """Legacy alias → inbox."""
        await self.admin_inbox(update, mode)

    async def admin_export_list(self, update: Update, segment: Optional[str]) -> None:
        message = update.effective_message
        if not message or not await self._admin_guard(update):
            return
        users = await run_storage_io(self.load_users)
        data = await run_storage_io(user_registry.export_users_csv, users, segment=segment)
        import io

        name = f"users_{segment or 'all'}.csv"
        await message.reply_document(
            document=InputFile(io.BytesIO(data), filename=name),
            caption=f"Сегмент: {segment or 'all'}",
            reply_markup=ui.get_admin_lists_keyboard(),
        )

    async def _audit_admin_action(self,
        update: Update,
        *,
        action: str,
        target_ids=(),
        reason: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        actor = update.effective_user
        if not actor:
            return
        await admin_audit.append(
            self.audit_file,
            actor_id=actor.id,
            action=action,
            target_ids=target_ids,
            reason=reason,
            meta=meta,
        )

    async def _admin_exec_user_cmd(self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        cmd: str,
        uid: int,
        reason: str,
    ) -> None:
        """Выполнить изменение пользователя после подтверждения и записать аудит."""
        message = update.effective_message
        if uid in self.seed_admin_ids:
            if message:
                await message.reply_text(ui.ADMIN_USER_PROTECTED)
            return

        changed = False
        action = ""
        audit_action = ""
        async with self.users_lock:
            users = await run_storage_io(self.load_users)
            if cmd in ("vip", "grant_vip"):
                changed = user_registry.grant_vip(users, uid, source="admin_grant")
                action = "VIP выдан" if changed else "VIP уже был выдан"
                audit_action = "vip_grant"
            elif cmd in ("unvip", "revoke_vip"):
                changed = user_registry.revoke_vip(users, uid)
                action = "VIP снят" if changed else "VIP уже был снят"
                audit_action = "vip_revoke"
            elif cmd == "block":
                changed = not user_registry.is_admin_blocked(users, uid)
                user_registry.set_admin_blocked(users, uid, True)
                action = "доступ ограничен" if changed else "доступ уже был ограничен"
                audit_action = "user_block"
            elif cmd == "unblock":
                changed = user_registry.is_admin_blocked(users, uid)
                user_registry.set_admin_blocked(users, uid, False)
                action = "ограничение снято" if changed else "ограничения уже не было"
                audit_action = "user_unblock"
            await run_storage_io(self.save_users, users)

        await self._audit_admin_action(
            update,
            action=audit_action,
            target_ids=[uid],
            reason=reason,
            meta={"changed": changed, "source": "god"},
        )

        if changed and cmd in ("vip", "grant_vip"):
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=ui.MSG_VIP_APPROVE_USER,
                    parse_mode="HTML",
                    reply_markup=await run_storage_io(self.main_keyboard_for, uid),
                )
            except Exception as exc:
                log_error("vip_user_notification_failed", exc)

        if message:
            await message.reply_text(
                ui.ADMIN_USER_CMD_OK.format(action=action, target=uid) + " Журнал обновлён.",
                parse_mode="HTML",
                reply_markup=ui.get_admin_users_manage_keyboard(),
            )

    async def _admin_queue_user_action(self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        cmd: str,
        uid: int,
        reason: str,
    ) -> None:
        context.user_data["admin_confirm"] = {"cmd": cmd, "target": uid, "reason": reason}
        await update.effective_message.reply_text(
            ui.ADMIN_CONFIRM_PROMPT.format(
                action=_ADMIN_ACTIONS[cmd], target=uid, reason=html.escape(reason)
            ),
            parse_mode="HTML",
            reply_markup=ui.get_admin_confirm_keyboard(),
        )

    async def _admin_apply_user_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
        """Текстовые команды /god с обязательным основанием."""
        parts = text.split()
        if not parts or parts[0].casefold() not in _ADMIN_ACTIONS:
            return False
        cmd = parts[0].casefold()
        if len(parts) < 2:
            await update.message.reply_text(ui.ADMIN_USER_CMD_REASON_REQUIRED, parse_mode="HTML")
            return True
        target_raw = parts[1]
        uid = user_registry.parse_user_ref(target_raw)
        if uid is None:
            uid = user_registry.find_user_id_by_username(await run_storage_io(self.load_users), target_raw)
        if uid is None:
            await update.message.reply_text(
                ui.ADMIN_USER_CMD_FAIL.format(target=html.escape(target_raw)), parse_mode="HTML"
            )
            return True
        reason = _admin_reason(" ".join(parts[2:]))
        if not reason:
            await update.message.reply_text(ui.ADMIN_USER_CMD_REASON_REQUIRED, parse_mode="HTML")
            return True
        await self._admin_queue_user_action(update, context, cmd=cmd, uid=uid, reason=reason)
        return True

    async def _admin_find_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str) -> None:
        target = (raw or "").strip()
        uid = user_registry.parse_user_ref(target)
        users = await run_storage_io(self.load_users)
        if uid is None:
            uid = user_registry.find_user_id_by_username(users, target)
        row = users.get(str(uid)) if uid is not None else None
        if uid is None or not isinstance(row, dict):
            await update.effective_message.reply_text(
                ui.ADMIN_USER_CMD_FAIL.format(target=html.escape(target)), parse_mode="HTML",
                reply_markup=ui.get_admin_users_manage_keyboard(),
            )
            return
        context.user_data["admin_selected_user"] = uid
        await update.effective_message.reply_text(
            self._admin_user_card_text(uid, row),
            parse_mode="HTML",
            reply_markup=ui.get_admin_user_card_keyboard(),
        )

    async def _admin_prepare_selected_user_action(self,
        update: Update, context: ContextTypes.DEFAULT_TYPE, cmd: str
    ) -> None:
        uid = context.user_data.get("admin_selected_user")
        if not isinstance(uid, int) or cmd not in _ADMIN_ACTIONS:
            await update.effective_message.reply_text(
                ui.ADMIN_MENU_USERS,
                parse_mode="HTML",
                reply_markup=ui.get_admin_users_manage_keyboard(),
            )
            return
        context.user_data["admin_user_action"] = {"cmd": cmd, "target": uid}
        context.user_data["admin_mode"] = "user_action_reason"
        await update.effective_message.reply_text(
            f"{_ADMIN_ACTIONS[cmd]} для <code>{uid}</code>. {ui.ADMIN_BATCH_REASON_PROMPT}",
            parse_mode="HTML",
            reply_markup=ui.get_admin_users_manage_keyboard(),
        )

    async def _admin_receive_selected_user_reason(self,
        update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
    ) -> None:
        pending = context.user_data.pop("admin_user_action", None)
        reason = _admin_reason(text)
        context.user_data.pop("admin_mode", None)
        if not isinstance(pending, dict) or not reason:
            await update.effective_message.reply_text(ui.ADMIN_USER_CMD_REASON_REQUIRED, parse_mode="HTML")
            return
        await self._admin_queue_user_action(
            update,
            context,
            cmd=pending["cmd"],
            uid=pending["target"],
            reason=reason,
        )

    async def _admin_prepare_vip_codes(self,
        update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
    ) -> None:
        added, duplicates, skipped = await vip_codes.preview_codes_bulk(text)
        if added == 0:
            await update.effective_message.reply_text(
                "Нет новых кодов для добавления. Проверь список и попробуй ещё раз.",
                reply_markup=ui.get_admin_vip_prompt_keyboard(),
            )
            return
        context.user_data["admin_batch"] = {
            "kind": "vip_codes",
            "raw": text,
            "preview": {"added": added, "duplicates": duplicates, "skipped": skipped},
            "reason": VIP_CODES_AUDIT_REASON,
        }
        context.user_data.pop("admin_mode", None)
        await update.effective_message.reply_text(
            ui.ADMIN_BATCH_CODE_PREVIEW.format(
                added=added,
                dup=duplicates,
                skipped=skipped,
            ),
            parse_mode="HTML",
            reply_markup=ui.get_admin_batch_confirm_keyboard(),
        )

    async def _admin_prepare_vip_import(self,
        update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
    ) -> None:
        ids, invalid = vip_codes.parse_vip_user_ids(text)
        unique_ids = list(dict.fromkeys(ids))
        duplicates = len(ids) - len(unique_ids)
        if not unique_ids:
            await update.effective_message.reply_text(
                "Не нашёл корректных Telegram ID. Пришли список ещё раз.",
                reply_markup=ui.get_admin_vip_prompt_keyboard(),
            )
            return
        async with self.users_lock:
            users = await run_storage_io(self.load_users)
            granted = sum(not bool(users.get(str(uid), {}).get("vip")) for uid in unique_ids)
        context.user_data["admin_batch"] = {
            "kind": "vip_import",
            "ids": unique_ids,
            "invalid": invalid,
            "duplicates": duplicates,
            "preview": {"granted": granted, "skipped": len(unique_ids) - granted},
        }
        context.user_data["admin_mode"] = "vip_import_reason"
        await update.effective_message.reply_text(ui.ADMIN_BATCH_REASON_PROMPT, parse_mode="HTML")

    async def _admin_receive_batch_reason(self,
        update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
    ) -> None:
        batch = context.user_data.get("admin_batch")
        reason = _admin_reason(text)
        context.user_data.pop("admin_mode", None)
        if not isinstance(batch, dict) or not reason:
            context.user_data.pop("admin_batch", None)
            await update.effective_message.reply_text(ui.ADMIN_USER_CMD_REASON_REQUIRED, parse_mode="HTML")
            return
        batch["reason"] = reason
        context.user_data["admin_batch"] = batch
        if batch["kind"] == "vip_codes":
            text_out = ui.ADMIN_BATCH_CODE_PREVIEW.format(
                added=batch["preview"]["added"],
                dup=batch["preview"]["duplicates"],
                skipped=batch["preview"]["skipped"],
                reason=html.escape(reason),
            )
        else:
            text_out = ui.ADMIN_BATCH_IMPORT_PREVIEW.format(
                granted=batch["preview"]["granted"],
                skipped=batch["preview"]["skipped"],
                duplicates=batch["duplicates"],
                invalid=batch["invalid"],
                reason=html.escape(reason),
            )
        await update.effective_message.reply_text(
            text_out,
            parse_mode="HTML",
            reply_markup=ui.get_admin_batch_confirm_keyboard(),
        )

    async def _admin_confirm_batch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        batch = context.user_data.pop("admin_batch", None)
        message = update.effective_message
        if not isinstance(batch, dict) or not message:
            return
        if batch.get("kind") == "vip_codes":
            added, duplicates, skipped = await vip_codes.add_codes_bulk(batch["raw"])
            await self._audit_admin_action(
                update,
                action="vip_codes_added",
                reason=batch.get("reason") or VIP_CODES_AUDIT_REASON,
                meta={"added": added, "duplicates": duplicates, "skipped": skipped},
            )
            await message.reply_text(
                ui.ADMIN_VIP_ADD_RESULT.format(added=added, dup=duplicates, skipped=skipped),
                parse_mode="HTML",
                reply_markup=ui.get_admin_vip_keyboard(),
            )
            return
        if batch.get("kind") == "vip_import":
            granted = skipped = 0
            async with self.users_lock:
                users = await run_storage_io(self.load_users)
                for uid in batch["ids"]:
                    if user_registry.grant_vip(users, uid, source="import"):
                        granted += 1
                    else:
                        skipped += 1
                await run_storage_io(self.save_users, users)
            await self._audit_admin_action(
                update,
                action="vip_import",
                target_ids=batch["ids"],
                reason=batch["reason"],
                meta={
                    "granted": granted,
                    "already_vip": skipped,
                    "duplicates": batch["duplicates"],
                    "invalid": batch["invalid"],
                },
            )
            await message.reply_text(
                ui.ADMIN_VIP_IMPORT_RESULT.format(
                    granted=granted,
                    skipped=skipped,
                    duplicates=batch["duplicates"],
                    invalid=batch["invalid"],
                ),
                parse_mode="HTML",
                reply_markup=ui.get_admin_vip_keyboard(),
            )

    async def admin_audit_summary(self, update: Update) -> None:
        message = update.effective_message
        if not message or not await self._admin_guard(update):
            return
        entries = await admin_audit.recent(self.audit_file)
        if not entries:
            await message.reply_text(ui.ADMIN_AUDIT_EMPTY, reply_markup=ui.get_admin_audit_keyboard())
            return
        await message.reply_text(
            ui.ADMIN_AUDIT_SUMMARY.format(rows="\n".join(_admin_audit_row(entry) for entry in entries)),
            parse_mode="HTML",
            reply_markup=ui.get_admin_audit_keyboard(),
        )

    async def admin_audit_export(self, update: Update) -> None:
        message = update.effective_message
        if not message or not await self._admin_guard(update):
            return
        data = await admin_audit.export_csv_bytes(self.audit_file)
        import io

        await message.reply_document(
            document=InputFile(io.BytesIO(data), filename="admin_audit.csv"),
            caption="Журнал действий /god",
            reply_markup=ui.get_admin_audit_keyboard(),
        )

    async def admin_consent_summary(self, update: Update) -> None:
        message = update.effective_message
        if not message or not await self._admin_guard(update):
            return
        entries = await consent_log.recent(self.consent_file, limit=10)
        if not entries:
            await message.reply_text(ui.ADMIN_CONSENT_EMPTY, reply_markup=ui.get_admin_audit_keyboard())
            return
        await message.reply_text(
            ui.ADMIN_CONSENT_SUMMARY.format(rows="\n".join(_admin_consent_row(entry) for entry in entries)),
            parse_mode="HTML",
            reply_markup=ui.get_admin_audit_keyboard(),
        )

    async def admin_consent_export(self, update: Update) -> None:
        message = update.effective_message
        if not message or not await self._admin_guard(update):
            return
        data = await consent_log.export_csv_bytes(self.consent_file)
        import io

        await message.reply_document(
            document=InputFile(io.BytesIO(data), filename="consent_log.csv"),
            caption="Журнал согласий",
            reply_markup=ui.get_admin_audit_keyboard(),
        )

    async def admin_users_summary(self, update: Update) -> None:
        message = update.effective_message
        if not message or not await self._admin_guard(update):
            return
        users = await run_storage_io(self.load_users)
        st = await run_storage_io(user_registry.stats_summary, users)
        await message.reply_text(
            ui.ADMIN_USERS_SUMMARY.format(
                total=st["total"],
                real=st["real"],
                week=st["new_7d"],
                vip=st["vip"],
                no_policy=st["no_policy"],
                marketing=st["marketing"],
                sleeping=st["sleeping"],
            ),
            parse_mode="HTML",
            reply_markup=ui.get_admin_bot_keyboard(),
        )

    async def admin_bot_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message or not await self._admin_guard(update):
            return
        me = await context.bot.get_me()
        username = me.username or "?"
        cmd_count = len(ui.get_bot_commands())
        delivery = await notification_outbox.stats()
        await message.reply_text(
            ui.ADMIN_STATUS.format(
                profile=self.bot_profile(),
                username=username,
                cmd_count=cmd_count,
            ) + (
                f"\nУведомления: ожидают {delivery['pending']}, ошибок {delivery['failed']}, "
                f"истёк срок {delivery['expired']}"
            ),
            parse_mode="HTML",
            reply_markup=ui.get_admin_bot_keyboard(),
        )

    async def admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Inline-кнопки режима бога."""
        query = update.callback_query
        if not query or not query.message:
            return
        await query.answer()
        if not await self._admin_guard(update):
            return
        data = query.data or ""

        if data == ui.CB_ADMIN_HOME:
            _reset_admin_navigation(context)
            await _admin_edit_panel(query.message, ui.ADMIN_STUB, ui.get_admin_home_keyboard())
            return
        if data == ui.CB_ADMIN_MENU_INBOX or data == ui.CB_ADMIN_MENU_ANGELS:
            _reset_admin_navigation(context)
            await _admin_edit_panel(
                query.message, ui.ADMIN_MENU_INBOX, ui.get_admin_inbox_keyboard()
            )
            return
        if data == ui.CB_ADMIN_MENU_LISTS:
            _reset_admin_navigation(context)
            await _admin_edit_panel(
                query.message, ui.ADMIN_LISTS_HINT, ui.get_admin_lists_keyboard()
            )
            return
        if data == ui.CB_ADMIN_MENU_USERS:
            _reset_admin_navigation(context)
            await _admin_edit_panel(
                query.message, ui.ADMIN_MENU_USERS, ui.get_admin_users_manage_keyboard()
            )
            return
        if data == ui.CB_ADMIN_MENU_BOT:
            _reset_admin_navigation(context)
            await _admin_edit_panel(query.message, ui.ADMIN_MENU_BOT, ui.get_admin_bot_keyboard())
            return
        if data == ui.CB_ADMIN_MENU_VIP:
            _reset_admin_navigation(context)
            await _admin_edit_panel(query.message, ui.ADMIN_MENU_VIP, ui.get_admin_vip_keyboard())
            return
        if data == ui.CB_ADMIN_MENU_AUDIT:
            _reset_admin_navigation(context)
            await _admin_edit_panel(query.message, ui.ADMIN_MENU_AUDIT, ui.get_admin_audit_keyboard())
            return

        if data in (ui.CB_ADMIN_UNKNOWN, ui.CB_ADMIN_INBOX):
            await self.admin_inbox(update, "summary")
            return
        if data in (ui.CB_ADMIN_UNKNOWN_CSV, ui.CB_ADMIN_INBOX_CSV):
            await self.admin_inbox(update, "file")
            return
        if data == ui.CB_ADMIN_LIST_ALL:
            await self.admin_export_list(update, None)
            return
        if data.startswith(ui.CB_ADMIN_LIST_SEGMENT_PREFIX) and data != ui.CB_ADMIN_LIST_ALL:
            seg = data[len(ui.CB_ADMIN_LIST_SEGMENT_PREFIX) :]
            await self.admin_export_list(update, seg)
            return
        if data == ui.CB_ADMIN_USERS:
            await self.admin_users_summary(update)
            return
        if data == ui.CB_ADMIN_STATUS:
            await self.admin_bot_status(update, context)
            return
        if data == ui.CB_ADMIN_AUDIT:
            await self.admin_audit_summary(update)
            return
        if data == ui.CB_ADMIN_AUDIT_CSV:
            await self.admin_audit_export(update)
            return
        if data == ui.CB_ADMIN_CONSENT:
            await self.admin_consent_summary(update)
            return
        if data == ui.CB_ADMIN_CONSENT_CSV:
            await self.admin_consent_export(update)
            return
        if data == ui.CB_ADMIN_USER_FIND:
            _clear_admin_input_mode(context)
            context.user_data["admin_mode"] = "user_lookup"
            await query.message.reply_text(
                ui.ADMIN_USER_FIND_PROMPT,
                reply_markup=ui.get_admin_users_manage_keyboard(),
            )
            return
        if data == ui.CB_ADMIN_USER_BACK:
            _reset_admin_navigation(context)
            await _admin_edit_panel(
                query.message, ui.ADMIN_MENU_USERS, ui.get_admin_users_manage_keyboard()
            )
            return
        user_actions = {
            ui.CB_ADMIN_USER_VIP_ON: "vip",
            ui.CB_ADMIN_USER_VIP_OFF: "unvip",
            ui.CB_ADMIN_USER_BLOCK: "block",
            ui.CB_ADMIN_USER_UNBLOCK: "unblock",
        }
        if data in user_actions:
            await self._admin_prepare_selected_user_action(update, context, user_actions[data])
            return
        if data == ui.CB_ADMIN_VIP:
            await vip_handlers.admin_vip_summary(update, self._admin_guard)
            return
        if data == ui.CB_ADMIN_VIP_EXPORT:
            await vip_handlers.admin_vip_export(update, self._admin_guard)
            return
        if data == ui.CB_ADMIN_VIP_ADD:
            await vip_handlers.admin_vip_add_prompt(update, context, self._admin_guard)
            return
        if data == ui.CB_ADMIN_VIP_IMPORT:
            await vip_handlers.admin_vip_import_prompt(update, context, self._admin_guard)
            return
        if data == ui.CB_ADMIN_VIP_CANCEL:
            _clear_admin_input_mode(context)
            await _admin_edit_panel(
                query.message,
                ui.ADMIN_MENU_VIP,
                ui.get_admin_vip_keyboard(),
            )
            return
        if data == ui.CB_ADMIN_BATCH_CONFIRM:
            await self._admin_confirm_batch(update, context)
            return
        if data == ui.CB_ADMIN_BATCH_CANCEL:
            _clear_admin_input_mode(context)
            await _admin_edit_panel(
                query.message,
                ui.ADMIN_MENU_VIP,
                ui.get_admin_vip_keyboard(),
            )
            return
        if data == ui.CB_ADMIN_CONFIRM:
            confirm = context.user_data.pop("admin_confirm", None)
            if confirm:
                await self._admin_exec_user_cmd(
                    update,
                    context,
                    confirm["cmd"],
                    confirm["target"],
                    confirm["reason"],
                )
            else:
                await _admin_edit_panel(
                    query.message, ui.ADMIN_MENU_USERS, ui.get_admin_users_manage_keyboard()
                )
            return
        if data == ui.CB_ADMIN_CANCEL:
            context.user_data.pop("admin_confirm", None)
            await _admin_edit_panel(
                query.message, ui.ADMIN_MENU_USERS, ui.get_admin_users_manage_keyboard()
            )
            return

    def _admin_user_card_text(self, uid: int, row: Dict[str, Any]) -> str:
        users = {str(uid): row}
        username = (row.get("username") or "").strip()
        username_text = f"@{html.escape(username)}" if username else "—"
        vip_source = html.escape(user_registry.vip_source_label(row.get("vip_source")))
        return ui.ADMIN_USER_CARD.format(
            user_id=uid,
            username=username_text,
            vip="да" if row.get("vip") else "нет",
            vip_source=vip_source,
            bot_status="заблокировал" if row.get("bot_status") == "blocked" else "доступен",
            admin_blocked="да" if row.get("admin_blocked") else "нет",
            marketing="да" if user_registry.has_current_marketing_consent(users, uid) else "нет",
            policy="да" if user_registry.has_current_policy(users, uid) else "нет",
            agreement="да" if user_registry.has_current_user_agreement(users, uid) else "нет",
            last_seen=html.escape(str(row.get("last_seen") or "—")),
        )

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
        """True — административный ввод обработан; False — обычный текст."""
        user = update.effective_user
        if not user or user.id not in self.seed_admin_ids:
            return False
        if not await self.require_private_chat(update):
            return True
        if text.casefold() == ui.GOD_TEXT_TRIGGER:
            await self._open_god_panel(update)
            return True
        admin_angel = _is_admin_unknown_angels_cmd(text)
        if admin_angel:
            await self.admin_inbox(update, admin_angel)
            return True
        admin_mode = context.user_data.get("admin_mode")
        if admin_mode == "user_lookup":
            context.user_data.pop("admin_mode", None)
            await self._admin_find_user(update, context, text)
            return True
        if admin_mode == "user_action_reason":
            await self._admin_receive_selected_user_reason(update, context, text)
            return True
        if admin_mode == "vip_add":
            await self._admin_prepare_vip_codes(update, context, text)
            return True
        if admin_mode == "vip_import":
            await self._admin_prepare_vip_import(update, context, text)
            return True
        if admin_mode == "vip_import_reason":
            await self._admin_receive_batch_reason(update, context, text)
            return True
        if await self._admin_apply_user_cmd(update, context, text):
            return True
        return False
