import pytest

from airlock.business_logic import AuditEvent, AuditEventType
from local_db import data_access


dal = data_access.LocalDBDataAccessLayer()

pytestmark = pytest.mark.django_db


def create_event(
    type_,
    user="user",
    workspace="workspace",
    request="request",
    path="foo/bar",
    extra={"foo": "bar"},
):
    event = AuditEvent(
        type=type_,
        user=user,
        workspace=workspace,
        request=request,
        path=path,
        extra=extra,
    )
    dal.audit_event(event)
    return event


def test_get_audit_log():
    workspace_view = create_event(AuditEventType.WORKSPACE_FILE_VIEW, request=None)
    other_workspace_view = create_event(
        AuditEventType.WORKSPACE_FILE_VIEW, workspace="other", request=None
    )
    request_view = create_event(AuditEventType.REQUEST_FILE_VIEW)
    other_request_view = create_event(AuditEventType.REQUEST_FILE_VIEW, request="other")
    other_user = create_event(AuditEventType.REQUEST_FILE_VIEW, user="other")

    assert dal.get_audit_log() == [
        other_user,
        other_request_view,
        request_view,
        other_workspace_view,
        workspace_view,
    ]

    assert dal.get_audit_log(user="user") == [
        other_request_view,
        request_view,
        other_workspace_view,
        workspace_view,
    ]

    assert dal.get_audit_log(workspace="workspace") == [
        other_user,
        other_request_view,
        request_view,
        workspace_view,
    ]

    assert dal.get_audit_log(request="request") == [
        other_user,
        request_view,
    ]

    assert dal.get_audit_log(request="request", user="user") == [
        request_view,
    ]
