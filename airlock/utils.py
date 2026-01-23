from collections import Counter
from itertools import zip_longest
from pathlib import Path
from typing import IO

from opentelemetry import trace
from pipeline.constants import LEVEL4_FILE_TYPES

from airlock.types import UrlPath


def is_valid_file_type(path: Path | UrlPath):
    return not path.name.startswith(".") and path.suffix in LEVEL4_FILE_TYPES


def truncate_log_stream(stream: IO[str], n: int):
    """Efficiently read the last n bytes from a log file.

    If it has been truncated, remove any partial lines.
    """
    span = trace.get_current_span()
    truncated = False
    log = stream.read()
    size = len(log)
    span.set_attribute("job.log_size", size)

    if size > n:
        truncated_log = log[-n:]
        newline_pos = truncated_log.find("\n")
        # if there is more than 1 line
        if newline_pos != len(truncated_log) - 1:
            # remove any partial lines
            truncated_log = truncated_log[newline_pos + 1 :]
        log = truncated_log
        truncated = True

    span.set_attribute("job.log_truncated", truncated)

    return log, truncated


def _is_not_divisible_by(value: int | float, divider: int):
    return value % divider != 0


def _is_not_midpoint6_rounded(value: int | float):
    return value != 0 and ((value - 3) % 6 != 0)


def summarize_column(column_name: str, column_data: tuple[str]):
    # Likely missing/null/redacted values removed for checking for type and for numeric summaries
    missing_strings = ["", "null", "none"]
    # redacted strings counted separately for numeric columns
    redacted_strings = ["[redacted]", "redacted", "na", "n/a"]

    # strip whitespace, lower, and count; ignore non-None, these only occur for
    # CSVs with uneven columns
    counter = Counter(i.strip().lower() for i in column_data if i is not None)
    non_missing_counter = {
        val: count
        for val, count in counter.items()
        if val not in missing_strings + redacted_strings
    }

    try:
        numeric_data = {int(i): count for i, count in non_missing_counter.items()}
        type_ = "integer"
    except ValueError:
        try:
            numeric_data = {float(i): count for i, count in non_missing_counter.items()}
            type_ = "float"
        except ValueError:
            type_ = "text"
            numeric_data = None

    column_summary = {
        "column_name": column_name,
        "type": type_,
        "total_rows": len(column_data),
        "missing_values": sum(
            count for val, count in counter.items() if val in missing_strings
        ),
        # defaults for numeric calculations
        "redacted": "-",
        "min": "-",
        "min_gt_0": "-",
        "max": "-",
        "sum": "-",
        "divisible_by_5": "-",
        "divisible_by_6": "-",
        "midpoint6_rounded": "-",
    }

    if numeric_data is not None:
        column_summary.update(
            {
                "redacted": sum(
                    count for val, count in counter.items() if val in redacted_strings
                ),
                "min": min(numeric_data),
                "min_gt_0": min(abs(i) for i in set(numeric_data) if i > 0),
                "max": max(numeric_data),
                "sum": sum(i * count for i, count in numeric_data.items()),
                "divisible_by_5": not any(
                    _is_not_divisible_by(i, 5) for i in numeric_data
                ),
                # https://docs.opensafely.org/outputs/sdc/#midpoint-6-rounding
                # Divisible by 6 indicates midpoint 6 derived (0, 6, 12...)
                # midpoint 6 takes values 0, 3, 9, 15...)
                "divisible_by_6": not any(
                    _is_not_divisible_by(i, 6) for i in numeric_data
                ),
                "midpoint6_rounded": not any(
                    _is_not_midpoint6_rounded(i) for i in numeric_data
                ),
            }
        )

    return column_summary


def summarize_csv(column_names: list[str], enumerated_rows: list[tuple[str]]):
    if not enumerated_rows:
        return
    # Get just the row values, without the row number
    row_values = list(zip(*enumerated_rows))[1]
    # Get a list of values for each column, allowing for odd CSVs with rows that are shorter than the header row
    column_values = list(zip_longest(*row_values))
    return {
        "headers": [
            "Column name",
            "Column type",
            "Total rows",
            "Null / missing",
            "Redacted",
            "Min value",
            "Min non-zero",
            "Max value",
            "Sum",
            "Divisible by 5",
            "Divisible by 6",
            "Midpoint 6 rounded",
        ],
        "rows": [
            list(summarize_column(column_name, column_values[i]).values())
            for i, column_name in enumerate(column_names)
        ],
    }
