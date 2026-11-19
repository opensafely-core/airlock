# Operational Data Script

For the HDRS input assurance project it is useful to get operational metric
data from the secure back end to establish how much time is spent on output
checking (both researcher/checker clock time, and calendar time from request
to completion). These scripts extract the relevant data into 2 CSV files:

- `files.csv`: One row per file, with the following columns:
  - `request_id`
  - `job_id` - id of the action that produced the file
  - `format` - [csv|jpeg|png|svg|html|text|log|json|markdown|other_or_unknown]
  - `size_bytes`
- `requests.csv`: One row per request, with the following columns:
  - `request_id`
  - `num_files` - number of output files in the request; equals the number of
    rows for the request in `files.csv`
  - `num_submission_rounds`
  - `<role>_minutes_<threshold>m_<estimate>[_min_session_5m]`, e.g.
    `checker_minutes_10m_request_only` - person-minutes spent on the request.
    This is 36 columns (3 roles x 3 thresholds x 2 estimates x 2 min_session options - see below)
  - `hours_to_completion` - total hours from request creation to "completion"
  - `completion_status` - [approved|rejected|withdrawn|unresolved]

Researcher, checker and unclassified minutes are calculated from the `occurred_at` timestamps of events from the audit log. They are calculated based on 3 thresholds (5, 10 and 15 minutes). I.e. under the 5 minute threshold, if a researcher has 2 events within 5 minutes of each other, that counts as 1 session. If the next event is more than 5 minutes later, that counts as a new session. The total time spent on the request is the sum of the durations of all sessions. Minutes are summed over every user with that role, so a request reviewed by two checkers reports both checkers' time.

For each role and threshold, two estimates are exported:

- `<role>_minutes_<threshold>m_request_only`: ignores events without a request.
- `<role>_minutes_<threshold>m_including_nearby`: includes events without a request
  in the user's active session. Before any request session, a run of these events
  is assigned to the next request if every gap is within the threshold.

Both estimates also have a `_min_session_5m` column applying a five-minute minimum
per session. Null roles are counted as `unclassified`; nearby events inherit the
request session's role.

A gap exceeding the threshold ends a session. Explicit events on a different
request always end it too. Nearby activity stays with its existing request, so it
is never also assigned to the next request. Once a session expires, subsequent
non-request events can form a new leading run. Leading runs with no following
request are discarded. The inclusive estimate can keep extending through
non-request events as long as the gaps stay within the threshold, assuming that
the user is still working on the same request.

## Usage

```bash
# Set airlock db snapshot path
export AIRLOCK_SNAPSHOT=/path/to/airlock-snapshot.sqlite3

# Create temp working dir for script execution and output CSVs
workdir=$(mktemp -d /tmp/airlock-analysis.XXXXXX)
mkdir -p $workdir/csvs-to-export

# Copy the 5 SQL files, and 1 python file to the working dir
# ...

cd $workdir
sqlite3 -readonly $AIRLOCK_SNAPSHOT < 0_main.sql

python3 process.py
```

Only the two CSVs in `csvs-to-export/` are intended to leave the secure environment. The
JSON files should be deleted after the CSVs are exported.
