import pytest
from django.core.management import call_command

from airlock.enums import RequestStatus
from tests import factories


@pytest.mark.django_db
def test_change_release_request_status(bll, monkeypatch, capsys, mock_notifications):
    """
    Test that we can change a release request's status using the management command.
    """
    monkeypatch.setattr("builtins.input", lambda _: "y")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[factories.request_file(changes_requested=True)],
    )
    factories.create_airlock_user(username="testuser")

    call_command(
        "change_release_request_status",
        release_request.id,
        user="testuser",
        status=RequestStatus.WITHDRAWN,
    )

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.WITHDRAWN
    assert "Succeeded" in capsys.readouterr().out


@pytest.mark.django_db
def test_change_release_request_status_aborted(bll, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[factories.request_file(changes_requested=True)],
    )
    factories.create_airlock_user(username="testuser")

    call_command(
        "change_release_request_status",
        release_request.id,
        user="testuser",
        status=RequestStatus.WITHDRAWN,
    )

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.RETURNED
    assert "Aborted" in capsys.readouterr().out


@pytest.mark.django_db
def test_change_release_request_status_with_error(bll, capsys):
    factories.create_airlock_user(username="testuser")

    call_command(
        "change_release_request_status",
        "bad_request_id",
        user="testuser",
        status=RequestStatus.WITHDRAWN,
    )

    assert "Error: No release request" in capsys.readouterr().out
