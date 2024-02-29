from airlock.views.helpers import login_exempt

from .base_views import components as upstream_components


@login_exempt
def components(request):
    return upstream_components(request)
