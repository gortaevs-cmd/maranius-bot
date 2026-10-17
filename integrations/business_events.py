"""JSON business operations and their durable notification intent.

Callbacks below perform no network I/O. The caller holds the users lock when
users.json is involved; atomic_json serializes every participating disk store.
"""
from integrations import admin_alerts, inbox, legacy_contacts, user_registry, vip_codes
from integrations.json_storage import atomic_json, run_storage_io


def profile_visit(user, *, load_users, save_users, seed_admin_ids, force=False, notify=False):
    def commit():
        users = load_users()
        if not force and not user_registry.has_current_access(users, user.id):
            return False, False
        uid = str(user.id)
        previous = users.get(uid)
        is_new = not isinstance(previous, dict) or not previous.get('first_seen')
        user_registry.merge_telegram_profile(
            users, user_id=user.id, username=user.username,
            first_name=user.first_name or '', last_name=user.last_name or '',
            language_code=user.language_code or '', is_premium=getattr(user, 'is_premium', False),
            seed_admin_ids=seed_admin_ids,
        )
        if user.id in seed_admin_ids:
            users[uid]['vip'] = True
        save_users(users)
        returned = legacy_contacts._claim_returning_inactive_user_sync(user.id) if notify else None
        if returned:
            admin_alerts._notify_legacy_contact_return_sync(
                seed_admin_ids, user_id=user.id, username=user.username,
                first_name=user.first_name or '', source=str(returned.get('source') or 'legacy import'),
            )
        elif is_new and notify:
            admin_alerts._notify_new_subscriber_sync(
                seed_admin_ids, user_id=user.id, username=user.username, first_name=user.first_name or '',
            )
        return True, is_new
    return atomic_json(commit)


async def add_inbox_entry(seed_admin_ids, *, entry_type, user_id, username, text, meta=None):
    notify = await admin_alerts._can_notify_inbox(user_id)
    def commit():
        entry = inbox._add_entry_sync(
            entry_type=entry_type, user_id=user_id, username=username, text=text, meta=meta,
        )
        if notify:
            admin_alerts._notify_inbox_entry_sync(
                seed_admin_ids, entry_type=entry_type, user_id=user_id, username=username,
                text=text, entry_id=entry['id'],
            )
        return entry
    return await run_storage_io(atomic_json, commit)


def activate_vip_code(raw_code, user, *, load_users, save_users, seed_admin_ids):
    def commit():
        users = load_users()
        if user_registry.is_vip(users, user.id, seed_admin_ids=seed_admin_ids):
            return False, 'already_vip'
        ok, reason = vip_codes._redeem_code_sync(raw_code, user_id=user.id, username=user.username)
        if ok:
            user_registry.grant_vip(users, user.id, source='code')
            save_users(users)
            admin_alerts._notify_vip_code_redeemed_sync(
                seed_admin_ids, user_id=user.id, username=user.username,
                first_name=user.first_name or '', code=raw_code,
            )
        return ok, reason
    return atomic_json(commit)
