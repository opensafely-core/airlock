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
from django.urls import path

import airlock.views
import assets.views


urlpatterns = [
    path("", airlock.views.index, name="home"),
    path("login/", airlock.views.login, name="login"),
    path("logout/", airlock.views.logout, name="logout"),
    path("ui-components/", assets.views.components),
    # workspaces
    path(
        "workspaces/",
        airlock.views.workspace_index_view,
        name="workspace_index",
    ),
    path(
        "workspaces/<str:workspace_name>/",
        airlock.views.workspace_view,
        name="workspace_home",
    ),
    path(
        "workspaces/<str:workspace_name>/<path:path>",
        airlock.views.workspace_view,
        name="workspace_view",
    ),
    # requests
    path(
        "requests/",
        airlock.views.request_index_view,
        name="request_index",
    ),
    path(
        "requests/<str:workspace_name>/<str:request_id>/",
        airlock.views.request_view,
        name="request_home",
    ),
    path(
        "requests/<str:workspace_name>/<str:request_id>/<path:path>",
        airlock.views.request_view,
        name="request_view",
    ),
    path(
        "requests/<str:workspace_name>/add",
        airlock.views.request_add_file,
        name="request_add_file",
    ),
]
