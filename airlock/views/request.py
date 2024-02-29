import requests
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from airlock.api import Status
from airlock.file_browser_api import get_request_tree
from local_db.api import LocalDBProvider

from .helpers import validate_release_request


api = LocalDBProvider()


def request_index(request):
    authored_requests = api.get_requests_authored_by_user(request.user)

    outstanding_requests = []
    if request.user.output_checker:
        outstanding_requests = api.get_outstanding_requests_for_review(request.user)

    return TemplateResponse(
        request,
        "requests.html",
        {
            "authored_requests": authored_requests,
            "outstanding_requests": outstanding_requests,
        },
    )


def request_view(request, request_id: str, path: str = ""):
    release_request = validate_release_request(request.user, request_id)

    tree = get_request_tree(release_request, path)
    try:
        path_item = tree.get_path(path)
    except tree.PathNotFound:
        raise Http404()

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    is_author = release_request.author == request.user.username
    # hack for testing w/o having to switch users
    if "is_author" in request.GET:  # pragma: nocover
        is_author = request.GET["is_author"].lower() == "true"

    request_submit_url = reverse(
        "request_submit",
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

    context = {
        "workspace": api.get_workspace(release_request.workspace),
        "release_request": release_request,
        "root": tree,
        "path_item": path_item,
        "context": "request",
        "title": f"Request for {release_request.workspace} by {release_request.author}",
        # TODO file these in from user/models
        "is_author": is_author,
        "is_output_checker": request.user.output_checker,
        "request_submit_url": request_submit_url,
        "request_reject_url": request_reject_url,
        "release_files_url": release_files_url,
    }

    return TemplateResponse(request, "file_browser/index.html", context)


@require_http_methods(["POST"])
def request_submit(request, request_id):
    release_request = validate_release_request(request.user, request_id)

    try:
        api.set_status(release_request, Status.SUBMITTED, request.user)
    except api.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.success(request, "Request has been submitted")
    return redirect(release_request.get_url())


@require_http_methods(["POST"])
def request_reject(request, request_id):
    release_request = validate_release_request(request.user, request_id)

    try:
        api.set_status(release_request, Status.REJECTED, request.user)
    except api.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.error(request, "Request has been rejected")
    return redirect(release_request.get_url())


@require_http_methods(["POST"])
def request_release_files(request, request_id):
    release_request = validate_release_request(request.user, request_id)

    try:
        # For now, we just implicitly approve when release files is requested
        api.set_status(release_request, Status.APPROVED, request.user)
        api.release_files(release_request, request.user)
    except api.RequestPermissionDenied as exc:
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
