import unittest

import ui
from integrations import vip_content


class BotCommandsTests(unittest.TestCase):
    def test_contact_replaces_services_in_telegram_menu(self):
        commands = {item.command: item.description for item in ui.get_bot_commands()}
        self.assertEqual(commands["contact"], "Связь с автором")
        self.assertEqual(commands["learning"], "Курсы/Практики")
        self.assertNotIn("services", commands)

    def test_store_button_uses_telegram_utm_tags(self):
        button = ui.get_store_inline_keyboard().inline_keyboard[0][0]
        self.assertEqual(button.text, "🛍 Перейти в Лавку")
        self.assertEqual(
            button.url,
            "https://maranius.ru/?utm_source=telegram&utm_medium=bot&utm_campaign=lavka",
        )
        self.assertIn(ui.URL_CATALOG, ui.MSG_STORE_STUB)

    def test_more_menu_includes_social_networks_placeholder(self):
        buttons = [
            button
            for row in ui.get_more_inline_keyboard().inline_keyboard
            for button in row
        ]
        social = next(button for button in buttons if button.callback_data == ui.CB_SOCIAL_NETWORKS)
        self.assertEqual(social.text, "📱 Социальные сети")
        self.assertIn("Социальные сети", ui.MSG_SOCIAL_NETWORKS_STUB)

    def test_every_inline_button_uses_a_registered_callback_family(self):
        keyboards = [
            ui.get_policy_gate_keyboard(), ui.get_marketing_offer_keyboard(),
            ui.get_policy_keyboard(), ui.get_marketing_unsubscribe_keyboard(),
            ui.get_admin_home_keyboard(), ui.get_admin_inbox_keyboard(),
            ui.get_admin_lists_keyboard(), ui.get_admin_users_manage_keyboard(),
            ui.get_admin_confirm_keyboard(), ui.get_admin_batch_confirm_keyboard(),
            ui.get_admin_user_card_keyboard(), ui.get_admin_bot_keyboard(),
            ui.get_admin_audit_keyboard(),
            ui.get_admin_vip_keyboard(), ui.get_today_inline_keyboard(),
            ui.get_card_hub_keyboard(), ui.get_card_hub_back_keyboard(),
            ui.get_more_inline_keyboard(), vip_content.deck_menu_keyboard(),
            ui.get_profile_menu_keyboard(), ui.get_profile_subs_keyboard(marketing_opt_in=False),
            ui.get_profile_status_back_keyboard(),
        ]
        callbacks = {
            button.callback_data
            for keyboard in keyboards
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        }
        allowed = ("consent:", "admin:", "today:", "more:", "vip:")
        self.assertTrue(callbacks)
        self.assertEqual([value for value in callbacks if not value.startswith(allowed)], [])


if __name__ == "__main__":
    unittest.main()
