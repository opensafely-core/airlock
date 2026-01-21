import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command


def test_validate_config_with_error(tmp_path):
    """
    Test validating release request config using the management command.
    """

    config_path = tmp_path / "regular_release_requests.json"
    config_path.write_text(
        json.dumps([{"workspace_name": "foo", "username": ["user"]}])
    )

    out = StringIO()
    with patch(
        "airlock.jobs.daily.create_regular_release_requests.CONFIG_PATH", config_path
    ):
        call_command("validate_regular_release_request_config", stdout=out)
    assert out.getvalue() == (
        "\n======foo======\n"
        "Config errors found:\n"
        "- keys missing in config: dirs\n"
        "- Invalid config type for 'username': expected str | None, got <class 'list'>\n"
    )


def test_validate_config_no_error(tmp_path):
    config_path = tmp_path / "regular_release_requests.json"
    config_path.write_text(
        json.dumps([{"workspace_name": "foo", "username": "user", "dirs": ["output"]}])
    )

    out = StringIO()
    with patch(
        "airlock.jobs.daily.create_regular_release_requests.CONFIG_PATH", config_path
    ):
        call_command("validate_regular_release_request_config", stdout=out)
    assert out.getvalue() == (
        "\n======foo======\n"
        "Config OK\n"
        "workspace_name: foo\n"
        "username: user\n"
        "dirs: ['output']\n"
    )


def test_validate_config_bad_json(tmp_path):
    config_path = tmp_path / "regular_release_requests.json"
    config_path.write_text('[{"workspace_name": "foo"]')

    out = StringIO()
    with patch(
        "airlock.jobs.daily.create_regular_release_requests.CONFIG_PATH", config_path
    ):
        with pytest.raises(json.JSONDecodeError):
            call_command("validate_regular_release_request_config", stdout=out)
