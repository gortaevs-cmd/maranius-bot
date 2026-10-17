#!/usr/bin/env python3
"""Run unit tests from a temporary source copy, without secrets or live data."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")
    sources = set(filter(None, tracked))
    sources.update(name for name in untracked if name.endswith(".py"))
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("BOT_TOKEN", "MARANIUS_")) or key in {"BOT_PROFILE", "TELEGRAM_PROXY_URL", "PYTHONPATH"}:
            env.pop(key, None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="maranius-tests-") as temp:
        target = Path(temp)
        for name in sorted(sources):
            path = Path(name)
            if ".runtime" in path.parts or path.name == ".env":
                continue
            src = ROOT / path
            if src.is_file() and not src.is_symlink():
                dest = target / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        # Tests must mock network integrations. Catch accidental real requests.
        code = """
import socket
import unittest
def deny_network(*args, **kwargs):
    raise AssertionError('Network access is disabled in unit tests')
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network
suite = unittest.defaultTestLoader.discover('tests')
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
"""
        return subprocess.run([sys.executable, "-B", "-c", code], cwd=target, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
