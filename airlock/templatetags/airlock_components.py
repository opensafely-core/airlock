from django import template
from slippers.templatetags.slippers import register_components  # type: ignore


register = template.Library()


register_components(
    {
        "airlock_header": "_components/header.html",
    },
    register,
)
