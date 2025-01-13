"""
URL configuration for airlock project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.urls import include, path
from django.views.generic import RedirectView

import airlock.views
import airlock.views.code
import assets.views


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="workspace_index"), name="home"),
    path("login/", airlock.views.login, name="login"),
    path("logout/", airlock.views.logout, name="logout"),
    path("ui-components/", assets.views.components),
    # workspaces
    path(
        "workspaces/",
        airlock.views.workspace_index,
        name="workspace_index",
    ),
    path(
        "workspaces/view/<str:workspace_name>/",
        airlock.views.workspace_view,
        kwargs={"path": ""},
        name="workspace_view",
    ),
    path(
        "workspaces/view/<str:workspace_name>/<path:path>",
        airlock.views.workspace_view,
        name="workspace_view",
    ),
    path(
        "workspaces/content/<str:workspace_name>/<path:path>",
        airlock.views.workspace_contents,
        name="workspace_contents",
    ),
    path(
        "workspaces/multiselect/<str:workspace_name>",
        airlock.views.workspace_multiselect,
        name="workspace_multiselect",
    ),
    path(
        "workspaces/add-file-to-request/<str:workspace_name>",
        airlock.views.workspace_add_file_to_request,
        name="workspace_add_file",
    ),
    path(
        "workspaces/update-file-in-request/<str:workspace_name>",
        airlock.views.workspace_update_file_in_request,
        name="workspace_update_file",
    ),
    # requests
    path(
        "requests/",
        RedirectView.as_view(pattern_name="requests_for_researcher"),
        name="requests",
    ),
    path(
        "requests/researcher",
        airlock.views.requests_for_researcher,
        name="requests_for_researcher",
    ),
    path(
        "requests/output_checker",
        airlock.views.requests_for_output_checker,
        name="requests_for_output_checker",
    ),
    path(
        "requests/view/<str:request_id>/",
        airlock.views.request_view,
        name="request_view",
        kwargs={"path": ""},
    ),
    path(
        "requests/view/<str:request_id>/<path:path>",
        airlock.views.request_view,
        name="request_view",
    ),
    path(
        "requests/content/<str:request_id>/<path:path>",
        airlock.views.request_contents,
        name="request_contents",
    ),
    path(
        "requests/approve/<str:request_id>/<path:path>",
        airlock.views.file_approve,
        name="file_approve",
    ),
    path(
        "requests/request_changes/<str:request_id>/<path:path>",
        airlock.views.file_request_changes,
        name="file_request_changes",
    ),
    path(
        "requests/reset_review/<str:request_id>/<path:path>",
        airlock.views.file_reset_review,
        name="file_reset_review",
    ),
    path(
        "requests/withdraw/<str:request_id>/<path:path>",
        airlock.views.file_withdraw,
        name="file_withdraw",
    ),
    path(
        "requests/multiselect/<str:request_id>",
        airlock.views.request_multiselect,
        name="request_multiselect",
    ),
    path(
        "requests/release/<str:request_id>",
        airlock.views.request_release_files,
        name="request_release_files",
    ),
    path(
        "requests/submit/<str:request_id>",
        airlock.views.request_submit,
        name="request_submit",
    ),
    path(
        "requests/review/<str:request_id>",
        airlock.views.request_review,
        name="request_review",
    ),
    path(
        "requests/reject/<str:request_id>",
        airlock.views.request_reject,
        name="request_reject",
    ),
    path(
        "requests/withdraw/<str:request_id>",
        airlock.views.request_withdraw,
        name="request_withdraw",
    ),
    path(
        "requests/return/<str:request_id>",
        airlock.views.request_return,
        name="request_return",
    ),
    path(
        "requests/workspace/<str:workspace_name>",
        airlock.views.requests_for_workspace,
        name="requests_for_workspace",
    ),
    path(
        "requests/edit/<str:request_id>/<str:group>",
        airlock.views.group_edit,
        name="group_edit",
    ),
    path(
        "requests/comment/create/<str:request_id>/<str:group>",
        airlock.views.group_comment_create,
        name="group_comment_create",
    ),
    path(
        "requests/comment/delete/<str:request_id>/<str:group>",
        airlock.views.group_comment_delete,
        name="group_comment_delete",
    ),
    path(
        "requests/comment/visibility_public/<str:request_id>/<str:group>",
        airlock.views.group_comment_visibility_public,
        name="group_comment_visibility_public",
    ),
    path(
        "code/view/<str:workspace_name>/<str:commit>/",
        airlock.views.code.view,
        name="code_view",
    ),
    path(
        "code/view/<str:workspace_name>/<str:commit>/<path:path>",
        airlock.views.code.view,
        name="code_view",
    ),
    path(
        "code/contents/<str:workspace_name>/<str:commit>/<path:path>",
        airlock.views.code.contents,
        name="code_contents",
    ),
    path(r"docs/", airlock.views.serve_docs, name="docs_home"),
    path(r"docs/<path:path>", airlock.views.serve_docs),
]

if settings.DJANGO_DEBUG_TOOLBAR:  # pragma: nocover
    urlpatterns.append(path("__debug__/", include("debug_toolbar.urls")))
