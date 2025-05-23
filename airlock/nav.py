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
        NavItem(name="Workspaces & Requests", url_name="workspace_index"),
        NavItem(name="Docs", url_name="docs_home"),
    ]
    nav_index = 1

    if request.user.is_authenticated:
        if permissions.user_can_review(request.user):
            reviews_menu = NavItem(
                name="Reviews", url_name="requests_for_output_checker"
            )
            items.insert(nav_index, reviews_menu)
        if request.user.copiloted_workspaces:
            copiloted_workspaces_menu = NavItem(
                name="Copiloted Workspaces",
                url_name="copiloted_workspace_index",
            )
            items.insert(nav_index, copiloted_workspaces_menu)
    return {"nav": list(iter_nav(items, request))}


def dev_users(request):
    return {"dev_users": settings.DEV_USERS}
