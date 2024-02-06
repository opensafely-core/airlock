import hashlib
from datetime import datetime, timezone

import requests
from django.conf import settings

from old_api.schema import FileList, FileMetadata


session = requests.Session()
session.headers["Authorization"] = settings.AIRLOCK_API_TOKEN


def create_filelist(request):
    files = []
    root = request.root()

    for relpath in request.filelist():
        abspath = root / relpath
        stat = abspath.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        files.append(
            FileMetadata(
                name=str(relpath),
                size=stat.st_size,
                sha256=hashlib.sha256(abspath.read_bytes()).hexdigest(),
                date=mtime,
                url=str(relpath),  # not needed, but has to be set
                metadata={},  # not needed *yet*, but can't be None
            )
        )

    return FileList(files=files, metadata={"tool": "airlock"})


def create_release(workspace_name, release_json, username):
    """API call to job server to create a release."""
    response = session.post(
        url=f"{settings.AIRLOCK_API_ENDPOINT}/releases/workspace/{workspace_name}",
        data=release_json,
        headers={
            "OS-User": username,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    response.raise_for_status()

    return response.headers["Release-Id"]


def upload_file(release_id, relpath, abspath, username):
    """Upload file to job server."""
    response = session.post(
        url=f"{settings.AIRLOCK_API_ENDPOINT}/releases/release/{release_id}",
        data=abspath.open("rb"),
        headers={
            "OS-User": username,
            "Content-Disposition": f'attachment; filename="{relpath}"',
            "Content-Type": "application/octet-stream",
            "Accept": "application/json",
        },
    )

    response.raise_for_status()
    return response
