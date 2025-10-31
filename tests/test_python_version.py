"""
Ensure all the various places in which we need to reference our target Python version
are consistent.
"""

import re
from pathlib import Path

import pytest


BASE_DIR = Path(__file__).parents[1]


@pytest.fixture(scope="session")
def python_version():
    # We treat the `.python-version` file as the canonical specifier
    return (BASE_DIR / ".python-version").read_text().strip()


@pytest.mark.parametrize(
    "filename",
    BASE_DIR.glob(".github/workflows/*.yml"),
    ids=lambda path: path.name,
)
def test_github_workflows(filename, python_version):
    contents = filename.read_text()
    for match in re.findall(r"python-version:.*?([\d\.]+)", contents):
        assert match == python_version


@pytest.mark.parametrize(
    "filename",
    BASE_DIR.glob("docker/dependencies*.txt"),
    ids=lambda path: path.name,
)
def test_docker_dependencies(filename, python_version):
    contents = filename.read_text()
    for match in re.findall(r"python.*?([\d\.]+)", contents):
        assert match == python_version
