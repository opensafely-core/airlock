import pytest

from airlock.users import User


def test_session_user_from_session():
    mock_session = {
        "user": {
            "id": 1,
            "username": "test",
            "workspaces": {
                "test-workspace-1": {"project": "Project 1"},
                "test_workspace2": {"project": "Project 2"},
            },
            "output_checker": True,
        }
    }
    user = User.from_session(mock_session)
    assert set(user.workspaces) == {"test-workspace-1", "test_workspace2"}
    assert user.workspaces["test-workspace-1"]["project"] == "Project 1"
    assert user.workspaces["test_workspace2"]["project"] == "Project 2"
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
    mock_session = {}
    user = User.from_session(mock_session)
    assert user is None


@pytest.mark.parametrize(
    "output_checker,workspaces,has_permission",
    [
        (True, [], True),
        (True, {"other": {}, "other1": {}}, True),
        (False, {"test": {}, "other": {}, "other1": {}}, True),
        (False, {"other": {}, "other1": {}}, False),
    ],
)
def test_session_user_has_permission(output_checker, workspaces, has_permission):
    mock_session = {
        "user": {
            "id": 1,
            "username": "test",
            "workspaces": workspaces,
            "output_checker": output_checker,
        }
    }
    user = User.from_session(mock_session)
    assert user.has_permission("test") == has_permission


@pytest.mark.parametrize(
    "output_checker,workspaces,can_create_request",
    [
        (True, [], False),
        (True, {"other": {}, "other1": {}}, False),
        (False, {"test": {}, "other": {}, "other1": {}}, True),
        (False, {"other": {}, "other1": {}}, False),
    ],
)
def test_session_user_can_create_request(
    output_checker, workspaces, can_create_request
):
    mock_session = {
        "user": {
            "id": 1,
            "username": "test",
            "workspaces": workspaces,
            "output_checker": output_checker,
        }
    }
    user = User.from_session(mock_session)
    assert user.can_create_request("test") == can_create_request
