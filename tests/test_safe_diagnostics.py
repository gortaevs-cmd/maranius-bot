import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from integrations.diagnostics import log_error


SECRET = "synthetic-secret-never-log"


class SafeDiagnosticTests(unittest.TestCase):
    def test_exception_message_and_locals_are_excluded_but_location_is_kept(self):
        try:
            token = SECRET
            raise RuntimeError("private input: " + token)
        except RuntimeError as error:
            with self.assertLogs("integrations.diagnostics", level="ERROR") as captured:
                log_error("test_failure", error, update_id=123)
        text = "\n".join(captured.output)
        self.assertIn("RuntimeError", text)
        self.assertIn("test_safe_diagnostics.py:", text)
        self.assertIn("update_id=123", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("private input", text)

    def test_exception_stringification_is_never_called(self):
        class UnsafeError(Exception):
            def __str__(self):
                raise AssertionError("Must not stringify request exception")
        with self.assertLogs("integrations.diagnostics", level="ERROR") as captured:
            log_error("test_failure", UnsafeError())
        self.assertIn("UnsafeError", captured.output[0])


class HandlerDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_handler_does_not_log_update_payload_or_send_error_text(self):
        update = SimpleNamespace(
            update_id=123, effective_message=SimpleNamespace(reply_text=AsyncMock()), private_input=SECRET,
        )
        context = SimpleNamespace(error=RuntimeError(SECRET), bot=SimpleNamespace(send_message=AsyncMock()))
        stdout = io.StringIO()
        with self.assertLogs("integrations.diagnostics", level="ERROR") as captured, redirect_stdout(stdout):
            await bot._on_error(update, context)
        self.assertNotIn(SECRET, "\n".join(captured.output) + stdout.getvalue())
        for call in context.bot.send_message.await_args_list:
            self.assertNotIn(SECRET, str(call))
        update.effective_message.reply_text.assert_awaited_once()


class ScriptDiagnosticTests(unittest.TestCase):
    def load_script(self, name):
        path = Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_smoke_exception_does_not_expose_token_url(self):
        module = self.load_script("smoke_post_deploy")
        output = io.StringIO()
        with patch.object(module, "_resolve_token", return_value=SECRET), \
             patch.object(module.httpx, "get", side_effect=RuntimeError("https://api.telegram.org/bot" + SECRET)), \
             redirect_stdout(output), redirect_stderr(output), \
             self.assertLogs("integrations.diagnostics", level="ERROR") as captured:
            self.assertEqual(module.main(), 1)
        self.assertNotIn(SECRET, output.getvalue() + "\n".join(captured.output))

    def test_curl_exception_does_not_expose_command_with_token(self):
        module = self.load_script("warm_pdf_file_ids")
        error = subprocess.CalledProcessError(1, ["curl", "https://api.telegram.org/bot" + SECRET])
        with patch.object(module.subprocess, "check_output", side_effect=error), \
             self.assertLogs("integrations.diagnostics", level="ERROR") as captured:
            self.assertIsNone(module._upload_via_curl(SECRET, 42, Path("book.pdf"), "test", ""))
        self.assertNotIn(SECRET, "\n".join(captured.output))

    def test_smoke_rejection_does_not_print_response_body(self):
        module = self.load_script("smoke_post_deploy")
        response = SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"ok": False, "description": SECRET})
        output = io.StringIO()
        with patch.object(module, "_resolve_token", return_value=SECRET), \
             patch.object(module.httpx, "get", return_value=response), redirect_stderr(output):
            self.assertEqual(module.main(), 1)
        self.assertNotIn(SECRET, output.getvalue())

    def test_curl_rejection_does_not_print_response_body(self):
        module = self.load_script("warm_pdf_file_ids")
        output = io.StringIO()
        with patch.object(module.subprocess, "check_output", return_value='{"ok":false,"description":"' + SECRET + '"}'), \
             redirect_stdout(output):
            self.assertIsNone(module._upload_via_curl(SECRET, 42, Path("book.pdf"), "test", ""))
        self.assertNotIn(SECRET, output.getvalue())
