from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from django.db import transaction

from airlock.api import (
    FileGroup,
    ProviderAPI,
    ReleaseRequest,
    RequestFile,
    Status,
    User,
)
from local_db.models import FileGroupMetadata, RequestFileMetadata, RequestMetadata


@dataclass
class LocalDBProvider(ProviderAPI):
    """Implementation of ProviderAPI using local_db models to store data."""

    def _request(self, metadata: RequestMetadata = None):
        """Unpack the db data into the Request object."""
        return ReleaseRequest(
            id=metadata.id,
            workspace=metadata.workspace,
            status=metadata.status,
            author=metadata.author,
            created_at=metadata.created_at,
            filegroups=self._get_filegroups(metadata),
        )

    def _create_release_request(self, **kwargs):
        metadata = RequestMetadata.objects.create(**kwargs)
        return self._request(metadata)

    def _find_metadata(self, request_id: str):
        try:
            return RequestMetadata.objects.get(id=request_id)
        except RequestMetadata.DoesNotExist:
            raise self.ReleaseRequestNotFound(request_id)

    def _filegroup(self, filegroup_metadata: FileGroupMetadata):
        """Unpack file group db data into FileGroup and RequestFile objects."""
        return FileGroup(
            name=filegroup_metadata.name,
            files=[
                RequestFile(relpath=Path(file_metadata.relpath))
                for file_metadata in filegroup_metadata.request_files.all()
            ],
        )

    def _get_filegroups(self, metadata: RequestMetadata):
        return [
            self._filegroup(group_metadata)
            for group_metadata in metadata.filegroups.all()
        ]

    def _get_or_create_filegroupmetadata(self, request_id: str, group_name: str):
        metadata = self._find_metadata(request_id)
        groupmetadata, _ = FileGroupMetadata.objects.get_or_create(
            request=metadata, name=group_name
        )
        return groupmetadata

    def get_release_request(self, request_id: str):
        return self._request(self._find_metadata(request_id))

    def get_current_request(self, workspace: str, user: User, create=False):
        requests = list(
            RequestMetadata.objects.filter(
                workspace=workspace,
                author=user.username,
                status__in=[Status.PENDING, Status.SUBMITTED],
            )
        )
        n = len(requests)
        if n > 1:
            raise Exception(
                f"Multiple active release requests for user {user.username} in workspace {workspace}"
            )
        elif n == 1:
            return self._request(requests[0])
        elif create:
            # To create a request, you must have explicit workspace permissions.
            # Output checkers can view all workspaces, but are not allowed to
            # create requests for all workspaces.
            if workspace not in user.workspaces:
                raise self.RequestPermissionDenied(workspace)

            return self._create_release_request(
                workspace=workspace,
                author=user.username,
            )

    def get_requests_authored_by_user(self, user: User):
        requests = []

        for metadata in RequestMetadata.objects.filter(author=user.username).order_by(
            "status"
        ):
            # to create a request, user *must* have explicit workspace
            # permissions - being an output checker is not enough
            if metadata.workspace in user.workspaces:
                requests.append(self._request(metadata))

        return requests

    def get_outstanding_requests_for_review(self, user: User):
        requests = []

        if not user.output_checker:
            return []

        for metadata in RequestMetadata.objects.filter(status=Status.SUBMITTED):
            # do not show output_checker their own requests
            if metadata.author != user.username:
                requests.append(self._request(metadata))

        return requests

    def set_status(self, request: ReleaseRequest, status: Status, user: User):
        with transaction.atomic():
            # validate transition/permissions ahead of time
            self.check_status(request, status, user)
            # persist state change
            metadata = self._find_metadata(request.id)
            metadata.status = status
            metadata.save()
            super().set_status(request, status, user)

    def add_file_to_request(
        self,
        release_request: ReleaseRequest,
        relpath: Path,
        user: User,
        group_name: Optional[str] = "default",
    ) -> ReleaseRequest:
        # call super() to copy the file
        super().add_file_to_request(release_request, relpath, user, group_name)
        with transaction.atomic():
            # Get/create the FileGroupMetadata if it doesn't already exist
            filegroupmetadata = self._get_or_create_filegroupmetadata(
                release_request.id, group_name
            )
            # Check if this file is already on the request, in any group
            try:
                existing_file = RequestFileMetadata.objects.get(
                    filegroup__request_id=release_request.id, relpath=relpath
                )
            except RequestFileMetadata.DoesNotExist:
                # create the RequestFile
                RequestFileMetadata.objects.create(
                    relpath=str(relpath), filegroup=filegroupmetadata
                )
            else:
                raise self.APIException(
                    "File has already been added to request "
                    f"(in file group '{existing_file.filegroup.name}')"
                )

        # return a new request object with the updated groups
        return self._request(self._find_metadata(release_request.id))
