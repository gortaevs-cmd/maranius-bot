#!/usr/bin/env python3
"""Read the polling process's watchdog snapshot; no API requests or writes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.runtime_health import check_snapshot

if __name__ == "__main__":
    healthy, reason = check_snapshot()
    print("runtime_health=" + reason)
    raise SystemExit(0 if healthy else 1)
