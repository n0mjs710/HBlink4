"""
User database filter acceptance and forced refresh.

A country is never too small to load: the only filter result refused is an
empty one, which means a misspelled country rather than a small one. Deleting
the two files on disk is the documented way to force a download, so a restart
with neither file present must refresh and must not send If-Modified-Since.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard.user_db import UserDatabase, filter_rows_from_csv_bytes


_CSV = (
    b"RADIO_ID,CALLSIGN,FIRST_NAME,LAST_NAME,CITY,STATE,COUNTRY\n"
    b"3100001,W1AAA,A,,City,State,United States\n"
    b"5300001,ZL1AAA,B,,City,State,New Zealand\n"
)
_LAST_MODIFIED = "Tue, 03 Nov 2026 05:01:55 GMT"


def _config(countries):
    return {"filter": {"countries": countries}}


class TestFilterAcceptance(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        self.db = UserDatabase(self.data_dir)

    def _refresh(self, countries, status=200):
        with mock.patch(
            "dashboard.user_db._http_get_with_conditional",
            return_value=(status, _CSV, _LAST_MODIFIED),
        ) as http_get:
            result = self.db._refresh_sync(_config(countries))
        return result, http_get

    def test_single_row_country_is_loaded(self):
        result, _ = self._refresh(["New Zealand"])
        self.assertEqual(result, "ok")
        self.assertEqual(self.db.get(5300001), "ZL1AAA")
        self.assertEqual(self.db.get(3100001), "")

    def test_no_match_keeps_old_snapshot(self):
        self._refresh(["New Zealand"])
        result, _ = self._refresh(["New Zeland"])
        self.assertEqual(result, "error")
        self.assertEqual(self.db.get(5300001), "ZL1AAA")
        self.assertIn("no rows", self.db.meta.last_error)

    def test_all_loads_every_country(self):
        self.assertEqual(
            set(filter_rows_from_csv_bytes(_CSV, {"countries": "all"})),
            {3100001, 5300001},
        )

    def test_deleting_both_files_forces_full_download(self):
        self._refresh(["United States"])

        for name in ("user_db.json", "user_db.meta.json"):
            (self.data_dir / name).unlink()

        restarted = UserDatabase(self.data_dir)
        restarted.load_from_disk()
        self.assertIsNone(restarted.snapshot_age_hours())

        self.db = restarted
        result, http_get = self._refresh(["New Zealand"])
        self.assertEqual(result, "ok")
        self.assertIsNone(http_get.call_args.kwargs["if_modified_since"])
        self.assertEqual(restarted.get(5300001), "ZL1AAA")

    def test_keeping_meta_file_blocks_startup_refresh(self):
        self._refresh(["United States"])
        (self.data_dir / "user_db.json").unlink()

        restarted = UserDatabase(self.data_dir)
        restarted.load_from_disk()
        self.assertIsNotNone(restarted.snapshot_age_hours())


if __name__ == "__main__":
    unittest.main()
