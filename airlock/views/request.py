import requests
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods
from django.views.decorators.vary import vary_on_headers
from opentelemetry import trace

from airlock.business_logic import (
    FileReviewStatus,
    RequestFileType,
    RequestStatus,
    bll,
)
from airlock.file_browser_api import get_request_tree
from airlock.types import UrlPath
from services.tracing import instrument

from .helpers import (
    download_file,
    get_path_item_from_tree_or_404,
    get_release_request_or_raise,
    serve_file,
)


tracer = trace.get_tracer_provider().get_tracer("airlock")


@instrument
def request_index(request):
    authored_requests = bll.get_requests_authored_by_user(request.user)

    outstanding_requests = []
    if request.user.output_checker:
        outstanding_requests = bll.get_outstanding_requests_for_review(request.user)

    return TemplateResponse(
        request,
        "requests.html",
        {
            "authored_requests": authored_requests,
            "outstanding_requests": outstanding_requests,
        },
    )


# we return different content if it is a HTMX request.
@vary_on_headers("HX-Request")
@instrument(func_attributes={"release_request": "request_id"})
def request_view(request, request_id: str, path: str = ""):
    release_request = get_release_request_or_raise(request.user, request_id)

    template = "file_browser/index.html"
    selected_only = False

    if request.htmx:
        template = "file_browser/contents.html"
        selected_only = True

    tree = get_request_tree(release_request, path, selected_only)
    path_item = get_path_item_from_tree_or_404(tree, path)

    is_directory_url = path.endswith("/") or path == ""

    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    is_author = release_request.author == request.user.username
    # hack for testing w/o having to switch users
    if "is_author" in request.GET:  # pragma: nocover
        is_author = request.GET["is_author"].lower() == "true"

    if is_directory_url or release_request.status == RequestStatus.WITHDRAWN:
        file_withdraw_url = None
    else:
        file_withdraw_url = reverse(
            "file_withdraw",
            kwargs={"request_id": request_id, "path": path},
        )

    request_submit_url = reverse(
        "request_submit",
        kwargs={"request_id": request_id},
    )
    request_withdraw_url = reverse(
        "request_withdraw",
        kwargs={"request_id": request_id},
    )
    request_reject_url = reverse(
        "request_reject",
        kwargs={"request_id": request_id},
    )
    release_files_url = reverse(
        "request_release_files",
        kwargs={"request_id": request_id},
    )

    if (
        is_directory_url
        or release_request.request_filetype(path) == RequestFileType.SUPPORTING
    ):
        file_approve_url = None
        file_reject_url = None
    else:
        file_approve_url = reverse(
            "file_approve",
            kwargs={"request_id": request_id, "path": path},
        )
        file_reject_url = reverse(
            "file_reject",
            kwargs={"request_id": request_id, "path": path},
        )

        existing_review = release_request.get_file_review_for_reviewer(
            path, request.user.username
        )
        if existing_review:
            if existing_review.status == FileReviewStatus.APPROVED:
                file_approve_url = None
            elif existing_review.status == FileReviewStatus.REJECTED:
                file_reject_url = None
            else:
                assert False, "Invalid FileReviewStatus value"

    context = {
        "workspace": bll.get_workspace(release_request.workspace, request.user),
        "release_request": release_request,
        "root": tree,
        "path_item": path_item,
        "context": "request",
        "title": f"Request for {release_request.workspace} by {release_request.author}",
        # TODO file these in from user/models
        "is_author": is_author,
        "is_output_checker": request.user.output_checker,
        "file_approve_url": file_approve_url,
        "file_reject_url": file_reject_url,
        "file_withdraw_url": file_withdraw_url,
        "request_submit_url": request_submit_url,
        "request_reject_url": request_reject_url,
        "request_withdraw_url": request_withdraw_url,
        "release_files_url": release_files_url,
    }

    return TemplateResponse(request, template, context)


@instrument(func_attributes={"release_request": "request_id"})
@xframe_options_sameorigin
@require_http_methods(["GET"])
def request_contents(request, request_id: str, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        abspath = release_request.abspath(path)
    except bll.FileNotFound:
        raise Http404()

    download = "download" in request.GET
    # Downloads are only allowed for output checkers
    # Downloads are not allowed for request authors (including those that are also
    # output checkers)
    if download:
        if not request.user.output_checker or (
            release_request.author == request.user.username
        ):
            raise PermissionDenied()

        bll.audit_request_file_download(release_request, UrlPath(path), request.user)
        return download_file(abspath, filename=path)

    bll.audit_request_file_access(release_request, UrlPath(path), request.user)
    renderer = release_request.get_renderer(UrlPath(path))
    return serve_file(request, renderer)


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_submit(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        bll.set_status(release_request, RequestStatus.SUBMITTED, request.user)
    except bll.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.success(request, "Request has been submitted")
    return redirect(release_request.get_url())


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_reject(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        bll.set_status(release_request, RequestStatus.REJECTED, request.user)
    except bll.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.error(request, "Request has been rejected")
    return redirect(release_request.get_url())


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_withdraw(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        bll.set_status(release_request, RequestStatus.WITHDRAWN, request.user)
    except bll.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.error(request, "Request has been withdrawn")
    return redirect(release_request.get_url())


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_withdraw(request, request_id, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)
    grouppath = UrlPath(path)

    try:
        release_request.get_request_file(grouppath)
    except bll.FileNotFound:
        raise Http404()

    try:
        bll.withdraw_file_from_request(release_request, grouppath, request.user)
    except bll.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    try:
        release_request.get_request_file(grouppath)
    except bll.FileNotFound:
        # its been removed - redirect to group that contained
        redirect_url = release_request.get_url(grouppath.parts[0])
    else:
        # its been set to withdrawn - redirect to it directly
        redirect_url = release_request.get_url(grouppath)

    messages.error(request, f"The file {grouppath} has been withdrawn from the request")
    return redirect(redirect_url)


@instrument
def requests_for_workspace(request, workspace_name: str):
    requests_for_workspace = bll.get_requests_for_workspace(
        workspace_name, request.user
    )

    return TemplateResponse(
        request,
        "requests_for_workspace.html",
        {
            "workspace": workspace_name,
            "requests_for_workspace": requests_for_workspace,
        },
    )


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_approve(request, request_id, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        relpath = release_request.get_request_file(path).relpath
    except bll.FileNotFound:
        raise Http404()

    try:
        bll.approve_file(release_request, relpath, request.user)
    except bll.ApprovalPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.success(request, "File has been approved")
    return redirect(release_request.get_url(path))


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_reject(request, request_id, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        relpath = release_request.get_request_file(path).relpath
    except bll.FileNotFound:
        raise Http404()

    try:
        bll.reject_file(release_request, relpath, request.user)
    except bll.ApprovalPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.success(request, "File has been rejected")
    return redirect(release_request.get_url(path))


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_release_files(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        # For now, we just implicitly approve when release files is requested
        bll.set_status(release_request, RequestStatus.APPROVED, request.user)
        bll.release_files(release_request, request.user)
    except bll.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))
    except requests.HTTPError as err:
        if settings.DEBUG:
            return TemplateResponse(
                request,
                "jobserver-error.html",
                {
                    "response": err.response,
                    "type": err.response.headers["Content-Type"],
                },
            )

        if err.response.status_code == 403:
            raise PermissionDenied() from None
        raise

    messages.success(request, "Files have been released to jobs.opensafely.org")
    return redirect(release_request.get_url())
