import unittest

import ui
from handlers import profile as profile_handlers
from integrations import user_registry


class ProfileKeyboardTests(unittest.TestCase):
    def test_main_keyboard_shows_marketing_subscribe_when_opted_out(self):
        kb = ui.get_main_keyboard(show_marketing_subscribe=True)
        labels = [btn.text for row in kb.keyboard for btn in row]
        self.assertIn(ui.BTN_MARKETING_ON, labels)
        self.assertEqual(len(kb.keyboard), 3)

    def test_main_keyboard_hides_marketing_when_subscribed(self):
        kb = ui.get_main_keyboard(show_marketing_subscribe=False)
        labels = [btn.text for row in kb.keyboard for btn in row]
        self.assertNotIn(ui.BTN_MARKETING_ON, labels)
        self.assertEqual(len(kb.keyboard), 2)

    def test_show_marketing_button_for_opted_out_user_with_policy(self):
        users = {
            "42": {
                "id": 42,
                "policy_accepted_at": "2026-08-15T00:00:00Z",
                "policy_version": user_registry.PERSONAL_DATA_CONSENT_VERSION,
                "user_agreement_accepted_at": "2026-08-15T00:00:00Z",
                "user_agreement_version": user_registry.USER_AGREEMENT_VERSION,
                "marketing_opt_in": False,
            }
        }
        self.assertTrue(
            user_registry.show_marketing_subscribe_in_main_menu(
                users, 42, seed_admin_ids={1}
            )
        )

    def test_hide_marketing_button_when_subscribed(self):
        users = {
            "42": {
                "id": 42,
                "policy_accepted_at": "2026-08-15T00:00:00Z",
                "policy_version": user_registry.PERSONAL_DATA_CONSENT_VERSION,
                "user_agreement_accepted_at": "2026-08-15T00:00:00Z",
                "user_agreement_version": user_registry.USER_AGREEMENT_VERSION,
                "marketing_opt_in": True,
                "marketing_consent_version": user_registry.MARKETING_CONSENT_VERSION,
            }
        }
        self.assertFalse(
            user_registry.show_marketing_subscribe_in_main_menu(
                users, 42, seed_admin_ids={1}
            )
        )


class ProfileStatusTests(unittest.TestCase):
    def test_status_html_in_russian(self):
        row = {
            "vip": True,
            "vip_granted_at": "2026-08-01T12:00:00Z",
            "vip_source": "code",
            "marketing_opt_in": True,
            "marketing_opt_in_at": "2026-08-02T12:00:00Z",
            "marketing_consent_version": user_registry.MARKETING_CONSENT_VERSION,
            "policy_accepted_at": "2026-08-01T10:00:00Z",
            "policy_version": user_registry.PERSONAL_DATA_CONSENT_VERSION,
            "user_agreement_accepted_at": "2026-08-01T10:00:00Z",
            "user_agreement_version": user_registry.USER_AGREEMENT_VERSION,
        }
        text = profile_handlers.format_status_html(
            row,
            is_vip=True,
            courses=[{"course_name": "Курс ангелов", "status": "active", "enrolled_at": "2026-08-03T00:00:00Z"}],
        )
        self.assertIn("VIP-доступ", text)
        self.assertIn("активирован VIP-код", text)
        self.assertIn("подписаны", text)
        self.assertIn("Пользовательское соглашение", text)
        self.assertIn("Курс ангелов", text)

    def test_profile_subs_keyboard_toggle(self):
        off_kb = ui.get_profile_subs_keyboard(marketing_opt_in=False)
        on_kb = ui.get_profile_subs_keyboard(marketing_opt_in=True)
        off_labels = [btn.text for row in off_kb.inline_keyboard for btn in row]
        on_labels = [btn.text for row in on_kb.inline_keyboard for btn in row]
        self.assertIn(ui.BTN_MARKETING_ON, off_labels)
        self.assertIn(ui.BTN_MARKETING_OFF, on_labels)


if __name__ == "__main__":
    unittest.main()
