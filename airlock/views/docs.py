from pathlib import Path

from django.conf import settings
from django.views.static import serve

from .helpers import login_exempt


@login_exempt
def serve_docs(request, path: Path | str = ""):
    path = Path(path)
    # If the path in the mkdocs build directory is a directory, it contains an
    # index.html which is the file we want to serve
    if (settings.DOCS_DIR / path).is_dir():
        path = path / "index.html"
    return serve(request, str(path), document_root=str(settings.DOCS_DIR))
