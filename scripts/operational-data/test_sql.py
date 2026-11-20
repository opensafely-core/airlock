import csv  # noqa: INP001
import json
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT_DIR = Path(__file__).parent


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE local_db_requestmetadata (
                id TEXT PRIMARY KEY, author TEXT, status TEXT, created_at TEXT);
            CREATE TABLE local_db_auditlog (
                id INTEGER PRIMARY KEY, user TEXT, request TEXT, type TEXT, created_at TEXT);
            CREATE TABLE local_db_requestfilemetadata (
                id INTEGER PRIMARY KEY, request_id TEXT, job_id TEXT,
                filetype TEXT, relpath TEXT, size INTEGER);
        """)

    def request(
        self, rid="A", created="2024-05-08", status="APPROVED", author="author"
    ):
        self.db.execute(
            "INSERT INTO local_db_requestmetadata VALUES (?, ?, ?, ?)",
            (rid, author, status, created),
        )

    def event(self, rid="A", kind="REQUEST_SUBMIT", user="author", time="2024-06-01"):
        self.db.execute(
            "INSERT INTO local_db_auditlog (user, request, type, created_at) VALUES (?, ?, ?, ?)",
            (user, rid, kind, time),
        )

    def file(self, path, rid="A", kind="OUTPUT", size=42, job="job"):
        self.db.execute(
            "INSERT INTO local_db_requestfilemetadata (request_id, job_id, filetype, relpath, size) VALUES (?, ?, ?, ?, ?)",
            (rid, job, kind, path, size),
        )

    def summary(self):
        self.db.executescript(
            (SCRIPT_DIR / "1_create_temp_requests_table.sql").read_text()
        )

    def extract(self, filename):
        return [
            dict(row) for row in self.db.execute((SCRIPT_DIR / filename).read_text())
        ]

    def test_summary_cutoff_submissions_counts_and_earliest_completion(self):
        """Request summaries enforce cohort eligibility and calculate counts and first completion dates."""
        for rid, created in [
            ("before", "2024-05-07T23:59:59.999"),
            ("boundary", "2024-05-08T01:00:00+01:00"),
            ("after", "2024-05-09"),
            ("draft", "2024-05-09"),
        ]:
            self.request(rid, created)
            if rid != "draft":
                self.event(rid)
        self.event("boundary")
        self.event("missing-request")
        self.event(None)
        for kind in ("APPROVE", "REJECT", "WITHDRAW"):
            for date in ("2024-06-03", "2024-06-02"):
                self.event("boundary", "REQUEST_" + kind, time=date)
        self.file("one.csv", "boundary")
        self.file("two.csv", "boundary")
        self.file("support.csv", "boundary", "SUPPORTING")
        self.summary()
        rows = self.extract("2_extract_requests.sql")
        self.assertEqual([row["request_id"] for row in rows], ["after", "boundary"])
        self.assertEqual(
            rows[0],
            {
                "request_id": "after",
                "num_submission_rounds": 1,
                "num_files": 0,
                "status": "APPROVED",
                "created_at": "2024-05-09",
                "approved_at": None,
                "rejected_at": None,
                "withdrawn_at": None,
            },
        )
        self.assertEqual(rows[1]["num_submission_rounds"], 2)
        self.assertEqual(rows[1]["num_files"], 2)
        for field in ("approved_at", "rejected_at", "withdrawn_at"):
            self.assertEqual(rows[1][field], "2024-06-02")

    def test_checker_actions_classify_all_events_without_duplicates(self):
        """Each qualifying checker action assigns a consistent role without multiplying events."""
        self.request()
        self.event()
        actions = [
            "REQUEST_REVIEW",
            "REQUEST_FILE_APPROVE",
            "REQUEST_FILE_REQUEST_CHANGES",
            "REQUEST_FILE_DOWNLOAD",
            "REQUEST_RETURN",
            "REQUEST_EARLY_RETURN",
            "REQUEST_APPROVE",
            "REQUEST_REJECT",
        ]
        for action in actions:
            self.event(kind="VIEW", user=action, time="2024-05-31")
            self.event(kind=action, user=action)
            self.event(kind=action, user=action)
        self.event(kind="REQUEST_APPROVE", user="author")
        self.event(kind="VIEW", user="observer")
        self.summary()
        rows = self.extract("3_extract_events.sql")
        self.assertEqual(len(rows), 3 * len(actions) + 3)
        for row in rows:
            expected = (
                "researcher"
                if row["user_id"] == "author"
                else ("checker" if row["user_id"] in actions else None)
            )
            self.assertEqual(row["user_role"], expected)

    def test_event_scope_roles_and_chronological_tie_order(self):
        """Eligible users retain outside activity with correct roles and stable chronological ordering."""
        self.request()
        self.request("outside")  # No submission, so outside the cohort.
        self.event(time="2024-06-01T10:00:00+01:00")
        self.event(None, "VIEW", time="2024-06-01T09:00:00Z")
        self.event("outside", "REQUEST_APPROVE", time="2024-06-01T08:59:00Z")
        self.event("A", "VIEW", user="", time="2024-06-01")
        self.event("A", "VIEW", user=None, time="2024-06-01")
        self.event("outside", "VIEW", user="unrelated", time="2024-06-01")
        self.summary()
        rows = self.extract("3_extract_events.sql")
        self.assertEqual([row["request_id"] for row in rows], ["outside", "A", None])
        self.assertEqual(
            [row["user_role"] for row in rows], ["researcher", "researcher", None]
        )
        self.assertEqual({row["user_id"] for row in rows}, {"author"})

    def test_formats_case_insensitivity_filtering_and_null_metadata(self):
        """File extraction classifies extensions and preserves missing metadata while filtering ineligible files."""
        self.request()
        self.event()
        formats = {
            "csv": "csv",
            "jpg": "jpeg",
            "jpeg": "jpeg",
            "png": "png",
            "svg": "svg",
            "svgz": "svg",
            "html": "html",
            "txt": "text",
            "log": "log",
            "json": "json",
            "md": "markdown",
        }
        expected = []
        for extension, label in formats.items():
            for suffix in (extension, extension.upper()):
                self.file("nested/path/file." + suffix)
                expected.append(label)
        for path in (
            None,
            "",
            "no-extension",
            "csv",
            "file.csv.bak",
            "file.csv/child",
            "file.pdf",
        ):
            self.file(path, size=None, job=None)
            expected.append("other_or_unknown")
        self.file("ignored.csv", kind="SUPPORTING")
        self.file("ignored.csv", rid="outside")
        self.summary()
        rows = self.extract("4_extract_files.sql")
        self.assertEqual([row["format"] for row in rows], expected)
        self.assertTrue(all(row["request_id"] == "A" for row in rows))
        self.assertEqual(
            rows[0],
            {"request_id": "A", "job_id": "job", "format": "csv", "size_bytes": 42},
        )
        self.assertIsNone(rows[-1]["job_id"])
        self.assertIsNone(rows[-1]["size_bytes"])

    def test_empty_database_extracts_nothing(self):
        """An empty snapshot yields no requests, events or files."""
        self.summary()
        for filename in (
            "2_extract_requests.sql",
            "3_extract_events.sql",
            "4_extract_files.sql",
        ):
            with self.subTest(filename=filename):
                self.assertEqual(self.extract(filename), [])

    @unittest.skipUnless(
        shutil.which("sqlite3"), "sqlite3 CLI required for orchestration test"
    )
    def test_main_sql_runs_on_readonly_snapshot_and_preserves_database(self):
        """The SQL entry point exports the expected files without changing its source snapshot."""
        self.request()
        self.event()
        self.file('folder/研究,"quoted".CSV', size=0)
        self.db.commit()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot.sqlite3"
            with sqlite3.connect(snapshot) as disk:
                self.db.backup(disk)
            original = snapshot.read_bytes()
            for source in SCRIPT_DIR.glob("*.sql"):
                shutil.copy(source, root)
            (root / "csvs-to-export").mkdir()
            result = subprocess.run(
                ["sqlite3", "-readonly", str(snapshot)],
                input=(root / "0_main.sql").read_text(),
                cwd=root,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stderr, "")
            self.assertEqual(snapshot.read_bytes(), original)
            requests = json.loads((root / "requests.json").read_text())
            events = json.loads((root / "events.json").read_text())
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["num_files"], 1)
            self.assertEqual(
                events,
                [
                    {
                        "user_id": "author",
                        "request_id": "A",
                        "occurred_at": "2024-06-01",
                        "user_role": "researcher",
                    }
                ],
            )
            with (root / "csvs-to-export/files.csv").open(newline="") as stream:
                self.assertEqual(
                    list(csv.DictReader(stream)),
                    [
                        {
                            "request_id": "A",
                            "job_id": "job",
                            "format": "csv",
                            "size_bytes": "0",
                        }
                    ],
                )


if __name__ == "__main__":
    unittest.main()
