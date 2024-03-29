from pathlib import Path

from django.db import transaction

from airlock.business_logic import (
    AuditEvent,
    BusinessLogicLayer,
    DataAccessLayerProtocol,
    FileReviewStatus,
    RequestFileType,
    RequestStatus,
    UrlPath,
)
from local_db.models import (
    AuditLog,
    FileGroupMetadata,
    FileReview,
    RequestFileMetadata,
    RequestMetadata,
)


class LocalDBDataAccessLayer(DataAccessLayerProtocol):
    """
    Implementation of DataAccessLayerProtocol using local_db models to store data
    """

    def _request(self, metadata: RequestMetadata):
        """Unpack the db data into the Request object."""
        return dict(
            id=metadata.id,
            workspace=metadata.workspace,
            status=metadata.status,
            author=metadata.author,
            created_at=metadata.created_at,
            filegroups=self._get_filegroups(metadata),
        )

    def create_release_request(
        self,
        workspace: str,
        author: str,
        status: RequestStatus,
        audit: AuditEvent,
        id: str | None = None,  # noqa: A002
    ):
        # Note: id is created automatically, but can be set manually if needed
        with transaction.atomic():
            metadata = RequestMetadata.objects.create(
                workspace=workspace,
                author=author,
                status=status,
                id=id,  # noqa: A002
            )
            # ensure correct id
            audit.request = metadata.id
            self._create_audit_log(audit)

        return self._request(metadata)

    def _find_metadata(self, request_id: str):
        try:
            return RequestMetadata.objects.get(id=request_id)
        except RequestMetadata.DoesNotExist:
            raise BusinessLogicLayer.ReleaseRequestNotFound(request_id)

    def _request_file(self, file_metadata: RequestFileMetadata):
        return dict(
            relpath=Path(file_metadata.relpath),
            file_id=file_metadata.file_id,
            filetype=file_metadata.filetype,
            reviews=[
                self._filereview(file_review)
                for file_review in file_metadata.reviews.all()
            ],
        )

    def _filegroup(self, filegroup_metadata: FileGroupMetadata):
        """Unpack file group db data into FileGroup and RequestFile objects."""
        return dict(
            name=filegroup_metadata.name,
            files=[
                self._request_file(file_metadata)
                for file_metadata in filegroup_metadata.request_files.all()
            ],
        )

    def _get_filegroups(self, metadata: RequestMetadata):
        return {
            group_metadata.name: self._filegroup(group_metadata)
            for group_metadata in metadata.filegroups.all()
        }

    def _filereview(self, file_review: FileReview):
        """Convert a FileReview object into a dict"""
        return dict(
            reviewer=file_review.reviewer,
            status=file_review.status,
            created_at=file_review.created_at,
            updated_at=file_review.updated_at,
        )

    def _get_or_create_filegroupmetadata(self, request_id: str, group_name: str):
        metadata = self._find_metadata(request_id)
        groupmetadata, _ = FileGroupMetadata.objects.get_or_create(
            request=metadata, name=group_name
        )
        return groupmetadata

    def get_release_request(self, request_id: str):
        return self._request(self._find_metadata(request_id))

    def get_active_requests_for_workspace_by_user(self, workspace: str, username: str):
        return [
            self._request(request)
            for request in RequestMetadata.objects.filter(
                workspace=workspace,
                author=username,
                status__in=[RequestStatus.PENDING, RequestStatus.SUBMITTED],
            )
        ]

    def get_requests_authored_by_user(self, username: str):
        return [
            self._request(request)
            for request in RequestMetadata.objects.filter(author=username).order_by(
                "status"
            )
        ]

    def get_outstanding_requests_for_review(self):
        return [
            self._request(metadata)
            for metadata in RequestMetadata.objects.filter(
                status=RequestStatus.SUBMITTED
            )
        ]

    def set_status(self, request_id: str, status: RequestStatus, audit: AuditEvent):
        with transaction.atomic():
            # persist state change
            metadata = self._find_metadata(request_id)
            metadata.status = status
            metadata.save()
            self._create_audit_log(audit)

    def add_file_to_request(
        self,
        request_id,
        relpath: UrlPath,
        file_id: str,
        group_name: str,
        filetype: RequestFileType,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            # Get/create the FileGroupMetadata if it doesn't already exist
            filegroupmetadata = self._get_or_create_filegroupmetadata(
                request_id, group_name
            )
            # Check if this file is already on the request, in any group
            # A file (including supporting files) may only be on a request once.
            try:
                existing_file = RequestFileMetadata.objects.get(
                    filegroup__request_id=request_id, relpath=relpath
                )
                # We should never be able to attempt to add a file to a request
                # with a different filetype
                assert existing_file.filetype == filetype
            except RequestFileMetadata.DoesNotExist:
                # create the RequestFile
                RequestFileMetadata.objects.create(
                    relpath=str(relpath),
                    file_id=file_id,
                    filegroup=filegroupmetadata,
                    filetype=filetype,
                )
            else:
                raise BusinessLogicLayer.APIException(
                    "{filetype} file has already been added to request "
                    f"(in file group '{existing_file.filegroup.name}')"
                )

            self._create_audit_log(audit)

        # Return updated FileGroups data
        metadata = self._find_metadata(request_id)
        return self._get_filegroups(metadata)

    def approve_file(
        self, request_id: str, relpath: UrlPath, username: str, audit: AuditEvent
    ):
        with transaction.atomic():
            # nb. the business logic layer approve_file() should confirm that this path
            # is part of the request before calling this method
            request_file = RequestFileMetadata.objects.get(
                filegroup__request_id=request_id, relpath=relpath
            )

            review, _ = FileReview.objects.get_or_create(
                file=request_file, reviewer=username
            )
            review.status = FileReviewStatus.APPROVED
            review.save()

            self._create_audit_log(audit)

    def reject_file(
        self, request_id: str, relpath: UrlPath, username: str, audit: AuditEvent
    ):
        with transaction.atomic():
            request_file = RequestFileMetadata.objects.get(
                filegroup__request_id=request_id, relpath=relpath
            )

            review, _ = FileReview.objects.get_or_create(
                file=request_file, reviewer=username
            )
            review.status = FileReviewStatus.REJECTED
            review.save()

            self._create_audit_log(audit)

    def _create_audit_log(self, audit: AuditEvent) -> AuditLog:
        event = AuditLog.objects.create(
            type=audit.type,
            user=audit.user,
            workspace=audit.workspace,
            request=audit.request,
            path=str(audit.path) if audit.path else None,
            extra=audit.extra,
            created_at=audit.created_at,
        )
        return event

    def audit_event(self, audit: AuditEvent):
        with transaction.atomic():
            self._create_audit_log(audit)

    def get_audit_log(
        self,
        user: str | None = None,
        workspace: str | None = None,
        request: str | None = None,
    ) -> list[AuditEvent]:
        qs = AuditLog.objects.all().order_by("-created_at")

        # TODO: we probably will need pagination?

        if user:
            qs = qs.filter(user=user)

        if workspace:
            qs = qs.filter(workspace=workspace)
        elif request:
            qs = qs.filter(request=request)

        # Note: we ignore the type here as we haven't figured out how to make
        # EnumField type-correct yet
        return [
            AuditEvent(
                type=audit.type,  # type: ignore
                user=audit.user,
                workspace=audit.workspace,
                request=audit.request,
                path=UrlPath(audit.path) if audit.path else None,
                extra=audit.extra,
                created_at=audit.created_at,
            )
            for audit in qs
        ]
