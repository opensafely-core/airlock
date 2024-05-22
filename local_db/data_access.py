from pathlib import Path

from django.db import transaction

from airlock.business_logic import (
    AuditEvent,
    AuditEventType,
    BusinessLogicLayer,
    DataAccessLayerProtocol,
    FileReviewStatus,
    NotificationUpdateType,
    RequestFileType,
    RequestStatus,
)
from airlock.types import UrlPath
from local_db.models import (
    AuditLog,
    FileGroupComment,
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
            timestamp=file_metadata.timestamp,
            size=file_metadata.size,
            commit=file_metadata.commit,
            repo=file_metadata.repo,
            job_id=file_metadata.job_id,
            row_count=file_metadata.row_count,
            col_count=file_metadata.col_count,
            reviews=[
                self._filereview(file_review)
                for file_review in file_metadata.reviews.all()
            ],
        )

    def _filegroup(self, filegroup_metadata: FileGroupMetadata):
        """Unpack file group db data into FileGroup and RequestFile objects."""
        return dict(
            name=filegroup_metadata.name,
            context=filegroup_metadata.context,
            controls=filegroup_metadata.controls,
            updated_at=filegroup_metadata.updated_at,
            comments=[
                self._comment(comment)
                for comment in filegroup_metadata.comments.all().order_by("created_at")
            ],
            files=[
                self._request_file(file_metadata)
                for file_metadata in filegroup_metadata.request_files.all()
            ],
        )

    def _comment(self, comment: FileGroupComment):
        return {
            "comment": comment.comment,
            "author": comment.author,
            "created_at": comment.created_at,
        }

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

    def get_requests_for_workspace(self, workspace: str):
        return [
            self._request(request)
            for request in RequestMetadata.objects.filter(workspace=workspace).order_by(
                "created_at"
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
        request_id: str,
        relpath: UrlPath,
        file_id: str,
        group_name: str,
        filetype: RequestFileType,
        timestamp: int,
        size: int,
        commit: str,
        repo: str,
        job_id: str,
        row_count: int | None,
        col_count: int | None,
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
                    request_id=request_id, relpath=relpath
                )
                # We should never be able to attempt to add a file to a request
                # with a different filetype
                assert existing_file.filetype == filetype
            except RequestFileMetadata.DoesNotExist:
                # create the RequestFile
                RequestFileMetadata.objects.create(
                    request_id=request_id,
                    relpath=str(relpath),
                    file_id=file_id,
                    filegroup=filegroupmetadata,
                    filetype=filetype,
                    timestamp=timestamp,
                    size=size,
                    commit=commit,
                    repo=repo,
                    job_id=job_id,
                    row_count=row_count,
                    col_count=col_count,
                )
            else:
                raise BusinessLogicLayer.APIException(
                    f"{filetype.name.title()} file has already been added to request "
                    f"(in file group '{existing_file.filegroup.name}')"
                )

            self._create_audit_log(audit)

        # Return updated FileGroups data
        metadata = self._find_metadata(request_id)
        return self._get_filegroups(metadata)

    def delete_file_from_request(
        self,
        request_id: str,
        relpath: UrlPath,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            # defense in depth
            request = self._find_metadata(request_id)
            assert request.status == RequestStatus.PENDING

            try:
                request_file = RequestFileMetadata.objects.get(
                    request_id=request_id,
                    relpath=relpath,
                )
            except RequestFileMetadata.DoesNotExist:
                raise BusinessLogicLayer.FileNotFound(relpath)

            request_file.delete()
            self._create_audit_log(audit)

        # Return updated FileGroups data
        metadata = self._find_metadata(request_id)
        return self._get_filegroups(metadata)

    def withdraw_file_from_request(
        self,
        request_id: str,
        relpath: UrlPath,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            # defense in depth, we can only withdraw from active submitted requests
            request = self._find_metadata(request_id)
            assert request.status == RequestStatus.SUBMITTED

            try:
                request_file = RequestFileMetadata.objects.get(
                    request_id=request_id,
                    relpath=relpath,
                )
            except RequestFileMetadata.DoesNotExist:
                raise BusinessLogicLayer.FileNotFound(relpath)

            request_file.filetype = RequestFileType.WITHDRAWN
            request_file.save()

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
                request_id=request_id, relpath=relpath
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
                request_id=request_id, relpath=relpath
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
        group: str | None = None,
        exclude: set[AuditEventType] | None = None,
        size: int | None = None,
    ) -> list[AuditEvent]:
        qs = AuditLog.objects.all().order_by("-created_at")

        # TODO: we probably will need pagination?

        if user:
            qs = qs.filter(user=user)

        if workspace:
            qs = qs.filter(workspace=workspace)
        elif request:
            qs = qs.filter(request=request)

        if group:
            qs = qs.filter(extra__group=group)

        if exclude:
            qs = qs.exclude(type__in=exclude)

        if size is not None:
            qs = qs[:size]

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

    def _get_filegroup(self, request_id: str, group: str):
        try:
            return FileGroupMetadata.objects.get(request_id=request_id, name=group)
        except FileGroupMetadata.DoesNotExist:
            raise BusinessLogicLayer.FileNotFound(group)

    def group_edit(
        self,
        request_id: str,
        group: str,
        context: str,
        controls: str,
        audit: AuditEvent,
    ) -> list[NotificationUpdateType]:
        changed = []
        with transaction.atomic():
            filegroup = self._get_filegroup(request_id, group)
            if filegroup.context != context:
                changed.append(NotificationUpdateType.CONTEXT_EDITIED)
            if filegroup.controls != controls:
                changed.append(NotificationUpdateType.CONTROLS_EDITED)
            filegroup.context = context
            filegroup.controls = controls
            filegroup.save()
            self._create_audit_log(audit)
        return changed

    def group_comment(
        self,
        request_id: str,
        group: str,
        comment: str,
        username: str,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            filegroup = self._get_filegroup(request_id, group)

            FileGroupComment.objects.create(
                filegroup=filegroup,
                comment=comment,
                author=username,
            )

            self._create_audit_log(audit)
