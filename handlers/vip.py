"""VIP: коды, меню колод, approve/reject, админка."""

from __future__ import annotations

import html
import io
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Set

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import ContextTypes

from integrations import vip_codes, vip_content
from integrations.json_storage import load_json, save_json
import ui

ADMIN_NOTIFY_COOLDOWN_SEC = 300


def _reply_keyboard(user_id: int, main_keyboard_for: Optional[Callable[[int], Any]] = None):
    if main_keyboard_for:
        return main_keyboard_for(user_id)
    return ui.get_main_keyboard()


async def load_vip_notify(path: str) -> Dict[str, str]:
    data = load_json(path, {})
    if not isinstance(data, dict):
        raise ValueError(f"admin_notify.json должен содержать JSON-объект: {path}")
    return data


async def save_vip_notify(path: str, data: Dict[str, str]) -> None:
    save_json(path, data, trailing_newline=True)


def user_link(user_id: int, username: Optional[str], first_name: str = "") -> str:
    if username:
        safe = html.escape(username)
        return f'<a href="https://t.me/{safe}">@{safe}</a>'
    label = html.escape(first_name) if first_name else str(user_id)
    return f'<a href="tg://user?id={user_id}">{label}</a>'


async def notify_admin_wrong_code(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    notify_path: str,
    notify_lock,
    seed_admin_ids: Set[int],
    load_admins: Callable[[], Set[int]],
    user_id: int,
    username: Optional[str],
    first_name: str,
    code: str,
) -> None:
    async with notify_lock:
        data = await load_vip_notify(notify_path)
        raw = data.get(str(user_id))
        if raw:
            try:
                last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=pytz.UTC)
                now = datetime.now(pytz.UTC)
                if (now - last).total_seconds() < ADMIN_NOTIFY_COOLDOWN_SEC:
                    return
            except (ValueError, TypeError):
                pass
        data[str(user_id)] = datetime.now(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        await save_vip_notify(notify_path, data)

    text = ui.ADMIN_VIP_WRONG_CODE.format(
        user_link=user_link(user_id, username, first_name),
        code=html.escape(code.strip()[:120]),
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Открыть доступ",
                    callback_data=f"{ui.CB_VIP_APPROVE_PREFIX}{user_id}",
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=f"{ui.CB_VIP_REJECT_PREFIX}{user_id}",
                ),
            ]
        ]
    )
    for admin_id in seed_admin_ids:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as exc:
            print(f"VIP notify admin {admin_id}: {exc!r}")


async def try_vip_code(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw_code: str,
    *,
    is_vip: Callable[[int], bool],
    grant_vip: Callable[[int], Any],
    show_vip_home: Callable[[Update, ContextTypes.DEFAULT_TYPE], Any],
    notify_wrong: Callable[..., Any],
    notify_duplicate: Optional[Callable[..., Any]] = None,
    protect_kwargs: Dict[str, Any],
    main_keyboard_for: Optional[Callable[[int], Any]] = None,
    notify_success: Optional[Callable[..., Any]] = None,
) -> bool:
    user = update.effective_user
    if not user or not update.message:
        return False
    if is_vip(user.id):
        context.user_data.pop("awaiting_vip_code", None)
        return False
    if not context.user_data.get("awaiting_vip_code"):
        return False

    ok, reason = await vip_codes.redeem_code(
        raw_code,
        user_id=user.id,
        username=user.username,
    )
    if ok:
        try:
            await grant_vip(user.id)
        except Exception:
            await vip_codes.rollback_redemption(raw_code, user_id=user.id)
            raise
        context.user_data.pop("awaiting_vip_code", None)
        if notify_success:
            await notify_success(
                context,
                user_id=user.id,
                username=user.username,
                first_name=user.first_name or "",
                code=raw_code,
            )
        await update.message.reply_text(
            ui.MSG_VIP_CODE_OK,
            parse_mode="HTML",
            reply_markup=_reply_keyboard(user.id, main_keyboard_for),
        )
        await show_vip_home(update, context)
        return True

    if reason == "already_used":
        context.user_data.pop("awaiting_vip_code", None)
        await update.message.reply_text(
            ui.MSG_VIP_CODE_INVALID,
            parse_mode="HTML",
            reply_markup=_reply_keyboard(user.id, main_keyboard_for),
            **protect_kwargs,
        )
        owner = vip_codes.find_used_code_owner(raw_code)
        if owner and notify_duplicate:
            await notify_duplicate(
                context,
                user_id=user.id,
                username=user.username,
                code=raw_code,
                owner=owner,
            )
        elif notify_wrong:
            await notify_wrong(
                context,
                user_id=user.id,
                username=user.username,
                first_name=user.first_name or "",
                code=raw_code,
            )
        return True

    context.user_data.pop("awaiting_vip_code", None)
    await update.message.reply_text(
        ui.MSG_VIP_CODE_INVALID,
        parse_mode="HTML",
        reply_markup=_reply_keyboard(user.id, main_keyboard_for),
        **protect_kwargs,
    )
    await notify_wrong(
        context,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name or "",
        code=raw_code,
    )
    return True


async def send_vip_html_parts(message, html: str, reply_markup, protect_kwargs) -> None:
    parts = vip_content.split_html_message(html)
    for i, chunk in enumerate(parts):
        await message.reply_text(
            chunk,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup if i == len(parts) - 1 else None,
            **protect_kwargs,
        )


async def _vip_edit_or_reply(message, text: str, reply_markup, protect_kwargs) -> None:
    """Обновить inline-панель VIP или отправить новое сообщение."""
    try:
        await message.edit_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            **protect_kwargs,
        )
    except Exception:
        await message.reply_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            **protect_kwargs,
        )


async def vip_menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    is_vip: Callable[[int], bool],
    protect_kwargs: Dict[str, Any],
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    user = query.from_user
    if not user or not is_vip(user.id):
        await query.message.reply_text(
            vip_content.no_access_html(),
            parse_mode="HTML",
        )
        return

    data = query.data or ""
    msg = query.message

    if data in ("vip:welcome", "vip:decks"):
        await _vip_edit_or_reply(
            msg,
            vip_content.vip_home_html(),
            vip_content.deck_menu_keyboard(),
            protect_kwargs,
        )
        return

    if data.startswith("vip:deck:"):
        deck_id = data.split(":", 2)[2]
        deck = vip_content.get_deck(deck_id)
        if not deck:
            return
        if deck_id == "kristally":
            await _vip_edit_or_reply(
                msg,
                deck.get("html") or "",
                vip_content.kristally_back_keyboard(),
                protect_kwargs,
            )
            return
        await _vip_edit_or_reply(
            msg,
            deck.get("menu_html") or deck.get("title") or "",
            vip_content.deck_sections_keyboard(deck_id),
            protect_kwargs,
        )
        return

    if data.startswith("vip:sec:"):
        _, _, rest = data.partition("vip:sec:")
        deck_id, _, section_id = rest.partition(":")
        html = vip_content.get_section_html(deck_id, section_id)
        if not html:
            return
        await send_vip_html_parts(
            msg,
            html,
            vip_content.section_back_keyboard(deck_id),
            protect_kwargs,
        )
        return

    if data.startswith("vip:pdf:ten:"):
        variant = data.rsplit(":", 1)[-1]
        if variant not in ("dark", "light"):
            return
        path = vip_content.pdf_local_path(variant)
        if not path.is_file():
            await msg.reply_text(
                ui.MSG_VIP_PDF_MISSING,
                parse_mode="HTML",
            )
            return

        captions = {
            "dark": "Книга «Тень души» темное оформление",
            "light": "Книга «Тень души» светлое оформление",
        }
        caption = captions[variant]
        cached_id = vip_content.get_pdf_file_id(variant)
        if cached_id:
            await msg.reply_document(
                document=cached_id,
                caption=caption,
                **protect_kwargs,
            )
            return

        status = await msg.reply_text(
            "📤 Первый раз загружаю книгу в Telegram (~40 МБ), подожди 1–2 мин…",
            parse_mode="HTML",
        )
        try:
            with path.open("rb") as fh:
                sent = await msg.reply_document(
                    document=InputFile(fh, filename=path.name),
                    caption=caption,
                    **protect_kwargs,
                )
        except Exception as exc:
            print(f"VIP PDF upload {variant}: {exc!r}")
            await msg.reply_text(
                "Не удалось отправить PDF, попробуй позже.",
                parse_mode="HTML",
            )
            try:
                await status.delete()
            except Exception:
                pass
            return

        doc = sent.document if sent else None
        if doc and doc.file_id:
            vip_content.save_pdf_file_id(variant, doc.file_id)
        try:
            await status.delete()
        except Exception:
            pass


async def vip_approve_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    admin_guard: Callable[[Update], Any],
    grant_vip: Callable[[int], Any],
    main_keyboard_for: Optional[Callable[[int], Any]] = None,
    audit_action: Optional[Callable[[str, int], Any]] = None,
) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    if not await admin_guard(update):
        return

    if query.data.startswith(ui.CB_VIP_APPROVE_PREFIX):
        try:
            target_id = int(query.data[len(ui.CB_VIP_APPROVE_PREFIX) :])
        except ValueError:
            return
        await grant_vip(target_id)
        if audit_action:
            await audit_action("vip_grant_from_alert", target_id)
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=ui.MSG_VIP_APPROVE_USER,
                parse_mode="HTML",
                reply_markup=_reply_keyboard(target_id, main_keyboard_for),
            )
        except Exception as exc:
            print(f"VIP approve notify {target_id}: {exc!r}")
        if query.message:
            await query.message.reply_text(
                f"✅ VIP выдан пользователю <code>{target_id}</code>",
                parse_mode="HTML",
            )
        return

    if query.data.startswith(ui.CB_VIP_REJECT_PREFIX):
        try:
            target_id = int(query.data[len(ui.CB_VIP_REJECT_PREFIX) :])
        except ValueError:
            return
        if audit_action:
            await audit_action("vip_reject_from_alert", target_id)
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=ui.MSG_VIP_REJECT_USER,
                parse_mode="HTML",
                reply_markup=_reply_keyboard(target_id, main_keyboard_for),
            )
        except Exception as exc:
            print(f"VIP reject notify {target_id}: {exc!r}")
        if query.message:
            await query.message.reply_text(
                f"❌ Запрос отклонён для <code>{target_id}</code>",
                parse_mode="HTML",
            )


async def admin_vip_summary(update: Update, admin_guard) -> None:
    message = update.effective_message
    if not message or not await admin_guard(update):
        return
    active, used = await vip_codes.counts()
    await message.reply_text(
        ui.ADMIN_VIP_SUMMARY.format(active=active, used=used),
        parse_mode="HTML",
        reply_markup=ui.get_admin_vip_keyboard(),
    )


async def admin_vip_export(update: Update, admin_guard) -> None:
    message = update.effective_message
    if not message or not await admin_guard(update):
        return
    payload = await vip_codes.export_csv_bytes()
    await message.reply_document(
        document=InputFile(io.BytesIO(payload), filename="vip_codes.csv"),
        caption="VIP-коды (активные и отработанные)",
        reply_markup=ui.get_admin_vip_keyboard(),
    )


async def admin_vip_add_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_guard) -> None:
    message = update.effective_message
    if not message or not await admin_guard(update):
        return
    context.user_data["admin_mode"] = "vip_add"
    await message.reply_text(
        ui.ADMIN_VIP_ADD_PROMPT,
        parse_mode="HTML",
        reply_markup=ui.get_admin_vip_prompt_keyboard(),
    )


async def admin_vip_import_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_guard) -> None:
    message = update.effective_message
    if not message or not await admin_guard(update):
        return
    context.user_data["admin_mode"] = "vip_import"
    await message.reply_text(
        ui.ADMIN_VIP_IMPORT_PROMPT,
        parse_mode="HTML",
        reply_markup=ui.get_admin_vip_prompt_keyboard(),
    )
