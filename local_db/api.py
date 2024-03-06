from pathlib import Path

from django.db import transaction

from airlock.api import (
    DataAccessLayerProtocol,
    ProviderAPI,
    Status,
)
from local_db.models import FileGroupMetadata, RequestFileMetadata, RequestMetadata


class LocalDBDataAccessLayer(DataAccessLayerProtocol):
    """
    Implementation of DataAccessLayerProtocol using local_db models to store data
    """

    def _request(self, metadata: RequestMetadata = None):
        """Unpack the db data into the Request object."""
        return dict(
            id=metadata.id,
            workspace=metadata.workspace,
            status=metadata.status,
            author=metadata.author,
            created_at=metadata.created_at,
            filegroups=self._get_filegroups(metadata),
        )

    def create_release_request(self, **kwargs):
        metadata = RequestMetadata.objects.create(**kwargs)
        return self._request(metadata)

    def _find_metadata(self, request_id: str):
        try:
            return RequestMetadata.objects.get(id=request_id)
        except RequestMetadata.DoesNotExist:
            raise ProviderAPI.ReleaseRequestNotFound(request_id)

    def _filegroup(self, filegroup_metadata: FileGroupMetadata):
        """Unpack file group db data into FileGroup and RequestFile objects."""
        return dict(
            name=filegroup_metadata.name,
            files=[
                dict(relpath=Path(file_metadata.relpath))
                for file_metadata in filegroup_metadata.request_files.all()
            ],
        )

    def _get_filegroups(self, metadata: RequestMetadata):
        return {
            group_metadata.name: self._filegroup(group_metadata)
            for group_metadata in metadata.filegroups.all()
        }

    def _get_or_create_filegroupmetadata(self, request_id: str, group_name: str):
        metadata = self._find_metadata(request_id)
        groupmetadata, _ = FileGroupMetadata.objects.get_or_create(
            request=metadata, name=group_name
        )
        return groupmetadata

    def get_release_request(self, request_id: str):
        return self._request(self._find_metadata(request_id))

    def get_current_request(
        self, workspace: str, username: str, user_workspaces: list[str], create=False
    ):
        requests = list(
            RequestMetadata.objects.filter(
                workspace=workspace,
                author=username,
                status__in=[Status.PENDING, Status.SUBMITTED],
            )
        )
        n = len(requests)
        if n > 1:
            raise Exception(
                f"Multiple active release requests for user {username} in workspace {workspace}"
            )
        elif n == 1:
            return self._request(requests[0])
        elif create:
            # To create a request, you must have explicit workspace permissions.
            # Output checkers can view all workspaces, but are not allowed to
            # create requests for all workspaces.
            if workspace not in user_workspaces:
                raise ProviderAPI.RequestPermissionDenied(workspace)

            return self.create_release_request(
                workspace=workspace,
                author=username,
            )

    def get_requests_authored_by_user(self, username: str, user_workspaces: list[str]):
        requests = []

        for metadata in RequestMetadata.objects.filter(author=username).order_by(
            "status"
        ):
            # to create a request, user *must* have explicit workspace
            # permissions - being an output checker is not enough
            if metadata.workspace in user_workspaces:
                requests.append(self._request(metadata))

        return requests

    def get_outstanding_requests_for_review(
        self, username: str, user_is_output_checker: bool
    ):
        requests = []

        if not user_is_output_checker:
            return []

        for metadata in RequestMetadata.objects.filter(status=Status.SUBMITTED):
            # do not show output_checker their own requests
            if metadata.author != username:
                requests.append(self._request(metadata))

        return requests

    def set_status(self, request_id: str, status: Status):
        with transaction.atomic():
            # persist state change
            metadata = self._find_metadata(request_id)
            metadata.status = status
            metadata.save()

    def add_file_to_request(self, request_id, relpath: Path, group_name: str):
        with transaction.atomic():
            # Get/create the FileGroupMetadata if it doesn't already exist
            filegroupmetadata = self._get_or_create_filegroupmetadata(
                request_id, group_name
            )
            # Check if this file is already on the request, in any group
            try:
                existing_file = RequestFileMetadata.objects.get(
                    filegroup__request_id=request_id, relpath=relpath
                )
            except RequestFileMetadata.DoesNotExist:
                # create the RequestFile
                RequestFileMetadata.objects.create(
                    relpath=str(relpath), filegroup=filegroupmetadata
                )
            else:
                raise ProviderAPI.APIException(
                    "File has already been added to request "
                    f"(in file group '{existing_file.filegroup.name}')"
                )

        # Return updated FileGroups data
        metadata = self._find_metadata(request_id)
        return self._get_filegroups(metadata)
