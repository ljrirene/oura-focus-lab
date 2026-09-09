#!/usr/bin/env python3
"""Analyze local Oura history for recovery patterns relevant to cognitive work."""

from __future__ import annotations

import csv
import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = PROJECT_ROOT / "data" / "csv"
REPORT_PATH = PROJECT_ROOT / "reports" / "oura_cognition_analysis.md"
LOCAL_TZ = datetime.now().astimezone().tzinfo


def read_csv(name: str) -> list[dict[str, str]]:
    path = CSV_DIR / f"{name}.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    if not math.isfinite(result):
        return None
    return result


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
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def clock_hour(value: datetime | None, evening: bool = False) -> float | None:
    if value is None:
        return None
    result = value.hour + value.minute / 60 + value.second / 3600
    if evening and result < 12:
        result += 24
    return result


def mean(values: Iterable[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return statistics.fmean(clean) if clean else None


def median(values: Iterable[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return statistics.median(clean) if clean else None


def percentile(values: Iterable[float | None], quantile: float) -> float | None:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return None
    position = (len(clean) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    fraction = position - lower
    return clean[lower] * (1 - fraction) + clean[upper] * fraction


def fmt(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def fmt_clock(value: float | None) -> str:
    if value is None:
        return "n/a"
    minutes = round((value % 24) * 60)
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def fmt_hours(value: float | None) -> str:
    if value is None:
        return "n/a"
    minutes = round(value * 60)
    return f"{minutes // 60}h{minutes % 60:02d}m"


def fmt_minutes(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}m"


def index_by_day(rows: list[dict[str, str]]) -> dict[date, dict[str, str]]:
    output: dict[date, dict[str, str]] = {}
    for row in rows:
        day = parse_day(row.get("day"))
        if day:
            output[day] = row
    return output


def table_span(rows: list[dict[str, str]]) -> tuple[str, str]:
    days: list[date] = []
    for row in rows:
        for key in ("day", "timestamp", "bedtime_start", "start_datetime", "start_day"):
            value = row.get(key)
            parsed = parse_day(value)
            if parsed:
                days.append(parsed)
                break
    if not days:
        return "n/a", "n/a"
    return min(days).isoformat(), max(days).isoformat()


def choose_main_sleeps(rows: list[dict[str, str]]) -> dict[date, dict[str, str]]:
    grouped: dict[date, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        day = parse_day(row.get("day"))
        if day:
            grouped[day].append(row)

    output: dict[date, dict[str, str]] = {}
    for day, candidates in grouped.items():
        long_sleeps = [row for row in candidates if row.get("type") == "long_sleep"]
        pool = long_sleeps or candidates
        output[day] = max(pool, key=lambda row: number(row.get("total_sleep_duration")) or 0)
    return output


def sleep_record(row: dict[str, str]) -> dict[str, Any]:
    start = parse_dt(row.get("bedtime_start"))
    end = parse_dt(row.get("bedtime_end"))
    return {
        "start": start,
        "end": end,
        "bedtime": clock_hour(start, evening=True),
        "wake": clock_hour(end),
        "sleep_h": (number(row.get("total_sleep_duration")) or 0) / 3600,
        "tib_h": (number(row.get("time_in_bed")) or 0) / 3600,
        "efficiency": number(row.get("efficiency")),
        "latency_m": (number(row.get("latency")) or 0) / 60,
        "awake_m": (number(row.get("awake_time")) or 0) / 60,
        "deep_h": (number(row.get("deep_sleep_duration")) or 0) / 3600,
        "rem_h": (number(row.get("rem_sleep_duration")) or 0) / 3600,
        "hrv": number(row.get("average_hrv")),
        "avg_hr": number(row.get("average_heart_rate")),
        "low_hr": number(row.get("lowest_heart_rate")),
        "breath": number(row.get("average_breath")),
    }


def build_daily() -> tuple[dict[date, dict[str, Any]], dict[str, list[dict[str, str]]]]:
    names = [
        "daily_activity",
        "daily_cardiovascular_age",
        "daily_readiness",
        "daily_resilience",
        "daily_sleep",
        "daily_spo2",
        "daily_stress",
        "enhanced_tag",
        "heartrate",
        "rest_mode_period",
        "ring_battery_level",
        "ring_configuration",
        "session",
        "sleep",
        "sleep_time",
        "vO2_max",
        "workout",
    ]
    tables = {name: read_csv(name) for name in names}
    main_sleeps = choose_main_sleeps(tables["sleep"])
    daily_tables = {
        name: index_by_day(tables[name])
        for name in (
            "daily_activity",
            "daily_cardiovascular_age",
            "daily_readiness",
            "daily_resilience",
            "daily_sleep",
            "daily_spo2",
            "daily_stress",
            "sleep_time",
        )
    }

    all_days = sorted(set(main_sleeps) | set().union(*(set(table) for table in daily_tables.values())))
    daily: dict[date, dict[str, Any]] = {}
    for day in all_days:
        result: dict[str, Any] = {"day": day}
        if day in main_sleeps:
            result.update(sleep_record(main_sleeps[day]))
        readiness = daily_tables["daily_readiness"].get(day, {})
        daily_sleep = daily_tables["daily_sleep"].get(day, {})
        activity = daily_tables["daily_activity"].get(day, {})
        stress = daily_tables["daily_stress"].get(day, {})
        spo2 = daily_tables["daily_spo2"].get(day, {})
        cardio = daily_tables["daily_cardiovascular_age"].get(day, {})
        result.update(
            {
                "readiness": number(readiness.get("score")),
                "sleep_score": number(daily_sleep.get("score")),
                "sleep_timing": number(daily_sleep.get("contributors.timing")),
                "sleep_regularity": number(readiness.get("contributors.sleep_regularity")),
                "temperature": number(readiness.get("temperature_deviation")),
                "steps": number(activity.get("steps")),
                "sedentary_h": (number(activity.get("sedentary_time")) or 0) / 3600,
                "medium_high_m": (
                    (number(activity.get("medium_activity_time")) or 0)
                    + (number(activity.get("high_activity_time")) or 0)
                )
                / 60,
                "inactivity_alerts": number(activity.get("inactivity_alerts")),
                "stress_h": (number(stress.get("stress_high")) or 0) / 3600,
                "recovery_h": (number(stress.get("recovery_high")) or 0) / 3600,
                "day_summary": stress.get("day_summary") or None,
                "spo2": number(spo2.get("spo2_percentage.average")),
                "bdi": number(spo2.get("breathing_disturbance_index")),
                "vascular_age": number(cardio.get("vascular_age")),
                "resilience": daily_tables["daily_resilience"].get(day, {}).get("level") or None,
            }
        )
        daily[day] = result

    # Link the previous day's behavior to the following morning's sleep and readiness.
    for day, result in daily.items():
        previous = daily.get(day - timedelta(days=1), {})
        for key in ("steps", "medium_high_m", "sedentary_h", "stress_h", "recovery_h"):
            result[f"prior_{key}"] = previous.get(key)

    return daily, tables


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n": len(rows),
        "sleep_h": median(row.get("sleep_h") for row in rows),
        "tib_h": median(row.get("tib_h") for row in rows),
        "bedtime": median(row.get("bedtime") for row in rows),
        "wake": median(row.get("wake") for row in rows),
        "readiness": mean(row.get("readiness") for row in rows),
        "sleep_score": mean(row.get("sleep_score") for row in rows),
        "efficiency": median(row.get("efficiency") for row in rows),
        "latency_m": median(row.get("latency_m") for row in rows),
        "awake_m": median(row.get("awake_m") for row in rows),
        "deep_h": median(row.get("deep_h") for row in rows),
        "rem_h": median(row.get("rem_h") for row in rows),
        "hrv": median(row.get("hrv") for row in rows),
        "low_hr": median(row.get("low_hr") for row in rows),
        "stress_h": median(row.get("stress_h") for row in rows),
        "recovery_h": median(row.get("recovery_h") for row in rows),
        "steps": median(row.get("steps") for row in rows),
    }


def summary_line(label: str, summary: dict[str, Any]) -> str:
    return (
        f"| {label} | {summary['n']} | {fmt_hours(summary['sleep_h'])} | "
        f"{fmt_hours(summary['tib_h'])} | {fmt_clock(summary['bedtime'])} | "
        f"{fmt_clock(summary['wake'])} | {fmt(summary['readiness'])} | "
        f"{fmt(summary['sleep_score'])} | {fmt(summary['hrv'], 0)} | "
        f"{fmt(summary['low_hr'], 0)} |"
    )


def bucket_rows(
    rows: list[dict[str, Any]],
    buckets: list[tuple[str, Callable[[dict[str, Any]], bool]]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    return [(label, [row for row in rows if predicate(row)]) for label, predicate in buckets]


def workout_by_day(rows: list[dict[str, str]]) -> dict[date, dict[str, Any]]:
    grouped: dict[date, dict[str, Any]] = defaultdict(lambda: {"minutes": 0.0, "late": False, "count": 0})
    for row in rows:
        start = parse_dt(row.get("start_datetime"))
        end = parse_dt(row.get("end_datetime"))
        day = parse_day(row.get("day")) or (start.date() if start else None)
        if not day or not start or not end:
            continue
        duration = max(0.0, (end - start).total_seconds() / 60)
        if duration < 5:
            continue
        grouped[day]["minutes"] += duration
        grouped[day]["count"] += 1
        if clock_hour(end) is not None and clock_hour(end) >= 20:
            grouped[day]["late"] = True
    return grouped


def naps_by_day(rows: list[dict[str, str]]) -> dict[date, list[dict[str, Any]]]:
    output: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("type") == "long_sleep":
            continue
        start = parse_dt(row.get("bedtime_start"))
        if not start or not (9 <= start.hour < 20):
            continue
        tib_m = (number(row.get("time_in_bed")) or 0) / 60
        sleep_m = (number(row.get("total_sleep_duration")) or 0) / 60
        if 10 <= tib_m <= 180:
            output[start.date()].append(
                {"start": clock_hour(start), "tib_m": tib_m, "sleep_m": sleep_m, "type": row.get("type")}
            )
    return output


def add_rolling_regularity(rows: list[dict[str, Any]]) -> None:
    by_day = {row["day"]: row for row in rows}
    for row in rows:
        values = []
        for offset in range(7):
            value = by_day.get(row["day"] - timedelta(days=offset), {}).get("bedtime")
            if value is not None:
                values.append(value)
        row["bedtime_sd_m"] = statistics.pstdev(values) * 60 if len(values) >= 5 else None


def stream_heartrate_summary(path: Path) -> dict[str, Any]:
    sources: Counter[str] = Counter()
    local_days: Counter[date] = Counter()
    first: datetime | None = None
    last: datetime | None = None
    valid = 0
    by_hour: dict[int, Counter[str]] = defaultdict(Counter)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp = parse_dt(row.get("timestamp"))
            bpm = number(row.get("bpm"))
            if timestamp is None or bpm is None:
                continue
            local = timestamp.astimezone()
            first = timestamp if first is None or timestamp < first else first
            last = timestamp if last is None or timestamp > last else last
            local_days[local.date()] += 1
            sources[row.get("source") or "unknown"] += 1
            by_hour[local.hour][row.get("source") or "unknown"] += 1
            valid += 1
    return {
        "valid": valid,
        "first": first.date().isoformat() if first else "n/a",
        "last": last.date().isoformat() if last else "n/a",
        "days": len(local_days),
        "median_samples": median(local_days.values()),
        "sources": sources,
        "by_hour": by_hour,
    }


def tag_associations(
    rows: list[dict[str, str]], daily: dict[date, dict[str, Any]], cutoff: date
) -> list[tuple[str, int, float | None, float | None, float | None]]:
    grouped: dict[str, set[date]] = defaultdict(set)
    for row in rows:
        day = parse_day(row.get("start_day"))
        tag = row.get("custom_name") or row.get("tag_type_code")
        if day and tag and day >= cutoff - timedelta(days=365):
            grouped[tag].add(day)

    all_rows = [row for day, row in daily.items() if cutoff - timedelta(days=365) <= day <= cutoff]
    baseline_readiness = mean(row.get("readiness") for row in all_rows)
    baseline_sleep = mean(row.get("sleep_h") for row in all_rows)
    output = []
    for tag, days in grouped.items():
        next_rows = [daily[day + timedelta(days=1)] for day in days if day + timedelta(days=1) in daily]
        if len(next_rows) < 5:
            continue
        readiness = mean(row.get("readiness") for row in next_rows)
        sleep_h = mean(row.get("sleep_h") for row in next_rows)
        output.append(
            (
                tag,
                len(next_rows),
                None if readiness is None or baseline_readiness is None else readiness - baseline_readiness,
                None if sleep_h is None or baseline_sleep is None else sleep_h - baseline_sleep,
                median(row.get("bedtime") for row in next_rows),
            )
        )
    return sorted(output, key=lambda item: item[1], reverse=True)


def write_report() -> None:
    daily, tables = build_daily()
    sleep_rows = [row for row in daily.values() if row.get("sleep_h", 0) >= 3]
    if not sleep_rows:
        raise SystemExit("No main sleep records found")
    sleep_rows.sort(key=lambda row: row["day"])
    add_rolling_regularity(sleep_rows)
    latest = sleep_rows[-1]["day"]
    last365 = [row for row in sleep_rows if row["day"] >= latest - timedelta(days=364)]

    workouts = workout_by_day(tables["workout"])
    naps = naps_by_day(tables["sleep"])
    for row in sleep_rows:
        previous_day = row["day"] - timedelta(days=1)
        workout = workouts.get(previous_day, {"minutes": 0.0, "late": False, "count": 0})
        row["prior_workout_m"] = workout["minutes"]
        row["prior_late_workout"] = workout["late"]
        prior_naps = naps.get(previous_day, [])
        row["prior_nap_count"] = len(prior_naps)
        row["prior_nap_tib_m"] = sum(nap["tib_m"] for nap in prior_naps)
        row["prior_nap_start"] = median(nap["start"] for nap in prior_naps)

    lines = [
        "# Oura Cognition-Oriented Review",
        "",
        f"Generated from local Oura CSV files through {latest.isoformat()}.",
        "This report uses sleep and recovery as proxies for conditions that support cognitive work; Oura does not measure cognition directly.",
        "",
        "## Data Audit",
        "",
        "| Table | Rows | First date | Last date |",
        "|---|---:|---|---|",
    ]
    for name, rows in tables.items():
        first, last = table_span(rows)
        lines.append(f"| {name} | {len(rows)} | {first} | {last} |")

    hr_path = CSV_DIR / "heartrate.csv"
    if hr_path.exists():
        hr = stream_heartrate_summary(hr_path)
        source_text = ", ".join(f"{key} {value}" for key, value in hr["sources"].most_common())
        lines.extend(
            [
                "",
                f"Heart-rate audit: {hr['valid']:,} valid samples across {hr['days']} local dates "
                f"({hr['first']} to {hr['last']}), median {fmt(hr['median_samples'], 0)} samples per observed date. "
                f"Sources: {source_text}.",
                "",
                "| Local hour | Rest-labelled share of rest + awake HR samples |",
                "|---:|---:|",
            ]
        )
        for hour in range(8, 24):
            counts = hr["by_hour"][hour]
            denominator = counts["rest"] + counts["awake"]
            share = counts["rest"] / denominator * 100 if denominator else None
            lines.append(f"| {hour:02d}:00 | {fmt(share)}% |")

    lines.extend(
        [
            "",
            "## Time Windows",
            "",
            "Medians are used for timing and physiology; readiness and sleep scores are means.",
            "",
            "| Window | Nights | Sleep | Time in bed | Bedtime | Wake | Readiness | Sleep score | HRV | Lowest HR |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for days in (14, 30, 90, 365):
        rows = [row for row in sleep_rows if row["day"] >= latest - timedelta(days=days - 1)]
        lines.append(summary_line(f"Last {days}d", summarize(rows)))

    recent14 = [row for row in sleep_rows if row["day"] >= latest - timedelta(days=13)]
    previous14 = [
        row
        for row in sleep_rows
        if latest - timedelta(days=27) <= row["day"] <= latest - timedelta(days=14)
    ]
    lines.extend(
        [
            "",
            "## Recent Change",
            "",
            "| Window | Nights | Sleep | Time in bed | Bedtime | Wake | Readiness | Sleep score | HRV | Lowest HR |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            summary_line("Most recent 14d", summarize(recent14)),
            summary_line("Previous 14d", summarize(previous14)),
        ]
    )

    strong = [row for row in last365 if (row.get("readiness") or 0) >= 80 and (row.get("sleep_score") or 0) >= 80]
    strained = [row for row in last365 if (row.get("readiness") or 100) < 70 or (row.get("sleep_score") or 100) < 70]
    lines.extend(
        [
            "",
            "## Strong Versus Strained Mornings",
            "",
            "Strong = readiness and sleep score both at least 80. Strained = either score below 70.",
            "",
            "| Morning group | Nights | Sleep | Time in bed | Bedtime | Wake | Readiness | Sleep score | HRV | Lowest HR |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            summary_line("Strong", summarize(strong)),
            summary_line("Strained", summarize(strained)),
            "",
            "| Morning group | Same-day high stress | Same-day recovery | Same-day steps |",
            "|---|---:|---:|---:|",
        ]
    )
    for label, rows in (("Strong", strong), ("Strained", strained)):
        info = summarize(rows)
        lines.append(
            f"| {label} | {fmt_hours(info['stress_h'])} | {fmt_hours(info['recovery_h'])} | {fmt(info['steps'], 0)} |"
        )

    lines.extend(
        [
            "",
            "## Sleep Structure",
            "",
            "Sleep-stage values are wearable estimates; use trends rather than treating a single night as exact.",
            "",
            "| Group | Nights | Efficiency | Latency | Awake in bed | Deep sleep | REM sleep |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for label, rows in (
        ("Most recent 14d", recent14),
        ("Last 365d", last365),
        ("Strong", strong),
        ("Strained", strained),
    ):
        info = summarize(rows)
        lines.append(
            f"| {label} | {len(rows)} | {fmt(info['efficiency'])}% | {fmt_minutes(info['latency_m'])} | "
            f"{fmt_minutes(info['awake_m'])} | {fmt_hours(info['deep_h'])} | {fmt_hours(info['rem_h'])} |"
        )

    duration_groups = bucket_rows(
        last365,
        [
            ("<6h30", lambda row: row.get("sleep_h", 0) < 6.5),
            ("6h30-7h29", lambda row: 6.5 <= row.get("sleep_h", 0) < 7.5),
            ("7h30-8h29", lambda row: 7.5 <= row.get("sleep_h", 0) < 8.5),
            (">=8h30", lambda row: row.get("sleep_h", 0) >= 8.5),
        ],
    )
    lines.extend(
        [
            "",
            "## Sleep Duration Dose",
            "",
            "| Actual sleep | Nights | Readiness | Sleep score | HRV | Lowest HR | Same-day stress | Same-day recovery |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for label, rows in duration_groups:
        info = summarize(rows)
        lines.append(
            f"| {label} | {len(rows)} | {fmt(info['readiness'])} | {fmt(info['sleep_score'])} | "
            f"{fmt(info['hrv'], 0)} | {fmt(info['low_hr'], 0)} | {fmt_hours(info['stress_h'])} | "
            f"{fmt_hours(info['recovery_h'])} |"
        )

    duration_matched = [row for row in last365 if 7 <= row.get("sleep_h", 0) <= 9]
    bedtime_groups = bucket_rows(
        duration_matched,
        [
            ("Before 00:15", lambda row: row.get("bedtime") is not None and row["bedtime"] < 24.25),
            ("00:15-01:14", lambda row: row.get("bedtime") is not None and 24.25 <= row["bedtime"] < 25.25),
            ("01:15-02:14", lambda row: row.get("bedtime") is not None and 25.25 <= row["bedtime"] < 26.25),
            ("02:15 or later", lambda row: row.get("bedtime") is not None and row["bedtime"] >= 26.25),
        ],
    )
    lines.extend(
        [
            "",
            "## Bedtime With Sleep Duration Held Between 7 and 9 Hours",
            "",
            "| Bedtime | Nights | Sleep | Readiness | Sleep score | HRV | Lowest HR |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for label, rows in bedtime_groups:
        info = summarize(rows)
        lines.append(
            f"| {label} | {len(rows)} | {fmt_hours(info['sleep_h'])} | {fmt(info['readiness'])} | "
            f"{fmt(info['sleep_score'])} | {fmt(info['hrv'], 0)} | {fmt(info['low_hr'], 0)} |"
        )

    regularity_groups = bucket_rows(
        last365,
        [
            ("7d bedtime SD <=45m", lambda row: row.get("bedtime_sd_m") is not None and row["bedtime_sd_m"] <= 45),
            ("45-90m", lambda row: row.get("bedtime_sd_m") is not None and 45 < row["bedtime_sd_m"] <= 90),
            (">90m", lambda row: row.get("bedtime_sd_m") is not None and row["bedtime_sd_m"] > 90),
        ],
    )
    lines.extend(
        [
            "",
            "## Seven-Day Bedtime Regularity",
            "",
            "| Regularity | Nights | Sleep | Readiness | Sleep score | HRV | Lowest HR |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for label, rows in regularity_groups:
        info = summarize(rows)
        lines.append(
            f"| {label} | {len(rows)} | {fmt_hours(info['sleep_h'])} | {fmt(info['readiness'])} | "
            f"{fmt(info['sleep_score'])} | {fmt(info['hrv'], 0)} | {fmt(info['low_hr'], 0)} |"
        )

    workday = [row for row in last365 if row["day"].weekday() < 5]
    weekend = [row for row in last365 if row["day"].weekday() >= 5]
    lines.extend(
        [
            "",
            "## Workday Versus Weekend Wake Days",
            "",
            "| Wake-day type | Nights | Sleep | Bedtime | Wake | Readiness | Sleep score |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for label, rows in (("Mon-Fri", workday), ("Sat-Sun", weekend)):
        info = summarize(rows)
        lines.append(
            f"| {label} | {len(rows)} | {fmt_hours(info['sleep_h'])} | {fmt_clock(info['bedtime'])} | "
            f"{fmt_clock(info['wake'])} | {fmt(info['readiness'])} | {fmt(info['sleep_score'])} |"
        )

    workday_wake = median(row.get("wake") for row in workday)
    weekend_wake = median(row.get("wake") for row in weekend)
    wake_shift = None if workday_wake is None or weekend_wake is None else (weekend_wake - workday_wake) * 60
    lines.append(f"\nMedian weekend wake-time shift: {fmt_minutes(wake_shift)} later than Mon-Fri.")

    prior_step_groups = bucket_rows(
        last365,
        [
            ("<4,000", lambda row: row.get("prior_steps") is not None and row["prior_steps"] < 4000),
            ("4,000-6,999", lambda row: row.get("prior_steps") is not None and 4000 <= row["prior_steps"] < 7000),
            ("7,000-9,999", lambda row: row.get("prior_steps") is not None and 7000 <= row["prior_steps"] < 10000),
            (">=10,000", lambda row: row.get("prior_steps") is not None and row["prior_steps"] >= 10000),
        ],
    )
    lines.extend(
        [
            "",
            "## Previous-Day Steps and Next Morning",
            "",
            "| Previous-day steps | Nights | Next sleep | Next readiness | Next sleep score | Next HRV |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for label, rows in prior_step_groups:
        info = summarize(rows)
        lines.append(
            f"| {label} | {len(rows)} | {fmt_hours(info['sleep_h'])} | {fmt(info['readiness'])} | "
            f"{fmt(info['sleep_score'])} | {fmt(info['hrv'], 0)} |"
        )

    workout_groups = bucket_rows(
        last365,
        [
            ("No recorded workout", lambda row: row.get("prior_workout_m", 0) < 10),
            ("10-29 min", lambda row: 10 <= row.get("prior_workout_m", 0) < 30),
            ("30-59 min", lambda row: 30 <= row.get("prior_workout_m", 0) < 60),
            (">=60 min", lambda row: row.get("prior_workout_m", 0) >= 60),
        ],
    )
    lines.extend(
        [
            "",
            "## Recorded Exercise and Next Morning",
            "",
            "| Previous-day recorded exercise | Nights | Next sleep | Next readiness | Next sleep score | Next HRV |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for label, rows in workout_groups:
        info = summarize(rows)
        lines.append(
            f"| {label} | {len(rows)} | {fmt_hours(info['sleep_h'])} | {fmt(info['readiness'])} | "
            f"{fmt(info['sleep_score'])} | {fmt(info['hrv'], 0)} |"
        )

    activity_counts = Counter(row.get("activity") or "missing" for row in tables["workout"])
    lines.append(
        "\nRecorded activity mix: "
        + ", ".join(f"{activity} {count}" for activity, count in activity_counts.most_common(12))
        + "."
    )

    exercise_days = [row for row in last365 if row.get("prior_workout_m", 0) >= 20]
    early_exercise = [row for row in exercise_days if not row.get("prior_late_workout")]
    late_exercise = [row for row in exercise_days if row.get("prior_late_workout")]
    lines.extend(
        [
            "",
            "| Exercise finish | Nights | Next sleep | Next bedtime | Next readiness | Next HRV |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for label, rows in (("Before 20:00", early_exercise), ("20:00 or later", late_exercise)):
        info = summarize(rows)
        lines.append(
            f"| {label} | {len(rows)} | {fmt_hours(info['sleep_h'])} | {fmt_clock(info['bedtime'])} | "
            f"{fmt(info['readiness'])} | {fmt(info['hrv'], 0)} |"
        )

    nap_days = [row for row in last365 if row.get("prior_nap_count", 0) > 0]
    no_nap_days = [row for row in last365 if row.get("prior_nap_count", 0) == 0]
    short_nap_days = [row for row in nap_days if row.get("prior_nap_tib_m", 0) <= 40]
    long_nap_days = [row for row in nap_days if row.get("prior_nap_tib_m", 0) > 40]
    lines.extend(
        [
            "",
            "## Daytime Rest or Nap and Following Night",
            "",
            "| Previous day | Nights | Following sleep | Following bedtime | Readiness | Sleep score |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for label, rows in (
        ("No detected daytime rest", no_nap_days),
        ("Detected rest <=40 min in bed", short_nap_days),
        ("Detected rest >40 min in bed", long_nap_days),
    ):
        info = summarize(rows)
        lines.append(
            f"| {label} | {len(rows)} | {fmt_hours(info['sleep_h'])} | {fmt_clock(info['bedtime'])} | "
            f"{fmt(info['readiness'])} | {fmt(info['sleep_score'])} |"
        )

    recommendations = Counter(row.get("recommendation") or "missing" for row in tables["sleep_time"])
    statuses = Counter(row.get("status") or "missing" for row in tables["sleep_time"])
    optimal = [row for row in tables["sleep_time"] if number(row.get("optimal_bedtime.start_offset")) is not None]
    start_offsets = [number(row.get("optimal_bedtime.start_offset")) / 3600 for row in optimal]
    end_offsets = [number(row.get("optimal_bedtime.end_offset")) / 3600 for row in optimal]
    lines.extend(
        [
            "",
            "## Oura Bedtime Recommendations",
            "",
            f"- Recommendation counts: {', '.join(f'{key} {value}' for key, value in recommendations.most_common())}.",
            f"- Status counts: {', '.join(f'{key} {value}' for key, value in statuses.most_common())}.",
            f"- Median optimal window where supplied: {fmt_clock(24 + median(start_offsets))} to "
            f"{fmt_clock(24 + median(end_offsets))} (n={len(optimal)}).",
        ]
    )

    tag_rows = tag_associations(tables["enhanced_tag"], daily, latest)
    lines.extend(
        [
            "",
            "## Tag Associations in the Last Year",
            "",
            "These are unadjusted, self-selected observations, not causal effects.",
            "",
            "| Tag | Tagged days | Next readiness vs baseline | Next sleep vs baseline | Next bedtime |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for tag, count, readiness_delta, sleep_delta, bedtime in tag_rows:
        lines.append(
            f"| {tag} | {count} | {fmt(readiness_delta, 1)} | {fmt_minutes(None if sleep_delta is None else sleep_delta * 60)} | "
            f"{fmt_clock(bedtime)} |"
        )

    recent30 = [row for row in sleep_rows if row["day"] >= latest - timedelta(days=29)]
    lines.extend(
        [
            "",
            "## Recent Data-Quality and Health Signals",
            "",
            f"- Main sleep records: {len(sleep_rows)} nights from {sleep_rows[0]['day']} to {latest}.",
            f"- Last-30 median sleep efficiency: {fmt(median(row.get('efficiency') for row in recent30))}%.",
            f"- Last-30 median sleep latency: {fmt_minutes(median(row.get('latency_m') for row in recent30))}.",
            f"- Last-30 median average SpO2: {fmt(median(row.get('spo2') for row in recent30))}%.",
            f"- Last-30 resilience levels: {', '.join(f'{key} {value}' for key, value in Counter(row.get('resilience') or 'missing' for row in recent30).most_common())}.",
            "- VO2 max is unavailable in the API export, so aerobic-fitness conclusions are not supported by this dataset.",
        ]
    )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    write_report()
