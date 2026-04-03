import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fetch_strava


class StreamBackfillTests(unittest.TestCase):
    def test_select_stream_backfill_candidates_respects_zero_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            streams_dir = Path(tmpdir)
            activities = [
                {"id": 100, "type": "Run", "average_heartrate": 150, "start_date": "2026-01-10T10:00:00Z"},
            ]

            selected = fetch_strava.select_stream_backfill_candidates(activities, 0, streams_dir)

            self.assertEqual(selected, [])

    def test_select_stream_backfill_candidates_prefers_recent_run_rows_with_hr(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            streams_dir = Path(tmpdir)
            fetch_strava.save_json(fetch_strava.activity_stream_path(101, streams_dir), {"cached": True})
            activities = [
                {"id": 101, "type": "Run", "average_heartrate": 150, "start_date": "2026-01-10T10:00:00Z"},
                {"id": 102, "type": "Ride", "average_heartrate": 140, "start_date": "2026-01-09T10:00:00Z"},
                {"id": 103, "type": "Run", "average_heartrate": None, "max_heartrate": None, "start_date": "2026-01-08T10:00:00Z"},
                {"id": 104, "type": "Run", "average_heartrate": 148, "start_date": "2026-01-07T10:00:00Z"},
                {"id": 105, "type": "Run", "max_heartrate": 170, "start_date": "2026-01-06T10:00:00Z"},
            ]

            selected = fetch_strava.select_stream_backfill_candidates(activities, 2, streams_dir)

            self.assertEqual([act["id"] for act in selected], [104, 105])

    def test_cache_missing_activity_streams_writes_only_missing_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            streams_dir = Path(tmpdir)
            activities = [
                {"id": 201, "type": "Run", "average_heartrate": 151, "start_date": "2026-01-10T10:00:00Z"},
                {"id": 202, "type": "Run", "max_heartrate": 175, "start_date": "2026-01-09T10:00:00Z"},
                {"id": 203, "type": "Run", "average_heartrate": None, "max_heartrate": None, "start_date": "2026-01-08T10:00:00Z"},
            ]

            with mock.patch.object(
                fetch_strava,
                "fetch_activity_streams",
                side_effect=[
                    {"heartrate": {"data": [145, 146]}},
                    {"heartrate": {"data": [150, 151]}},
                ],
            ) as mocked_fetch:
                result = fetch_strava.cache_missing_activity_streams(
                    activities,
                    "token",
                    limit=100,
                    streams_dir=streams_dir,
                )

            self.assertEqual(result["requested"], 2)
            self.assertEqual(result["cached"], 2)
            self.assertEqual(result["stopped_for_rate_limit"], 0)
            self.assertEqual(mocked_fetch.call_count, 2)
            self.assertTrue(fetch_strava.activity_stream_path(201, streams_dir).exists())
            self.assertTrue(fetch_strava.activity_stream_path(202, streams_dir).exists())
            self.assertFalse(fetch_strava.activity_stream_path(203, streams_dir).exists())

    def test_cache_missing_activity_streams_stops_on_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            streams_dir = Path(tmpdir)
            activities = [
                {"id": 301, "type": "Run", "average_heartrate": 151, "start_date": "2026-01-10T10:00:00Z"},
                {"id": 302, "type": "Run", "average_heartrate": 152, "start_date": "2026-01-09T10:00:00Z"},
            ]

            with mock.patch.object(
                fetch_strava,
                "fetch_activity_streams",
                side_effect=[
                    {"heartrate": {"data": [145, 146]}},
                    RuntimeError("Rate limited while fetching activity streams (429 Too Many Requests)."),
                ],
            ):
                result = fetch_strava.cache_missing_activity_streams(
                    activities,
                    "token",
                    limit=100,
                    streams_dir=streams_dir,
                )

            self.assertEqual(result["requested"], 2)
            self.assertEqual(result["cached"], 1)
            self.assertEqual(result["stopped_for_rate_limit"], 1)
            self.assertTrue(fetch_strava.activity_stream_path(301, streams_dir).exists())
            self.assertFalse(fetch_strava.activity_stream_path(302, streams_dir).exists())

    def test_cache_activity_stream_by_id_writes_single_stream_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            streams_dir = Path(tmpdir)
            with mock.patch.object(
                fetch_strava,
                "fetch_activity_streams",
                return_value={"heartrate": {"data": [140, 142]}},
            ) as mocked_fetch:
                stream_path = fetch_strava.cache_activity_stream_by_id(401, "token", streams_dir)

            self.assertEqual(stream_path, fetch_strava.activity_stream_path(401, streams_dir))
            self.assertTrue(stream_path.exists())
            with stream_path.open() as fh:
                payload = __import__("json").load(fh)
            self.assertEqual(payload["activity_id"], 401)
            self.assertIn("streams", payload)
            self.assertIn("heartrate", payload["streams"])
            mocked_fetch.assert_called_once_with("token", 401)


if __name__ == "__main__":
    unittest.main()
