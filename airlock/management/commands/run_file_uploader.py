import logging
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from opentelemetry import trace

import old_api
from airlock.business_logic import bll
from airlock.enums import RequestStatus
from airlock.users import User
from services.tracing import instrument


logger = logging.getLogger(__name__)

# We need a user with the output-checker role to access some
# bll methods
system_user = User("system", output_checker=True)


class Command(BaseCommand):
    """
    Poll database for approved requests that still have files pending upload
    """

    def add_arguments(self, parser):
        # In production, we want this check to run forever. Using a
        # function means that we can test it on a finite number of loops.
        parser.add_argument("--run-fn", default=lambda: True)

    def handle(self, *args, **options):
        run_fn = options["run_fn"]

        logger.debug("Watching for tasks")

        while run_fn():  # pragma: no branch
            # Find approved requests
            approved_requests = bll.get_approved_requests(user=system_user)

            if not approved_requests:
                # No pending file uploads found; wait for UPLOAD_DELAY seconds
                # before checking again
                time.sleep(settings.UPLOAD_DELAY)
                continue

            for approved_request in approved_requests:
                # Find incomplete file uploads for this request
                # This retrieves ALL incomplete file uploads, including those that have
                # been reached the max attempt limit, so we can check for any
                # requests that should be set to released
                files_for_upload = get_upload_files_and_update_request_status(
                    approved_request
                )
                if not files_for_upload:
                    # All files are now uploaded, the status has been updated to released,
                    # nothing to do
                    continue

                workspace = bll.get_workspace(approved_request.workspace, system_user)
                for file_for_upload in files_for_upload:
                    if file_for_upload.upload_attempts >= settings.UPLOAD_MAX_ATTEMPTS:
                        logger.debug(
                            "Max upload attempts reached for %s - %s, skipping",
                            approved_request.id,
                            file_for_upload.relpath,
                        )
                        continue
                    # increment the retry attempts; if something goes wrong, we still
                    # want this to be updated
                    file_for_upload = bll.register_file_upload_attempt(
                        approved_request, file_for_upload.relpath
                    )

                    tracer = trace.get_tracer(
                        os.environ.get("OTEL_SERVICE_NAME", "airlock")
                    )
                    with tracer.start_as_current_span(
                        "file_uploader",
                        attributes={
                            "release_request": approved_request.id,
                            "workspace": approved_request.workspace,
                            "file": str(file_for_upload.relpath),
                        },
                    ) as span:
                        try:
                            do_upload_task(file_for_upload, approved_request, workspace)
                        except Exception as error:
                            # The most likely error here is old_api.FileUploadError, however
                            # we catch any unexpected exception here so we don't stop the task runner
                            # from running
                            span.record_exception(error)
                            logger.error(
                                "Upload for %s - %s failed (attempt %d of %d): %s",
                                approved_request.id,
                                file_for_upload.relpath,
                                file_for_upload.upload_attempts,
                                settings.UPLOAD_MAX_ATTEMPTS,
                                str(error),
                            )

                # After we've tried to upload all files for this request, check if
                # there are any still pending and set the request status now, so it's
                # done as soon as possible and doesn't have to wait on the next loop
                get_upload_files_and_update_request_status(approved_request)


@instrument
def do_upload_task(file_for_upload, release_request, workspace):
    """
    Perform an upload task.
    """
    old_api.upload_file(
        release_request.id,
        release_request.workspace,
        file_for_upload.relpath,
        workspace.abspath(file_for_upload.relpath),
        file_for_upload.released_by,
    )
    # mark the request file as uploaded and set the task completed time
    # we use the released_by user for this, for consistency with the
    # user who initiated the release
    bll.register_file_upload(
        release_request, file_for_upload.relpath, get_user_for_file(file_for_upload)
    )
    logger.info("File uploaded: %s - %s", release_request.id, file_for_upload.relpath)


def get_upload_files_and_update_request_status(release_request):
    """
    Get all files for upload for a given approved release request
    If there are no files left to upload, set status to released
    """
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
            get_user_for_file(last_uploaded_file),
        )
    return files_for_upload


def get_user_for_file(request_file):
    return User(username=request_file.released_by, output_checker=True)
