import json

import requests

from airlock.business_logic import BusinessLogicLayer, RequestStatus
from airlock.notifications import send_notification_event


def test_notification_no_api_endpoint(settings):
    settings.AIRLOCK_API_TOKEN = None
    assert send_notification_event({}, "") == {"status": "ok"}


def test_send_notification(notifications_stubber):
    mock_notifications = notifications_stubber()
    event_json = json.dumps({"event_type": "request_submitted"})
    assert send_notification_event(event_json=event_json, username="test-user") == {
        "status": "ok"
    }
    assert len(mock_notifications.calls) == 1
    call = mock_notifications.calls[0]

    assert call.request.headers["OS-User"] == "test-user"
    assert call.request.headers["Authorization"] == "token"
    assert call.request.body == event_json


def test_send_notification_error(notifications_stubber):
    response = requests.Response()
    response.status_code = 403
    api403 = requests.HTTPError(response=response)

    notifications_stubber(exception=api403)
    event_json = json.dumps({"event_type": "request_submitted"})
    assert send_notification_event(event_json=event_json, username="test-user") == {
        "status": "error",
        "message": "Error sending notification: 403 Forbidden",
    }


def test_all_expected_status_changes_notify():
    """
    For every possible request status that a request can move
    to (i.e. all except PENDING), assert that there is a corresponding
    notification event that will be sent.
    """
    to_statuses = set(RequestStatus) - {RequestStatus.PENDING}
    assert set(BusinessLogicLayer.STATUS_EVENT_NOTIFICATION) == to_statuses
