from django import template
from django.utils.safestring import mark_safe


register = template.Library()

ICON = """
<span class="icons--sort">
  <img class="icon datatable-icon--no-sort" src="/static/icons/swap_vert.svg" alt="">
  <img class="icon datatable-icon--ascending" src="/static/icons/arrow_upward.svg" alt="">
  <img class="icon datatable-icon--descending" src="/static/icons/arrow_downward.svg" alt="">
  <img class="icon datatable-icon--sorting animate-spin" src="/static/icons/progress_activity.svg" alt="">
</span>
""".replace("\n", "")


@register.simple_tag
def datatable_sort_icon():
    return mark_safe(ICON)
