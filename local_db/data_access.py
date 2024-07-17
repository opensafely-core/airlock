from django.db import transaction
from django.utils import timezone

from airlock.business_logic import (
    AuditEvent,
    AuditEventType,
    BusinessLogicLayer,
    CommentVisibility,
    DataAccessLayerProtocol,
    RequestFileType,
    RequestFileVote,
    RequestStatus,
    RequestStatusOwner,
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

        return metadata.to_dict()

    def _find_metadata(self, request_id: str):
        try:
            return RequestMetadata.objects.get(id=request_id)
        except RequestMetadata.DoesNotExist:
            raise BusinessLogicLayer.ReleaseRequestNotFound(request_id)

    def _get_or_create_filegroupmetadata(self, request_id: str, group_name: str):
        metadata = self._find_metadata(request_id)
        groupmetadata, _ = FileGroupMetadata.objects.get_or_create(
            request=metadata, name=group_name
        )
        return groupmetadata

    def get_release_request(self, request_id: str):
        return self._find_metadata(request_id).to_dict()

    def get_active_requests_for_workspace_by_user(self, workspace: str, username: str):
        # Requests in these statuses are still editable by either an
        # author or a reviewer, and are considered active
        editable_status = [
            status
            for status, owner in BusinessLogicLayer.STATUS_OWNERS.items()
            if owner != RequestStatusOwner.SYSTEM
        ]
        return [
            request.to_dict()
            for request in RequestMetadata.objects.filter(
                workspace=workspace,
                author=username,
                status__in=editable_status,
            )
        ]

    def get_requests_authored_by_user(self, username: str):
        return [
            request.to_dict()
            for request in RequestMetadata.objects.filter(author=username).order_by(
                "status"
            )
        ]

    def get_requests_for_workspace(self, workspace: str):
        return [
            request.to_dict()
            for request in RequestMetadata.objects.filter(workspace=workspace).order_by(
                "created_at"
            )
        ]

    def get_released_files_for_workspace(self, workspace: str):
        return set(
            RequestFileMetadata.objects.filter(
                request__workspace=workspace, released_at__isnull=False
            ).values_list("file_id", flat=True)
        )

    def get_requests_by_status(self, *states: RequestStatus):
        return [
            metadata.to_dict()
            for metadata in RequestMetadata.objects.filter(status__in=states)
        ]

    def set_status(self, request_id: str, status: RequestStatus, audit: AuditEvent):
        with transaction.atomic():
            # persist state change
            metadata = self._find_metadata(request_id)
            metadata.status = status
            metadata.save()
            self._create_audit_log(audit)

    def record_review(self, request_id: str, reviewer: str):
        with transaction.atomic():
            # persist reviewer state
            metadata = self._find_metadata(request_id)
            metadata.completed_reviews[reviewer] = timezone.now().isoformat()
            metadata.save()

    def start_new_turn(self, request_id: str):
        with transaction.atomic():
            metadata = self._find_metadata(request_id)
            metadata.turn_reviewers = ",".join(list(metadata.completed_reviews.keys()))
            metadata.completed_reviews = {}
            metadata.review_turn += 1
            metadata.save()

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
        return metadata.get_filegroups_to_dict()

    def delete_file_from_request(
        self,
        request_id: str,
        relpath: UrlPath,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            # defense in depth
            request = self._find_metadata(request_id)

            assert (
                BusinessLogicLayer.STATUS_OWNERS[request.status]
                == RequestStatusOwner.AUTHOR
            )

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
        return metadata.get_filegroups_to_dict()

    def withdraw_file_from_request(
        self,
        request_id: str,
        relpath: UrlPath,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            # defense in depth, we can only withdraw from active returned requests
            request = self._find_metadata(request_id)
            assert request.status == RequestStatus.RETURNED

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
        return metadata.get_filegroups_to_dict()

    def release_file(
        self, request_id: str, relpath: UrlPath, username: str, audit: AuditEvent
    ):
        with transaction.atomic():
            # nb. the business logic layer release_file() should confirm that this path
            # is part of the request before calling this method
            request_file = RequestFileMetadata.objects.get(
                request_id=request_id, relpath=relpath
            )

            request_file.released_at = timezone.now()
            request_file.released_by = username
            request_file.save()

            self._create_audit_log(audit)

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
            review.status = RequestFileVote.APPROVED
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
            review.status = RequestFileVote.REJECTED
            review.save()

            self._create_audit_log(audit)

    def reset_review_file(
        self, request_id: str, relpath: UrlPath, username: str, audit: AuditEvent
    ):
        with transaction.atomic():
            request_file = RequestFileMetadata.objects.get(
                request_id=request_id, relpath=relpath
            )
            try:
                review = FileReview.objects.get(file=request_file, reviewer=username)
            except FileReview.DoesNotExist:
                raise BusinessLogicLayer.FileReviewNotFound(relpath, username)

            review.delete()

            self._create_audit_log(audit)

    def mark_file_undecided(
        self, request_id: str, relpath: UrlPath, reviewer: str, audit: AuditEvent
    ):
        with transaction.atomic():
            request_file = RequestFileMetadata.objects.get(
                request_id=request_id, relpath=relpath
            )
            review = FileReview.objects.get(file=request_file, reviewer=reviewer)
            review.status = RequestFileVote.UNDECIDED
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
    ):
        with transaction.atomic():
            filegroup = self._get_filegroup(request_id, group)
            filegroup.context = context
            filegroup.controls = controls
            filegroup.save()
            self._create_audit_log(audit)

    def group_comment_create(
        self,
        request_id: str,
        group: str,
        comment: str,
        visibility: CommentVisibility,
        review_turn: int,
        username: str,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            filegroup = self._get_filegroup(request_id, group)

            FileGroupComment.objects.create(
                filegroup=filegroup,
                comment=comment,
                author=username,
                visibility=visibility,
                review_turn=review_turn,
            )

            self._create_audit_log(audit)

    def group_comment_delete(
        self,
        request_id: str,
        group: str,
        comment_id: str,
        username: str,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            # we can just get the comment directly by id
            comment = FileGroupComment.objects.get(
                id=comment_id,
            )
            # but let's verify we're looking at the right thing
            if not (
                comment.author == username
                and comment.filegroup.name == group
                and comment.filegroup.request.id == request_id
            ):
                raise BusinessLogicLayer.APIException(
                    "Comment for deletion has inconsistent attributes "
                    f"(in file group '{comment.filegroup.name}')"
                )
            comment.delete()

            self._create_audit_log(audit)
