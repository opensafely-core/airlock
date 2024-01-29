from airlock.users import User


def test_session_user_from_session():
    mock_session = {
        "user": {
            "id": 1,
            "workspaces": ["test-workspace-1", "test_workspace2"],
            "is_output_checker": True,
        }
    }
    user = User.from_session(mock_session)
    assert user.workspaces == ("test-workspace-1", "test_workspace2")
    assert user.is_output_checker


def test_session_user_with_defaults():
    mock_session = {"user": {"id": 1}}
    user = User.from_session(mock_session)
    assert user.workspaces == ()
    assert not user.is_output_checker


def test_session_user_no_user_set():
    mock_session = {}
    user = User.from_session(mock_session)
    assert user is None
