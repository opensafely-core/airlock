import logging

from django.conf import settings
from django.core.management import call_command
from django_bgt import service

from airlock.management.commands.run_file_uploader import run_upload


logger = logging.getLogger(__name__)
logger.setLevel(level=logging.INFO)


@service(name="session_clearer", interval=60, leader=True)
def clear_expired_sessions() -> None:
    """Clear expired sessions, once every hour."""
    logger.info("Running clearsessions")
    call_command("clearsessions")


@service(name="file_uploader", interval=int(settings.UPLOAD_DELAY), leader=True)
def upload_files() -> bool:
    """Run the file uploader"""
    # Using a mock run_fn for now to force it to only run once. The service
    # will handle re-running.
    logger.info("Running file uploader")
    # run_upload returns False if there was nothing to do (and will wait for
    # UPLOAD_DELAY seconds before trying again), or True to retry immediately
    return run_upload()


@service(name="regular_release_requests", interval=24 * 60 * 60, leader=True)
def create_regular_release_requests() -> None:
    """Run the daily regular release requests"""
    logger.info("Running regular release requests")
    call_command("runjob", "create_regular_release_requests")
