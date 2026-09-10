import importlib.util
import base64
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("oura_app", ROOT / "scripts" / "oura_app.py")
oura_app = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(oura_app)


def sample_profile():
    return {
        "timezone": "UTC",
        "schedule": {
            "startDate": "2025-01-01",
            "phases": [
                {
                    "label": "Start",
                    "windDown": "22:30",
                    "bed": "23:00",
                    "lightsOut": "23:15",
                    "naturalWakeWindow": "07:00-07:15",
                    "wake": "07:15",
                    "days": 3,
                },
                {
                    "label": "Stable",
                    "windDown": "22:15",
                    "bed": "22:45",
                    "lightsOut": "23:00",
                    "naturalWakeWindow": "06:45-07:00",
                    "wake": "07:00",
                    "days": None,
                },
            ],
        },
        "targets": {"sleepHours": 8, "readiness": 80, "sleepScore": 80, "hrv": None},
        "dailyItems": [],
    }


class DailyItemTests(unittest.TestCase):
    def test_items_are_created_toggled_and_deleted_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(oura_app, "PROFILE_PATH", root / "user.json"), mock.patch.object(
                oura_app, "PROFILE_EXAMPLE_PATH", root / "example.json"
            ), mock.patch.object(oura_app, "DAILY_ITEM_LOG_PATH", root / "daily.json"):
                oura_app.write_json_atomic(root / "example.json", sample_profile())
                item = oura_app.add_profile_item(
                    {"name": "Example item", "category": "supplement", "time": "12:30", "note": "With food"}
                )
                state = oura_app.save_daily_item(
                    {"date": "2025-01-02", "id": item["id"], "taken": True}
                )

                self.assertTrue(state["items"][0]["taken"])
                self.assertTrue(oura_app.delete_profile_item(item["id"]))
                self.assertEqual(oura_app.profile_items(), [])

    def test_invalid_item_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "项目类型无效"):
            oura_app.add_profile_item({"name": "Example", "category": "invalid"})


class ScheduleTests(unittest.TestCase):
    def test_schedule_uses_configured_phase_lengths(self):
        profile = sample_profile()
        self.assertEqual(oura_app.tonight_sleep_plan(profile, date(2025, 1, 1))["phaseIndex"], 0)
        self.assertEqual(oura_app.tonight_sleep_plan(profile, date(2025, 1, 4))["phaseIndex"], 1)

    def test_calendar_uses_profile_timezone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = sample_profile()
            profile["timezone"] = "Asia/Shanghai"
            with mock.patch.object(oura_app, "PROFILE_PATH", root / "user.json"), mock.patch.object(
                oura_app, "PROFILE_EXAMPLE_PATH", root / "example.json"
            ):
                oura_app.write_json_atomic(root / "example.json", profile)
                self.assertIn("TZID=Asia/Shanghai", oura_app.calendar_ics())


class SyncTests(unittest.TestCase):
    def test_incremental_sync_only_requests_recent_configured_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "sync.json"
            profile = sample_profile()
            profile["sync"] = {
                "enabled": True,
                "intervalMinutes": 60,
                "lookbackDays": 3,
                "endpoints": ["daily_sleep", "sleep"],
            }
            completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
            with mock.patch.object(oura_app, "SYNC_STATE_PATH", state_path), mock.patch.object(
                oura_app, "SYNC_PROCESS_LOCK_PATH", Path(directory) / ".sync.lock"
            ), mock.patch.object(oura_app, "load_profile", return_value=profile
            ), mock.patch.object(oura_app.subprocess, "run", return_value=completed) as run:
                oura_app.run_incremental_sync("test")

            command = run.call_args.args[0]
            self.assertEqual(command[-4:], ["--endpoints", "daily_sleep", "sleep", "--skip-raw"])
            self.assertEqual(oura_app.read_json(state_path, {})["status"], "success")

    def test_basic_auth_uses_constant_credentials(self):
        token = base64.b64encode(b"oura:secret-value").decode("ascii")
        self.assertTrue(oura_app.valid_basic_authorization(f"Basic {token}", "oura", "secret-value"))
        self.assertFalse(oura_app.valid_basic_authorization(f"Basic {token}", "oura", "wrong"))
        self.assertTrue(oura_app.valid_basic_authorization("", "oura", ""))


class AIPlanTests(unittest.TestCase):
    def test_ai_plan_cannot_move_configured_wake_time(self):
        base = {
            "label": "Stable",
            "windDown": "22:15",
            "bed": "22:45",
            "lightsOut": "23:00",
            "wake": "07:00",
            "instruction": "Base",
            "phaseIndex": 1,
        }
        generated = {
            "status": {"key": "amber", "label": "ADJUST", "title": "Reduce load", "reason": "Short sleep"},
            "sleepPlan": {"windDown": "21:55", "bed": "22:30", "lightsOut": "22:45", "wake": "09:30", "instruction": "Earlier night"},
            "guidance": {"work": "One focus block", "exercise": "Walk", "evening": "Stop early"},
            "timeline": [
                {"time": "09:00", "title": "Start", "detail": "Plan one task"},
                {"time": "12:00", "title": "Lunch", "detail": "Eat and walk"},
                {"time": "18:00", "title": "Movement", "detail": "Easy walk"},
            ],
        }
        checked = oura_app.validate_ai_plan(generated, base)
        self.assertEqual(checked["sleepPlan"]["wake"], "07:00")
        self.assertEqual(checked["sleepPlan"]["lightsOut"], "22:45")

    def test_response_output_text_finds_message_content(self):
        payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"ok\":true}"}]}]}
        self.assertEqual(oura_app.response_output_text(payload), '{"ok":true}')

    def test_ai_context_excludes_medication_names(self):
        dashboard = {
            "latest": {"readiness": 75},
            "current14": {},
            "previous14": {},
            "status": {"key": "amber"},
            "sleepPlan": {"wake": "07:00"},
        }
        profile = sample_profile()
        profile["schedule"]["workday"] = [["08:00", "Private medicine", "Take Private medicine"]]
        private_items = [{"id": "med", "name": "Private medicine", "category": "medication", "time": "morning"}]
        with mock.patch.object(oura_app, "profile_items", return_value=private_items):
            context = oura_app.ai_plan_context(dashboard, profile)

        serialized = str(context)
        self.assertNotIn("Private medicine", serialized)
        self.assertIn("固定用药", serialized)
        self.assertIn("morning", serialized)


if __name__ == "__main__":
    unittest.main()
