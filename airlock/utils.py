from pathlib import Path

from pipeline.constants import LEVEL4_FILE_TYPES

from airlock.types import UrlPath


def is_valid_file_type(path: Path | UrlPath):
    return not path.name.startswith(".") and path.suffix in LEVEL4_FILE_TYPES
