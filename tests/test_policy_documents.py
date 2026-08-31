import unittest

import ui


class PolicyDocumentUiTests(unittest.TestCase):
    def test_all_document_urls_use_official_maranius_pages(self):
        self.assertEqual(
            ui.URL_PRIVACY_POLICY,
            "https://maranius.ru/legal/privacy-policy/",
        )
        self.assertEqual(
            ui.URL_PERSONAL_DATA_CONSENT,
            "https://maranius.ru/legal/personal-data-consent/",
        )
        self.assertEqual(
            ui.URL_MARKETING_CONSENT,
            "https://maranius.ru/legal/marketing-consent/",
        )
        self.assertEqual(
            ui.URL_USER_AGREEMENT,
            "https://maranius.ru/legal/user-agreement/",
        )

    def test_gate_has_document_buttons_and_two_independent_required_actions(self):
        buttons = [
            button
            for row in ui.get_policy_gate_keyboard().inline_keyboard
            for button in row
        ]
        urls = {button.url for button in buttons if button.url}
        self.assertEqual(
            urls,
            {
                ui.URL_PRIVACY_POLICY,
                ui.URL_PERSONAL_DATA_CONSENT,
                ui.URL_USER_AGREEMENT,
            },
        )
        callbacks = {button.callback_data for button in buttons if button.callback_data}
        self.assertTrue(
            {ui.CB_USER_AGREEMENT_ACCEPT, ui.CB_POLICY_ACCEPT} <= callbacks
        )

    def test_gate_hides_already_completed_action(self):
        buttons = [
            button
            for row in ui.get_policy_gate_keyboard(
                user_agreement_accepted=True,
                personal_data_consent_accepted=False,
            ).inline_keyboard
            for button in row
        ]
        callbacks = {button.callback_data for button in buttons if button.callback_data}
        self.assertNotIn(ui.CB_USER_AGREEMENT_ACCEPT, callbacks)
        self.assertIn(ui.CB_POLICY_ACCEPT, callbacks)

    def test_marketing_offer_has_all_required_document_buttons_before_opt_in(self):
        buttons = [
            button
            for row in ui.get_marketing_offer_keyboard().inline_keyboard
            for button in row
        ]
        urls = {button.url for button in buttons if button.url}
        self.assertEqual(
            urls,
            {
                ui.URL_MARKETING_CONSENT,
                ui.URL_PRIVACY_POLICY,
                ui.URL_USER_AGREEMENT,
            },
        )
        callbacks = {button.callback_data for button in buttons if button.callback_data}
        self.assertTrue({ui.CB_MARKETING_YES, ui.CB_MARKETING_NO} <= callbacks)


if __name__ == "__main__":
    unittest.main()
