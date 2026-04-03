import tempfile
import unittest
from pathlib import Path

from analysis_metrics import METHODOLOGY_VERSION, build_fitness_analysis
from fetch_strava import save_json


def make_run(activity_id: int, start_date: str, *, avg_speed: float, avg_hr: float | None, trainer: bool = False) -> dict:
    return {
        "id": activity_id,
        "type": "Run",
        "start_date": start_date,
        "distance": 10_000,
        "moving_time": 2_400,
        "average_speed": avg_speed,
        "average_heartrate": avg_hr,
        "trainer": trainer,
    }


def make_stream_payload(hr_values: list[float], vel_values: list[float]) -> dict:
    length = min(len(hr_values), len(vel_values))
    times = [0, 480, 540, 600][:length]
    moving = [True] * length
    return {
        "activity_id": 0,
        "fetched_at": "2026-04-03T00:00:00Z",
        "keys_requested": ["heartrate", "time", "distance", "velocity_smooth", "moving"],
        "streams": {
            "heartrate": {"data": hr_values[:length]},
            "velocity_smooth": {"data": vel_values[:length]},
            "time": {"data": times},
            "moving": {"data": moving},
        },
    }


class AnalysisMetricsTests(unittest.TestCase):
    def test_build_fitness_analysis_marks_mixed_source_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            streams_dir = Path(tmpdir)
            activities = [
                make_run(1, "2026-03-03T06:00:00Z", avg_speed=3.0, avg_hr=150),
                make_run(2, "2026-03-04T06:00:00Z", avg_speed=3.05, avg_hr=152),
                make_run(3, "2026-03-05T06:00:00Z", avg_speed=3.0, avg_hr=150),
                make_run(4, "2026-03-06T06:00:00Z", avg_speed=3.1, avg_hr=149),
            ]
            save_json(streams_dir / "1.json", make_stream_payload([145, 148, 150, 152], [3.0, 3.1, 3.1, 3.2]))
            save_json(streams_dir / "2.json", make_stream_payload([142, 144, 146, 150], [3.0, 3.0, 3.1, 3.1]))
            save_json(streams_dir / "4.json", make_stream_payload([130, 132, 133, 134], [3.1, 3.1, 3.1, 3.1]))

            analysis = build_fitness_analysis(activities, streams_dir)

            self.assertEqual(analysis["methodology_version"], METHODOLOGY_VERSION)
            self.assertEqual(analysis["source_policy"]["name"], METHODOLOGY_VERSION)
            self.assertIn("fitness_trend_zones", analysis["series"])
            zone_series = analysis["series"]["fitness_trend_zones"]["hr_141_150"]
            self.assertEqual(zone_series["window_policy"], "mixed_source_allowed")
            point = zone_series["points"][-1]
            self.assertEqual(point["source_mix"]["stream_points"], 2)
            self.assertEqual(point["source_mix"]["summary_points"], 1)
            self.assertEqual(point["source_mix"]["excluded_points"], 1)

    def test_build_fitness_analysis_uses_summary_fallback_when_no_streams_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            streams_dir = Path(tmpdir)
            activities = [
                make_run(11, "2026-03-03T06:00:00Z", avg_speed=3.0, avg_hr=145),
                make_run(12, "2026-03-04T06:00:00Z", avg_speed=3.1, avg_hr=148),
                make_run(13, "2026-03-05T06:00:00Z", avg_speed=3.0, avg_hr=149),
            ]

            analysis = build_fitness_analysis(activities, streams_dir)

            fitness_point = analysis["series"]["fitness_trend_zones"]["hr_141_150"]["points"][-1]
            self.assertEqual(fitness_point["source_mix"]["stream_points"], 0)
            self.assertEqual(fitness_point["source_mix"]["summary_points"], 3)
            self.assertEqual(fitness_point["source_mix"]["excluded_points"], 0)
            self.assertGreater(fitness_point["value"], 0)

    def test_build_fitness_analysis_emits_requested_zone_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            streams_dir = Path(tmpdir)
            activities = [
                make_run(21, "2026-03-03T06:00:00Z", avg_speed=3.0, avg_hr=118),
                make_run(22, "2026-03-04T06:00:00Z", avg_speed=3.0, avg_hr=130),
                make_run(23, "2026-03-05T06:00:00Z", avg_speed=3.0, avg_hr=145),
                make_run(24, "2026-03-06T06:00:00Z", avg_speed=3.0, avg_hr=155),
                make_run(25, "2026-03-07T06:00:00Z", avg_speed=3.0, avg_hr=165),
            ]

            analysis = build_fitness_analysis(activities, streams_dir)

            self.assertEqual(
                set(analysis["series"]["fitness_trend_zones"].keys()),
                {"lt_120", "hr_120_140", "hr_141_150", "hr_151_160", "gt_160"},
            )


if __name__ == "__main__":
    unittest.main()
