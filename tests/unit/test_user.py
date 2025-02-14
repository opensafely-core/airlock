from typing import Any

from airlock.users import User
from tests import factories


def test_session_user_from_session():
    mock_session = {
        "user": factories.create_api_user(
            username="test",
            workspaces={
                "workspace1": factories.create_api_workspace(
                    project="Project 1", archived=False
                ),
                "workspace2": factories.create_api_workspace(
                    project="Project 2", archived=True
                ),
            },
            output_checker=True,
        )
    }
    user = User.from_session(mock_session)
    assert set(user.workspaces) == {"workspace1", "workspace2"}
    assert user.workspaces["workspace1"]["project_details"] == {
        "name": "Project 1",
        "ongoing": True,
    }
    assert user.workspaces["workspace2"]["project_details"] == {
        "name": "Project 2",
        "ongoing": True,
    }
    assert user.workspaces["workspace1"]["archived"] is False
    assert user.workspaces["workspace2"]["archived"] is True
    assert user.output_checker


def test_session_user_with_defaults():
    mock_session = {
        "user": {
            "id": 1,
            "username": "test",
        }
    }
    user = User.from_session(mock_session)
    assert user.workspaces == {}
    assert not user.output_checker


def test_session_user_no_user_set():
    mock_session: dict[str, Any] = {}
    user = User.from_session(mock_session)
    assert user is None
