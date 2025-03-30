from django import template
from slippers.templatetags.slippers import register_components  # type: ignore


register = template.Library()


register_components(
    {
        "airlock_header": "_components/header/base.html",
        "airlock_workspace_header": "_components/header/workspace/header.html",
        "airlock_request_header": "_components/header/request/header.html",
        "airlock_repo_header": "_components/header/repo/header.html",
        "datatable": "_components/datatable.html",
        "clusterize_table": "_components/clusterize-table.html",
        "airlock_user": "_components/user.html",
        "airlock_list_dropdown": "_components/list-group/list-group-item-dropdown.html",
        "airlock_list_group_rich_item": "_components/list-group/list-group-rich-item.html",
        "airlock_list_group": "_components/list-group/list-group.html",
    },
    register,
)
