from pathlib import Path

from pipeline.constants import LEVEL4_FILE_TYPES

from airlock.types import FilePath


def is_valid_file_type(path: Path | FilePath):
    return not path.name.startswith(".") and path.suffix in LEVEL4_FILE_TYPES
