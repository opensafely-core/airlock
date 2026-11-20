import csv  # noqa: INP001
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import process
from process import aggregate, elapsed, main, read_json


class AggregateTests(unittest.TestCase):
    def aggregate_events(self, events, include_nearby=True):
        data = [
            {
                "user_id": user,
                "request_id": request,
                "user_role": role,
                "occurred_at": f"2024-06-01T10:{minute:02d}:00",
            }
            for minute, request, user, role in events
        ]
        with patch("process.read_json", return_value=data):
            return aggregate([{"request_id": "A"}, {"request_id": "B"}], include_nearby)

    def test_leading_and_trailing_activity(self):
        """Nearby activity before and after a request extends its session only when enabled.

        Example: Nearby events at minutes 0, 4 and 12 around A at minute 8 give
        12 minutes with nearby activity enabled, versus 0 request-only minutes.
        """
        events = [(0, None), (4, None), (8, "A"), (12, None)]
        events = [(minute, request, "u", "checker") for minute, request in events]
        for nearby, minutes in [(False, 0), (True, 12)]:
            totals, minimums = self.aggregate_events(events, nearby)
            for threshold in (5, 10, 15):
                self.assertAlmostEqual(totals["A", "checker", threshold], minutes)
                self.assertAlmostEqual(
                    minimums["A", "checker", threshold], max(minutes, 5)
                )

    def test_switch_does_not_assign_activity_twice(self):
        """Switching requests keeps nearby activity attached to exactly one request.

        Example: A at minute 0, nearby at 4, B at 8 and nearby at 12 give
        4 minutes to each request, with no overlap.
        """
        events = [(0, "A"), (4, None), (8, "B"), (12, None)]
        totals, _ = self.aggregate_events(
            [(minute, request, "u", "checker") for minute, request in events]
        )
        for request in ("A", "B"):
            self.assertAlmostEqual(totals[request, "checker", 5], 4)

    def test_non_cohort_request_retains_nearby_activity(self):
        """Activity on excluded requests cannot leak into the exported cohort.

        Example: A at minute 0, nearby at 4, an excluded request at 8, nearby
        at 12 and B at 16 give A 4 minutes and B 0 minutes.
        """
        events = [(0, "A"), (4, None), (8, "outside"), (12, None), (16, "B")]
        totals, _ = self.aggregate_events(
            [(minute, request, "u", "checker") for minute, request in events]
        )
        self.assertAlmostEqual(totals["A", "checker", 5], 4)
        self.assertEqual(totals["B", "checker", 5], 0)
        self.assertFalse(any(key[0] == "outside" for key in totals))

    def test_timeout_users_roles_and_unassigned_activity(self):
        """Session expiry respects each user, threshold and inferred role.

        Example: For a five-minute threshold, nearby activity at minute 10
        starts a new run before B at 14, giving B 4 unclassified minutes.
        At a ten-minute threshold it stays with A, leaving B 0 minutes.
        """
        totals, minimums = self.aggregate_events(
            [
                (0, "A", "u1", "checker"),
                (10, None, "u1", None),
                (11, None, "u2", None),
                (14, "B", "u1", None),
                (30, None, "u1", None),
            ]
        )
        self.assertEqual(totals["A", "checker", 5], 0)
        self.assertAlmostEqual(totals["A", "checker", 10], 10)
        self.assertAlmostEqual(totals["B", "unclassified", 5], 4)
        self.assertEqual(totals["B", "unclassified", 10], 0)
        self.assertAlmostEqual(minimums["B", "unclassified", 5], 5)
        self.assertFalse(
            any(key[1] == "unclassified" and key[0] == "A" for key in totals)
        )


class AggregateEdgeCaseTests(unittest.TestCase):
    def run_events(self, events, nearby=False, requests=("A", "B")):
        data = [
            {
                "occurred_at": (
                    datetime(2024, 6, 1) + timedelta(seconds=seconds)
                ).isoformat(),
                "request_id": request,
                "user_id": user,
                "user_role": role,
            }
            for seconds, request, user, role in events
        ]
        with patch("process.read_json", return_value=data):
            return aggregate([{"request_id": rid} for rid in requests], nearby)

    def test_empty_events_cohort_and_unassigned_events(self):
        """Empty inputs and activity with no eligible request produce no totals.

        Example: Two nearby events a minute apart with no following request
        produce empty totals, even when nearby activity is enabled.
        """
        for events, requests in [
            ([], ("A",)),
            ([(0, "A", "u", "checker")], ()),
            ([(0, None, "u", None), (60, None, "u", None)], ("A",)),
        ]:
            for nearby in (False, True):
                with self.subTest(events=events, requests=requests, nearby=nearby):
                    self.assertEqual(
                        self.run_events(events, nearby, requests), ({}, {})
                    )

    def test_threshold_boundaries_to_the_microsecond(self):
        """A gap joins a session exactly up to and including the threshold.

        Example: Two A events exactly five minutes apart give 5 minutes at
        the five-minute threshold; one microsecond more gives 0 minutes
        and two separate five-minute minimums.
        """
        for threshold in (5, 10, 15):
            for delta in (-0.000001, 0, 0.000001):
                seconds = threshold * 60 + delta
                with self.subTest(threshold=threshold, delta=delta):
                    totals, minimums = self.run_events(
                        [(0, "A", "u", "checker"), (seconds, "A", "u", "checker")]
                    )
                    key = "A", "checker", threshold
                    self.assertAlmostEqual(
                        totals[key], seconds / 60 if delta <= 0 else 0
                    )
                    self.assertAlmostEqual(
                        minimums[key], max(5, seconds / 60) if delta <= 0 else 10
                    )

    def test_minimum_applies_per_session_and_per_user(self):
        """Each user session receives its own minimum before totals are combined.

        Example: At a ten-minute threshold, two one-minute sessions from
        different users plus a later seven-minute session give 9 actual
        minutes and 5 + 5 + 7 = 17 minutes after applying minimums.
        """
        totals, minimums = self.run_events(
            [
                (0, "A", "u1", "checker"),
                (60, "A", "u1", "checker"),
                (60, "A", "u2", "checker"),
                (120, "A", "u2", "checker"),
                (1800, "A", "u1", "checker"),
                (2220, "A", "u1", "checker"),
            ]
        )
        for threshold, actual, minimum in [(5, 2, 20), (10, 9, 17), (15, 9, 17)]:
            self.assertEqual(totals["A", "checker", threshold], actual)
            self.assertEqual(minimums["A", "checker", threshold], minimum)

    def test_long_session_uses_consecutive_gaps(self):
        """Frequent activity can sustain a session longer than the gap threshold.

        Example: Events every five minutes from minute 0 through minute 60
        form one 60-minute session, even at the five-minute threshold.
        """
        totals, minimums = self.run_events(
            [(minute * 60, "A", "u", "researcher") for minute in range(0, 61, 5)]
        )
        for threshold in (5, 10, 15):
            self.assertEqual(totals["A", "researcher", threshold], 60)
            self.assertEqual(minimums["A", "researcher", threshold], 60)

    def test_switching_back_creates_separate_sessions(self):
        """Returning to a previous request starts a separate session and minimum.

        Example: A at minute 0, B at 1, then A at 2 and 3 give A 1 actual
        minute across two sessions, or 10 minutes with minimums.
        """
        totals, minimums = self.run_events(
            [
                (0, "A", "u", "checker"),
                (60, "B", "u", "researcher"),
                (120, "A", "u", "checker"),
                (180, "A", "u", "checker"),
            ]
        )
        for threshold in (5, 10, 15):
            self.assertEqual(totals["A", "checker", threshold], 1)
            self.assertEqual(minimums["A", "checker", threshold], 10)
            self.assertEqual(totals["B", "researcher", threshold], 0)
            self.assertEqual(minimums["B", "researcher", threshold], 5)

    def test_expired_nearby_run_is_not_assigned_to_next_request(self):
        """Unassigned activity that expires before a request is discarded.

        Example: Nearby events at minutes 0 and 1 expire before A at 30.
        A and a nearby event at 31 contribute only 1 actual minute.
        """
        totals, minimums = self.run_events(
            [
                (0, None, "u", None),
                (60, None, "u", None),
                (1800, "A", "u", "researcher"),
                (1860, None, "u", None),
            ],
            True,
        )
        for threshold in (5, 10, 15):
            self.assertEqual(totals["A", "researcher", threshold], 1)
            self.assertEqual(minimums["A", "researcher", threshold], 5)
        self.assertEqual({key[1] for key in totals}, {"researcher"})

    def test_nearby_events_cannot_bridge_timeout_in_request_only_mode(self):
        """Ignoring nearby events can split activity that otherwise forms one session.

        Example: A at minutes 0 and 10 with a nearby event at 5 gives
        0 request-only minutes but 10 including-nearby minutes at
        the five-minute threshold.
        """
        events = [
            (0, "A", "u", "checker"),
            (300, None, "u", None),
            (600, "A", "u", "checker"),
        ]
        totals, minimums = self.run_events(events)
        self.assertEqual(totals["A", "checker", 5], 0)
        self.assertEqual(minimums["A", "checker", 5], 10)
        totals, _ = self.run_events(events, True)
        self.assertEqual(totals["A", "checker", 5], 10)

    def test_interleaved_users_do_not_interrupt_each_other(self):
        """Concurrent activity from different users is aggregated independently.

        Example: User 1 visits A at minutes 0 and 2 while user 2 visits B
        at minutes 1 and 3; each contributes 2 minutes to their request.
        """
        totals, _ = self.run_events(
            [
                (0, "A", "u1", "researcher"),
                (60, "B", "u2", "checker"),
                (120, "A", "u1", "researcher"),
                (180, "B", "u2", "checker"),
            ]
        )
        for threshold in (5, 10, 15):
            self.assertEqual(totals["A", "researcher", threshold], 2)
            self.assertEqual(totals["B", "checker", threshold], 2)

    def test_null_and_empty_roles_are_unclassified(self):
        """Missing or empty roles contribute to unclassified activity.

        Example: Two A events 30 seconds apart with a null or empty role
        give 0.5 unclassified minutes, or 5 minutes with the minimum.
        """
        for role in (None, ""):
            with self.subTest(role=role):
                totals, minimums = self.run_events(
                    [(0, "A", "u", role), (30, "A", "u", role)]
                )
                for threshold in (5, 10, 15):
                    self.assertEqual(totals["A", "unclassified", threshold], 0.5)
                    self.assertEqual(minimums["A", "unclassified", threshold], 5)

    def test_duplicate_timestamps_do_not_duplicate_time_or_minimum(self):
        """Repeated simultaneous events count as one session with no elapsed time.

        Example: Three A events at the same instant give 0 actual minutes
        and a single five-minute minimum.
        """
        totals, minimums = self.run_events([(0, "A", "u", "checker")] * 3)
        for threshold in (5, 10, 15):
            self.assertEqual(totals["A", "checker", threshold], 0)
            self.assertEqual(minimums["A", "checker", threshold], 5)

    def test_backwards_timestamp_starts_new_session(self):
        """An event earlier than the active session ends that session safely.

        Example: A events received in minute order 2, 1, 3 give a singleton
        session followed by a two-minute session: 2 actual minutes
        and 10 minutes with minimums.
        """
        totals, minimums = self.run_events(
            [
                (120, "A", "u", "checker"),
                (60, "A", "u", "checker"),
                (180, "A", "u", "checker"),
            ]
        )
        for threshold in (5, 10, 15):
            self.assertEqual(totals["A", "checker", threshold], 2)
            self.assertEqual(minimums["A", "checker", threshold], 10)


class ReadJsonTests(unittest.TestCase):
    def test_empty_whitespace_and_utf8_json(self):
        """JSON loading accepts empty exports, whitespace and Unicode content."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for content, expected in [
                ("", []),
                (" \n\t", []),
                ("[]", []),
                ('[{"name": "研究"}]', [{"name": "研究"}]),
            ]:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    self.assertEqual(read_json(path), expected)

    def test_missing_and_malformed_files_fail(self):
        """Missing files and invalid JSON raise their underlying input errors."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            with self.assertRaises(FileNotFoundError):
                read_json(path)
            path.write_text("[{", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                read_json(path)


class ElapsedTests(unittest.TestCase):
    def test_missing_reversed_equal_fractional_and_timezone_dates(self):
        """Elapsed hours handle missing values, date boundaries and timezone offsets."""
        cases = [
            (None, None, None),
            (None, "invalid", None),
            ("invalid", None, None),
            ("2024-01-02", "2024-01-01", None),
            ("2024-01-01", "2024-01-01", 0),
            ("2024-02-28T23:30:00", "2024-03-01T00:00:00", 24.5),
            ("2024-01-01T00:00:00.000000", "2024-01-01T00:00:00.500000", 0.5 / 3600),
            ("2024-01-01T10:00:00+01:00", "2024-01-01T10:30:00Z", 1.5),
            ("2024-01-01T10:00:00Z", "2024-01-01T10:30:00+01:00", None),
        ]
        for start, end, expected in cases:
            with self.subTest(start=start, end=end):
                self.assertEqual(elapsed(start, end), expected)

    def test_malformed_dates_and_mixed_timezone_awareness_fail(self):
        """Invalid or incompatible timestamps raise errors instead of yielding a duration."""
        with self.assertRaises(ValueError):
            elapsed("bad", "2024-01-01")
        with self.assertRaises(TypeError):
            elapsed("2024-01-01", "2024-01-01T00:00:00Z")


class MainTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.requests = root / "requests.json"
        self.events = root / "events.json"
        self.output = root / "requests.csv"
        for name, path in [
            ("REQUESTS_JSON", self.requests),
            ("EVENTS_JSON", self.events),
            ("OUTPUT_CSV", self.output),
        ]:
            patcher = patch.object(process, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.events.write_text("[]", encoding="utf-8")

    def request(self, rid="A", status="APPROVED", **changes):
        return dict(
            request_id=rid,
            status=status,
            num_files=0,
            num_submission_rounds=2,
            created_at="2024-01-01T00:00:00",
            approved_at="2024-01-01T01:00:00",
            rejected_at="2024-01-01T02:00:00",
            withdrawn_at="2024-01-01T03:00:00",
            **changes,
        )

    def run_main(self, requests):
        self.requests.write_text(json.dumps(requests), encoding="utf-8")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            main()
        with self.output.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            return reader.fieldnames, rows, stdout.getvalue()

    def test_all_completion_statuses_and_csv_schema(self):
        """CSV exports map statuses correctly and include every activity column in order."""
        statuses = [
            "APPROVED",
            "RELEASED",
            "REJECTED",
            "WITHDRAWN",
            "PENDING",
            "UNKNOWN",
        ]
        fields, rows, output = self.run_main(
            [self.request(str(i), status) for i, status in enumerate(statuses)]
        )
        activity = [
            f"{role}_minutes_{gap}m_{mode}{suffix}"
            for mode in ("request_only", "including_nearby")
            for role in ("researcher", "checker", "unclassified")
            for gap in (5, 10, 15)
            for suffix in ("", "_min_session_5m")
        ]
        self.assertEqual(
            fields,
            ["request_id", "num_files", "num_submission_rounds"]
            + activity
            + ["hours_to_completion", "completion_status"],
        )
        self.assertEqual(
            [row["completion_status"] for row in rows],
            [
                "approved",
                "approved",
                "rejected",
                "withdrawn",
                "unresolved",
                "unresolved",
            ],
        )
        self.assertEqual(
            [row["hours_to_completion"] for row in rows],
            ["1.0", "1.0", "2.0", "3.0", "", ""],
        )
        for row in rows:
            self.assertEqual(row["num_files"], "0")
            self.assertEqual(row["num_submission_rounds"], "2")
            self.assertTrue(all(float(row[column]) == 0 for column in activity))
        self.assertEqual(output, "Processed 6 requests, skipped 0 bad requests.\n")

    def test_missing_or_negative_completion_times_are_skipped(self):
        """Completed requests with unusable durations are omitted and counted as skipped."""
        requests = []
        for status, field in [
            ("APPROVED", "approved_at"),
            ("RELEASED", "approved_at"),
            ("REJECTED", "rejected_at"),
            ("WITHDRAWN", "withdrawn_at"),
        ]:
            for invalid in (None, "2023-12-31"):
                request = self.request(status=status)
                request[field] = invalid
                requests.append(request)
            request = self.request(status=status)
            request["created_at"] = None
            requests.append(request)
        requests.append(self.request("valid"))
        _, rows, output = self.run_main(requests)
        self.assertEqual([row["request_id"] for row in rows], ["valid"])
        self.assertEqual(output, "Processed 1 requests, skipped 12 bad requests.\n")

    def test_empty_input_writes_header(self):
        """An empty request export still produces a CSV with its complete header."""
        fields, rows, output = self.run_main([])
        self.assertEqual(len(fields), 41)
        self.assertEqual(rows, [])
        self.assertEqual(output, "Processed 0 requests, skipped 0 bad requests.\n")

    def test_real_events_reach_correct_csv_columns_and_ids_are_escaped(self):
        """Events populate both activity estimates and unusual identifiers survive CSV export."""
        rid = '研究,"quoted"\nrequest'
        self.events.write_text(
            json.dumps(
                [
                    {
                        "user_id": "u",
                        "request_id": request,
                        "user_role": role,
                        "occurred_at": time,
                    }
                    for request, role, time in [
                        (None, None, "2024-01-01T23:58:00Z"),
                        (rid, "researcher", "2024-01-02T00:00:00Z"),
                        (rid, "researcher", "2024-01-02T00:01:00Z"),
                        (None, None, "2024-01-02T00:02:00Z"),
                    ]
                ]
            ),
            encoding="utf-8",
        )
        _, rows, _ = self.run_main([self.request(rid), self.request("no-events")])
        self.assertEqual(rows[0]["request_id"], rid)
        for gap in (5, 10, 15):
            for mode, minutes in [("request_only", 1), ("including_nearby", 4)]:
                column = f"researcher_minutes_{gap}m_{mode}"
                self.assertEqual(float(rows[0][column]), minutes)
                self.assertEqual(float(rows[0][column + "_min_session_5m"]), 5)
                self.assertEqual(float(rows[1][column + "_min_session_5m"]), 0)

    def test_command_line_entry_point_uses_script_directory(self):
        """Running the script elsewhere reads and writes files relative to the script."""
        script = self.output.parent / "process.py"
        script.write_text(Path(process.__file__).read_text(), encoding="utf-8")
        self.requests.write_text(json.dumps([self.request()]), encoding="utf-8")
        export = self.output.parent / "csvs-to-export"
        export.mkdir()
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=export,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(
            result.stdout, "Processed 1 requests, skipped 0 bad requests.\n"
        )
        self.assertEqual(result.stderr, "")
        with (export / "requests.csv").open(newline="", encoding="utf-8") as stream:
            self.assertEqual(
                [row["request_id"] for row in csv.DictReader(stream)], ["A"]
            )

    def test_existing_output_is_never_overwritten(self):
        """Exporting refuses to replace an existing CSV and preserves its contents."""
        self.output.write_text("existing data", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.run_main([])
        self.assertEqual(self.output.read_text(encoding="utf-8"), "existing data")


if __name__ == "__main__":
    unittest.main()
