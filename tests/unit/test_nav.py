from airlock import nav
from tests import factories


def predicate_false(request):
    return False


def test_iter_nav(rf):
    items = [
        nav.NavItem("Workspace", "workspace_index"),
        nav.NavItem("Requests", "requests_for_researcher", predicate_false),
    ]

    request = rf.get("/workspaces/")
    assert list(nav.iter_nav(items, request)) == [
        {
            "name": "Workspace",
            "url": "/workspaces/",
            "is_active": True,
        }
    ]

    request = rf.get("/other/")
    assert list(nav.iter_nav(items, request)) == [
        {
            "name": "Workspace",
            "url": "/workspaces/",
            "is_active": False,
        }
    ]


def test_iter_nav_output_checker(rf):
    factories.create_user("user", ["test"], output_checker=True)
    items = [
        nav.NavItem("Reviews", "requests_for_output_checker"),
    ]

    request = rf.get("/requests/output_checker")
    assert list(nav.iter_nav(items, request)) == [
        {
            "name": "Reviews",
            "url": "/requests/output_checker",
            "is_active": True,
        }
    ]
