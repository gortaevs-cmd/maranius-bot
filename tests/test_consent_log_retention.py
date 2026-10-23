"""Consent log retention: history is kept while the user is active and for 3 years after."""
import unittest
from datetime import datetime, timedelta, timezone

from integrations import consent_log


def _entry(user_id, days_ago, event="policy_accepted"):
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": f"{user_id}-{days_ago}",
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user_id": user_id,
        "event": event,
    }


class ConsentLogRetentionTests(unittest.TestCase):
    def test_retention_is_three_years(self):
        self.assertEqual(consent_log.RETENTION_DAYS, 3 * 365)

    def test_old_record_survives_while_user_is_active(self):
        entries = [_entry(1, 1000), _entry(1, 10, "marketing_opt_in")]
        self.assertEqual([e["id"] for e in consent_log._purge(entries)], ["1-1000", "1-10"])

    def test_year_old_record_is_no_longer_dropped(self):
        entries = [_entry(2, 400)]
        self.assertEqual(len(consent_log._purge(entries)), 1)

    def test_user_is_purged_three_years_after_last_record(self):
        entries = [_entry(3, 1500), _entry(3, 1200, "marketing_opt_out"), _entry(4, 5)]
        kept = consent_log._purge(entries)
        self.assertEqual([e["user_id"] for e in kept], [4])

    def test_record_just_inside_the_window_is_kept(self):
        entries = [_entry(5, 1090)]
        self.assertEqual(len(consent_log._purge(entries)), 1)

    def test_invalid_entries_are_dropped(self):
        entries = ["junk", {"user_id": 6, "created_at": "not-a-date"}, _entry(6, 1)]
        self.assertEqual([e["id"] for e in consent_log._purge(entries)], ["6-1"])

    def test_event_cap_keeps_newest(self):
        original = consent_log.MAX_EVENTS
        consent_log.MAX_EVENTS = 2
        try:
            entries = [_entry(7, 3), _entry(7, 2), _entry(7, 1)]
            self.assertEqual([e["id"] for e in consent_log._purge(entries)], ["7-2", "7-1"])
        finally:
            consent_log.MAX_EVENTS = original


if __name__ == "__main__":
    unittest.main()
