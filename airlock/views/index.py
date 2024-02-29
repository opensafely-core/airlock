from django.template.response import TemplateResponse

from .helpers import login_exempt


@login_exempt  # for now
def index(request):
    return TemplateResponse(request, "index.html")
