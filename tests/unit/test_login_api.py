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
                        "output_checker": True,
                        "workspaces": {
                            "test1": {"project": "project1"},
                        },
                    },
                },
            }
        )
    )

    assert login_api.get_user_data("test_user", "foo bar baz") == {
        "output_checker": True,
        "workspaces": {
            "test1": {"project": "project1"},
        },
    }


def test_get_user_data_with_dev_users_invalid(settings, tmp_path):
    dev_user_file = tmp_path / "dev_users.json"
    settings.AIRLOCK_API_TOKEN = ""
    settings.AIRLOCK_DEV_USERS_FILE = dev_user_file
    dev_user_file.write_text(
        json.dumps(
            {
                "test_user": {"token": "foo bar baz"},
                "details": {
                    "workspaces": {
                        "test1": {"project": "project1"},
                    }
                },
            }
        )
    )

    with pytest.raises(login_api.LoginError):
        login_api.get_user_data("test_user", "bad token")
