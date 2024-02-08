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
from airlock.workspace_api import (
    ReleaseRequest,
    Workspace,
    get_requests_for_user,
    get_workspaces_for_user,
)


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
    workspace = Workspace(workspace_name)
    if not workspace.exists():
        raise Http404()
    if user is None or not user.has_permission(workspace_name):
        raise PermissionDenied()

    return workspace


def validate_output_request(user, workspace, request_id):
    """Ensure the release request exists for this workspace."""
    output_request = ReleaseRequest(workspace, request_id)
    # TODO output request authorization?
    if not output_request.exists():
        raise Http404()

    return output_request


def workspace_index_view(request):
    workspaces = get_workspaces_for_user(request.user)
    return TemplateResponse(request, "workspaces.html", {"workspaces": workspaces})


def workspace_view(request, workspace_name: str, path: str = ""):
    workspace = validate_workspace(request.user, workspace_name)
    path_item = workspace.get_path(path)

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
            "add_file_url": reverse(
                "request_add_file", kwargs={"workspace_name": workspace_name}
            ),
        },
    )


def request_index_view(request):
    requests = get_requests_for_user(request.user)
    return TemplateResponse(request, "requests.html", {"requests": requests})


def request_view(request, workspace_name: str, request_id: str, path: str = ""):
    workspace = validate_workspace(request.user, workspace_name)
    output_request = validate_output_request(request.user, workspace, request_id)

    path_item = output_request.get_path(path)

    if not path_item.exists():
        raise Http404()

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    is_author = request_id.endswith(request.user.username)
    # hack for testing w/o having to switch users
    if "is_author" in request.GET:  # pragma: nocover
        is_author = request.GET["is_author"].lower() == "true"

    release_files_url = reverse(
        "request_release_files",
        kwargs={"workspace_name": workspace_name, "request_id": request_id},
    )

    context = {
        "workspace": workspace,
        "output_request": output_request,
        "path_item": path_item,
        "context": "request",
        "title": f"Request {request_id} for workspace {workspace_name}",
        # TODO file these in from user/models
        "is_author": is_author,
        "is_output_checker": request.user.output_checker,
        "release_files_url": release_files_url,
    }

    return TemplateResponse(request, "file_browser/index.html", context)


@require_http_methods(["POST"])
def request_add_file(request, workspace_name):
    workspace = validate_workspace(request.user, workspace_name)
    path = workspace.get_path(request.POST["path"])
    if not path.exists():
        raise Http404()

    release_request = workspace.get_current_request(request.user, create=True)
    release_request.add_file(path.relpath)

    # redirect to this just added file
    return redirect(release_request.get_url(path.relpath))


@require_http_methods(["POST"])
def request_release_files(request, workspace_name, request_id):
    workspace = validate_workspace(request.user, workspace_name)
    output_request = validate_output_request(request.user, workspace, request_id)
    try:
        output_request.release_files(request.user)
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

    return redirect(output_request.url())
