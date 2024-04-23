from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods
from django.views.decorators.vary import vary_on_headers

from airlock.business_logic import CodeRepo, bll
from airlock.file_browser_api import get_code_tree
from airlock.types import UrlPath
from airlock.users import User
from airlock.views.helpers import (
    get_path_item_from_tree_or_404,
    get_workspace_or_raise,
    serve_file,
)
from services.tracing import instrument


def get_repo_or_raise(user: User, workspace_name: str, commit: str):
    workspace = get_workspace_or_raise(user, workspace_name)

    try:
        return CodeRepo.from_workspace(workspace, commit)
    except bll.FileNotFound:
        # cannot find manifest.json
        raise Http404()
    except (CodeRepo.RepoNotFound, CodeRepo.CommitNotFound):
        raise Http404()


# we return different content if it is a HTMX request.
@vary_on_headers("HX-Request")
@instrument(func_attributes={"workspace": "workspace_name", "commit": "commit"})
def view(request, workspace_name: str, commit: str, path: str = ""):
    repo = get_repo_or_raise(request.user, workspace_name, commit)
    template = "file_browser/index.html"
    selected_only = False

    if request.htmx:
        template = "file_browser/contents.html"
        selected_only = True

    tree = get_code_tree(repo, UrlPath(path), selected_only)

    path_item = get_path_item_from_tree_or_404(tree, path)

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    current_request = bll.get_current_request(workspace_name, request.user)

    return TemplateResponse(
        request,
        template,
        {
            "workspace": workspace_name,
            "repo": repo,
            "root": tree,
            "path_item": path_item,
            "is_supporting_file": False,
            "is_author": False,
            "is_output_checker": False,
            "context": "repo",
            "title": f"{repo.repo}@{commit[:7]}",
            "current_request": current_request,
        },
    )


@instrument(func_attributes={"workspace": "workspace_name", "commit": "commit"})
@xframe_options_sameorigin
@require_http_methods(["GET"])
def contents(request, workspace_name: str, commit: str, path: str):
    repo = get_repo_or_raise(request.user, workspace_name, commit)

    try:
        renderer = repo.get_renderer(UrlPath(path))
    except bll.FileNotFound:
        raise Http404()

    return serve_file(request, renderer)
