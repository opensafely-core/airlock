import csv  # noqa: INP001
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path


CURRENT_DIR = Path(__file__).parent
REQUESTS_JSON = CURRENT_DIR / "requests.json"
EVENTS_JSON = CURRENT_DIR / "events.json"
OUTPUT_CSV = CURRENT_DIR / "csvs-to-export" / "requests.csv"

THRESHOLDS = (5, 10, 15)
ROLES = ("researcher", "checker", "unclassified")


def read_json(filename: Path):
    text = filename.read_text(encoding="utf-8")
    return json.loads(text) if text.strip() else []


def aggregate(requests, include_nearby=False):
    """Return session minutes by request, role and threshold, with and without a five-minute minimum."""
    cohort = {r["request_id"] for r in requests}
    totals = defaultdict(float)
    minimum_totals = defaultdict(float)

    def finish_session(session, threshold):
        rid, role, start, end = session
        if rid not in cohort:
            return
        seconds = (end - start).total_seconds()
        totals[rid, role, threshold] += seconds / 60
        minimum_totals[rid, role, threshold] += max(seconds, 5 * 60) / 60

    sessions = {}

    for event in read_json(EVENTS_JSON):
        user, rid = event["user_id"], event["request_id"]
        if rid is None and not include_nearby:
            continue
        time = datetime.fromisoformat(event["occurred_at"])
        role = event["user_role"] or "unclassified"
        for threshold in THRESHOLDS:
            key = user, threshold
            if key in sessions:
                old_rid, old_role, start, end = sessions[key]
                gap = (time - end).total_seconds()
                if 0 <= gap <= threshold * 60:
                    if rid is None or rid == old_rid:
                        sessions[key] = old_rid, old_role, start, time
                        continue
                    if old_rid is None:
                        # Assign a leading run of nearby events to this request.
                        sessions[key] = rid, role, start, time
                        continue
                finish_session(sessions.pop(key), threshold)
            # Track non-cohort requests too, so nearby activity stays with them.
            sessions[key] = rid, role, time, time
    for (_, threshold), session in sessions.items():
        finish_session(session, threshold)
    return totals, minimum_totals


def elapsed(start, end):
    """
    Return the elapsed time in hours between two timestamps
    """
    if start is None or end is None:
        return None
    hours = (
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
    ).total_seconds() / 3600
    return hours if hours >= 0 else None


def main():
    """
    Read requests, aggregate events, output CSV
    """
    requests = read_json(REQUESTS_JSON)
    estimates = {
        "request_only": aggregate(requests),
        "including_nearby": aggregate(requests, include_nearby=True),
    }
    activity_columns = {}
    for mode, (activity, minimum_activity) in estimates.items():
        for role in ROLES:
            for gap in THRESHOLDS:
                name = f"{role}_minutes_{gap}m_{mode}"
                activity_columns[name] = activity, role, gap
                activity_columns[f"{name}_min_session_5m"] = minimum_activity, role, gap
    fields = (
        ["request_id", "num_files", "num_submission_rounds"]
        + list(activity_columns)
        + ["hours_to_completion", "completion_status"]
    )
    rows = []
    bad_requests = 0
    for request in requests:
        rid = request["request_id"]
        status, field = {
            "APPROVED": ("approved", "approved_at"),
            "RELEASED": ("approved", "approved_at"),
            "REJECTED": ("rejected", "rejected_at"),
            "WITHDRAWN": ("withdrawn", "withdrawn_at"),
        }.get(request["status"], ("unresolved", None))
        hours = elapsed(request["created_at"], request[field]) if field else None
        if field and hours is None:
            # Hmm, this means the request has completed, but the time is
            # missing or invalid. Skip it, but count it as a bad request
            # so we can report on it later
            bad_requests += 1
            continue
        row = {
            "request_id": rid,
            "num_files": request["num_files"],
            "num_submission_rounds": request["num_submission_rounds"],
            "hours_to_completion": hours,
            "completion_status": status,
        }
        for name, (totals, role, gap) in activity_columns.items():
            row[name] = totals[rid, role, gap]
        rows.append(row)

    print(f"Processed {len(rows)} requests, skipped {bad_requests} bad requests.")

    with (OUTPUT_CSV).open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
