import argparse
import csv
import importlib.util
import stat
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("oura_sync", ROOT / "scripts" / "oura_sync.py")
oura_sync = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = oura_sync
SPEC.loader.exec_module(oura_sync)


class TokenTests(unittest.TestCase):
    def test_refresh_keeps_rotating_credentials_when_omitted(self):
        previous = {"refresh_token": "keep-me", "scope": "daily", "token_type": "bearer"}
        refreshed = {"access_token": "new-access", "expires_in": 3600}

        merged = oura_sync.merge_refreshed_tokens(previous, refreshed)

        self.assertEqual(merged["refresh_token"], "keep-me")
        self.assertEqual(merged["scope"], "daily")
        self.assertEqual(merged["access_token"], "new-access")

    def test_private_json_is_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.json"
            oura_sync.write_json(path, {"access_token": "secret"}, private=True)
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, stat.S_IRUSR | stat.S_IWUSR)


class CsvTests(unittest.TestCase):
    def test_incremental_write_preserves_old_rows_and_replaces_matching_day(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily_sleep.csv"
            oura_sync.write_csv(path, [{"day": "2026-09-01", "score": 70}])
            oura_sync.write_csv(
                path,
                [
                    {"day": "2026-09-01", "score": 82},
                    {"day": "2026-09-02", "score": 85},
                ],
                merge_existing=True,
            )

            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual([row["day"] for row in rows], ["2026-09-01", "2026-09-02"])
            self.assertEqual(rows[0]["score"], "82")

    def test_failed_endpoint_does_not_replace_existing_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_dir = root / "csv"
            raw_dir = root / "raw"
            csv_dir.mkdir()
            existing = csv_dir / "daily_sleep.csv"
            existing.write_text("day,score\n2026-09-01,77\n", encoding="utf-8")
            args = argparse.Namespace(
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 2),
                endpoints=["daily_sleep"],
                raw_dir=raw_dir,
                csv_dir=csv_dir,
            )
            failed = {
                "endpoint": "daily_sleep",
                "kind": "date",
                "description": "test",
                "fetched_at": "2026-09-02T00:00:00+00:00",
                "range": {"start_date": "2026-09-01", "end_date": "2026-09-02"},
                "pages": [],
                "errors": ["network error"],
                "data": [],
            }

            with mock.patch.object(oura_sync, "get_access_token", return_value="token"), mock.patch.object(
                oura_sync, "sync_endpoint", return_value=failed
            ):
                oura_sync.command_sync(args)

            self.assertEqual(existing.read_text(encoding="utf-8"), "day,score\n2026-09-01,77\n")


if __name__ == "__main__":
    unittest.main()
