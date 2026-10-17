"""Exercise backup and rollback functions against temporary files only."""

from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


DEPLOY_SCRIPT = Path(__file__).resolve().parents[1] / "infra" / "deploy-maranius-bot.sh"


class DeploymentSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / "app"
        self.app.mkdir()
        (self.app / "data").mkdir()
        (self.app / "data" / "inbox.json").write_text('{"entries":[]}')
        self.archive = self.root / "backup.tar.gz"

    def write_users(self, *, runtime=True):
        folder = self.app / ".runtime" if runtime else self.app
        folder.mkdir(exist_ok=True)
        for name in ("users.json", "admins.json"):
            (folder / name).write_text('{"preserved":true}')
        return folder

    def backup(self, prelude=":"):
        return subprocess.run(
            ["bash", "-c", 'source "$1"; ' + prelude + '; backup_runtime "$2" "$3"',
             "backup-test", str(DEPLOY_SCRIPT), str(self.app), str(self.archive)],
            text=True, capture_output=True,
        )

    def assert_failed_without_archive(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob("*.partial.*")), [])

    def test_runtime_is_preserved_without_optional_legacy_files(self):
        runtime = self.write_users()
        (runtime / "consent_log.json").write_text('{"events":[]}')
        result = self.backup(prelude=":")
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(self.archive) as archive:
            names = archive.getnames()
            for name in (".runtime/users.json", ".runtime/admins.json", ".runtime/consent_log.json", "data/inbox.json"):
                self.assertIn(name, names)
            self.assertEqual(archive.extractfile(".runtime/users.json").read(), b'{"preserved":true}')
        self.assertEqual(self.archive.stat().st_mode & 0o077, 0)

    def test_first_deploy_backs_up_legacy_state(self):
        self.write_users(runtime=False)
        result = self.backup(prelude=":")
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(self.archive) as archive:
            self.assertIn("users.json", archive.getnames())
            self.assertIn("admins.json", archive.getnames())

    def test_missing_runtime_users_does_not_fall_back_to_stale_root_files(self):
        self.write_users(runtime=False)
        (self.app / ".runtime").mkdir()
        self.assert_failed_without_archive(self.backup(prelude=":"))

    def test_missing_data_aborts_backup(self):
        self.write_users()
        (self.app / "data" / "inbox.json").unlink()
        (self.app / "data").rmdir()
        self.assert_failed_without_archive(self.backup(prelude=":"))

    def test_archiver_failure_does_not_accept_nonempty_partial_file(self):
        self.write_users()
        result = self.backup(prelude='tar() { printf broken > "$2"; return 1; }')
        self.assert_failed_without_archive(result)

    def test_corrupt_archive_is_rejected_even_if_archiver_returns_success(self):
        self.write_users()
        result = self.backup(prelude='tar() { if [[ "$1" == -czf ]]; then printf broken > "$2"; else command tar "$@"; fi; }')
        self.assert_failed_without_archive(result)

    def test_readable_archive_missing_required_users_is_rejected(self):
        self.write_users()
        result = self.backup(prelude='tar() { if [[ "$1" == -czf ]]; then command tar -czf "$2" -C "$4" data; else command tar "$@"; fi; }')
        self.assert_failed_without_archive(result)

    def test_failed_backup_keeps_previous_verified_archive(self):
        self.write_users()
        self.archive.write_bytes(b"previous-backup")
        result = self.backup(prelude='tar() { return 1; }')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.archive.read_bytes(), b"previous-backup")

    def test_failure_before_replacement_does_not_restart_container(self):
        marker = self.root / "docker-called"
        result = subprocess.run([
            "bash", "-c",
            'source "$1"; marker=$2; docker() { touch "$marker"; }; PREVIOUS_IMAGE=old; '
            'DEPLOY_STARTED=0; trap rollback ERR; false',
            "rollback-test", str(DEPLOY_SCRIPT), str(marker),
        ], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())

    def test_failure_after_replacement_restores_previous_image(self):
        marker = self.root / "docker-called"
        result = subprocess.run([
            "bash", "-c",
            'source "$1"; marker=$2; docker() { printf "%s\\n" "$*" >> "$marker"; }; '
            'PREVIOUS_IMAGE="$IMAGE_REPOSITORY:previous"; IMAGE="$IMAGE_REPOSITORY:new"; '
            'DEPLOY_STARTED=1; trap rollback ERR; false',
            "rollback-test", str(DEPLOY_SCRIPT), str(marker),
        ], cwd=self.root, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compose --env-file .rollback.env", marker.read_text())
        self.assertEqual((self.root / ".rollback.env").read_text(), "MARANIUS_IMAGE_TAG=previous\n")

    def test_consistent_backup_pauses_and_resumes_running_container(self):
        self.write_users()
        calls = self.root / 'docker-calls'
        result = subprocess.run([
            'bash', '-c',
            'source "$1"; calls=$4; docker() { printf "%s\\n" "$1" >> "$calls"; '
            'if [[ "$1" == inspect ]]; then printf "true false\\n"; fi; }; '
            'backup_consistent "$2" "$3"; test "$PAUSED_BY_DEPLOY" == 0',
            'snapshot-test', str(DEPLOY_SCRIPT), str(self.app), str(self.archive), str(calls),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.read_text().splitlines(), ['inspect','pause','unpause'])
        self.assertTrue(self.archive.exists())

    def test_failed_consistent_backup_resumes_without_restart(self):
        self.write_users()
        calls = self.root / 'docker-calls'
        result = subprocess.run([
            'bash', '-c',
            'source "$1"; calls=$4; docker() { printf "%s\\n" "$1" >> "$calls"; '
            'if [[ "$1" == inspect ]]; then printf "true false\\n"; fi; }; '
            'tar() { return 1; }; backup_consistent "$2" "$3"',
            'snapshot-test', str(DEPLOY_SCRIPT), str(self.app), str(self.archive), str(calls),
        ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls.read_text().splitlines(), ['inspect','pause','unpause'])
        self.assertFalse(self.archive.exists())

    def test_already_paused_container_is_preserved(self):
        self.write_users()
        calls = self.root / 'docker-calls'
        result = subprocess.run([
            'bash', '-c',
            'source "$1"; calls=$4; docker() { printf "%s\\n" "$1" >> "$calls"; '
            'printf "true true\\n"; }; backup_consistent "$2" "$3"',
            'snapshot-test', str(DEPLOY_SCRIPT), str(self.app), str(self.archive), str(calls),
        ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls.read_text().splitlines(), ['inspect'])

    def test_pending_journal_rejects_old_image_but_allows_compatible_image(self):
        runtime = self.write_users()
        (runtime / 'storage_transaction.json').write_text('{"pending":true}')
        for capability, expected_success in (('1', True), ('<no value>', False)):
            with self.subTest(capability=capability):
                result = subprocess.run([
                    'bash', '-c',
                    'source "$1"; expected_capability=$3; docker() { printf "%s\\n" "$expected_capability"; }; '
                    'can_restore_image "$2" example:previous',
                    'rollback-test', str(DEPLOY_SCRIPT), str(runtime), capability,
                ], capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, expected_success, result.stderr)

    def test_snapshot_must_include_existing_transaction_decision(self):
        runtime = self.write_users()
        (runtime / 'storage_transaction.json').write_text('{"pending":true}')
        result = self.backup(prelude='tar() { if [[ "$1" == -czf ]]; then command tar -czf "$2" -C "$4" data .runtime/users.json .runtime/admins.json; else command tar "$@"; fi; }')
        self.assert_failed_without_archive(result)

    def test_rollback_stops_writer_before_rejecting_incompatible_pending_journal(self):
        import shlex
        runtime = self.write_users()
        (runtime / 'storage_transaction.json').write_text('{"pending":true}')
        helper = self.root / 'helper.sh'
        helper.write_text(DEPLOY_SCRIPT.read_text().replace(
            'readonly APP_DIR="/opt/apps/maranius"', 'readonly APP_DIR='+shlex.quote(str(self.app)),
        ))
        calls = self.root / 'docker-calls'
        result = subprocess.run([
            'bash', '-c',
            'source "$1"; calls=$2; docker() { printf "%s\\n" "$*" >> "$calls"; '
            'if [[ "$1" == inspect ]]; then printf "true\\n"; '
            'elif [[ "$1" == image && "${3:-}" == --format ]]; then printf "<no value>\\n"; fi; }; '
            'PREVIOUS_IMAGE="$IMAGE_REPOSITORY:old"; DEPLOY_STARTED=1; trap rollback ERR; false',
            'rollback-test', str(helper), str(calls),
        ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        lines = calls.read_text().splitlines()
        self.assertTrue(any(line.startswith('stop -t 60') for line in lines))
        self.assertFalse(any(line.startswith('compose') for line in lines))
        self.assertIn('rollback blocked', result.stderr)
