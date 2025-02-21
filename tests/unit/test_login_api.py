import json

import pytest

from airlock import login_api
from tests import factories


def test_get_user_data_with_dev_users(settings, tmp_path):
    dev_user_file = tmp_path / "dev_users.json"
    settings.AIRLOCK_API_TOKEN = ""
    settings.AIRLOCK_DEV_USERS_FILE = dev_user_file
    dev_user_file.write_text(
        json.dumps(
            {
                "test_user": {
                    "token": "foo bar baz",
                    "details": factories.create_api_user(
                        output_checker=True,
                        workspaces={
                            "test1": factories.create_api_workspace(project="project1")
                        },
                    ),
                },
            },
        )
    )

    dev_data = login_api.get_user_data("test_user", "foo bar baz")
    assert dev_data["output_checker"] is True
    assert dev_data["workspaces"] == {
        "test1": {
            "project_details": {"name": "project1", "ongoing": True},
            "archived": False,
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
                "details": factories.create_api_user(),
            }
        )
    )

    with pytest.raises(login_api.LoginError):
        login_api.get_user_data("test_user", "bad token")
