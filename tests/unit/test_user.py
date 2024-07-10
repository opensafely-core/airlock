import pytest

from airlock.users import ActionDenied, User


def test_session_user_from_session():
    mock_session = {
        "user": {
            "id": 1,
            "username": "test",
            "workspaces": {
                "test-workspace-1": {
                    "project": "Project 1",
                    "project_details": {"name": "Project 1", "ongoing": True},
                    "archived": False,
                },
                "test_workspace2": {
                    "project": "Project 2",
                    "project_details": {"name": "Project 2", "ongoing": True},
                    "archived": True,
                },
            },
            "output_checker": True,
        }
    }
    user = User.from_session(mock_session)
    assert set(user.workspaces) == {"test-workspace-1", "test_workspace2"}
    assert user.workspaces["test-workspace-1"]["project"] == "Project 1"
    assert user.workspaces["test_workspace2"]["project"] == "Project 2"
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
    mock_session = {}
    user = User.from_session(mock_session)
    assert user is None


@pytest.mark.parametrize(
    "output_checker,workspaces,has_permission",
    [
        (True, {}, True),
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


def _details(archived=False, ongoing=True):
    return {
        "project_details": {"name": "Project", "ongoing": ongoing},
        "archived": archived,
    }


@pytest.mark.parametrize(
    "output_checker,workspaces,can_action_request,expected_reason",
    [
        (True, {}, False, "do not have permission"),
        (
            True,
            {"other": _details(), "other1": _details()},
            False,
            "do not have permission",
        ),
        (
            False,
            {"test": _details(), "other": _details(), "other1": _details()},
            True,
            None,
        ),
        (
            False,
            {"other": _details(), "other1": _details()},
            False,
            "do not have permission",
        ),
        (
            False,
            {"test": _details(archived=True)},
            False,
            "archived",
        ),
        (
            False,
            {"test": _details(ongoing=False)},
            False,
            "inactive project",
        ),
        (
            False,
            {"test": _details(archived=True, ongoing=False)},
            False,
            "archived",
        ),
    ],
)
def test_session_user_can_action_request(
    output_checker, workspaces, can_action_request, expected_reason
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
    if can_action_request:
        assert user.verify_can_action_request("test") is None
    else:
        with pytest.raises(ActionDenied, match=expected_reason):
            user.verify_can_action_request("test")
