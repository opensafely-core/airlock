import logging

from airlock.business_logic import bll
from airlock.enums import RequestStatus
from airlock.exceptions import ActionDenied, ReleaseRequestNotFound
from airlock.models import AuditEvent
from users.auth import Level4AuthenticationBackend
from users.models import User


logger = logging.getLogger(__name__)


def change_release_request_status(
    request_id: str,
    *,
    username: str,
    to_status: RequestStatus,
    **kwargs,
) -> str:
    # Extra audit log kwargs to indicate actions performed by this code were automated
    audit_extra = {"automated_action": "true"}

    user = Level4AuthenticationBackend().get_user(username)
    if user is None:
        raise ActionDenied(f"No user found with username '{username}'")

    # The user being used for this action does not necessarily have access to the
    # workspace, which we need in order to retrieve the request
    # Note that this user is ephemeral, it does not get persisted to the db
    system_user = User(
        user_id="system", api_data={"username": "system", "output_checker": True}
    )
    try:
        release_request = bll.get_release_request(request_id, system_user)
    except ReleaseRequestNotFound:
        raise ActionDenied(f"No release request found with id {request_id}")

    if release_request.status == to_status:
        raise ActionDenied(f"Release request is already in status {to_status.value}")

    confirm = input(
        f"Request ID: {release_request.id}\n"
        f"Workspace: {release_request.workspace}\n"
        f"Changing release request status from {release_request.status.value} to {to_status.value}\n"
        f"Confirm: y/n\n"
    )
    if confirm.strip().lower() == "y":
        audit = AuditEvent.from_request(
            release_request,
            type=bll.STATUS_AUDIT_EVENT[to_status],
            user=user,
            path=None,
            **audit_extra,
        )
        # Set status using the DAL to bypass the usual workflow; this means we can
        # move a "stuck" author-owned request (i.e. a request where the author has since lost
        # relevant access to the workspace or to Airlock itself.)
        bll._dal.set_status(release_request.id, to_status, audit)
        # Send a notification to close the GitHub issue
        bll.send_notification(
            release_request, bll.STATUS_EVENT_NOTIFICATION[to_status], user
        )

        return "Succeeded"
    return "Aborted"
