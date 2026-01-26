from dataclasses import dataclass

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponseNotModified
from django.urls import reverse
from django.utils.safestring import mark_safe

from airlock import exceptions
from airlock.business_logic import bll
from airlock.file_browser_api import PathItem
from airlock.types import UrlPath


class ServeFileException(Exception):
    pass


@dataclass
class ButtonContext:
    """Holds information about the status of a button for template context"""

    show: bool = False
    disabled: bool = True
    selected: bool = False
    url: str = ""
    tooltip: str = ""
    label: str = ""

    @classmethod
    def with_request_defaults(
        cls, release_request_id, url_name, label="", **extra_kwargs
    ):
        return cls(
            url=reverse(
                url_name, kwargs={"request_id": release_request_id, **extra_kwargs}
            ),
            label=label,
        )

    @classmethod
    def with_workspace_defaults(cls, workspace_name, url_name, **extra_kwargs):
        return cls(
            url=reverse(
                url_name, kwargs={"workspace_name": workspace_name, **extra_kwargs}
            ),
        )


def login_exempt(view):
    view.login_exempt = True
    return view


def get_workspace_or_raise(user, workspace_name):
    """Get the workspace, converting any errors to http codes."""
    try:
        workspace = bll.get_workspace(workspace_name, user)
    except exceptions.WorkspaceNotFound:
        raise Http404()
    except exceptions.WorkspacePermissionDenied:
        raise PermissionDenied()

    return workspace


def get_release_request_or_raise(user, request_id):
    """Get the release request, converting any errors to http codes."""
    try:
        release_request = bll.get_release_request(request_id, user)
    except exceptions.ReleaseRequestNotFound:
        raise Http404()
    except exceptions.WorkspacePermissionDenied:
        raise PermissionDenied()

    return release_request


def download_file(abspath, filename=None):
    """Simple Helper to download file."""
    return FileResponse(abspath.open("rb"), as_attachment=True, filename=filename)


def serve_file(request, renderer):
    """Serve file contents using the renderer provided.

    Handles sending 304 Not Modified if possible.
    """

    if request.headers.get("If-None-Match") == renderer.etag:
        response = HttpResponseNotModified(headers=renderer.headers())
    else:
        # Temporary feature flag for summarizing column data in csv renderer
        if "summarize_csv" in request.GET:
            renderer.summarize = True
        response = renderer.get_response()

    return response


def get_path_item_from_tree_or_404(tree: PathItem, path: UrlPath | str):
    try:
        return tree.get_path(UrlPath(path))
    except tree.PathNotFound:
        raise Http404()


def display_multiple_messages(request, msgs, level="success"):
    if not msgs:
        return
    func = getattr(messages, level)
    func(request, mark_safe("<br/>".join(msgs)))


def display_form_errors(request, *form_errors):
    msgs = []

    for errors in form_errors:
        for name, error_list in errors.items():
            for error in error_list:
                if name:
                    msg = f"{name}: {error}"
                else:
                    msg = error
                msgs.append(msg)

    display_multiple_messages(request, msgs, "error")


def get_next_url_from_form(container, form):
    if "next_url" not in form.errors:
        return form.cleaned_data["next_url"]

    # default redirect in case of error
    return container.get_url()
