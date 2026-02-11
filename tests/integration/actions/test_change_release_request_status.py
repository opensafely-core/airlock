import json

import pytest

from airlock.actions import change_release_request_status
from airlock.enums import AuditEventType, NotificationEventType, RequestStatus
from airlock.exceptions import ActionDenied, RequestPermissionDenied
from tests import factories


@pytest.mark.django_db
def test_change_release_request_status(bll, monkeypatch, mock_notifications):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[factories.request_file(changes_requested=True)],
    )
    action_user = factories.create_airlock_user(username="testuser")

    # action user can't withdraw this request in the usual way
    with pytest.raises(RequestPermissionDenied):
        bll.set_status(release_request, RequestStatus.WITHDRAWN, action_user)

    result = change_release_request_status(
        release_request.id, username="testuser", to_status=RequestStatus.WITHDRAWN
    )

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.WITHDRAWN
    assert result == "Succeeded"

    audit_log = bll._dal.get_audit_log(request=release_request.id)

    latest_log = audit_log[0]
    assert latest_log.type == AuditEventType.REQUEST_WITHDRAW
    assert latest_log.extra["automated_action"] == "true"

    last_notification = mock_notifications.calls[-1]
    notification_request_body = json.loads(last_notification.request.body)
    assert (
        notification_request_body["event_type"]
        == NotificationEventType.REQUEST_WITHDRAWN.value
    )


@pytest.mark.django_db
def test_change_release_request_status_aborted(bll, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[factories.request_file(changes_requested=True)],
    )
    factories.create_airlock_user(username="testuser")

    result = change_release_request_status(
        release_request.id, username="testuser", to_status=RequestStatus.WITHDRAWN
    )

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.RETURNED
    assert result == "Aborted"


@pytest.mark.django_db
def test_change_release_request_status_no_user(bll):
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[factories.request_file(changes_requested=True)],
    )

    with pytest.raises(ActionDenied, match="No user"):
        change_release_request_status(
            release_request.id, username="testuser", to_status=RequestStatus.WITHDRAWN
        )

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.RETURNED


@pytest.mark.django_db
def test_change_release_request_status_no_request(bll):
    factories.create_airlock_user(username="testuser")

    with pytest.raises(ActionDenied, match="No release request"):
        change_release_request_status(
            "FOO", username="testuser", to_status=RequestStatus.WITHDRAWN
        )


@pytest.mark.django_db
def test_change_release_request_status_no_status_change(bll):
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[factories.request_file(changes_requested=True)],
    )
    factories.create_airlock_user(username="testuser")

    with pytest.raises(ActionDenied, match="already in status RETURNED"):
        change_release_request_status(
            release_request.id, username="testuser", to_status=RequestStatus.RETURNED
        )

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.RETURNED
