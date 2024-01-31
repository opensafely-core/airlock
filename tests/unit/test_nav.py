from airlock import nav


def predicate_false(request):
    return False


def test_iter_nav(rf):
    items = [
        nav.NavItem("Workspace", "workspace_index"),
        nav.NavItem("Requests", "request_index", predicate_false),
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
