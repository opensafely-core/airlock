import csv  # noqa: INP001
import shutil
import sqlite3
import subprocess
import sys
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory


SCRIPT_DIR = Path(__file__).parent
DAY = "2024-06-03"
IMAGE_FORMATS = {"jpeg", "png", "svg"}
JOB_CATEGORIES = {"job-measures": "measures", "job-r": "R"}


def stamp(clock):
    return f"{DAY} {clock}.000000"


# (request_id, final status, created_at, [(relpath, job_id)])
REQUESTS = [
    (
        "csv-heavy",
        "APPROVED",
        "09:00:00",
        [
            ("a.csv", "job-measures"),
            ("b.csv", "job-measures"),
            ("c.csv", "job-measures"),
            ("plot.png", "job-r"),
        ],
    ),
    (
        "image-heavy",
        "APPROVED",
        "13:00:00",
        [
            ("table.csv", "job-r"),
            ("one.png", "job-r"),
            ("two.PNG", "job-r"),
            ("three.jpg", "job-r"),
        ],
    ),
    ("no-files", "WITHDRAWN", "18:00:00", []),
    # Approved, but with no approval event, so its completion time is unusable.
    ("bad-completion", "APPROVED", "19:00:00", [("bad.csv", "job-r")]),
]

# (user, request, type, clock)
EVENTS = [
    ("res1", "csv-heavy", "REQUEST_SUBMIT", "09:00:00"),
    ("res1", "csv-heavy", "REQUEST_FILE_VIEW", "09:02:00"),
    ("chk1", "csv-heavy", "REQUEST_FILE_VIEW", "10:00:00"),
    ("chk1", "csv-heavy", "REQUEST_FILE_APPROVE", "10:04:00"),
    ("chk1", "csv-heavy", "REQUEST_REVIEW", "10:08:00"),
    ("chk2", "csv-heavy", "REQUEST_FILE_VIEW", "11:00:00"),
    ("chk2", "csv-heavy", "REQUEST_REVIEW", "11:02:00"),
    ("chk1", "csv-heavy", "REQUEST_APPROVE", "12:00:00"),
    ("res1", "image-heavy", "REQUEST_SUBMIT", "13:00:00"),
    ("res1", "image-heavy", "REQUEST_FILE_VIEW", "13:03:00"),
    ("chk1", "image-heavy", "REQUEST_FILE_VIEW", "14:00:00"),
    ("chk1", "image-heavy", "REQUEST_FILE_VIEW", "14:05:00"),
    ("chk1", "image-heavy", "REQUEST_FILE_VIEW", "14:10:00"),
    ("chk1", "image-heavy", "REQUEST_REVIEW", "14:15:00"),
    ("chk2", "image-heavy", "REQUEST_FILE_VIEW", "15:00:00"),
    ("chk2", "image-heavy", "REQUEST_FILE_APPROVE", "15:05:00"),
    ("chk2", "image-heavy", "REQUEST_REVIEW", "15:10:00"),
    ("chk1", "image-heavy", "REQUEST_APPROVE", "17:00:00"),
    ("res1", "no-files", "REQUEST_SUBMIT", "18:00:00"),
    ("res1", "no-files", "REQUEST_WITHDRAW", "18:30:00"),
    ("res1", "bad-completion", "REQUEST_SUBMIT", "19:00:00"),
]


def build_snapshot(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE local_db_requestmetadata (
            id TEXT PRIMARY KEY, author TEXT, status TEXT, created_at TEXT);
        CREATE TABLE local_db_auditlog (
            id INTEGER PRIMARY KEY, user TEXT, request TEXT, type TEXT, created_at TEXT);
        CREATE TABLE local_db_requestfilemetadata (
            id INTEGER PRIMARY KEY, request_id TEXT, job_id TEXT,
            filetype TEXT, relpath TEXT, size INTEGER);
    """)
    for rid, status, created, files in REQUESTS:
        db.execute(
            "INSERT INTO local_db_requestmetadata VALUES (?, 'res1', ?, ?)",
            (rid, status, stamp(created)),
        )
        for relpath, job_id in files:
            db.execute(
                "INSERT INTO local_db_requestfilemetadata "
                "(request_id, job_id, filetype, relpath, size) VALUES (?, ?, 'OUTPUT', ?, 10)",
                (rid, job_id, relpath),
            )
    for user, rid, kind, clock in EVENTS:
        db.execute(
            "INSERT INTO local_db_auditlog (user, request, type, created_at) VALUES (?, ?, ?, ?)",
            (user, rid, kind, stamp(clock)),
        )
    db.commit()
    db.close()


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


@unittest.skipUnless(shutil.which("sqlite3"), "sqlite3 CLI required")
class AnalysisContractTests(unittest.TestCase):
    """Run the real pipeline once, then use files.csv and requests.csv together.

    Job categories (e.g. "measures", "R") are assigned outside the secure
    environment, so they are supplied here as a job_id -> category mapping.
    """

    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        root = Path(directory.name)
        snapshot = root / "snapshot.sqlite3"
        build_snapshot(snapshot)
        for source in [*SCRIPT_DIR.glob("*.sql"), SCRIPT_DIR / "process.py"]:
            shutil.copy(source, root)
        (root / "csvs-to-export").mkdir()
        sql = (root / "0_main.sql").read_text()
        subprocess.run(
            ["sqlite3", "-readonly", str(snapshot)],
            input=sql,
            cwd=root,
            text=True,
            check=True,
        )
        subprocess.run([sys.executable, "process.py"], cwd=root, check=True)
        cls.requests = {
            row["request_id"]: row
            for row in read_csv(root / "csvs-to-export/requests.csv")
        }
        cls.files = defaultdict(list)
        for row in read_csv(root / "csvs-to-export/files.csv"):
            cls.files[row["request_id"]].append(row)

    def test_csvs_join_on_request_id(self):
        """Every exported request's file count matches its rows in files.csv."""
        self.assertEqual(set(self.requests), {"csv-heavy", "image-heavy", "no-files"})
        # An inner join silently drops a request whose completion time was unusable;
        # a left join from requests.csv keeps requests without any output files.
        self.assertEqual(
            set(self.files), {"csv-heavy", "image-heavy", "bad-completion"}
        )
        for rid, request in self.requests.items():
            self.assertEqual(int(request["num_files"]), len(self.files.get(rid, [])))

    def test_checker_time_by_file_format(self):
        """A request's format mix from files.csv can be compared with its checker time."""
        mostly_csv, mostly_images = [], []
        for rid, request in self.requests.items():
            formats = Counter(row["format"] for row in self.files.get(rid, []))
            if not formats:
                continue
            images = sum(formats[name] for name in IMAGE_FORMATS)
            group = mostly_csv if formats["csv"] > images else mostly_images
            group.append(float(request["checker_minutes_5m_request_only"]))
        self.assertEqual(mostly_csv, [10])
        self.assertEqual(mostly_images, [25])

    def test_checker_time_by_job_category(self):
        """Externally assigned job categories link requests to their review time."""
        categories = defaultdict(set)
        for rid, rows in self.files.items():
            for row in rows:
                categories[rid].add(JOB_CATEGORIES[row["job_id"]])
        with_measures, r_only = [], []
        for rid, request in self.requests.items():
            if rid not in categories:
                continue
            minutes = float(request["checker_minutes_5m_request_only"])
            (with_measures if "measures" in categories[rid] else r_only).append(minutes)
        self.assertEqual(mean(with_measures), 10)
        self.assertEqual(mean(r_only), 25)

    def test_researcher_checker_and_calendar_time_per_request(self):
        """Researcher, checker and calendar time are all present for each request."""
        expected = {
            "csv-heavy": (2, 10, 3.0),
            "image-heavy": (3, 25, 4.0),
            "no-files": (0, 0, 0.5),
        }
        for rid, (researcher, checker, hours) in expected.items():
            request = self.requests[rid]
            for threshold in (5, 10, 15):
                self.assertEqual(
                    float(request[f"researcher_minutes_{threshold}m_request_only"]),
                    researcher,
                )
                self.assertEqual(
                    float(request[f"checker_minutes_{threshold}m_request_only"]),
                    checker,
                )
            self.assertEqual(float(request["hours_to_completion"]), hours)


if __name__ == "__main__":
    unittest.main()
