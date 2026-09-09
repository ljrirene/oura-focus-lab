#!/usr/bin/env python3
"""Estimate personal NREM-REM cycle durations from local Oura hypnograms."""

from __future__ import annotations

import csv
import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SLEEP_CSV = PROJECT_ROOT / "data" / "csv" / "sleep.csv"
READINESS_CSV = PROJECT_ROOT / "data" / "csv" / "daily_readiness.csv"
DAILY_SLEEP_CSV = PROJECT_ROOT / "data" / "csv" / "daily_sleep.csv"
REPORT_PATH = PROJECT_ROOT / "reports" / "oura_sleep_cycles.md"

STAGE_DEEP = "1"
STAGE_LIGHT = "2"
STAGE_REM = "3"
STAGE_AWAKE = "4"
VALID_STAGES = {STAGE_DEEP, STAGE_LIGHT, STAGE_REM, STAGE_AWAKE}


def read_csv(path: Path) -> list[dict[str, str]]:
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


def fmt(value: float | None, digits: int = 0) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def fmt_minutes(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f} min"


def fmt_hours(value: float | None) -> str:
    if value is None:
        return "n/a"
    total_minutes = round(value * 60)
    return f"{total_minutes // 60}h{total_minutes % 60:02d}m"


def fmt_clock(value: datetime | None) -> str:
    return "n/a" if value is None else value.strftime("%H:%M")


def index_score(path: Path) -> dict[date, float]:
    output: dict[date, float] = {}
    for row in read_csv(path):
        day = parse_day(row.get("day"))
        score = number(row.get("score"))
        if day and score is not None:
            output[day] = score
    return output


def choose_main_sleeps(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[date, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        day = parse_day(row.get("day"))
        if day:
            grouped[day].append(row)

    output = []
    for day, candidates in grouped.items():
        long_sleeps = [row for row in candidates if row.get("type") == "long_sleep"]
        if not long_sleeps:
            continue
        output.append(max(long_sleeps, key=lambda row: number(row.get("total_sleep_duration")) or 0))
    return sorted(output, key=lambda row: row.get("day") or "")


def clean_sequence(value: str | None) -> str:
    if not value:
        return ""
    return "".join(character for character in value.strip() if character in VALID_STAGES)


def sequence_is_aligned(sequence: str, interval_seconds: int, time_in_bed: float | None) -> bool:
    if not sequence or time_in_bed is None:
        return False
    represented = len(sequence) * interval_seconds
    tolerance = max(interval_seconds * 2, time_in_bed * 0.02)
    return abs(represented - time_in_bed) <= tolerance


def stage_runs(sequence: str, stage: str) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(sequence):
        if value == stage and start is None:
            start = index
        elif value != stage and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(sequence)))
    return runs


def merged_rem_episodes(
    sequence: str, interval_seconds: int, max_gap_minutes: float = 15
) -> list[tuple[int, int]]:
    raw = stage_runs(sequence, STAGE_REM)
    if not raw:
        return []
    max_gap = round(max_gap_minutes * 60 / interval_seconds)
    merged = [raw[0]]
    for start, end in raw[1:]:
        previous_start, previous_end = merged[-1]
        gap = sequence[previous_end:start]
        if start - previous_end <= max_gap and STAGE_DEEP not in gap:
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    return merged


def estimate_cycles(
    sequence: str, interval_seconds: int, max_gap_minutes: float = 15
) -> dict[str, Any]:
    if not sequence:
        return {"cycles": [], "onset": None, "sleep_end": None, "tail_m": None}

    non_awake = [index for index, stage in enumerate(sequence) if stage != STAGE_AWAKE]
    if not non_awake:
        return {"cycles": [], "onset": None, "sleep_end": None, "tail_m": None}

    onset = non_awake[0]
    sleep_end = non_awake[-1] + 1
    first_rem_bins = max(1, round(2.5 * 60 / interval_seconds))
    later_rem_bins = max(1, round(5 * 60 / interval_seconds))
    cycles: list[dict[str, float]] = []
    cycle_start = onset

    for rem_start, rem_end in merged_rem_episodes(sequence, interval_seconds, max_gap_minutes):
        if rem_end <= onset:
            continue
        rem_bins = sum(stage == STAGE_REM for stage in sequence[rem_start:rem_end])
        min_rem_bins = first_rem_bins if not cycles else later_rem_bins
        if rem_bins < min_rem_bins:
            continue

        wall_minutes = (rem_end - cycle_start) * interval_seconds / 60
        segment = sequence[cycle_start:rem_end]
        nrem_minutes = sum(stage in {STAGE_DEEP, STAGE_LIGHT} for stage in segment) * interval_seconds / 60
        rem_minutes = sum(stage == STAGE_REM for stage in segment) * interval_seconds / 60
        awake_minutes = sum(stage == STAGE_AWAKE for stage in segment) * interval_seconds / 60

        if wall_minutes < 45:
            continue
        if wall_minutes > 180:
            cycle_start = rem_end
            continue
        min_rem_minutes = 2.5 if not cycles else 5
        if nrem_minutes < 15 or rem_minutes < min_rem_minutes:
            continue

        cycles.append(
            {
                "length_m": wall_minutes,
                "nrem_m": nrem_minutes,
                "rem_m": rem_minutes,
                "awake_m": awake_minutes,
                "start_index": float(cycle_start),
                "end_index": float(rem_end),
            }
        )
        cycle_start = rem_end

    tail_m = None
    if cycles:
        tail_m = (sleep_end - int(cycles[-1]["end_index"])) * interval_seconds / 60
    return {"cycles": cycles, "onset": onset, "sleep_end": sleep_end, "tail_m": tail_m}


def stage_total_error(row: dict[str, str], sequence: str, interval_seconds: int) -> dict[str, float | None]:
    expected = {
        STAGE_DEEP: number(row.get("deep_sleep_duration")),
        STAGE_LIGHT: number(row.get("light_sleep_duration")),
        STAGE_REM: number(row.get("rem_sleep_duration")),
        STAGE_AWAKE: number(row.get("awake_time")),
    }
    output: dict[str, float | None] = {}
    for stage, reported_seconds in expected.items():
        sequence_seconds = sequence.count(stage) * interval_seconds
        output[stage] = None if reported_seconds is None else abs(sequence_seconds - reported_seconds) / 60
    return output


def build_nights() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    readiness = index_score(READINESS_CSV)
    sleep_scores = index_score(DAILY_SLEEP_CSV)
    rows = choose_main_sleeps(read_csv(SLEEP_CSV))
    nights: list[dict[str, Any]] = []
    audit: dict[str, Any] = {
        "main_sleep_rows": len(rows),
        "aligned_30": 0,
        "aligned_5": 0,
        "low_battery": 0,
        "errors_30": defaultdict(list),
        "algorithm_versions": Counter(),
    }

    for row in rows:
        day = parse_day(row.get("day"))
        if not day:
            continue
        time_in_bed = number(row.get("time_in_bed"))
        total_sleep = number(row.get("total_sleep_duration"))
        sequence_30 = clean_sequence(row.get("sleep_phase_30_sec"))
        sequence_5 = clean_sequence(row.get("sleep_phase_5_min"))
        aligned_30 = sequence_is_aligned(sequence_30, 30, time_in_bed)
        aligned_5 = sequence_is_aligned(sequence_5, 300, time_in_bed)
        audit["aligned_30"] += int(aligned_30)
        audit["aligned_5"] += int(aligned_5)
        audit["low_battery"] += int(str(row.get("low_battery_alert")).lower() == "true")
        audit["algorithm_versions"][row.get("sleep_algorithm_version") or "missing"] += 1

        if aligned_30:
            errors = stage_total_error(row, sequence_30, 30)
            for stage, error in errors.items():
                audit["errors_30"][stage].append(error)

        cycles_30 = estimate_cycles(sequence_30, 30) if aligned_30 else {"cycles": [], "tail_m": None}
        cycles_5 = estimate_cycles(sequence_5, 300) if aligned_5 else {"cycles": [], "tail_m": None}
        cycles_gap10 = estimate_cycles(sequence_30, 30, 10) if aligned_30 else {"cycles": []}
        cycles_gap20 = estimate_cycles(sequence_30, 30, 20) if aligned_30 else {"cycles": []}
        primary = cycles_30 if aligned_30 else cycles_5
        resolution = 30 if aligned_30 else 300 if aligned_5 else None
        low_battery = str(row.get("low_battery_alert")).lower() == "true"
        efficiency = number(row.get("efficiency"))
        qualified = (
            resolution is not None
            and not low_battery
            and total_sleep is not None
            and total_sleep >= 5 * 3600
            and efficiency is not None
            and efficiency >= 70
            and len(primary["cycles"]) >= 2
        )
        nights.append(
            {
                "day": day,
                "bedtime_start": parse_dt(row.get("bedtime_start")),
                "bedtime_end": parse_dt(row.get("bedtime_end")),
                "sleep_h": None if total_sleep is None else total_sleep / 3600,
                "tib_h": None if time_in_bed is None else time_in_bed / 3600,
                "efficiency": efficiency,
                "readiness": readiness.get(day),
                "sleep_score": sleep_scores.get(day),
                "cycles_30": cycles_30["cycles"],
                "cycles_5": cycles_5["cycles"],
                "cycles_gap10": cycles_gap10["cycles"],
                "cycles_gap20": cycles_gap20["cycles"],
                "cycles": primary["cycles"],
                "tail_m": primary.get("tail_m"),
                "resolution": resolution,
                "qualified": qualified,
            }
        )
    return nights, audit


def cycles_from(nights: list[dict[str, Any]], key: str = "cycles") -> list[dict[str, float]]:
    return [cycle for night in nights for cycle in night[key]]


def group_summary(nights: list[dict[str, Any]], key: str = "cycles") -> dict[str, Any]:
    cycles = cycles_from(nights, key)
    lengths = [cycle["length_m"] for cycle in cycles]
    nightly_medians = [median(cycle["length_m"] for cycle in night[key]) for night in nights if night[key]]
    return {
        "nights": len(nights),
        "cycles": len(cycles),
        "cycle_median": median(lengths),
        "cycle_p25": percentile(lengths, 0.25),
        "cycle_p75": percentile(lengths, 0.75),
        "cycle_p10": percentile(lengths, 0.10),
        "cycle_p90": percentile(lengths, 0.90),
        "nightly_median": median(nightly_medians),
        "cycles_per_night": median(len(night[key]) for night in nights),
        "tail_m": median(night.get("tail_m") for night in nights),
        "sleep_h": median(night.get("sleep_h") for night in nights),
        "tib_h": median(night.get("tib_h") for night in nights),
    }


def summary_row(label: str, info: dict[str, Any]) -> str:
    return (
        f"| {label} | {info['nights']} | {info['cycles']} | {fmt_minutes(info['nightly_median'])} | "
        f"{fmt_minutes(info['cycle_p25'])}-{fmt_minutes(info['cycle_p75'])} | "
        f"{fmt(info['cycles_per_night'], 1)} | {fmt_hours(info['sleep_h'])} | {fmt_hours(info['tib_h'])} |"
    )


def write_report() -> None:
    nights, audit = build_nights()
    qualified = [night for night in nights if night["qualified"]]
    if not qualified:
        raise SystemExit("No qualified main-sleep hypnograms found")
    latest = max(night["day"] for night in qualified)
    last365 = [night for night in qualified if night["day"] >= latest - timedelta(days=364)]
    recent90 = [night for night in qualified if night["day"] >= latest - timedelta(days=89)]
    strong = [
        night
        for night in last365
        if (night.get("readiness") or 0) >= 80 and (night.get("sleep_score") or 0) >= 80
    ]
    lower = [
        night
        for night in last365
        if (night.get("readiness") or 100) < 70 or (night.get("sleep_score") or 100) < 70
    ]

    report = [
        "# Personal Oura Sleep-Cycle Estimate",
        "",
        f"Data through {latest.isoformat()}.",
        "A cycle is estimated as an NREM episode followed by a valid REM episode. REM fragments separated by no more than 15 minutes without deep sleep are merged. Cycles shorter than 45 minutes, longer than 180 minutes, or without at least 15 minutes of NREM are excluded. The first REM episode may be shorter than later REM episodes.",
        "",
        "## Data Quality",
        "",
        f"- Main sleep records: {audit['main_sleep_rows']}.",
        f"- 30-second sequences aligned to time in bed: {audit['aligned_30']}.",
        f"- 5-minute sequences aligned to time in bed: {audit['aligned_5']}.",
        f"- Qualified nights after sleep-duration, efficiency, battery, and cycle-count filters: {len(qualified)}.",
        f"- Low-battery main sleep records: {audit['low_battery']}.",
        f"- Sleep algorithm versions: {', '.join(f'{key} {value}' for key, value in audit['algorithm_versions'].most_common())}.",
        "",
        "Median absolute difference between 30-second sequence totals and reported stage totals:",
        "",
        "| Stage | Difference |",
        "|---|---:|",
        f"| Deep | {fmt_minutes(median(audit['errors_30'][STAGE_DEEP]))} |",
        f"| Light | {fmt_minutes(median(audit['errors_30'][STAGE_LIGHT]))} |",
        f"| REM | {fmt_minutes(median(audit['errors_30'][STAGE_REM]))} |",
        f"| Awake | {fmt_minutes(median(audit['errors_30'][STAGE_AWAKE]))} |",
        "",
        "## Personal Cycle Range",
        "",
        "The nightly median gives each night equal weight. The middle 50% range is calculated from all accepted cycles.",
        "",
        "| Group | Nights | Cycles | Night-level typical cycle | Middle 50% of cycles | Complete cycles/night | Sleep | Time in bed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        summary_row("All qualified", group_summary(qualified)),
        summary_row("Last 365d", group_summary(last365)),
        summary_row("Recent 90d", group_summary(recent90)),
        summary_row("Strong recovery", group_summary(strong)),
        summary_row("Lower recovery", group_summary(lower)),
        "",
        "## Cycle Position Within the Night",
        "",
        "| Cycle position | All nights n | All median | All middle 50% | Strong nights n | Strong median | Strong middle 50% |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    for position in range(1, 7):
        all_values = [night["cycles"][position - 1]["length_m"] for night in qualified if len(night["cycles"]) >= position]
        strong_values = [night["cycles"][position - 1]["length_m"] for night in strong if len(night["cycles"]) >= position]
        if not all_values:
            continue
        report.append(
            f"| {position} | {len(all_values)} | {fmt_minutes(median(all_values))} | "
            f"{fmt_minutes(percentile(all_values, 0.25))}-{fmt_minutes(percentile(all_values, 0.75))} | "
            f"{len(strong_values)} | {fmt_minutes(median(strong_values))} | "
            f"{fmt_minutes(percentile(strong_values, 0.25))}-{fmt_minutes(percentile(strong_values, 0.75))} |"
        )

    count_all = Counter(len(night["cycles"]) for night in qualified)
    count_strong = Counter(len(night["cycles"]) for night in strong)
    report.extend(
        [
            "",
            "## Complete Cycles per Night",
            "",
            "| Complete cycles | All qualified nights | Strong-recovery nights |",
            "|---:|---:|---:|",
        ]
    )
    for count in sorted(set(count_all) | set(count_strong)):
        report.append(f"| {count} | {count_all[count]} | {count_strong[count]} |")

    all_30 = [night for night in qualified if night["cycles_30"]]
    all_5 = [night for night in qualified if night["cycles_5"]]
    summary_30 = group_summary(all_30, "cycles_30")
    summary_5 = group_summary(all_5, "cycles_5")
    summary_gap10 = group_summary(all_30, "cycles_gap10")
    summary_gap20 = group_summary(all_30, "cycles_gap20")
    report.extend(
        [
            "",
            "## Resolution Sensitivity",
            "",
            "| Sequence | Nights | Accepted cycles | Night-level typical cycle | Middle 50% |",
            "|---|---:|---:|---:|---:|",
            f"| 30-second | {summary_30['nights']} | {summary_30['cycles']} | {fmt_minutes(summary_30['nightly_median'])} | {fmt_minutes(summary_30['cycle_p25'])}-{fmt_minutes(summary_30['cycle_p75'])} |",
            f"| 5-minute | {summary_5['nights']} | {summary_5['cycles']} | {fmt_minutes(summary_5['nightly_median'])} | {fmt_minutes(summary_5['cycle_p25'])}-{fmt_minutes(summary_5['cycle_p75'])} |",
            "",
            "| REM-fragment merge rule | Nights | Accepted cycles | Night-level typical cycle | Middle 50% |",
            "|---|---:|---:|---:|---:|",
            f"| 10-minute gap | {summary_gap10['nights']} | {summary_gap10['cycles']} | {fmt_minutes(summary_gap10['nightly_median'])} | {fmt_minutes(summary_gap10['cycle_p25'])}-{fmt_minutes(summary_gap10['cycle_p75'])} |",
            f"| 15-minute gap | {summary_30['nights']} | {summary_30['cycles']} | {fmt_minutes(summary_30['nightly_median'])} | {fmt_minutes(summary_30['cycle_p25'])}-{fmt_minutes(summary_30['cycle_p75'])} |",
            f"| 20-minute gap | {summary_gap20['nights']} | {summary_gap20['cycles']} | {fmt_minutes(summary_gap20['nightly_median'])} | {fmt_minutes(summary_gap20['cycle_p25'])}-{fmt_minutes(summary_gap20['cycle_p75'])} |",
            "",
            "## Wake-Timing Observation",
            "",
            f"- Median time from the end of the last detected complete REM-ending cycle to final sleep end: {fmt_minutes(median(night.get('tail_m') for night in qualified))}.",
            f"- On strong-recovery nights: {fmt_minutes(median(night.get('tail_m') for night in strong))}.",
            "- This remainder varies by night, so a fixed alarm cannot reliably target a cycle boundary from historical averages.",
            "",
            "## Sources and Limits",
            "",
            "- Oura stage codes: 1 deep, 2 light, 3 REM, 4 awake.",
            "- Oura hypnograms and cycle lengths are wearable estimates, not polysomnography.",
            "- The cycle detector is intentionally conservative and reports a range rather than one exact biological constant.",
        ]
    )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    write_report()
