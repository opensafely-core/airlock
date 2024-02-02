from dataclasses import dataclass

from django.conf import settings

from airlock.users import User
from airlock.workspace_api import ReleaseRequest, Workspace, generate_request_id


default_user = User(1, "test", [], True)


class FileWriterMixin:
    def path(self):  # pragma: no cover
        return NotImplemented()

    def mkdir(self, path):
        fp = self.path() / path
        fp.mkdir(parents=True, exist_ok=True)

    def write_file(self, path, contents=""):
        fp = self.path() / path
        self.mkdir(fp.parent)
        fp.write_text(contents)


@dataclass
class WorkspaceFactory(FileWriterMixin):
    name: str

    def __post_init__(self):
        self.path().mkdir(parents=True, exist_ok=True)

    def path(self):
        return settings.WORKSPACE_DIR / self.name

    def create_request(self, request_id):
        return ReleaseRequestFactory(request_id, self.name)

    def create_request_for_user(self, user=None):
        if user is None:
            user = default_user  # pragma: nocover
        request_id = generate_request_id(self.name, user)
        return ReleaseRequestFactory(request_id, self.name)

    def get(self):
        return Workspace(self.name)


@dataclass
class ReleaseRequestFactory(FileWriterMixin):
    request_id: str
    workspace: str

    def __post_init__(self):
        self.path().mkdir(parents=True, exist_ok=True)
        # ensure workspace dir exists
        WorkspaceFactory(self.workspace)

    def path(self):
        return settings.REQUEST_DIR / self.workspace / self.request_id

    def get(self):
        return ReleaseRequest(Workspace(self.workspace), self.request_id)
