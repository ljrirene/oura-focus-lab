#!/usr/bin/env python3
"""Run a local dashboard for the personalized Oura cognition experiment."""

from __future__ import annotations

import argparse
import base64
import csv
import hmac
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"
CSV_DIR = PROJECT_ROOT / "data" / "csv"
PROFILE_PATH = PROJECT_ROOT / "config" / "user.json"
PROFILE_EXAMPLE_PATH = PROJECT_ROOT / "config" / "profile.example.json"
DAILY_ITEM_LOG_PATH = PROJECT_ROOT / "data" / "daily_item_log.json"
SYNC_STATE_PATH = PROJECT_ROOT / "data" / "sync_state.json"
SYNC_PROCESS_LOCK_PATH = PROJECT_ROOT / "data" / ".sync.lock"
AI_PLAN_PATH = PROJECT_ROOT / "data" / "ai_daily_plan.json"
PROFILE_LOCK = threading.Lock()
DAILY_ITEM_LOCK = threading.Lock()
SYNC_LOCK = threading.Lock()
SYNC_STATE_LOCK = threading.Lock()
AI_PLAN_LOCK = threading.Lock()
APP_USERNAME = ""
APP_PASSWORD = ""
DEFAULT_SYNC_ENDPOINTS = ["daily_sleep", "daily_readiness", "sleep", "heartrate", "daily_activity", "daily_stress", "workout"]
AI_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "sleepPlan", "guidance", "timeline"],
    "properties": {
        "status": {
            "type": "object",
            "additionalProperties": False,
            "required": ["key", "label", "title", "reason"],
            "properties": {
                "key": {"type": "string", "enum": ["green", "amber", "red"]},
                "label": {"type": "string"},
                "title": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
        "sleepPlan": {
            "type": "object",
            "additionalProperties": False,
            "required": ["windDown", "bed", "lightsOut", "wake", "instruction"],
            "properties": {
                "windDown": {"type": "string"},
                "bed": {"type": "string"},
                "lightsOut": {"type": "string"},
                "wake": {"type": "string"},
                "instruction": {"type": "string"},
            },
        },
        "guidance": {
            "type": "object",
            "additionalProperties": False,
            "required": ["work", "exercise", "evening"],
            "properties": {
                "work": {"type": "string"},
                "exercise": {"type": "string"},
                "evening": {"type": "string"},
            },
        },
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["time", "title", "detail"],
                "properties": {
                    "time": {"type": "string"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
            },
        },
    },
}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return []


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sync_settings(profile: dict[str, Any]) -> dict[str, Any]:
    configured = profile.get("sync", {})
    if not isinstance(configured, dict):
        configured = {}
    endpoints = configured.get("endpoints", DEFAULT_SYNC_ENDPOINTS)
    if not isinstance(endpoints, list):
        endpoints = DEFAULT_SYNC_ENDPOINTS
    selected = [str(name) for name in endpoints if str(name) in DEFAULT_SYNC_ENDPOINTS]
    return {
        "enabled": configured.get("enabled") is not False,
        "intervalMinutes": max(15, min(1440, int(configured.get("intervalMinutes", 60)))),
        "lookbackDays": max(1, min(14, int(configured.get("lookbackDays", 3)))),
        "endpoints": selected or list(DEFAULT_SYNC_ENDPOINTS),
    }


def valid_basic_authorization(header: str, username: str, password: str) -> bool:
    if not password:
        return True
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        supplied_username, supplied_password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(supplied_username, username) and hmac.compare_digest(supplied_password, password)


def current_sync_state() -> dict[str, Any]:
    state = read_json(SYNC_STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    settings = sync_settings(load_profile())
    return {
        "status": state.get("status", "idle"),
        "message": state.get("message", "等待首次自动同步"),
        "lastAttempt": state.get("lastAttempt"),
        "lastSuccess": state.get("lastSuccess"),
        "nextSync": state.get("nextSync"),
        "range": state.get("range"),
        "enabled": settings["enabled"],
        "intervalMinutes": settings["intervalMinutes"],
        "lookbackDays": settings["lookbackDays"],
        "endpoints": settings["endpoints"],
    }


def update_sync_state(**changes: Any) -> dict[str, Any]:
    with SYNC_STATE_LOCK:
        state = current_sync_state()
        state.update(changes)
        write_json_atomic(SYNC_STATE_PATH, state)
        return state


def acquire_sync_process_lock() -> int | None:
    SYNC_PROCESS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(SYNC_PROCESS_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            stale = time.time() - SYNC_PROCESS_LOCK_PATH.stat().st_mtime > 20 * 60
        except OSError:
            stale = False
        if not stale:
            return None
        try:
            SYNC_PROCESS_LOCK_PATH.unlink()
        except OSError:
            return None
        return acquire_sync_process_lock()
    os.write(descriptor, str(os.getpid()).encode("ascii"))
    return descriptor


def release_sync_process_lock(descriptor: int | None) -> None:
    if descriptor is None:
        return
    os.close(descriptor)
    try:
        SYNC_PROCESS_LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def run_incremental_sync(trigger: str = "automatic") -> None:
    if not SYNC_LOCK.acquire(blocking=False):
        return
    process_lock = acquire_sync_process_lock()
    if process_lock is None:
        SYNC_LOCK.release()
        return
    try:
        settings = sync_settings(load_profile())
        now = datetime.now().astimezone()
        end_day = date.today()
        start_day = end_day - timedelta(days=settings["lookbackDays"] - 1)
        next_sync = now + timedelta(minutes=settings["intervalMinutes"])
        update_sync_state(
            status="syncing",
            message="正在读取 Oura 最新数据",
            lastAttempt=now.isoformat(timespec="seconds"),
            nextSync=next_sync.isoformat(timespec="seconds"),
            range={"start": start_day.isoformat(), "end": end_day.isoformat()},
            trigger=trigger,
        )
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "oura_sync.py"),
            "sync",
            "--start-date",
            start_day.isoformat(),
            "--end-date",
            end_day.isoformat(),
            "--endpoints",
            *settings["endpoints"],
            "--skip-raw",
        ]
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=15 * 60,
        )
        finished = datetime.now().astimezone().isoformat(timespec="seconds")
        if result.returncode == 0:
            update_sync_state(status="success", message="Oura 数据已更新", lastSuccess=finished)
            request_ai_plan_generation()
            return
        output = f"{result.stdout}\n{result.stderr}".lower()
        needs_auth = "run auth again" in output or "no token file" in output
        update_sync_state(
            status="needs_auth" if needs_auth else "error",
            message="需要重新连接 Oura" if needs_auth else "同步失败，将自动重试",
        )
    except subprocess.TimeoutExpired:
        update_sync_state(status="error", message="同步超时，将自动重试")
    except (OSError, ValueError) as error:
        update_sync_state(status="error", message=f"同步未完成：{str(error)[:100]}")
    finally:
        release_sync_process_lock(process_lock)
        SYNC_LOCK.release()


def request_incremental_sync(trigger: str = "manual") -> dict[str, Any]:
    if SYNC_LOCK.locked():
        return current_sync_state()
    threading.Thread(target=run_incremental_sync, args=(trigger,), daemon=True).start()
    for _ in range(20):
        state = current_sync_state()
        if state["status"] == "syncing":
            return state
        time.sleep(0.01)
    return current_sync_state()


def automatic_sync_loop(stop_event: threading.Event) -> None:
    stop_event.wait(2)
    while not stop_event.is_set():
        settings = sync_settings(load_profile())
        if settings["enabled"]:
            run_incremental_sync("automatic")
        stop_event.wait(settings["intervalMinutes"] * 60)


def load_profile() -> dict[str, Any]:
    example = read_json(PROFILE_EXAMPLE_PATH, {})
    profile = read_json(PROFILE_PATH, example)
    return profile if isinstance(profile, dict) else example


def save_profile(profile: dict[str, Any]) -> None:
    with PROFILE_LOCK:
        write_json_atomic(PROFILE_PATH, profile)


def profile_items() -> list[dict[str, str]]:
    items = load_profile().get("dailyItems", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id") and item.get("name")]


def add_profile_item(payload: dict[str, Any]) -> dict[str, str]:
    name = str(payload.get("name") or "").strip()[:60]
    category = str(payload.get("category") or "other").strip().lower()
    time_text = str(payload.get("time") or "").strip()[:5]
    note = str(payload.get("note") or "").strip()[:120]
    if not name:
        raise ValueError("请输入项目名称")
    if category not in {"medication", "supplement", "other"}:
        raise ValueError("项目类型无效")
    if time_text:
        try:
            datetime.strptime(time_text, "%H:%M")
        except ValueError as error:
            raise ValueError("时间格式无效") from error
    item = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "category": category,
        "time": time_text,
        "note": note,
    }
    with PROFILE_LOCK:
        profile = load_profile()
        items = profile.get("dailyItems", [])
        profile["dailyItems"] = [*items, item] if isinstance(items, list) else [item]
        write_json_atomic(PROFILE_PATH, profile)
    return item


def delete_profile_item(item_id: str) -> bool:
    with PROFILE_LOCK:
        profile = load_profile()
        items = profile.get("dailyItems", [])
        if not isinstance(items, list):
            return False
        remaining = [item for item in items if not isinstance(item, dict) or item.get("id") != item_id]
        if len(remaining) == len(items):
            return False
        profile["dailyItems"] = remaining
        write_json_atomic(PROFILE_PATH, profile)
    return True


def daily_item_state(day_text: str) -> dict[str, Any]:
    log = read_json(DAILY_ITEM_LOG_PATH, {})
    taken = log.get(day_text, {}) if isinstance(log, dict) else {}
    items = [{**item, "taken": taken.get(item["id"]) is True} for item in profile_items()]
    return {"date": day_text, "items": items}


def save_daily_item(payload: dict[str, Any]) -> dict[str, Any]:
    day_text = str(payload.get("date") or "")[:10]
    try:
        date.fromisoformat(day_text)
    except ValueError as error:
        raise ValueError("日期无效") from error
    item_id = str(payload.get("id") or "")
    if item_id not in {item["id"] for item in profile_items()}:
        raise ValueError("项目不存在")
    with DAILY_ITEM_LOCK:
        log = read_json(DAILY_ITEM_LOG_PATH, {})
        if not isinstance(log, dict):
            log = {}
        day_log = log.get(day_text, {})
        if not isinstance(day_log, dict):
            day_log = {}
        day_log[item_id] = payload.get("taken") is True
        log[day_text] = day_log
        write_json_atomic(DAILY_ITEM_LOG_PATH, log)
    return daily_item_state(day_text)


def ai_plan_status() -> dict[str, Any]:
    cached = read_json(AI_PLAN_PATH, {})
    if not isinstance(cached, dict):
        cached = {}
    configured = bool(os.environ.get("OPENAI_API_KEY"))
    if AI_PLAN_LOCK.locked():
        status = "generating"
        message = "AI 正在生成今日计划"
    elif not configured:
        status = "not_configured"
        message = "添加 OPENAI_API_KEY 后启用每日动态计划"
    elif cached.get("date") == date.today().isoformat() and cached.get("source") == "ai":
        status = "ready"
        message = "今日计划已由 AI 生成"
    elif cached.get("status") == "error" and cached.get("date") == date.today().isoformat():
        status = "error"
        message = str(cached.get("message") or "AI 计划生成失败")
    else:
        status = "pending"
        message = "等待生成今日计划"
    return {
        "status": status,
        "message": message,
        "configured": configured,
        "model": os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        "generatedAt": cached.get("generatedAt"),
    }


def response_output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text" and content.get("text"):
                return str(content["text"])
    raise ValueError("AI response did not contain output text")


def clock_distance(left: str, right: str) -> int:
    difference = abs(clock_value(left) - clock_value(right))
    return min(difference, 24 * 60 - difference)


def valid_clock(value: Any) -> bool:
    try:
        parsed = datetime.strptime(str(value), "%H:%M")
    except ValueError:
        return False
    return parsed.strftime("%H:%M") == str(value)


def validate_ai_plan(plan: Any, base_sleep: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("AI plan is not an object")
    status = plan.get("status")
    sleep = plan.get("sleepPlan")
    guidance = plan.get("guidance")
    timeline = plan.get("timeline")
    if not isinstance(status, dict) or status.get("key") not in {"green", "amber", "red"}:
        raise ValueError("AI plan status is invalid")
    if not isinstance(sleep, dict) or not isinstance(guidance, dict) or not isinstance(timeline, list):
        raise ValueError("AI plan sections are invalid")
    checked_sleep = dict(base_sleep)
    for key in ("windDown", "bed", "lightsOut"):
        candidate = str(sleep.get(key) or "")
        baseline = str(base_sleep.get(key) or "")
        if valid_clock(candidate) and valid_clock(baseline) and clock_distance(candidate, baseline) <= 60:
            checked_sleep[key] = candidate
    checked_sleep["wake"] = base_sleep.get("wake", "--")
    checked_sleep["instruction"] = str(sleep.get("instruction") or base_sleep.get("instruction") or "")[:220]
    checked_timeline = []
    for item in timeline[:10]:
        if not isinstance(item, dict):
            continue
        time_text = str(item.get("time") or "")[:30]
        title = str(item.get("title") or "")[:60]
        detail = str(item.get("detail") or "")[:180]
        if time_text and title and detail:
            checked_timeline.append({"time": time_text, "title": title, "detail": detail})
    if len(checked_timeline) < 3:
        raise ValueError("AI plan timeline is incomplete")
    return {
        "status": {
            "key": status["key"],
            "label": str(status.get("label") or "")[:40],
            "title": str(status.get("title") or "")[:100],
            "reason": str(status.get("reason") or "")[:220],
        },
        "sleepPlan": checked_sleep,
        "guidance": {
            "work": str(guidance.get("work") or "")[:220],
            "exercise": str(guidance.get("exercise") or "")[:220],
            "evening": str(guidance.get("evening") or "")[:220],
        },
        "timeline": checked_timeline,
    }


def ai_plan_context(dashboard: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    medication_names = [str(item.get("name")) for item in profile_items() if item.get("category") == "medication"]

    def redact(value: Any) -> Any:
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, str):
            result = value
            for name in medication_names:
                if name:
                    result = re.sub(re.escape(name), "固定用药", result, flags=re.IGNORECASE)
            return result
        return value

    fixed_slots = [
        {"category": item.get("category"), "time": item.get("time") or "user-defined"}
        for item in profile_items()
        if item.get("category") == "medication"
    ]
    return {
        "date": date.today().isoformat(),
        "weekday": date.today().strftime("%A"),
        "latestNight": dashboard["latest"],
        "recent14": dashboard["current14"],
        "previous14": dashboard["previous14"],
        "ruleBasedRecovery": dashboard["status"],
        "currentSleepPhase": dashboard["sleepPlan"],
        "configuredWorkday": redact(profile.get("schedule", {}).get("workday", [])),
        "weeklyTraining": redact(profile.get("schedule", {}).get("weekPlan", [])),
        "fixedMedicationSlotsWithoutNames": fixed_slots,
    }


def generate_ai_plan() -> None:
    if not AI_PLAN_LOCK.acquire(blocking=False):
        return
    try:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return
        dashboard = dashboard_payload(include_ai=False)
        if dashboard.get("error"):
            return
        profile = load_profile()
        base_sleep = dashboard["sleepPlan"]
        context = ai_plan_context(dashboard, profile)
        request_payload = {
            "model": os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
            "store": False,
            "instructions": (
                "You are a concise Chinese daily planning engine for a private sleep and cognition dashboard. "
                "Build a specific plan for this date from the supplied wearable data and configured constraints. "
                "Vary work intensity, exercise, breaks, learning, and bedtime when the data supports it. "
                "Keep the configured wake time unchanged. Sleep times may move by at most 60 minutes. "
                "Do not diagnose, recommend medication, change medication timing, dosage, or frequency, or name medicines. "
                "Medication slots are fixed constraints managed outside your output. Use short, direct Chinese."
            ),
            "input": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "daily_plan",
                    "strict": True,
                    "schema": AI_PLAN_SCHEMA,
                }
            },
        }
        request = Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=90) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        plan = validate_ai_plan(json.loads(response_output_text(response_payload)), base_sleep)
        fingerprint = hashlib.sha256(request_payload["input"].encode("utf-8")).hexdigest()[:16]
        write_json_atomic(
            AI_PLAN_PATH,
            {
                **plan,
                "date": date.today().isoformat(),
                "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "source": "ai",
                "model": request_payload["model"],
                "contextFingerprint": fingerprint,
            },
        )
    except HTTPError as error:
        write_json_atomic(AI_PLAN_PATH, {"date": date.today().isoformat(), "status": "error", "message": f"AI API 返回 {error.code}"})
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        write_json_atomic(AI_PLAN_PATH, {"date": date.today().isoformat(), "status": "error", "message": f"AI 计划失败：{str(error)[:100]}"})
    finally:
        AI_PLAN_LOCK.release()


def request_ai_plan_generation(force: bool = False) -> dict[str, Any]:
    cached = read_json(AI_PLAN_PATH, {})
    if (
        not force
        and isinstance(cached, dict)
        and cached.get("date") == date.today().isoformat()
        and cached.get("source") == "ai"
    ):
        return ai_plan_status()
    if not os.environ.get("OPENAI_API_KEY"):
        return ai_plan_status()
    threading.Thread(target=generate_ai_plan, daemon=True).start()
    return ai_plan_status()


def number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def median(values: Iterable[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return statistics.median(clean) if clean else None


def mean(values: Iterable[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return statistics.fmean(clean) if clean else None


def round_or_none(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def choose_main_sleeps(rows: list[dict[str, str]]) -> dict[date, dict[str, str]]:
    grouped: dict[date, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        day = parse_day(row.get("day"))
        if day and row.get("type") == "long_sleep":
            grouped[day].append(row)
    return {
        day: max(candidates, key=lambda row: number(row.get("total_sleep_duration")) or 0)
        for day, candidates in grouped.items()
    }


def index_score(name: str) -> dict[date, float]:
    output: dict[date, float] = {}
    for row in read_csv(CSV_DIR / f"{name}.csv"):
        day = parse_day(row.get("day"))
        score = number(row.get("score"))
        if day and score is not None:
            output[day] = score
    return output


def clock_minutes(value: datetime | None, evening: bool = False) -> float | None:
    if value is None:
        return None
    minutes = value.hour * 60 + value.minute + value.second / 60
    if evening and minutes < 12 * 60:
        minutes += 24 * 60
    return minutes


def clock_text(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    value = round(minutes) % (24 * 60)
    return f"{value // 60:02d}:{value % 60:02d}"


def sleep_record(day: date, row: dict[str, str], readiness: dict[date, float], sleep_scores: dict[date, float]) -> dict[str, Any]:
    start = parse_dt(row.get("bedtime_start"))
    end = parse_dt(row.get("bedtime_end"))
    return {
        "date": day.isoformat(),
        "readiness": readiness.get(day),
        "sleepScore": sleep_scores.get(day),
        "sleepHours": round_or_none((number(row.get("total_sleep_duration")) or 0) / 3600, 2),
        "timeInBedHours": round_or_none((number(row.get("time_in_bed")) or 0) / 3600, 2),
        "efficiency": number(row.get("efficiency")),
        "hrv": number(row.get("average_hrv")),
        "lowestHeartRate": number(row.get("lowest_heart_rate")),
        "bedtime": clock_text(clock_minutes(start, evening=True)),
        "wakeTime": clock_text(clock_minutes(end)),
        "bedtimeMinutes": clock_minutes(start, evening=True),
        "wakeMinutes": clock_minutes(end),
    }


def load_sleep_data() -> list[dict[str, Any]]:
    readiness = index_score("daily_readiness")
    sleep_scores = index_score("daily_sleep")
    main_sleeps = choose_main_sleeps(read_csv(CSV_DIR / "sleep.csv"))
    return [sleep_record(day, main_sleeps[day], readiness, sleep_scores) for day in sorted(main_sleeps)]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n": len(rows),
        "sleepHours": round_or_none(median(row.get("sleepHours") for row in rows), 2),
        "timeInBedHours": round_or_none(median(row.get("timeInBedHours") for row in rows), 2),
        "readiness": round_or_none(mean(row.get("readiness") for row in rows), 1),
        "sleepScore": round_or_none(mean(row.get("sleepScore") for row in rows), 1),
        "hrv": round_or_none(median(row.get("hrv") for row in rows), 0),
        "lowestHeartRate": round_or_none(median(row.get("lowestHeartRate") for row in rows), 0),
        "bedtime": clock_text(median(row.get("bedtimeMinutes") for row in rows)),
        "wakeTime": clock_text(median(row.get("wakeMinutes") for row in rows)),
    }


def recovery_status(latest: dict[str, Any], targets: dict[str, Any]) -> dict[str, str]:
    readiness = latest.get("readiness")
    sleep_score = latest.get("sleepScore")
    sleep_h = latest.get("sleepHours")
    readiness_target = number(targets.get("readiness")) or 80
    sleep_score_target = number(targets.get("sleepScore")) or 80
    sleep_target = number(targets.get("sleepHours")) or 8
    values = [value for value in (readiness, sleep_score) if value is not None]
    if (values and min(values) < 70) or (sleep_h is not None and sleep_h < sleep_target - 1.5):
        return {
            "key": "red",
            "label": "优先补觉",
            "title": "今天只做必要任务",
            "work": "只保留一个 60–90 分钟专注块；午间休息 20 分钟。",
            "exercise": "取消训练；轻走 20–30 分钟。",
            "evening": "取消学习；按首页时间睡。",
        }
    if (
        readiness is not None
        and sleep_score is not None
        and sleep_h is not None
        and readiness >= readiness_target
        and sleep_score >= sleep_score_target
        and sleep_h >= sleep_target - 0.5
    ):
        return {
            "key": "green",
            "label": "正常推进",
            "title": "今天可以执行完整计划",
            "work": "第一个专注块攻最难题；第二个专注块完成一项交付。",
            "exercise": "按周计划完成 30–50 分钟训练。",
            "evening": "学习 30 分钟；睡前至少两小时停止工作。",
        }
    return {
        "key": "amber",
        "label": "今天减量",
        "title": "保留两项重要交付",
        "work": "每个专注块只做一个交付；其余时间处理沟通。",
        "exercise": "30–40 分钟；力量每项减 1 组。",
        "evening": "学习不超过 20 分钟；睡前至少两小时停止工作。",
    }


def comparison_value(current: float | None, previous: float | None, digits: int = 1) -> dict[str, float | None]:
    return {
        "current": round_or_none(current, digits),
        "previous": round_or_none(previous, digits),
        "delta": round_or_none(None if current is None or previous is None else current - previous, digits),
    }


def sleep_stability(rows: list[dict[str, Any]]) -> float | None:
    values = [row.get("bedtimeMinutes") for row in rows if row.get("bedtimeMinutes") is not None]
    return statistics.pstdev(values) if len(values) >= 2 else None


def wake_deviation(rows: list[dict[str, Any]], target_minutes: int) -> float | None:
    return median(
        abs(row["wakeMinutes"] - target_minutes)
        for row in rows
        if row.get("wakeMinutes") is not None
    )


def schedule_settings(profile: dict[str, Any]) -> tuple[date, list[dict[str, Any]], str]:
    schedule = profile.get("schedule", {})
    if not isinstance(schedule, dict):
        schedule = {}
    start = parse_day(str(schedule.get("startDate") or "")) or date.today()
    phases = schedule.get("phases", [])
    phases = [phase for phase in phases if isinstance(phase, dict)] if isinstance(phases, list) else []
    timezone_name = str(profile.get("timezone") or "UTC")
    return start, phases, timezone_name


def clock_value(time_text: str, fallback: int = 7 * 60) -> int:
    try:
        hour, minute = (int(part) for part in time_text.split(":"))
    except (TypeError, ValueError):
        return fallback
    return hour * 60 + minute


def tonight_sleep_plan(profile: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    current_day = today or date.today()
    schedule_start, phases, _ = schedule_settings(profile)
    if not phases:
        return {
            "label": "Not configured",
            "windDown": "--",
            "bed": "--",
            "lightsOut": "--",
            "naturalWakeWindow": "--",
            "wake": "--",
            "phaseIndex": -1,
            "instruction": "Add a sleep schedule to config/user.json.",
        }
    elapsed = max(0, (current_day - schedule_start).days)
    phase_index = 0
    remaining = elapsed
    for index, phase in enumerate(phases):
        duration = phase.get("days")
        if duration is None or remaining < int(duration):
            phase_index = index
            break
        remaining -= int(duration)
        phase_index = min(index + 1, len(phases) - 1)
    plan = dict(phases[phase_index])
    plan["phaseIndex"] = phase_index
    natural_start = str(plan.get("naturalWakeWindow") or plan.get("wake") or "--").replace("–", "-").split("-")[0]
    plan["instruction"] = (
        f'{plan.get("bed", "--")} 上床；{natural_start} 后清醒即起，{plan.get("wake", "--")} 闹钟。'
    )
    return plan


def automatic_review(sleep_rows: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    schedule_start, phases, _ = schedule_settings(profile)
    experiment_start = schedule_start + timedelta(days=1)
    due = experiment_start + timedelta(days=13)
    baseline_start = experiment_start - timedelta(days=14)
    baseline = [
        row for row in sleep_rows
        if baseline_start <= date.fromisoformat(row["date"]) < experiment_start
    ]
    trial = [
        row for row in sleep_rows
        if experiment_start <= date.fromisoformat(row["date"]) <= due
    ]
    completed = len(trial)
    last_observed = min(date.today(), due)
    day_number = max(0, min(14, (last_observed - experiment_start).days + 1))

    baseline_summary = summarize(baseline)
    trial_summary = summarize(trial)
    metrics: dict[str, Any] = {}
    definitions = (
        ("sleepHours", "实际睡眠", "小时", baseline_summary.get("sleepHours"), trial_summary.get("sleepHours"), False, 0.25),
        ("readiness", "Readiness", "分", baseline_summary.get("readiness"), trial_summary.get("readiness"), False, 3),
        ("sleepScore", "睡眠分", "分", baseline_summary.get("sleepScore"), trial_summary.get("sleepScore"), False, 3),
        ("hrv", "HRV", "ms", baseline_summary.get("hrv"), trial_summary.get("hrv"), False, 2),
        ("stability", "入睡波动", "分钟", sleep_stability(baseline), sleep_stability(trial), True, 15),
        (
            "wakeDeviation",
            "起床偏差",
            "分钟",
            wake_deviation(baseline, clock_value(str(phases[-1].get("wake", ""))) if phases else 7 * 60),
            wake_deviation(trial, clock_value(str(phases[-1].get("wake", ""))) if phases else 7 * 60),
            True,
            15,
        ),
    )
    for key, label, unit, before, after, lower_is_better, meaningful_change in definitions:
        delta = None if before is None or after is None else after - before
        improved = None
        if delta is not None:
            improved = delta <= -meaningful_change if lower_is_better else delta >= meaningful_change
        metrics[key] = {
            "key": key,
            "label": label,
            "unit": unit,
            "baseline": round_or_none(before, 2 if key == "sleepHours" else 1),
            "trial": round_or_none(after, 2 if key == "sleepHours" else 1),
            "delta": round_or_none(delta, 2 if key == "sleepHours" else 1),
            "improved": improved,
            "lowerIsBetter": lower_is_better,
        }

    comparable = [metric for metric in metrics.values() if metric["delta"] is not None]
    improved_count = sum(metric["improved"] is True for metric in comparable)
    if completed == 0:
        verdict = "等待第 1 晚；7 晚初判，14 晚定案。"
    elif completed < 7:
        verdict = f"还需 {7 - completed} 晚初判；14 晚定案。"
    elif completed < 14:
        verdict = (
            "多数指标改善；保持当前时间到第 14 晚。"
            if comparable and improved_count >= math.ceil(len(comparable) / 2)
            else "变化未稳定；保持当前时间到第 14 晚。"
        )
    else:
        verdict = (
            "多数指标改善；继续当前固定作息。"
            if comparable and improved_count >= math.ceil(len(comparable) / 2)
            else "改善不足；下一轮只调整一个时间点。"
        )
    return {
        "startDate": experiment_start.isoformat(),
        "dueDate": due.isoformat(),
        "dayNumber": day_number,
        "completed": completed,
        "target": 14,
        "baselineNights": len(baseline),
        "verdict": verdict,
        "metrics": metrics,
        "mode": "automatic",
    }


def fallback_timeline(profile: dict[str, Any]) -> list[dict[str, str]]:
    schedule = profile.get("schedule", {})
    rows = schedule.get("workday", []) if isinstance(schedule, dict) else []
    output = []
    for row in rows:
        if isinstance(row, list) and len(row) >= 3:
            output.append({"time": str(row[0]), "title": str(row[1]), "detail": str(row[2])})
    return output


def dashboard_payload(include_ai: bool = True) -> dict[str, Any]:
    profile = load_profile()
    targets = profile.get("targets", {}) if isinstance(profile.get("targets"), dict) else {}
    sleep_rows = load_sleep_data()
    if not sleep_rows:
        return {"error": "尚未找到 Oura 睡眠 CSV 数据"}
    latest = sleep_rows[-1]
    latest_day = date.fromisoformat(latest["date"])
    current_rows = [row for row in sleep_rows if latest_day - timedelta(days=13) <= date.fromisoformat(row["date"]) <= latest_day]
    previous_rows = [row for row in sleep_rows if latest_day - timedelta(days=27) <= date.fromisoformat(row["date"]) <= latest_day - timedelta(days=14)]
    current = summarize(current_rows)
    previous = summarize(previous_rows)
    trend_start = latest_day - timedelta(days=27)
    trend = [row for row in sleep_rows if date.fromisoformat(row["date"]) >= trend_start]
    csv_files = list(CSV_DIR.glob("*.csv"))
    newest_mtime = max((path.stat().st_mtime for path in csv_files), default=0)
    payload = {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataThrough": latest["date"],
        "lastLocalSync": datetime.fromtimestamp(newest_mtime).astimezone().isoformat(timespec="minutes") if newest_mtime else None,
        "latest": latest,
        "status": recovery_status(latest, targets),
        "current14": current,
        "previous14": previous,
        "comparisons": {
            "sleepHours": comparison_value(current.get("sleepHours"), previous.get("sleepHours"), 2),
            "readiness": comparison_value(current.get("readiness"), previous.get("readiness"), 1),
            "sleepScore": comparison_value(current.get("sleepScore"), previous.get("sleepScore"), 1),
            "hrv": comparison_value(current.get("hrv"), previous.get("hrv"), 0),
        },
        "trend": trend,
        "profile": {
            "timezone": profile.get("timezone", "UTC"),
            "schedule": profile.get("schedule", {}),
            "targets": targets,
            "cycle": profile.get("cycle"),
        },
        "sleepPlan": tonight_sleep_plan(profile),
        "review": automatic_review(sleep_rows, profile),
    }
    if not include_ai:
        return payload
    cached_plan = read_json(AI_PLAN_PATH, {})
    valid_cached_plan = (
        isinstance(cached_plan, dict)
        and cached_plan.get("date") == date.today().isoformat()
        and cached_plan.get("source") == "ai"
    )
    if valid_cached_plan:
        guidance = cached_plan.get("guidance", {})
        ai_status = cached_plan.get("status", {})
        payload["status"] = {
            **ai_status,
            "work": guidance.get("work", payload["status"]["work"]),
            "exercise": guidance.get("exercise", payload["status"]["exercise"]),
            "evening": guidance.get("evening", payload["status"]["evening"]),
        }
        payload["sleepPlan"] = cached_plan.get("sleepPlan", payload["sleepPlan"])
        payload["todayPlan"] = {
            "source": "ai",
            "model": cached_plan.get("model"),
            "generatedAt": cached_plan.get("generatedAt"),
            "timeline": cached_plan.get("timeline", []),
        }
    else:
        payload["todayPlan"] = {
            "source": "fallback",
            "model": None,
            "generatedAt": None,
            "timeline": fallback_timeline(profile),
        }
    payload["ai"] = ai_plan_status()
    return payload


def calendar_ics() -> str:
    profile = load_profile()
    schedule_start, phases, timezone_name = schedule_settings(profile)
    experiment_start = schedule_start + timedelta(days=1)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Oura Focus Lab//CN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    def add_event(uid: str, event_day: date, time_text: str, title: str, description: str, rule: str | None = None) -> None:
        time_value = time_text.replace(":", "") + "00"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}@oura-focus.local",
                f"DTSTAMP:{stamp}",
                f"DTSTART;TZID={timezone_name}:{event_day.strftime('%Y%m%d')}T{time_value}",
                *([f"RRULE:{rule}"] if rule else []),
                f"SUMMARY:{title}",
                f"DESCRIPTION:{description}",
                "BEGIN:VALARM",
                "TRIGGER:-PT0M",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{title}",
                "END:VALARM",
                "END:VEVENT",
            ]
        )

    phase_offset = 0
    for index, plan in enumerate(phases):
        phase_start = schedule_start + timedelta(days=phase_offset)
        duration = plan.get("days")
        rule = "FREQ=DAILY" if duration is None else f"FREQ=DAILY;COUNT={int(duration)}"
        add_event(
            f"wind-down-{index}",
            phase_start,
            str(plan.get("windDown", "22:30")),
            "Wind down",
            f'Bed at {plan.get("bed", "--")}.',
            rule,
        )
        for kind, title in (("bed", "Bed"), ("lightsOut", "Lights out"), ("wake", "Wake")):
            time_text = str(plan.get(kind, "07:00" if kind == "wake" else "23:00"))
            hour = int(time_text.split(":")[0])
            day_shift = 1 if kind == "wake" or hour < 12 else 0
            description = f'Lights out at {plan.get("lightsOut", "--")}.' if kind == "bed" else (
                f'Wake at {plan.get("wake", "--")}.' if kind == "lightsOut" else "Start the configured morning routine."
            )
            add_event(
                f"{kind}-{index}", phase_start + timedelta(days=day_shift), time_text, title, description, rule
            )
        if duration is not None:
            phase_offset += int(duration)

    for item in profile_items():
        if not valid_clock(item.get("time")):
            continue
        add_event(
            f'daily-item-{item["id"]}',
            schedule_start,
            item["time"],
            item["name"],
            item.get("note") or "Daily item configured in Oura Focus Lab.",
            "FREQ=DAILY",
        )

    review_day = (experiment_start + timedelta(days=13)).strftime("%Y%m%d")
    lines.extend(
        [
            "BEGIN:VEVENT",
            "UID:review@oura-focus.local",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID={timezone_name}:{review_day}T170500",
            "SUMMARY:Oura 作息 14 天复盘",
            "DESCRIPTION:打开 Oura Focus Lab，查看自动生成的 14 晚睡眠与恢复复盘。",
            "BEGIN:VALARM",
            "TRIGGER:-PT0M",
            "ACTION:DISPLAY",
            "DESCRIPTION:现在做 14 天作息复盘",
            "END:VALARM",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )
    return "\r\n".join(lines) + "\r\n"


class OuraAppHandler(BaseHTTPRequestHandler):
    server_version = "OuraFocusLab/1.0"

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def send_bytes(self, body: bytes, content_type: str, status: int = HTTPStatus.OK, disposition: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(body, "application/json; charset=utf-8", status)

    def is_authorized(self) -> bool:
        return valid_basic_authorization(
            self.headers.get("Authorization", ""), APP_USERNAME, APP_PASSWORD
        )

    def require_authorization(self) -> bool:
        if self.is_authorized():
            return False
        body = b"Authentication required"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Oura Focus Lab", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/healthz":
            self.send_json({"status": "ok"})
            return
        if self.require_authorization():
            return
        if path == "/api/dashboard":
            self.send_json(dashboard_payload())
            return
        if path == "/api/sync-status":
            self.send_json(current_sync_state())
            return
        if path == "/api/ai-plan":
            self.send_json(ai_plan_status())
            return
        if path == "/api/reminders.ics":
            self.send_bytes(
                calendar_ics().encode("utf-8"),
                "text/calendar; charset=utf-8",
                disposition='attachment; filename="oura-focus-reminders.ics"',
            )
            return
        if path == "/api/profile":
            self.send_json(load_profile())
            return
        if path == "/api/daily-items":
            day_text = (parse_qs(parsed.query).get("date") or [date.today().isoformat()])[0][:10]
            try:
                date.fromisoformat(day_text)
            except ValueError:
                self.send_json({"error": "日期无效"}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json(daily_item_state(day_text))
            return
        if path.startswith("/api/"):
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        self.serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if self.require_authorization():
            return
        if path == "/api/sync":
            self.send_json(request_incremental_sync(), HTTPStatus.ACCEPTED)
            return
        if path == "/api/ai-plan":
            self.send_json(request_ai_plan_generation(force=True), HTTPStatus.ACCEPTED)
            return
        if path not in {"/api/items", "/api/daily-item"}:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2:
                raise ValueError("请求格式无效")
            if length > 4_000:
                raise ValueError("请求过大")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求格式无效")
            saved = add_profile_item(payload) if path == "/api/items" else save_daily_item(payload)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json(saved)

    def do_DELETE(self) -> None:
        if self.require_authorization():
            return
        parsed = urlparse(self.path)
        if parsed.path != "/api/items":
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        item_id = (parse_qs(parsed.query).get("id") or [""])[0]
        if not item_id or not delete_profile_item(item_id):
            self.send_json({"error": "项目不存在"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"deleted": item_id})

    def serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (APP_DIR / relative).resolve()
        try:
            candidate.relative_to(APP_DIR.resolve())
        except ValueError:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            candidate = APP_DIR / "index.html"
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".webmanifest": "application/manifest+json; charset=utf-8",
            ".svg": "image/svg+xml",
        }
        self.send_bytes(candidate.read_bytes(), content_types.get(candidate.suffix, "application/octet-stream"))


def main() -> None:
    global APP_PASSWORD, APP_USERNAME
    load_env_file(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    parser.add_argument("--no-auto-sync", action="store_true", help="Serve the dashboard without starting a second sync loop")
    args = parser.parse_args()
    APP_USERNAME = os.environ.get("OURA_APP_USERNAME", "oura")
    APP_PASSWORD = os.environ.get("OURA_APP_PASSWORD", "")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not APP_PASSWORD:
        parser.error("OURA_APP_PASSWORD is required when binding beyond localhost")
    server = ThreadingHTTPServer((args.host, args.port), OuraAppHandler)
    sync_stop = threading.Event()
    if not args.no_auto_sync:
        sync_thread = threading.Thread(target=automatic_sync_loop, args=(sync_stop,), daemon=True)
        sync_thread.start()
    print(f"Oura Focus Lab: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sync_stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
