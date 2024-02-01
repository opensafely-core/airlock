from django import forms
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from airlock import login_api
from airlock.workspace_api import ReleaseRequest, Workspace, WorkspacesRoot


class TokenLoginForm(forms.Form):
    user = forms.CharField()
    token = forms.CharField()


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


def index(request):
    return TemplateResponse(request, "index.html")


def validate_user(request):
    """Ensure we have a valid user."""
    if request.user is None:
        raise PermissionDenied()
    return request.user


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
    user = validate_user(request)
    return TemplateResponse(
        request,
        "file_browser/index.html",
        {
            "container": WorkspacesRoot(user=user),
        },
    )


def workspace_view(request, workspace_name: str, path: str = ""):
    user = validate_user(request)
    workspace = validate_workspace(user, workspace_name)
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
            "title": f"{workspace_name} workspace files",
        },
    )


def request_view(request, workspace_name: str, request_id: str, path: str = ""):
    user = validate_user(request)
    workspace = validate_workspace(user, workspace_name)
    output_request = validate_output_request(user, workspace, request_id)

    path_item = output_request.get_path(path)

    if not path_item.exists():
        raise Http404()

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    context = {
        "workspace": workspace,
        "output_request": output_request,
        "path_item": path_item,
        "context": "request",
        "title": f"{request_id} request files",
        # TODO file these in from user/models
        "is_author": request.GET.get("is_author", False),
        "is_output_checker": user.is_output_checker,
    }

    return TemplateResponse(request, "file_browser/index.html", context)
