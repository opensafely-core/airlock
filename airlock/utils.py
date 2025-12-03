from pathlib import Path
from typing import IO

from pipeline.constants import LEVEL4_FILE_TYPES

from airlock.types import UrlPath


def is_valid_file_type(path: Path | UrlPath):
    return not path.name.startswith(".") and path.suffix in LEVEL4_FILE_TYPES


def truncate_log_stream(stream: IO[str], n: int):
    """Efficiently read the last n bytes from a log file.

    If it has been truncated, remove any partial lines.
    """
    full_stream = stream.read()
    if len(full_stream) > n:
        truncated = full_stream[-n:]
        newline_pos = truncated.find("\n")
        # if there is more than 1 line
        if newline_pos != len(truncated) - 1:
            # remove any partial lines
            truncated = truncated[newline_pos + 1 :]
        return truncated, True
    else:
        return full_stream, False
