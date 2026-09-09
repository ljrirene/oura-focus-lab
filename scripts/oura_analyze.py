#!/usr/bin/env python3
"""Create a first-pass Markdown report from local Oura CSV exports."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV_DIR = PROJECT_ROOT / "data" / "csv"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "oura_overview.md"

DAILY_FILES = [
    "daily_activity",
    "daily_readiness",
    "daily_sleep",
    "daily_spo2",
    "daily_stress",
    "daily_resilience",
    "daily_cardiovascular_age",
    "vO2_max",
    "sleep",
]

KEY_METRICS = [
    ("daily_readiness.score", "Readiness score"),
    ("daily_sleep.score", "Sleep score"),
    ("daily_activity.score", "Activity score"),
    ("daily_activity.steps", "Steps"),
    ("sleep.total_sleep_duration", "Total sleep seconds"),
    ("sleep.efficiency", "Sleep efficiency"),
    ("sleep.average_hrv", "Average HRV"),
    ("sleep.lowest_heart_rate", "Lowest sleeping HR"),
    ("daily_readiness.temperature_deviation", "Temperature deviation"),
    ("daily_stress.stress_high", "High stress seconds"),
    ("daily_stress.recovery_high", "High recovery seconds"),
    ("daily_spo2.spo2_percentage.average", "Average SpO2"),
    ("daily_cardiovascular_age.vascular_age", "Vascular age"),
    ("vO2_max.vo2_max", "VO2 max"),
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def day_for_row(row: dict[str, str]) -> date | None:
    for key in ("day", "timestamp", "bedtime_start", "bedtime_end", "start_datetime", "start"):
        parsed = parse_date(row.get(key))
        if parsed:
            return parsed
    return None


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    if len(text) > 32:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    if abs(number) > 10_000_000:
        return None
    return number


def mean(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 14:
        return None
    x_vals = [pair[0] for pair in pairs]
    y_vals = [pair[1] for pair in pairs]
    x_mean = sum(x_vals) / len(x_vals)
    y_mean = sum(y_vals) / len(y_vals)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_den = math.sqrt(sum((x - x_mean) ** 2 for x in x_vals))
    y_den = math.sqrt(sum((y - y_mean) ** 2 for y in y_vals))
    if x_den == 0 or y_den == 0:
        return None
    return numerator / (x_den * y_den)


def format_number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def load_daily_table(csv_dir: Path) -> tuple[dict[date, dict[str, str]], dict[str, int]]:
    by_day: dict[date, dict[str, str]] = defaultdict(dict)
    counts: dict[str, int] = {}
    for table in DAILY_FILES:
        rows = read_csv(csv_dir / f"{table}.csv")
        counts[table] = len(rows)
        if table == "sleep":
            best_by_day: dict[date, dict[str, str]] = {}
            for row in rows:
                day = day_for_row(row)
                if not day:
                    continue
                duration = to_float(row.get("total_sleep_duration")) or 0
                current_duration = to_float(best_by_day.get(day, {}).get("total_sleep_duration")) or -1
                if duration > current_duration:
                    best_by_day[day] = row
            rows = list(best_by_day.values())
        for row in rows:
            day = day_for_row(row)
            if not day:
                continue
            for key, value in row.items():
                by_day[day][f"{table}.{key}"] = value
    return dict(sorted(by_day.items())), counts


def values_for_range(
    by_day: dict[date, dict[str, str]], metric: str, start: date, end: date
) -> list[float]:
    values: list[float] = []
    cursor = start
    while cursor <= end:
        values.append(to_float(by_day.get(cursor, {}).get(metric)))
        cursor += timedelta(days=1)
    return values


def metric_summary(by_day: dict[date, dict[str, str]], metric: str, today: date) -> tuple[float | None, float | None, float | None]:
    last_14 = values_for_range(by_day, metric, today - timedelta(days=13), today)
    prev_14 = values_for_range(by_day, metric, today - timedelta(days=27), today - timedelta(days=14))
    last_30 = values_for_range(by_day, metric, today - timedelta(days=29), today)
    return mean(last_14), mean(prev_14), mean(last_30)


def correlation_rows(by_day: dict[date, dict[str, str]], target: str) -> list[tuple[str, float, int]]:
    metrics = sorted({key for row in by_day.values() for key in row})
    target_values: list[float] = []
    metric_values: dict[str, list[float]] = {metric: [] for metric in metrics if metric != target}
    for day in sorted(by_day):
        row = by_day[day]
        target_values.append(to_float(row.get(target)))
        for metric in metric_values:
            metric_values[metric].append(to_float(row.get(metric)))

    output: list[tuple[str, float, int]] = []
    for metric, values in metric_values.items():
        corr = pearson(values, target_values)
        if corr is None:
            continue
        count = sum(1 for x, y in zip(values, target_values) if x is not None and y is not None)
        output.append((metric, corr, count))
    return sorted(output, key=lambda item: abs(item[1]), reverse=True)


def write_report(csv_dir: Path, report_path: Path) -> None:
    by_day, counts = load_daily_table(csv_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Oura Health Overview")
    lines.append("")
    lines.append("This is an exploratory self-analysis report, not medical advice.")
    lines.append("")

    if not by_day:
        lines.append("No daily CSV data found yet. Run `scripts/oura_sync.py sync` first.")
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    first_day = min(by_day)
    last_day = max(by_day)
    observed_days = len(by_day)
    calendar_days = (last_day - first_day).days + 1
    coverage = observed_days / calendar_days * 100 if calendar_days else 0
    lines.append("## Coverage")
    lines.append("")
    lines.append(f"- Date span: {first_day.isoformat()} to {last_day.isoformat()}")
    lines.append(f"- Days with at least one daily record: {observed_days}/{calendar_days} ({coverage:.1f}%)")
    for table, count in sorted(counts.items()):
        lines.append(f"- {table}: {count} rows")
    lines.append("")

    lines.append("## Recent Metrics")
    lines.append("")
    lines.append("| Metric | Last 14d | Previous 14d | Last 30d | 14d change |")
    lines.append("|---|---:|---:|---:|---:|")
    for metric, label in KEY_METRICS:
        last_14, prev_14, last_30 = metric_summary(by_day, metric, last_day)
        change = None if last_14 is None or prev_14 is None else last_14 - prev_14
        lines.append(
            f"| {label} | {format_number(last_14)} | {format_number(prev_14)} | "
            f"{format_number(last_30)} | {format_number(change)} |"
        )
    lines.append("")

    for target, label in [
        ("daily_readiness.score", "Readiness score"),
        ("daily_sleep.score", "Sleep score"),
    ]:
        rows = correlation_rows(by_day, target)
        lines.append(f"## Correlations With {label}")
        lines.append("")
        if not rows:
            lines.append("Not enough numeric overlap yet. Correlations require at least 14 paired days.")
            lines.append("")
            continue
        lines.append("| Metric | r | Paired days |")
        lines.append("|---|---:|---:|")
        for metric, corr, count in rows[:12]:
            lines.append(f"| {metric} | {corr:.2f} | {count} |")
        lines.append("")

    missing_days = []
    cursor = first_day
    while cursor <= last_day:
        if cursor not in by_day:
            missing_days.append(cursor)
        cursor += timedelta(days=1)
    if missing_days:
        sample = ", ".join(day.isoformat() for day in missing_days[:20])
        suffix = "..." if len(missing_days) > 20 else ""
        lines.append("## Missing Days")
        lines.append("")
        lines.append(f"{len(missing_days)} days have no daily records: {sample}{suffix}")
        lines.append("")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a first-pass Oura self-analysis report.")
    parser.add_argument("--csv-dir", type=Path, default=DEFAULT_CSV_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    write_report(args.csv_dir, args.report)
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
