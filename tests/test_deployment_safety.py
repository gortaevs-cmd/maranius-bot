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
