from django.db import transaction
from django.utils import timezone

from airlock import exceptions, permissions
from airlock.business_logic import DataAccessLayerProtocol
from airlock.enums import (
    AuditEventType,
    RequestFileType,
    RequestFileVote,
    RequestStatus,
    RequestStatusOwner,
    Visibility,
)
from airlock.models import AuditEvent
from airlock.types import UrlPath
from local_db.models import (
    AuditLog,
    FileGroupComment,
    FileGroupMetadata,
    FileReview,
    RequestFileMetadata,
    RequestMetadata,
)
from users.models import User


class LocalDBDataAccessLayer(DataAccessLayerProtocol):
    """
    Implementation of DataAccessLayerProtocol using local_db models to store data
    """

    def create_release_request(
        self,
        workspace: str,
        author: User,
        status: RequestStatus,
        audit: AuditEvent,
    ):
        # Note: id is created automatically, but can be set manually if needed
        with transaction.atomic():
            metadata = RequestMetadata.objects.create(
                workspace=workspace,
                author=author.user_id,
                status=status,
            )
            # special case: ensure audit has correct id now that we know it
            audit.request = metadata.id
            self._create_audit_log(audit)

        return metadata.to_dict()

    def _find_metadata(self, request_id: str):
        try:
            return RequestMetadata.objects.get(id=request_id)
        except RequestMetadata.DoesNotExist:
            raise exceptions.ReleaseRequestNotFound(request_id)

    def _get_or_create_filegroupmetadata(self, request_id: str, group_name: str):
        metadata = self._find_metadata(request_id)
        groupmetadata, _ = FileGroupMetadata.objects.get_or_create(
            request=metadata, name=group_name
        )
        return groupmetadata

    def get_release_request(self, request_id: str):
        return self._find_metadata(request_id).to_dict()

    def get_active_requests_for_workspace_by_user(self, workspace: str, user: User):
        # Requests in these statuses are still editable by either an
        # author or a reviewer, and are considered active
        editable_status = [
            status
            for status, owner in permissions.STATUS_OWNERS.items()
            if owner != RequestStatusOwner.SYSTEM
        ]
        return [
            request.to_dict()
            for request in RequestMetadata.objects.filter(
                workspace=workspace,
                author=user.user_id,
                status__in=editable_status,
            )
        ]

    def get_requests_authored_by_user(self, user: User):
        return [
            request.to_dict()
            for request in RequestMetadata.objects.filter(author=user.user_id).order_by(
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

    def record_review(self, request_id: str, reviewer: User):
        with transaction.atomic():
            # persist reviewer state
            metadata = self._find_metadata(request_id)
            metadata.submitted_reviews[reviewer.user_id] = timezone.now().isoformat()
            metadata.save()

    def start_new_turn(self, request_id: str):
        with transaction.atomic():
            metadata = self._find_metadata(request_id)
            metadata.turn_reviewers = ",".join(list(metadata.submitted_reviews.keys()))
            metadata.submitted_reviews = {}
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
                raise exceptions.APIException(
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
                permissions.STATUS_OWNERS[request.status] == RequestStatusOwner.AUTHOR
            )

            try:
                request_file = RequestFileMetadata.objects.get(
                    request_id=request_id,
                    relpath=relpath,
                )

            except RequestFileMetadata.DoesNotExist:
                raise exceptions.FileNotFound(relpath)

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
                raise exceptions.FileNotFound(relpath)

            request_file.filetype = RequestFileType.WITHDRAWN
            request_file.save()

            self._create_audit_log(audit)

        # Return updated FileGroups data
        metadata = self._find_metadata(request_id)
        return metadata.get_filegroups_to_dict()

    def release_file(
        self, request_id: str, relpath: UrlPath, user: User, audit: AuditEvent
    ):
        with transaction.atomic():
            # nb. the business logic layer release_file() should confirm that this path
            # is part of the request before calling this method
            request_file = RequestFileMetadata.objects.get(
                request_id=request_id, relpath=relpath
            )

            request_file.released_at = timezone.now()
            request_file.released_by = user.user_id
            request_file.save()

            self._create_audit_log(audit)

    def get_released_files_for_request(self, request_id: str):
        released_files = RequestFileMetadata.objects.filter(
            request_id=request_id,
            released_at__isnull=False,
        )
        return [request_file.to_dict() for request_file in released_files]

    def register_file_upload_attempt(self, request_id: str, relpath: UrlPath):
        with transaction.atomic():
            request_file = RequestFileMetadata.objects.get(
                request_id=request_id, relpath=relpath
            )
            request_file.upload_attempts += 1
            request_file.upload_attempted_at = timezone.now()
            request_file.save()
        return request_file.to_dict()

    def register_file_upload(
        self, request_id: str, relpath: UrlPath, audit: AuditEvent
    ):
        with transaction.atomic():
            # nb. the business logic layer register_file_upload() should confirm that
            # this path is part of the request before calling this method
            request_file = RequestFileMetadata.objects.get(
                request_id=request_id, relpath=relpath
            )
            request_file.uploaded = True
            request_file.uploaded_at = timezone.now()
            request_file.save()

            self._create_audit_log(audit)

    def approve_file(
        self,
        request_id: str,
        relpath: UrlPath,
        review_turn: int,
        user: User,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            # nb. the business logic layer approve_file() should confirm that this path
            # is part of the request before calling this method
            request_file = RequestFileMetadata.objects.get(
                request_id=request_id, relpath=relpath
            )

            review, _ = FileReview.objects.get_or_create(
                file=request_file, reviewer=user.user_id
            )
            review.status = RequestFileVote.APPROVED
            review.review_turn = review_turn
            review.save()

            self._create_audit_log(audit)

    def request_changes_to_file(
        self,
        request_id: str,
        relpath: UrlPath,
        review_turn: int,
        user: User,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            request_file = RequestFileMetadata.objects.get(
                request_id=request_id, relpath=relpath
            )

            review, _ = FileReview.objects.get_or_create(
                file=request_file, reviewer=user.user_id
            )
            review.status = RequestFileVote.CHANGES_REQUESTED
            review.review_turn = review_turn
            review.save()

            self._create_audit_log(audit)

    def reset_review_file(
        self, request_id: str, relpath: UrlPath, user: User, audit: AuditEvent
    ):
        with transaction.atomic():
            request_file = RequestFileMetadata.objects.get(
                request_id=request_id, relpath=relpath
            )
            try:
                review = FileReview.objects.get(
                    file=request_file, reviewer=user.user_id
                )
            except FileReview.DoesNotExist:
                raise exceptions.FileReviewNotFound(relpath, user.user_id)

            review.delete()

            self._create_audit_log(audit)

    def mark_file_undecided(
        self,
        request_id: str,
        relpath: UrlPath,
        review_turn: int,
        reviewer: User,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            request_file = RequestFileMetadata.objects.get(
                request_id=request_id, relpath=relpath
            )
            review = FileReview.objects.get(
                file=request_file, reviewer=reviewer.user_id
            )
            review.status = RequestFileVote.UNDECIDED
            review.review_turn = review_turn
            review.save()

            self._create_audit_log(audit)

    def _create_audit_log(self, audit: AuditEvent) -> AuditLog:
        event = AuditLog.objects.create(
            type=audit.type,
            user=audit.user.user_id,
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
                user=User.objects.get(pk=audit.user),
                workspace=audit.workspace,
                request=audit.request,
                path=UrlPath(audit.path) if audit.path else None,
                extra=audit.extra,
                created_at=audit.created_at,
                hidden=audit.hidden,
            )
            for audit in qs
        ]

    def hide_audit_events_for_turn(self, request_id: str, review_turn: int):
        with transaction.atomic():
            AuditLog.objects.filter(
                request=request_id, extra__review_turn=str(review_turn)
            ).update(hidden=True)

    def _get_filegroup(self, request_id: str, group: str):
        try:
            return FileGroupMetadata.objects.get(request_id=request_id, name=group)
        except FileGroupMetadata.DoesNotExist:
            raise exceptions.FileNotFound(group)

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
        visibility: Visibility,
        review_turn: int,
        user: User,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            filegroup = self._get_filegroup(request_id, group)

            FileGroupComment.objects.create(
                filegroup=filegroup,
                comment=comment,
                author=user.user_id,
                visibility=visibility,
                review_turn=review_turn,
            )

            self._create_audit_log(audit)

    def group_comment_delete(
        self,
        request_id: str,
        group: str,
        comment_id: str,
        user: User,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            # we can just get the comment directly by id
            comment = FileGroupComment.objects.get(
                id=comment_id,
            )
            # but let's verify we're looking at the right thing
            if not (
                comment.author == user.user_id
                and comment.filegroup.name == group
                and comment.filegroup.request.id == request_id
            ):
                raise exceptions.APIException(
                    "Comment for deletion has inconsistent attributes "
                    f"(in file group '{comment.filegroup.name}')"
                )
            comment.delete()

            self._create_audit_log(audit)

    def group_comment_visibility_public(
        self,
        request_id: str,
        group: str,
        comment_id: str,
        user: User,
        audit: AuditEvent,
    ):
        with transaction.atomic():
            # we can just get the comment directly by id
            comment = FileGroupComment.objects.get(
                id=comment_id,
            )
            release_request = RequestMetadata.objects.get(
                id=request_id,
            )
            # but let's verify we're looking at the right thing
            if not (
                comment.author == user.user_id
                and comment.filegroup.name == group
                and comment.filegroup.request.id == request_id
                and release_request.review_turn == comment.review_turn
            ):
                raise exceptions.APIException(
                    "Comment for deletion has inconsistent attributes "
                    f"(in file group '{comment.filegroup.name}')"
                )

            comment.visibility = Visibility.PUBLIC
            comment.save()

            self._create_audit_log(audit)
