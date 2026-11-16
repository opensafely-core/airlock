from pathlib import Path

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.views.static import serve

from .helpers import login_exempt


@login_exempt
def serve_docs(request, path: Path | str = ""):
    docs_root = Path(settings.DOCS_DIR)
    # redirect if this is not a valid docs path
    if not (docs_root / path).exists():
        return redirect(reverse("docs_home"))
    # If the path in the mkdocs build directory is a directory, it contains an
    # index.html which is the file we want to serve
    if (docs_root / path).is_dir():
        # redirect if no trailing slash
        if path and not str(path).endswith("/"):
            return redirect(f"{request.path_info}/")
        path = Path(path) / "index.html"
    return serve(request, str(path), document_root=str(settings.DOCS_DIR))
