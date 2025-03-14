from collections.abc import Callable
from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse

from airlock import permissions


def default_predicate(request):
    return True


@dataclass
class NavItem:
    name: str
    url_name: str
    predicate: Callable[[HttpRequest], bool] = default_predicate


def iter_nav(items, request):
    for item in items:
        if not item.predicate(request):
            continue

        url = reverse(item.url_name)
        yield {
            "name": item.name,
            "is_active": request.path.startswith(url),
            "url": url,
        }


def menu(request):
    items = [
        NavItem(name="Workspaces", url_name="workspace_index"),
        NavItem(name="Requests", url_name="requests_for_researcher"),
        NavItem(name="Docs", url_name="docs_home"),
    ]

    if request.user.is_authenticated:
        if request.user.copiloted_workspaces:
            copiloted_workspaces_menu = NavItem(
                name="Copiloted Workspaces", url_name="copiloted_workspace_index"
            )
            items.insert(1, copiloted_workspaces_menu)
        if permissions.user_can_review(request.user):
            reviews_menu = NavItem(
                name="Reviews", url_name="requests_for_output_checker"
            )
            items.insert(2, reviews_menu)
    return {"nav": list(iter_nav(items, request))}


def dev_users(request):
    return {"dev_users": settings.DEV_USERS}
