import logging
from dataclasses import dataclass, field

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
    context: str = ""
    controls: str = ""


def create_release_request(
    username, workspace_name, *, dirs, context="", controls="", submit=False, **kwargs
):
    user = User.objects.get(user_id=username)
    request = bll.get_or_create_current_request(workspace_name, user)
    if request.is_under_review():
        logger.info(
            f"A release request for workspace '{workspace_name}' is already under review for user '{username}'"
        )
        return {
            "completed": False,
            "request_id": request.id,
            "message": "Already submitted",
        }

    # If we retrieved an exisiting release request for this user, make sure
    # it's editable (i.e. in author-owned state) first
    permissions.check_user_can_edit_request(user, request)

    workspace = bll.get_workspace(workspace_name, user)
    # record some info about the files
    groups_data = []

    for dir_path in dirs:
        logger.debug(f"Finding files for {dir_path}")

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
                        # or a file that's had it's content updated since the request was created.
                        # This is expected to be rare, so if we're creating a pending release request,
                        # we just log the error and continue.
                        logger.error(f"Could not add file {relpath}")
                        # If we're creating an automated submitted release request, raise the error
                        if submit:
                            raise

        groups_data.append(group_data)

    # Return early if all files have already been released
    all_released = all(
        group_data.total_files == group_data.total_files_released
        for group_data in groups_data
    )
    if all_released:
        assert not request.output_files()
        logger.info("All files have already been released")
        return {
            "completed": False,
            "request_id": request.id,
            "message": "Already released",
        }

    for group_data in groups_data:
        if group_data.files_to_add:
            logger.info(f"Adding files for group {group_data.name}")
            total = len(group_data.files_to_add)
            added = 0
            for relpath in group_data.files_to_add:
                bll.add_file_to_request(
                    request, relpath, user, group_data.name, RequestFileType.OUTPUT
                )
                added += 1

                if added > 0 and added % 100 == 0:  # pragma: no cover
                    logger.debug(f"{added}/{total} files added")

            logger.info(f"Total: {added}/{total} added (group {group_data.name})")
        else:
            logger.info(
                f"No files to add for group {group_data.name}; {group_data.total_files_already_added} already added"
            )

        # Add group context and controls, but only if the group exists (i.e. it
        # has had some files added to it, either now or previously)
        if group_data.name in request.filegroups and (context or controls):
            logger.info(f"Updating context/controls for group {group_data.name}")
            bll.group_edit(
                request,
                group=group_data.name,
                context=group_data.context,
                controls=group_data.controls,
                user=user,
            )

    if submit:
        refreshed_request = bll.get_or_create_current_request(workspace.name, user)
        assert refreshed_request.id == request.id
        # The request should be submittable, because we already checked that
        # it's editable (i.e. in author-owned state), and we've checked that it's
        # not all-released. If there are any other errors, we let them be raised
        # here; if this is being called by the runjobs job, the exception will be
        # handled there.
        bll.submit_request(refreshed_request, user)
        logger.info("Release request submitted")

    return {
        "completed": True,
        "request_id": request.id,
        "message": "Success",
    }
