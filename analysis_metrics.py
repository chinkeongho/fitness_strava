from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Dict, Iterable, List, Optional

METHODOLOGY_VERSION = "stream_preferred_summary_fallback_v1"
SOURCE_POLICY_NAME = "stream_preferred_summary_fallback_v1"
COMPARABILITY_RULE_ID = "run_only_non_trainer_speed_gt_0_moving_20_to_90_min_distance_3_to_20_km"

MIN_DISTANCE_M = 3_000
MAX_DISTANCE_M = 20_000
MIN_MOVING_TIME_S = 20 * 60
MAX_MOVING_TIME_S = 90 * 60

EFFICIENCY_HR_MIN = 120
EFFICIENCY_HR_MAX = 185
STREAM_WARMUP_SECONDS = 8 * 60

FITNESS_WINDOW_DAYS = 42
EFFICIENCY_WINDOW_DAYS = 28
WINDOW_STEP_DAYS = 7
MIN_RUNS_PER_WINDOW = 3
WINDOW_POLICY = "mixed_source_allowed"

FITNESS_ZONES = [
    {"id": "lt_120", "label": "<120", "min_hr": None, "max_hr": 119},
    {"id": "hr_120_140", "label": "120-140", "min_hr": 120, "max_hr": 140},
    {"id": "hr_141_150", "label": "141-150", "min_hr": 141, "max_hr": 150},
    {"id": "hr_151_160", "label": "151-160", "min_hr": 151, "max_hr": 160},
    {"id": "gt_160", "label": ">160", "min_hr": 161, "max_hr": None},
]


@dataclass
class SeriesValue:
    value: Optional[float]
    source: Optional[str]
    exclusion_reason: Optional[str]


@dataclass
class RunAnalysis:
    activity_id: int
    start_date: date
    comparable: bool
    non_comparable_reason: Optional[str]
    has_stream_source: bool
    has_summary_source: bool
    fitness_by_zone: Dict[str, SeriesValue]
    efficiency: SeriesValue


def parse_activity_date(activity: Dict[str, Any]) -> Optional[date]:
    raw = activity.get("start_date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def median_or_none(values: Iterable[float]) -> Optional[float]:
    values_list = list(values)
    if not values_list:
        return None
    return float(median(values_list))


def activity_is_comparable(activity: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    if activity.get("type") != "Run":
        return False, "not_run"
    if bool(activity.get("trainer")):
        return False, "trainer"
    avg_speed = activity.get("average_speed") or 0
    if avg_speed <= 0:
        return False, "invalid_average_speed"
    moving_time = activity.get("moving_time") or 0
    if moving_time < MIN_MOVING_TIME_S or moving_time > MAX_MOVING_TIME_S:
        return False, "moving_time_out_of_range"
    distance = activity.get("distance") or 0
    if distance < MIN_DISTANCE_M or distance > MAX_DISTANCE_M:
        return False, "distance_out_of_range"
    if parse_activity_date(activity) is None:
        return False, "invalid_start_date"
    return True, None


def stream_payload_path(streams_dir: Path, activity_id: int) -> Path:
    return streams_dir / f"{activity_id}.json"


def load_stream_payload(streams_dir: Path, activity_id: int) -> Optional[Dict[str, Any]]:
    path = stream_payload_path(streams_dir, activity_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def has_stream_source(stream_payload: Optional[Dict[str, Any]]) -> bool:
    if not stream_payload:
        return False
    streams = stream_payload.get("streams") or {}
    required = ("heartrate", "velocity_smooth", "time", "moving")
    return all(isinstance(streams.get(name), dict) and isinstance(streams[name].get("data"), list) for name in required)


def has_summary_source(activity: Dict[str, Any]) -> bool:
    return activity.get("average_heartrate") is not None and (activity.get("average_speed") or 0) > 0


def iter_stream_samples(stream_payload: Dict[str, Any]) -> Iterable[tuple[float, float, float, bool]]:
    streams = stream_payload.get("streams") or {}
    hr = streams["heartrate"]["data"]
    vel = streams["velocity_smooth"]["data"]
    time_s = streams["time"]["data"]
    moving = streams["moving"]["data"]
    limit = min(len(hr), len(vel), len(time_s), len(moving))
    for idx in range(limit):
        try:
            yield float(hr[idx]), float(vel[idx]), float(time_s[idx]), bool(moving[idx])
        except (TypeError, ValueError):
            continue


def hr_in_zone(hr: float, zone: Dict[str, Any]) -> bool:
    minimum = zone.get("min_hr")
    maximum = zone.get("max_hr")
    if minimum is not None and hr < minimum:
        return False
    if maximum is not None and hr > maximum:
        return False
    return True


def compute_stream_fitness_metric(stream_payload: Dict[str, Any], zone: Dict[str, Any]) -> SeriesValue:
    values: List[float] = []
    for hr, velocity, elapsed, moving in iter_stream_samples(stream_payload):
        if not moving or elapsed < STREAM_WARMUP_SECONDS or velocity <= 0 or hr <= 0:
            continue
        if not hr_in_zone(hr, zone):
            continue
        values.append(velocity * 60)
    value = median_or_none(values)
    if value is None:
        return SeriesValue(None, "stream", f"stream_{zone['id']}_band_empty")
    return SeriesValue(value, "stream", None)


def compute_stream_efficiency_metric(stream_payload: Dict[str, Any]) -> SeriesValue:
    values: List[float] = []
    for hr, velocity, elapsed, moving in iter_stream_samples(stream_payload):
        if not moving or elapsed < STREAM_WARMUP_SECONDS or velocity <= 0 or hr <= 0:
            continue
        if hr < EFFICIENCY_HR_MIN or hr > EFFICIENCY_HR_MAX:
            continue
        values.append((velocity * 60) / hr)
    value = median_or_none(values)
    if value is None:
        return SeriesValue(None, "stream", "stream_efficiency_band_empty")
    return SeriesValue(value, "stream", None)


def compute_summary_fitness_metric(activity: Dict[str, Any], zone: Dict[str, Any]) -> SeriesValue:
    avg_hr = activity.get("average_heartrate")
    avg_speed = activity.get("average_speed") or 0
    if avg_hr is None or avg_speed <= 0:
        return SeriesValue(None, None, "summary_source_missing")
    if not hr_in_zone(avg_hr, zone):
        return SeriesValue(None, "summary", f"summary_{zone['id']}_band_miss")
    return SeriesValue(avg_speed * 60, "summary", None)


def compute_summary_efficiency_metric(activity: Dict[str, Any]) -> SeriesValue:
    avg_hr = activity.get("average_heartrate")
    avg_speed = activity.get("average_speed") or 0
    if avg_hr is None or avg_speed <= 0:
        return SeriesValue(None, None, "summary_source_missing")
    if avg_hr < EFFICIENCY_HR_MIN or avg_hr > EFFICIENCY_HR_MAX:
        return SeriesValue(None, "summary", "summary_efficiency_band_miss")
    return SeriesValue((avg_speed * 60) / avg_hr, "summary", None)


def analyze_run(activity: Dict[str, Any], streams_dir: Path) -> Optional[RunAnalysis]:
    activity_id = activity.get("id")
    activity_date = parse_activity_date(activity)
    if activity_id is None or activity_date is None:
        return None

    comparable, reason = activity_is_comparable(activity)
    stream_payload = load_stream_payload(streams_dir, int(activity_id))
    stream_source = has_stream_source(stream_payload)
    summary_source = has_summary_source(activity)

    if not comparable:
        empty = SeriesValue(None, None, reason)
        return RunAnalysis(
            activity_id=int(activity_id),
            start_date=activity_date,
            comparable=False,
            non_comparable_reason=reason,
            has_stream_source=stream_source,
            has_summary_source=summary_source,
            fitness_by_zone={zone["id"]: empty for zone in FITNESS_ZONES},
            efficiency=empty,
        )

    if stream_source:
        fitness_by_zone = {zone["id"]: compute_stream_fitness_metric(stream_payload, zone) for zone in FITNESS_ZONES}
        efficiency = compute_stream_efficiency_metric(stream_payload)
    elif summary_source:
        fitness_by_zone = {zone["id"]: compute_summary_fitness_metric(activity, zone) for zone in FITNESS_ZONES}
        efficiency = compute_summary_efficiency_metric(activity)
    else:
        missing = SeriesValue(None, None, "no_valid_source")
        fitness_by_zone = {zone["id"]: missing for zone in FITNESS_ZONES}
        efficiency = missing

    return RunAnalysis(
        activity_id=int(activity_id),
        start_date=activity_date,
        comparable=True,
        non_comparable_reason=None,
        has_stream_source=stream_source,
        has_summary_source=summary_source,
        fitness_by_zone=fitness_by_zone,
        efficiency=efficiency,
    )


def align_to_week_end(day: date) -> date:
    days_until_sunday = (6 - day.weekday()) % 7
    return day + timedelta(days=days_until_sunday)


def build_series_points(
    analyses: List[RunAnalysis],
    selector: Callable[[RunAnalysis], SeriesValue],
    window_days: int,
    unit: str,
) -> List[Dict[str, Any]]:
    comparable_runs = [a for a in analyses if a.comparable]
    if not comparable_runs:
        return []
    first_end = align_to_week_end(min(a.start_date for a in comparable_runs))
    last_end = align_to_week_end(max(a.start_date for a in comparable_runs))

    points: List[Dict[str, Any]] = []
    current_end = first_end
    while current_end <= last_end:
        window_start = current_end - timedelta(days=window_days - 1)
        window_runs = [a for a in comparable_runs if window_start <= a.start_date <= current_end]
        series_values = []
        stream_points = 0
        summary_points = 0
        for item in window_runs:
            series_value = selector(item)
            if series_value.value is None:
                continue
            series_values.append(series_value.value)
            if series_value.source == "stream":
                stream_points += 1
            elif series_value.source == "summary":
                summary_points += 1
        included_runs = len(series_values)
        excluded_points = len(window_runs) - included_runs
        if included_runs >= MIN_RUNS_PER_WINDOW:
            points.append(
                {
                    "window_start": window_start.isoformat(),
                    "window_end": current_end.isoformat(),
                    "value": round(float(median(series_values)), 4),
                    "unit": unit,
                    "included_runs": included_runs,
                    "source_mix": {
                        "stream_points": stream_points,
                        "summary_points": summary_points,
                        "excluded_points": excluded_points,
                    },
                }
            )
        current_end += timedelta(days=WINDOW_STEP_DAYS)
    return points


def build_coverage(activities: List[Dict[str, Any]], analyses: List[RunAnalysis]) -> Dict[str, Any]:
    total_runs = sum(1 for act in activities if act.get("type") == "Run")
    comparable_runs = [a for a in analyses if a.comparable]
    stream_backed_runs = sum(1 for a in comparable_runs if a.has_stream_source)
    summary_fallback_runs = sum(1 for a in comparable_runs if not a.has_stream_source and a.has_summary_source)
    excluded_no_valid_source = sum(1 for a in comparable_runs if not a.has_stream_source and not a.has_summary_source)
    return {
        "run_count_total": total_runs,
        "run_count_comparable": len(comparable_runs),
        "stream_backed_runs": stream_backed_runs,
        "summary_fallback_runs": summary_fallback_runs,
        "excluded_runs": excluded_no_valid_source + (total_runs - len(comparable_runs)),
        "excluded_reasons": {
            "non_comparable": total_runs - len(comparable_runs),
            "no_valid_source": excluded_no_valid_source,
        },
        "run_count_stream_source": stream_backed_runs,
        "run_count_summary_source": summary_fallback_runs,
    }


def build_fitness_analysis(
    activities: List[Dict[str, Any]],
    streams_dir: Path,
    generated_at: Optional[Any] = None,
) -> Dict[str, Any]:
    analyses = [analysis for analysis in (analyze_run(act, streams_dir) for act in activities) if analysis is not None]
    if generated_at is None:
        generated_at_value = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    elif isinstance(generated_at, str):
        generated_at_value = generated_at
    else:
        generated_at_value = generated_at.isoformat().replace("+00:00", "Z")
    fitness_zone_series = {}
    for zone in FITNESS_ZONES:
        fitness_zone_series[zone["id"]] = {
            "label": zone["label"],
            "window_policy": WINDOW_POLICY,
            "window_days": FITNESS_WINDOW_DAYS,
            "metric": "zone_speed_m_per_min",
            "unit": "m_per_min",
            "minimum_runs": MIN_RUNS_PER_WINDOW,
            "points": build_series_points(
                analyses,
                lambda analysis, zone_id=zone["id"]: analysis.fitness_by_zone[zone_id],
                FITNESS_WINDOW_DAYS,
                "m_per_min",
            ),
        }
    efficiency_points = build_series_points(
        analyses,
        lambda analysis: analysis.efficiency,
        EFFICIENCY_WINDOW_DAYS,
        "m_per_min_per_bpm",
    )

    return {
        "generated_at": generated_at_value,
        "methodology_version": METHODOLOGY_VERSION,
        "source_policy": {
            "name": SOURCE_POLICY_NAME,
            "window_policy": WINDOW_POLICY,
            "precedence": [
                "stream_with_required_keys",
                "summary_fallback_with_average_hr_and_average_speed",
                "exclude_if_no_valid_source",
            ],
        },
        "comparability_rule": {
            "id": COMPARABILITY_RULE_ID,
            "distance_m": [MIN_DISTANCE_M, MAX_DISTANCE_M],
            "moving_time_s": [MIN_MOVING_TIME_S, MAX_MOVING_TIME_S],
            "trainer": False,
            "requires_positive_average_speed": True,
        },
        "coverage": build_coverage(activities, analyses),
        "series": {
            "fitness_trend_zones": fitness_zone_series,
            "efficiency_at_hr_trend": {
                "window_policy": WINDOW_POLICY,
                "window_days": EFFICIENCY_WINDOW_DAYS,
                "metric": "speed_per_beat_m_per_min_per_bpm",
                "unit": "m_per_min_per_bpm",
                "minimum_runs": MIN_RUNS_PER_WINDOW,
                "points": efficiency_points,
            },
        },
        "caveats": [
            "Detailed streams are preferred when cached; summary fallback is used only under the versioned source policy.",
            "Source mix can vary across windows, so compare trend changes together with source composition.",
            "For fitness trend, stream-backed runs use only segments inside the selected heart-rate zone after minute 8; summary fallback uses whole-run average HR.",
            "Runs outside the comparability rule are excluded from longitudinal analysis.",
        ],
    }
