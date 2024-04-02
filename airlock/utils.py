from pathlib import Path

from pipeline.constants import LEVEL4_FILE_TYPES


def is_valid_file_type(path: Path):
    return path.suffix in LEVEL4_FILE_TYPES
