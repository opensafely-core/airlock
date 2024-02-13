from pathlib import Path

import requests
from django import forms
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from airlock import login_api
from airlock.api import Status
from airlock.file_browser_api import PathItem
from local_db.api import LocalDBProvider


api = LocalDBProvider()


def login_exempt(view):
    view.login_exempt = True
    return view


class TokenLoginForm(forms.Form):
    user = forms.CharField()
    token = forms.CharField()


@login_exempt
def login(request):
    default_next_url = reverse("workspace_index")

    if request.method != "POST":
        next_url = request.GET.get("next", default_next_url)
        token_login_form = TokenLoginForm()
    else:
        next_url = request.POST.get("next", default_next_url)
        token_login_form = TokenLoginForm(request.POST)
        user_data = get_user_data_or_set_form_errors(token_login_form)
        # If `user_data` is None then the form object will have the relevant errors
        if user_data is not None:
            # TODO: the current code expects an `id` field but the API doesn't return
            # one; we should work out what we're doing here
            user_data["id"] = user_data["username"]
            request.session["user"] = user_data
            return redirect(next_url)

    return TemplateResponse(
        request,
        "login.html",
        {
            "next_url": next_url,
            "token_login_form": token_login_form,
            "dev_users_file": settings.AIRLOCK_DEV_USERS_FILE,
        },
    )


def get_user_data_or_set_form_errors(form):
    if not form.is_valid():
        return
    try:
        return login_api.get_user_data(
            user=form.cleaned_data["user"],
            token=form.cleaned_data["token"],
        )
    except login_api.LoginError as exc:
        form.add_error("token", str(exc))


def logout(request):
    """
    User information is held in the session. On logout, remove
    session data and redirect to the home page.
    """
    request.session.flush()
    return redirect(reverse("home"))


@login_exempt  # for now
def index(request):
    return TemplateResponse(request, "index.html")


def validate_workspace(user, workspace_name):
    """Ensure the workspace exists and the current user has permissions to access it."""
    try:
        workspace = api.get_workspace(workspace_name)
    except api.WorkspaceNotFound:
        raise Http404()

    if user is None or not user.has_permission(workspace_name):
        raise PermissionDenied()

    return workspace


def validate_release_request(user, request_id):
    """Ensure the release request exists for this workspace."""
    try:
        release_request = api.get_release_request(request_id)
    except api.ReleaseRequestNotFound:
        raise Http404()

    # check user permissions for this workspace
    validate_workspace(user, release_request.workspace)

    return release_request


def workspace_index(request):
    workspaces = api.get_workspaces_for_user(request.user)
    return TemplateResponse(request, "workspaces.html", {"workspaces": workspaces})


def workspace_view(request, workspace_name: str, path: str = ""):
    workspace = validate_workspace(request.user, workspace_name)

    path_item = PathItem(workspace, path)

    if not path_item.exists():
        raise Http404()

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    return TemplateResponse(
        request,
        "file_browser/index.html",
        {
            "workspace": workspace,
            "path_item": path_item,
            "context": "workspace",
            "title": f"Files for workspace {workspace_name}",
            "request_file_url": reverse(
                "workspace_add_file",
                kwargs={"workspace_name": workspace_name},
            ),
        },
    )


@require_http_methods(["POST"])
def workspace_add_file_to_request(request, workspace_name):
    workspace = validate_workspace(request.user, workspace_name)
    relpath = Path(request.POST["path"])
    try:
        workspace.abspath(relpath)
    except api.FileNotFound:
        raise Http404()

    release_request = api.get_current_request(workspace_name, request.user, create=True)
    api.add_file_to_request(release_request, relpath)

    # redirect to this just added file
    return redirect(release_request.get_url_for_path(relpath))


def request_index(request):
    requests = api.get_requests_for_user(request.user)
    return TemplateResponse(request, "requests.html", {"requests": requests})


def request_view(request, request_id: str, path: str = ""):
    release_request = validate_release_request(request.user, request_id)

    path_item = PathItem(release_request, path)

    if not path_item.exists():
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
        "workspace": release_request.workspace,
        "release_request": release_request,
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

    return redirect(release_request.get_absolute_url())


@require_http_methods(["POST"])
def request_reject(request, request_id):
    release_request = validate_release_request(request.user, request_id)

    try:
        api.set_status(release_request, Status.REJECTED, request.user)
    except api.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    return redirect(release_request.get_absolute_url())


@require_http_methods(["POST"])
def request_release_files(request, request_id):
    if not request.user.output_checker:
        raise PermissionDenied()

    release_request = validate_release_request(request.user, request_id)

    # TODO: enforce this, but we need a way to bypass it in dev, or else it
    # gets really awkward to test w/o logging in/out as different users.
    # if request.user.username == release_request.author:
    #    raise PermissionDenied(f"You cannot approve your own request")

    try:
        # For now, we just implicitly approve when release files is requested
        api.set_status(release_request, Status.APPROVED, request.user)
        api.release_files(release_request, request.user)
    except api.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))  # pragma: nocover
    except requests.HTTPError as err:
        if settings.DEBUG:  # pragma: nocover
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

    return redirect(release_request.get_absolute_url())
