import json

import pytest

from airlock import login_api


def test_get_user_data_with_dev_users(settings, tmp_path):
    dev_user_file = tmp_path / "dev_users.json"
    settings.AIRLOCK_API_TOKEN = ""
    settings.AIRLOCK_DEV_USERS_FILE = dev_user_file
    dev_user_file.write_text(
        json.dumps(
            {
                "test_user": {
                    "token": "foo bar baz",
                    "details": {
                        "is_output_checker": True,
                    },
                },
            }
        )
    )

    assert login_api.get_user_data("test_user", "foo bar baz") == {
        "is_output_checker": True
    }


def test_get_user_data_with_dev_users_invalid(settings, tmp_path):
    dev_user_file = tmp_path / "dev_users.json"
    settings.AIRLOCK_API_TOKEN = ""
    settings.AIRLOCK_DEV_USERS_FILE = dev_user_file
    dev_user_file.write_text(
        json.dumps(
            {
                "test_user": {"token": "foo bar baz"},
            }
        )
    )

    with pytest.raises(login_api.LoginError):
        login_api.get_user_data("test_user", "bad token")
