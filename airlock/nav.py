from collections.abc import Callable
from dataclasses import dataclass

from django.http import HttpRequest
from django.urls import reverse


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
        NavItem(name="Requests", url_name="request_index"),
    ]
    return {"nav": list(iter_nav(items, request))}
