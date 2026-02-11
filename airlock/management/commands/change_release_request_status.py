"""
Update a release request's status outside of the UI.
"""

import logging

from django.core.management.base import BaseCommand

from airlock import actions
from airlock.enums import RequestStatus
from airlock.exceptions import ActionDenied


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Update a release request's status outside of the UI. This is only intended for withdrawing or
    rejecting release requests that can no longer be accessed by their original author.
    """

    def add_arguments(self, parser):
        parser.add_argument("request_id", help="release request ID")
        parser.add_argument(
            "--user",
            help="username of user to modify this release request",
        )
        parser.add_argument(
            "--status",
            help="request status to change to",
            type=RequestStatus,
            choices=[RequestStatus.REJECTED, RequestStatus.WITHDRAWN],
        )

    def handle(self, request_id, user, status, **options):
        try:
            result = actions.change_release_request_status(
                request_id, username=user, to_status=status, **options
            )
        except ActionDenied as e:
            self.stdout.write(f"Error: {str(e)}")
        else:
            self.stdout.write(result)
