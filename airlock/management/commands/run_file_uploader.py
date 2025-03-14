import argparse
import logging
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from opentelemetry import trace

import old_api
from airlock.business_logic import bll
from airlock.enums import RequestStatus
from airlock.types import FilePath
from services.tracing import instrument
from users.models import User


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Poll database for approved requests that still have files pending upload
    """

    def add_arguments(self, parser):
        # In production, we want this check to run forever. Using a
        # function means that we can test it on a finite number of loops.
        parser.add_argument("--run-fn", default=lambda: True, help=argparse.SUPPRESS)

    def handle(self, *args, **options):
        run_fn = options["run_fn"]

        logger.warning("File uploader started: watching for tasks")

        tracer = trace.get_tracer(os.environ.get("OTEL_SERVICE_NAME", "airlock"))

        # We need a user with the output-checker role to access some bll
        # methods. Note that this user is ephemeral, it does not get persisted
        # to the db
        system_user = User(
            user_id="system", api_data={"username": "system", "output_checker": True}
        )

        while run_fn():  # pragma: no branch
            # Find approved requests
            approved_requests = bll.get_approved_requests(user=system_user)

            if not approved_requests:
                # No pending file uploads found; wait for UPLOAD_DELAY seconds
                # before checking again
                time.sleep(settings.UPLOAD_DELAY)
                continue

            for approved_request in approved_requests:
                # Find incomplete file uploads that have not been attempted within
                # the past UPLOAD_RETRY_DELAY seconds for this request
                # This will also check for any requests that should be set to released
                files_for_upload = get_upload_files_and_update_request_status(
                    approved_request
                )
                if not files_for_upload:
                    # Either all files are now uploaded and the status has been updated to released,
                    # or files were retried within the past UPLOAD_RETRY_DELAY seconds.
                    # Either way, there's nothing to do
                    continue

                for file_for_upload in files_for_upload:
                    # increment the retry attempts; if something goes wrong, we still
                    # want this to be updated
                    file_for_upload = bll.register_file_upload_attempt(
                        approved_request, file_for_upload.file_path
                    )

                    with tracer.start_as_current_span(
                        "file_uploader",
                        attributes={
                            "release_request": approved_request.id,
                            "workspace": approved_request.workspace,
                            "group": file_for_upload.group,
                            "file": str(file_for_upload.file_path),
                            "username": file_for_upload.released_by.username,
                            "user_id": file_for_upload.released_by.user_id,
                        },
                    ) as span:
                        try:
                            do_upload_task(file_for_upload, approved_request)
                        except Exception as error:
                            # The most likely error here is old_api.FileUploadError, however
                            # we catch any unexpected exception here so we don't stop the task runner
                            # from running
                            span.record_exception(error)
                            logger.error(
                                "Upload for %s - %s/%s failed (attempt %d): %s",
                                approved_request.id,
                                file_for_upload.group,
                                file_for_upload.file_path,
                                file_for_upload.upload_attempts,
                                str(error),
                            )

                # After we've tried to upload all files for this request, check if
                # there are any still pending and set the request status now, so it's
                # done as soon as possible and doesn't have to wait on the next loop
                get_upload_files_and_update_request_status(approved_request)


@instrument
def do_upload_task(file_for_upload, release_request):
    """
    Perform an upload task.
    """
    old_api.upload_file(
        release_request.id,
        release_request.workspace,
        file_for_upload.file_path,
        release_request.abspath(
            FilePath(file_for_upload.group) / file_for_upload.file_path
        ),
        file_for_upload.released_by.username,
    )
    # mark the request file as uploaded and set the task completed time
    # we use the released_by user for this, for consistency with the
    # user who initiated the release
    bll.register_file_upload(
        release_request, file_for_upload.file_path, file_for_upload.released_by
    )
    logger.info("File uploaded: %s - %s", release_request.id, file_for_upload.file_path)


def get_upload_files_and_update_request_status(release_request):
    """
    Get all files for upload for a given approved release request
    If there are no files left to upload, set status to released
    """
    # Get all files for upload, irrespective of the last upload attempt time
    files_for_upload = bll.get_released_files_for_upload(release_request)
    if not files_for_upload:
        # All files are now uploaded, set the status to released
        last_uploaded_file = sorted(
            bll.get_released_files_for_request(release_request),
            key=lambda x: x.uploaded_at,
            reverse=True,
        )[0]
        bll.set_status(
            release_request,
            RequestStatus.RELEASED,
            last_uploaded_file.released_by,
        )
    return [
        request_file
        for request_file in files_for_upload
        if request_file.can_attempt_upload()
    ]
