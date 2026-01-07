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
