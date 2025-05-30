from airlock import nav
from tests import factories


def predicate_false(request):
    return False


def test_iter_nav(rf):
    items = [
        nav.NavItem("Workspaces & Requests", "workspace_index"),
    ]

    request = rf.get("/workspaces/")
    assert list(nav.iter_nav(items, request)) == [
        {
            "name": "Workspaces & Requests",
            "url": "/workspaces/",
            "is_active": True,
        }
    ]


def test_iter_nav_output_checker(rf):
    factories.create_airlock_user(
        username="user", workspaces=["test"], output_checker=True
    )
    items = [
        nav.NavItem("Reviews", "requests_for_output_checker"),
        nav.NavItem("Workspaces & Requests", "workspace_index", predicate_false),
    ]

    request = rf.get("/requests/output_checker")
    assert list(nav.iter_nav(items, request)) == [
        {
            "name": "Reviews",
            "url": "/requests/output_checker",
            "is_active": True,
        }
    ]

    request = rf.get("/other/")
    assert list(nav.iter_nav(items, request)) == [
        {
            "name": "Reviews",
            "url": "/requests/output_checker",
            "is_active": False,
        }
    ]


def test_iter_nav_copilot(rf):
    factories.create_airlock_user(
        username="user", workspaces=["test"], copiloted_workspaces=["test1"]
    )
    items = [
        nav.NavItem("Copiloted Workspaces", "copiloted_workspace_index"),
    ]

    request = rf.get("/copiloted-workspaces/")
    assert list(nav.iter_nav(items, request)) == [
        {
            "name": "Copiloted Workspaces",
            "url": "/copiloted-workspaces/",
            "is_active": True,
        }
    ]
