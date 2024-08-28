from django import template
from slippers.templatetags.slippers import register_components  # type: ignore


register = template.Library()


register_components(
    {
        "airlock_header": "_components/header/base.html",
        "airlock_workspace_header": "_components/header/workspace/header.html",
        "airlock_request_header": "_components/header/request/header.html",
        "airlock_repo_header": "_components/header/repo/header.html",
    },
    register,
)
