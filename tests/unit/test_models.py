import hashlib
import json
from hashlib import file_digest
from io import BytesIO

import pytest
from django.conf import settings

from airlock import exceptions, permissions
from airlock.enums import (
    RequestFileType,
    RequestStatus,
    ReviewTurnPhase,
    Visibility,
    WorkspaceFileStatus,
)
from airlock.models import (
    CodeRepo,
    Workspace,
)
from airlock.types import FileMetadata, UrlPath
from tests import factories


pytestmark = pytest.mark.django_db


def test_workspace_container():
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo/bar.html")

    foo_bar_relpath = UrlPath("foo/bar.html")

    assert workspace.root() == settings.WORKSPACE_DIR / "workspace"
    assert workspace.get_id() == "workspace"
    assert workspace.released_files == set()
    assert (
        workspace.get_url(foo_bar_relpath) == "/workspaces/view/workspace/foo/bar.html"
    )
    assert (
        "/workspaces/content/workspace/foo/bar.html?cache_id="
        in workspace.get_contents_url(foo_bar_relpath)
    )
    plaintext_contents_url = workspace.get_contents_url(foo_bar_relpath, plaintext=True)
    assert (
        "/workspaces/content/workspace/foo/bar.html?cache_id=" in plaintext_contents_url
    )
    assert "&plaintext=true" in plaintext_contents_url

    assert workspace.request_filetype(UrlPath("path")) is None  # type: ignore


def test_workspace_from_directory_errors():
    with pytest.raises(exceptions.WorkspaceNotFound):
        Workspace.from_directory("workspace", {})

    (settings.WORKSPACE_DIR / "workspace").mkdir()
    with pytest.raises(exceptions.ManifestFileError):
        Workspace.from_directory("workspace")

    manifest_path = settings.WORKSPACE_DIR / "workspace/metadata/manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(":")
    with pytest.raises(exceptions.ManifestFileError):
        Workspace.from_directory("workspace")


def test_workspace_request_filetype(bll):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo/bar.txt")
    assert workspace.request_filetype(UrlPath("foo/bar.txt")) is None  # type: ignore


def test_workspace_manifest_for_file():
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo/bar.csv", "c1,c2,c3\n1,2,3\n4,5,6")

    file_manifest = workspace.get_manifest_for_file(UrlPath("foo/bar.csv"))
    assert file_manifest["row_count"] == 2
    assert file_manifest["col_count"] == 3


def test_workspace_manifest_for_file_not_found(bll):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo/bar.txt")
    manifest_path = workspace.root() / "metadata/manifest.json"
    manifest_data = json.loads(manifest_path.read_text())
    manifest_data["outputs"] = {}
    manifest_path.write_text(json.dumps(manifest_data))

    workspace = bll.get_workspace(
        "workspace", factories.create_airlock_user(workspaces=["workspace"])
    )
    with pytest.raises(exceptions.ManifestFileError):
        workspace.get_manifest_for_file(UrlPath("foo/bar.txt"))


def test_get_file_metadata():
    workspace = factories.create_workspace("workspace")

    # non-existent file
    with pytest.raises(exceptions.FileNotFound):
        workspace.get_file_metadata(UrlPath("metadata/foo.log"))

    # directory
    (workspace.root() / "directory").mkdir()
    with pytest.raises(AssertionError):
        workspace.get_file_metadata(UrlPath("directory")) is None

    # small log file
    factories.write_workspace_file(
        workspace, "metadata/foo.log", contents="foo", manifest=False
    )

    from_file = workspace.get_file_metadata(UrlPath("metadata/foo.log"))
    assert isinstance(from_file, FileMetadata)
    assert from_file.size == 3
    assert from_file.timestamp is not None
    assert from_file.content_hash == hashlib.sha256(b"foo").hexdigest()

    # larger output file
    contents = "x," * 1024 * 1024
    factories.write_workspace_file(
        workspace, "output/bar.csv", contents=contents, manifest=True
    )

    from_metadata = workspace.get_file_metadata(UrlPath("output/bar.csv"))
    assert isinstance(from_metadata, FileMetadata)
    assert from_metadata.size == len(contents)
    assert from_metadata.timestamp is not None
    assert (
        from_metadata.content_hash
        == hashlib.sha256(contents.encode("utf8")).hexdigest()
    )


def test_workspace_get_workspace_archived_ongoing(bll):
    factories.create_workspace("normal_workspace")
    factories.create_workspace("archived_workspace")
    factories.create_workspace("not_ongoing")
    user = factories.create_airlock_user(
        "user",
        workspaces={
            "normal_workspace": factories.create_api_workspace(project="project-1"),
            "archived_workspace": factories.create_api_workspace(
                project="project-1", archived=True
            ),
            "not_ongoing": factories.create_api_workspace(
                project="project-2", ongoing=False
            ),
        },
    )
    checker = factories.create_airlock_user("checker", output_checker=True)

    active_workspace = bll.get_workspace("normal_workspace", user)
    archived_workspace = bll.get_workspace("archived_workspace", user)
    inactive_project = bll.get_workspace("not_ongoing", user)
    assert not active_workspace.is_archived()
    assert active_workspace.project().is_ongoing
    assert active_workspace.is_active()
    assert active_workspace.display_name() == "normal_workspace"
    assert active_workspace.project().display_name() == "project-1"

    assert archived_workspace.is_archived()
    assert archived_workspace.project().is_ongoing
    assert not archived_workspace.is_active()
    assert archived_workspace.display_name() == "archived_workspace (ARCHIVED)"
    assert archived_workspace.project().display_name() == "project-1"

    assert not inactive_project.is_archived()
    assert not inactive_project.project().is_ongoing
    assert not inactive_project.is_active()
    assert inactive_project.display_name() == "not_ongoing"
    assert inactive_project.project().display_name() == "project-2 (INACTIVE)"

    for workspace_name in ["normal_workspace", "archived_workspace", "not_ongoing"]:
        workspace = bll.get_workspace(workspace_name, checker)
        assert workspace.is_archived() is None
        assert bll.get_workspace(workspace_name, checker).project().is_ongoing
        assert workspace.display_name() == workspace_name
        assert "INACTIVE" not in workspace.project().display_name()


def test_workspace_get_workspace_file_status(bll):
    path = UrlPath("foo/bar.txt")
    workspace = factories.create_workspace("workspace")
    user = factories.create_airlock_user(workspaces=["workspace"])

    with pytest.raises(exceptions.FileNotFound):
        workspace.get_workspace_file_status(path)

    factories.write_workspace_file(workspace, path, contents="foo")
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNRELEASED

    release_request = factories.create_release_request(workspace, user=user)
    # refresh workspace
    workspace = bll.get_workspace("workspace", user)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNRELEASED

    factories.add_request_file(release_request, "group", path)
    # refresh workspace
    workspace = bll.get_workspace("workspace", user)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNDER_REVIEW

    factories.write_workspace_file(workspace, path, contents="changed")
    assert (
        workspace.get_workspace_file_status(path) == WorkspaceFileStatus.CONTENT_UPDATED
    )


def test_workspace_get_released_files(bll, mock_old_api):
    path = UrlPath("foo/bar.txt")
    path1 = UrlPath("foo/supporting_bar.txt")
    factories.create_request_at_status(
        "workspace",
        RequestStatus.RELEASED,
        files=[
            factories.request_file(
                path=path,
                contents="foo",
                approved=True,
                filetype=RequestFileType.OUTPUT,
            ),
            factories.request_file(
                path=path1,
                contents="bar",
                filetype=RequestFileType.SUPPORTING,
            ),
        ],
    )
    user = factories.create_airlock_user("test", workspaces=["workspace"])
    workspace = bll.get_workspace("workspace", user)
    # supporting file is not considered a released file
    assert len(workspace.released_files) == 1
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.RELEASED
    assert workspace.get_workspace_file_status(path1) == WorkspaceFileStatus.UNRELEASED


def test_request_returned_get_workspace_file_status(bll):
    user = factories.create_airlock_user(workspaces=["workspace"])
    path = "file1.txt"
    workspace_file = factories.request_file(
        group="group",
        contents="1",
        approved=True,
        path=path,
    )
    factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[
            workspace_file,
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", user)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNRELEASED


def test_request_pending_not_author_get_workspace_file_status(bll):
    user = factories.create_airlock_user(workspaces=["workspace"])
    path = "file1.txt"
    workspace_file = factories.request_file(
        group="group",
        contents="1",
        path=path,
    )
    factories.create_request_at_status(
        "workspace",
        status=RequestStatus.PENDING,
        files=[
            workspace_file,
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", user)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNRELEASED


def test_request_pending_author_get_workspace_file_status(bll):
    status = RequestStatus.PENDING

    author = factories.create_airlock_user("author", ["workspace"], False)
    workspace = factories.create_workspace("workspace")
    path = UrlPath("path/file.txt")

    workspace_file = factories.request_file(
        group="group",
        path=path,
        contents="1",
        user=author,
        changes_requested=True,
    )

    factories.create_request_at_status(
        workspace.name,
        author=author,
        status=status,
        files=[
            workspace_file,
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNDER_REVIEW


def test_request_returned_author_get_workspace_file_status(bll):
    status = RequestStatus.RETURNED

    author = factories.create_airlock_user("author", ["workspace"], False)
    workspace = factories.create_workspace("workspace")
    path = UrlPath("path/file.txt")

    workspace_file = factories.request_file(
        group="group",
        path=path,
        contents="1",
        user=author,
        changes_requested=True,
    )

    factories.create_request_at_status(
        workspace.name,
        author=author,
        status=status,
        files=[
            workspace_file,
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNDER_REVIEW


def test_request_container():
    release_request = factories.create_release_request("workspace")
    release_request = factories.add_request_file(release_request, "group", "bar.html")
    rid = release_request.get_id()

    assert release_request.root() == settings.REQUEST_DIR / "workspace" / rid

    assert (
        release_request.get_url("group/bar.html")
        == f"/requests/view/{rid}/group/bar.html"
    )
    assert (
        f"/requests/content/{rid}/group/bar.html?cache_id="
        in release_request.get_contents_url(UrlPath("group/bar.html"))
    )
    plaintext_contents_url = release_request.get_contents_url(
        UrlPath("group/bar.html"), plaintext=True
    )
    assert f"/requests/content/{rid}/group/bar.html?cache_id=" in plaintext_contents_url
    assert "&plaintext=true" in plaintext_contents_url


def test_request_file_manifest_data(mock_notifications, bll):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "bar.txt")
    user = factories.create_airlock_user(workspaces=["workspace"])
    release_request = factories.create_release_request(workspace, user=user)

    # modify the manifest data to known values for asserts
    manifest_path = workspace.root() / "metadata/manifest.json"
    manifest_data = json.loads(manifest_path.read_text())
    file_manifest = manifest_data["outputs"]["bar.txt"]
    file_manifest.update(
        {
            "job_id": "job-bar",
            "size": 10,
            "commit": "abcd",
            "timestamp": 1715000000,
        }
    )
    manifest_path.write_text(json.dumps(manifest_data))

    bll.add_file_to_request(release_request, UrlPath("bar.txt"), user, "group")

    request_file = release_request.filegroups["group"].files[UrlPath("bar.txt")]
    assert request_file.timestamp == 1715000000
    assert request_file.commit == "abcd"
    assert request_file.job_id == "job-bar"
    assert request_file.size == 10
    assert request_file.row_count is None
    assert request_file.col_count is None


def test_request_file_manifest_data_content_hash_mismatch(mock_notifications, bll):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "bar.txt")
    user = factories.create_airlock_user(workspaces=["workspace"])
    release_request = factories.create_release_request(workspace, user=user)

    # modify the manifest data to known values for asserts
    manifest = workspace.root() / "metadata/manifest.json"
    manifest_data = json.loads(manifest.read_text())
    file_manifest = manifest_data["outputs"]["bar.txt"]
    file_manifest.update(
        {
            "content_hash": file_digest(BytesIO(b"foo"), "sha256").hexdigest(),
        }
    )
    manifest.write_text(json.dumps(manifest_data))

    with pytest.raises(AssertionError):
        bll.add_file_to_request(release_request, UrlPath("bar.txt"), user, "group")


def test_request_file_upload_in_progress_failed(mock_notifications, mock_old_api, bll):
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.APPROVED,
        files=[
            factories.request_file(contents="1", approved=True, path="test/file1.txt")
        ],
    )
    relpath = UrlPath("test/file1.txt")
    request_file = release_request.get_request_file_from_output_path(relpath)
    assert request_file.upload_in_progress()
    assert request_file.upload_attempts == 0

    bll.register_file_upload_attempt(release_request, relpath)
    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_output_path(relpath)
    assert request_file.upload_in_progress()
    assert request_file.upload_attempts == 1

    for _ in range(settings.UPLOAD_MAX_ATTEMPTS - 1):
        bll.register_file_upload_attempt(release_request, relpath)

    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_output_path(relpath)
    assert not request_file.upload_in_progress()
    assert request_file.upload_attempts == settings.UPLOAD_MAX_ATTEMPTS
    assert request_file.upload_failed()


def test_request_can_be_rereleased(mock_notifications, mock_old_api, bll):
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.APPROVED,
        files=[
            factories.request_file(contents="1", approved=True, path="test/file1.txt"),
            factories.request_file(contents="2", approved=True, path="test/file2.txt"),
        ],
    )
    assert release_request.can_be_released()
    assert not release_request.can_be_rereleased()
    assert release_request.upload_in_progress()

    for relpath in [UrlPath("test/file1.txt"), UrlPath("test/file2.txt")]:
        for _ in range(settings.UPLOAD_MAX_ATTEMPTS):
            bll.register_file_upload_attempt(release_request, relpath)
    release_request = factories.refresh_release_request(release_request)

    assert release_request.can_be_released()
    assert release_request.can_be_rereleased()
    assert not release_request.upload_in_progress()


def test_request_upload_in_progress(mock_notifications, mock_old_api, bll):
    checker = factories.get_default_output_checkers()[0]
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.APPROVED,
        files=[
            factories.request_file(contents="1", approved=True, path="test/file1.txt"),
            factories.request_file(contents="2", approved=True, path="test/file2.txt"),
            factories.request_file(contents="3", approved=True, path="test/file3.txt"),
        ],
    )

    assert release_request.upload_in_progress()
    # upload file 1, files 2 and 3 still in progress
    bll.register_file_upload(release_request, UrlPath("test/file1.txt"), checker)

    release_request = factories.refresh_release_request(release_request)
    assert release_request.upload_in_progress()

    # max out attempts for file 2, file 3 still in progress
    for _ in range(settings.UPLOAD_MAX_ATTEMPTS):
        bll.register_file_upload_attempt(release_request, UrlPath("test/file2.txt"))
    release_request = factories.refresh_release_request(release_request)
    assert release_request.upload_in_progress()

    # upload file 3
    bll.register_file_upload(release_request, UrlPath("test/file3.txt"), checker)
    release_request = factories.refresh_release_request(release_request)
    assert not release_request.upload_in_progress()
    assert release_request.can_be_rereleased()


def test_code_repo_container():
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo.txt")
    repo = factories.create_repo(workspace)

    assert repo.get_id() == f"workspace@{repo.commit[:7]}"
    assert (
        repo.get_url(UrlPath("project.yaml"))
        == f"/code/view/workspace/{repo.commit}/project.yaml"
    )
    assert (
        f"/code/contents/workspace/{repo.commit}/project.yaml?cache_id="
        in repo.get_contents_url(UrlPath("project.yaml"))
    )

    plaintext_contents_url = repo.get_contents_url(
        UrlPath("project.yaml"), plaintext=True
    )
    assert (
        f"/code/contents/workspace/{repo.commit}/project.yaml?cache_id="
        in plaintext_contents_url
    )
    assert "&plaintext=true" in plaintext_contents_url

    assert repo.request_filetype(UrlPath("path")) == RequestFileType.CODE


def test_request_status_ownership(bll):
    """Test every RequestStatus has been assigned an ownership"""
    missing_states = set(RequestStatus) - permissions.STATUS_OWNERS.keys()
    assert missing_states == set()


def test_request_all_files_by_name(bll):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    path = UrlPath("path/file.txt")
    supporting_path = UrlPath("path/supporting_file.txt")

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(
                group="default",
                path=supporting_path,
                filetype=RequestFileType.SUPPORTING,
            ),
            factories.request_file(group="default", path=path),
        ],
    )

    # all_files_by_name consists of output files and supporting files
    assert release_request.all_files_by_name.keys() == {path, supporting_path}

    filegroup = release_request.filegroups["default"]
    assert len(filegroup.files) == 2
    assert len(filegroup.output_files) == 1
    assert len(filegroup.supporting_files) == 1


def test_request_release_get_request_file_from_urlpath(bll):
    path = UrlPath("foo/bar.txt")
    supporting_path = UrlPath("foo/bar1.txt")

    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(
                group="default",
                path=supporting_path,
                filetype=RequestFileType.SUPPORTING,
            ),
            factories.request_file(group="default", path=path),
        ],
    )

    with pytest.raises(exceptions.FileNotFound):
        release_request.get_request_file_from_urlpath("badgroup" / path)

    with pytest.raises(exceptions.FileNotFound):
        release_request.get_request_file_from_urlpath("default/does/not/exist")

    request_file = release_request.get_request_file_from_urlpath("default" / path)
    assert request_file.relpath == path


def test_request_release_abspath(bll):
    path = UrlPath("foo/bar.txt")
    supporting_path = UrlPath("foo/bar1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(
                group="default",
                path=supporting_path,
                filetype=RequestFileType.SUPPORTING,
            ),
            factories.request_file(group="default", path=path),
        ],
    )

    assert release_request.abspath("default" / path).exists()
    assert release_request.abspath("default" / supporting_path).exists()


def test_request_release_request_filetype(bll):
    path = UrlPath("foo/bar.txt")
    supporting_path = UrlPath("foo/bar1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(
                group="default",
                path=supporting_path,
                filetype=RequestFileType.SUPPORTING,
            ),
            factories.request_file(group="default", path=path),
        ],
    )

    assert release_request.request_filetype("default" / path) == RequestFileType.OUTPUT
    assert (
        release_request.request_filetype("default" / supporting_path)
        == RequestFileType.SUPPORTING
    )


def setup_empty_release_request():
    author = factories.create_airlock_user("author", ["workspace"], False)
    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )
    return release_request, path, author


def test_get_visible_comments_for_group_class(bll):
    author = factories.create_airlock_user("author", workspaces=["workspace"])
    checkers = factories.get_default_output_checkers()

    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        author=author,
        files=[factories.request_file(group="group", path="file.txt", approved=True)],
    )

    bll.group_comment_create(
        release_request,
        "group",
        "turn 1 checker 0 private",
        Visibility.PRIVATE,
        checkers[0],
    )
    bll.group_comment_create(
        release_request,
        "group",
        "turn 1 checker 1 private",
        Visibility.PRIVATE,
        checkers[1],
    )
    bll.group_comment_create(
        release_request,
        "group",
        "turn 1 checker 0 public",
        Visibility.PUBLIC,
        checkers[0],
    )

    release_request = factories.refresh_release_request(release_request)
    assert release_request.review_turn == 1
    assert release_request.get_turn_phase() == ReviewTurnPhase.INDEPENDENT

    def get_comment_metadata(user):
        return [
            m for _, m in release_request.get_visible_comments_for_group("group", user)
        ]

    assert get_comment_metadata(checkers[0]) == ["comment_blinded", "comment_blinded"]
    assert get_comment_metadata(checkers[1]) == ["comment_blinded"]
    assert get_comment_metadata(author) == []

    factories.submit_independent_review(release_request)

    release_request = factories.refresh_release_request(release_request)
    assert release_request.review_turn == 1
    assert release_request.get_turn_phase() == ReviewTurnPhase.CONSOLIDATING

    for checker in checkers:
        assert get_comment_metadata(checker) == [
            "comment_private",
            "comment_private",
            "comment_public",
        ]

    assert get_comment_metadata(author) == []

    bll.return_request(release_request, checkers[0])
    release_request = factories.refresh_release_request(release_request)
    assert release_request.review_turn == 2
    assert release_request.get_turn_phase() == ReviewTurnPhase.AUTHOR

    for checker in checkers:
        assert get_comment_metadata(checker) == [
            "comment_private",
            "comment_private",
            "comment_public",
        ]

    assert get_comment_metadata(author) == ["comment_public"]

    bll.submit_request(release_request, author)
    release_request = factories.refresh_release_request(release_request)
    assert release_request.review_turn == 3
    assert release_request.get_turn_phase() == ReviewTurnPhase.INDEPENDENT

    bll.group_comment_create(
        release_request,
        "group",
        "turn 3 checker 0 private",
        Visibility.PRIVATE,
        checkers[0],
    )
    bll.group_comment_create(
        release_request,
        "group",
        "turn 3 checker 1 private",
        Visibility.PRIVATE,
        checkers[1],
    )

    release_request = factories.refresh_release_request(release_request)

    # comments from previous round are visible
    assert get_comment_metadata(checkers[0]) == [
        "comment_private",
        "comment_private",
        "comment_public",
        "comment_blinded",
    ]
    assert get_comment_metadata(checkers[1]) == [
        "comment_private",
        "comment_private",
        "comment_public",
        "comment_blinded",
    ]


def test_release_request_filegroups_with_no_files(bll):
    release_request, _, _ = setup_empty_release_request()
    assert release_request.filegroups == {}


def test_release_request_filegroups_default_filegroup(bll):
    release_request, path, author = setup_empty_release_request()
    assert release_request.filegroups == {}
    bll.add_file_to_request(release_request, path, author)
    assert len(release_request.filegroups) == 1
    filegroup = release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert len(filegroup.files) == 1
    assert path in filegroup.files


def test_release_request_filegroups_named_filegroup(bll):
    release_request, path, author = setup_empty_release_request()
    assert release_request.filegroups == {}
    bll.add_file_to_request(release_request, path, author, "test_group")
    assert len(release_request.filegroups) == 1
    filegroup = release_request.filegroups["test_group"]
    assert filegroup.name == "test_group"
    assert len(filegroup.files) == 1
    assert path in filegroup.files


def test_release_request_filegroups_multiple_filegroups(bll):
    release_request, path, author = setup_empty_release_request()
    bll.add_file_to_request(release_request, path, author, "test_group")
    assert len(release_request.filegroups) == 1

    workspace = bll.get_workspace("workspace", author)
    path1 = UrlPath("path/file1.txt")
    path2 = UrlPath("path/file2.txt")
    factories.write_workspace_file(workspace, path1)
    factories.write_workspace_file(workspace, path2)
    bll.add_file_to_request(release_request, path1, author, "test_group")
    bll.add_file_to_request(release_request, path2, author, "test_group1")

    release_request = bll.get_release_request(release_request.id, author)
    assert len(release_request.filegroups) == 2

    release_request_files = {
        filegroup.name: list(filegroup.files)
        for filegroup in release_request.filegroups.values()
    }

    assert release_request_files == {
        "test_group": [UrlPath("path/file.txt"), UrlPath("path/file1.txt")],
        "test_group1": [UrlPath("path/file2.txt")],
    }


def test_release_request_add_same_file(bll):
    release_request, path, author = setup_empty_release_request()
    assert release_request.filegroups == {}
    bll.add_file_to_request(release_request, path, author)
    assert len(release_request.filegroups) == 1
    assert len(release_request.filegroups["default"].files) == 1

    # Adding the same file again should not create a new RequestFile
    with pytest.raises(exceptions.APIException):
        bll.add_file_to_request(release_request, path, author)

    # We also can't add the same file to a different group
    with pytest.raises(exceptions.APIException):
        bll.add_file_to_request(release_request, path, author, "new_group")

    release_request = bll.get_release_request(release_request.id, author)
    # No additional files or groups have been created
    assert len(release_request.filegroups) == 1
    assert len(release_request.filegroups["default"].files) == 1


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {"repo": None, "outputs": {}},
        {"repo": None, "outputs": {"file.txt": {"commit": "commit"}}},
    ],
)
def test_coderepo_from_workspace_no_repo_in_manifest(bll, manifest):
    workspace = factories.create_workspace("workspace")
    workspace.manifest = manifest
    with pytest.raises(CodeRepo.RepoNotFound):
        CodeRepo.from_workspace(workspace, "commit")


def test_coderepo_from_workspace(bll):
    workspace = factories.create_workspace("workspace")
    factories.create_repo(workspace)
    # No root repo, retrieved from first output in manifest instead
    workspace.manifest["repo"] = None
    CodeRepo.from_workspace(
        workspace, workspace.manifest["outputs"]["foo.txt"]["commit"]
    )
