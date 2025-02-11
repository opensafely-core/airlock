from typing import Any

from airlock.users import User


def test_session_user_from_session():
    mock_session = {
        "user": {
            "id": 1,
            "username": "test",
            "workspaces": {
                "test-workspace-1": {
                    "project_details": {"name": "Project 1", "ongoing": True},
                    "archived": False,
                },
                "test_workspace2": {
                    "project_details": {"name": "Project 2", "ongoing": True},
                    "archived": True,
                },
            },
            "output_checker": True,
        }
    }
    user = User.from_session(mock_session)
    assert set(user.workspaces) == {"test-workspace-1", "test_workspace2"}
    assert user.workspaces["test-workspace-1"]["project_details"] == {
        "name": "Project 1",
        "ongoing": True,
    }
    assert user.workspaces["test_workspace2"]["project_details"] == {
        "name": "Project 2",
        "ongoing": True,
    }
    assert user.workspaces["test-workspace-1"]["archived"] is False
    assert user.workspaces["test_workspace2"]["archived"] is True
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
