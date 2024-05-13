from django.core.management.base import BaseCommand

from airlock.business_logic import BusinessLogicLayer, Workspace
from airlock.types import UrlPath
from local_db.models import RequestFileMetadata


class Command(BaseCommand):
    def handle(self, **kwargs):
        for request_file in RequestFileMetadata.objects.all():  # pragma: no cover
            workspace = Workspace(name=request_file.request.workspace)
            try:
                manifest_data = workspace.get_manifest_for_file(
                    UrlPath(request_file.relpath)
                )
            except (BusinessLogicLayer.ManifestFileError, KeyError):
                print(
                    f"Could not update manifest.json data for {workspace.name}/{request_file.relpath}"
                )
                continue
            request_file.timestamp = manifest_data["timestamp"]
            request_file.size = manifest_data["size"]
            request_file.commit = manifest_data["commit"]
            request_file.repo = manifest_data["repo"]
            request_file.job_id = manifest_data["job_id"]
            request_file.row_count = manifest_data.get("row_count")
            request_file.col_count = manifest_data.get("col_count")
            request_file.save()
