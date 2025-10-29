"""
Automatically create a release request
"""

import logging
from dataclasses import dataclass, field

from django.core.management.base import BaseCommand

from airlock import permissions, policies
from airlock.business_logic import bll
from airlock.enums import RequestFileType, WorkspaceFileStatus
from airlock.exceptions import FileNotFound
from airlock.types import UrlPath
from users.models import User


logger = logging.getLogger(__name__)


@dataclass
class GroupData:
    name: str
    total_files: int = 0
    total_files_released: int = 0
    total_files_already_added: int = 0
    files_to_add: list[UrlPath] = field(default_factory=list)
    file_errors: list[UrlPath] = field(default_factory=list)
    context: str | None = None
    controls: str | None = None


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
            "--context",
            default="",
            help="Group context; if multiple groups are created, the same context will be added for each group",
        )
        parser.add_argument(
            "--controls",
            default="",
            help="Group controls; if multiple groups are created, the same controls will be added for each group",
        )

    def handle(self, username, workspace_name, **options):
        user = User.objects.get(user_id=username)
        request = bll.get_or_create_current_request(workspace_name, user)
        # If we retrieved an exisiting release request for this user, make sure
        # it's editable (i.e. in author-owned state) first
        permissions.check_user_can_edit_request(user, request)

        workspace = bll.get_workspace(workspace_name, user)
        # record some info about the files
        groups_data = []
        context = options["context"]
        controls = options["controls"]
        for dir_path in options["dirs"]:
            self.stdout.write(f"Finding files for {dir_path}")

            dir_relpath = UrlPath(dir_path)
            directory = workspace.abspath(dir_path)  # validate path
            # make a group for this directory, using the directory
            group_name = dir_path.replace("/", "-")
            group_data = GroupData(name=group_name, context=context, controls=controls)

            # add all files anywhere under this directory
            for filepath in directory.rglob("*"):
                if filepath.is_file():
                    group_data.total_files += 1
                    file_sub_path = str(filepath).split(f"{dir_path}/")[-1]
                    relpath = UrlPath(dir_relpath / file_sub_path)
                    state = workspace.get_workspace_file_status(relpath)

                    if policies.can_add_file_to_request(workspace, relpath):
                        group_data.files_to_add.append(relpath)
                    elif state == WorkspaceFileStatus.RELEASED:
                        group_data.total_files_released += 1
                    else:
                        # If the file can't be added to the request and isn't already
                        # released, we expect that it's on the request already
                        try:
                            request.get_request_file_from_output_path(relpath)
                            group_data.total_files_already_added += 1
                        except FileNotFound:
                            # If it's not on the request, it could be an invalid file type,
                            # a file that's under review, or a file that's had it's content
                            # updated since the request was created. This is expected to be
                            # rare, since this command is intended to be a one-off method of
                            # generating an occasional release request from scratch.
                            group_data.file_errors.append(relpath)

            groups_data.append(group_data)

        # Summarise

        for group_data in groups_data:
            self.stdout.write(f"Group: {group_data.name}")
            self.stdout.write("===================================================")
            self.stdout.write(f"Total files found: {group_data.total_files}")
            self.stdout.write(f"Files to add: {len(group_data.files_to_add)}")
            self.stdout.write(
                f"Files already added: {group_data.total_files_already_added}"
            )
            self.stdout.write(
                f"Files already released: {group_data.total_files_released}"
            )
            self.stdout.write(f"Couldn't add files: {len(group_data.file_errors)}")
            if options["verbosity"] > 1 and group_data.file_errors:
                for relpath in group_data.file_errors:
                    self.stdout.write(f"- {relpath}")

        for group_data in groups_data:
            self.stdout.write(f"\nAdding files for group {group_data.name}")
            self.stdout.write("===================================================")
            total = len(group_data.files_to_add)
            added = 0
            for relpath in group_data.files_to_add:
                bll.add_file_to_request(
                    request, relpath, user, group_data.name, RequestFileType.OUTPUT
                )
                added += 1

                if added > 0 and added % 100 == 0:  # pragma: no cover
                    self.stdout.write(f"{added}/{total} files added")

            # Add group context and controls, but only if the group exists (i.e. it
            # has had some files added to it, either now or previously)
            if group_data.name in request.filegroups and (context or controls):
                bll.group_edit(
                    request,
                    group=group_data.name,
                    context=group_data.context,
                    controls=group_data.controls,
                    user=user,
                )

            self.stdout.write(f"Total: {added}/{total} added (group {group_data.name})")
