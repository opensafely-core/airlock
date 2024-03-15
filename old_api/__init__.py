import hashlib
from datetime import datetime, timezone

import requests
from django.conf import settings

from old_api.schema import FileList, FileMetadata, UrlFileName


session = requests.Session()


def create_filelist(paths):
    files = []

    for relpath, abspath in paths:
        files.append(
            FileMetadata(
                name=UrlFileName(relpath),
                size=abspath.stat().st_size,
                sha256=hashlib.sha256(abspath.read_bytes()).hexdigest(),
                date=modified_time(abspath),
                url=UrlFileName(relpath),  # not needed, but has to be set
                metadata={"tool": "airlock"},
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
            "Authorization": settings.AIRLOCK_API_TOKEN,
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
            "Authorization": settings.AIRLOCK_API_TOKEN,
        },
    )

    response.raise_for_status()
    return response


def modified_time(path):
    mtime = path.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
