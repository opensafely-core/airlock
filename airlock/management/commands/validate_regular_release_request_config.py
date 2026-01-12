"""
Validate and report the config file for automated release requests
"""

from django.core.management.base import BaseCommand

from airlock.jobs.daily import create_regular_release_requests


class Command(BaseCommand):
    """
    Validate and report the config file for automated release requests
    """

    def handle(self, **options):
        release_requests_config = create_regular_release_requests.get_config_data()

        for release_request_data in release_requests_config:
            workspace = release_request_data.get("workspace_name", "unknown")
            self.stdout.write(f"\n======{workspace}======")
            try:
                create_regular_release_requests.validate_config_data(
                    release_request_data
                )
            except create_regular_release_requests.ConfigValidationError as e:
                self.stdout.write("Config errors found:")
                for err in str(e).split(";"):
                    self.stdout.write(f"- {err.strip()}")

            else:
                self.stdout.write("Config OK")
                for key, value in release_request_data.items():
                    self.stdout.write(f"{key}: {value}")
