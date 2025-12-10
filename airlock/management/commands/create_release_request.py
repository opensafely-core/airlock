"""
Automatically create a release request
"""

import logging

from django.core.management.base import BaseCommand

from airlock import actions


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Automatically create a release request
    """

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            help="user name of user to create this release request; must have permission to access the workspace",
        )
        parser.add_argument("workspace_name", help="workspace name")
        parser.add_argument(
            "--dirs",
            nargs="+",
            help="list of directory paths containing output files to add",
        )
        parser.add_argument(
            "--supporting-files",
            nargs="*",
            help="list of paths to files to be added as supporting files; must exist in one of the specified dirs",
        )
        parser.add_argument(
            "--context",
            default="",
            help="Group context; if multiple groups are created, the same context will be added for each group",
        )
        parser.add_argument(
            "--controls",
            default="",
            help="Group controls; if multiple groups are created, the same controls will be added for each group",
        )
        parser.add_argument(
            "--submit",
            action="store_true",
            help="Submit this release request for review",
        )

    def handle(self, username, workspace_name, **options):
        actions.create_release_request(username, workspace_name, **options)
