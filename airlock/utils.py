from pathlib import Path

from pipeline.constants import LEVEL4_FILE_TYPES


def is_valid_file_type(path: Path):
    return not path.name.startswith(".") and path.suffix in LEVEL4_FILE_TYPES
