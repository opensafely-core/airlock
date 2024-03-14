import errno

from django.core.management.base import BaseCommand

from airlock.business_logic import User, bll, store_file
from local_db.models import RequestFileMetadata


class Command(BaseCommand):
    def handle(self, **kwargs):
        empty_file_ids = RequestFileMetadata.objects.filter(file_id="")
        admin_user = User("admin", workspaces={}, output_checker=True)
        for file_meta in list(empty_file_ids):
            request_id = file_meta.filegroup.request_id
            request = bll.get_release_request(request_id, admin_user)
            original_path = request.root() / file_meta.relpath
            file_meta.file_id = store_file(request, original_path)
            file_meta.save()
            original_path.unlink()
            remove_empty_dirs(original_path.parent)


def remove_empty_dirs(path):
    while True:
        try:
            path.rmdir()
        except OSError as exc:
            if exc.errno == errno.ENOTEMPTY:
                break
            else:  # pragma: no cover
                raise
        path = path.parent
