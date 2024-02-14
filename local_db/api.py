from dataclasses import dataclass

from django.db import transaction

from airlock.api import ProviderAPI, ReleaseRequest, Status, User
from local_db.models import RequestMetadata


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
        )

    def _create_release_request(self, **kwargs):
        metadata = RequestMetadata.objects.create(**kwargs)
        return self._request(metadata)

    def _find_metadata(self, request_id: str):
        try:
            return RequestMetadata.objects.get(id=request_id)
        except RequestMetadata.DoesNotExist:
            raise self.ReleaseRequestNotFound(request_id)

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

    def get_requests_for_user(self, user: User):
        requests = []
        filter_args = {}

        # if not output checker, can only see your own requests
        if not user.output_checker:
            filter_args = {"author": user.username}

        for metadata in RequestMetadata.objects.filter(**filter_args):
            if user.output_checker or metadata.workspace in user.workspaces:
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
