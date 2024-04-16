import os

import pytest

from airlock import renderers
from airlock.business_logic import UrlPath
from tests import factories


RENDERER_TESTS = [
    (".html", "text/html", None),
    (".png", "image/png", None),
    (".csv", "text/html", "airlock/templates/file_browser/csv.html"),
    (".txt", "text/html", "airlock/templates/file_browser/text.html"),
]


@pytest.mark.parametrize("suffix,mimetype,template_path", RENDERER_TESTS)
def test_renderers_get_renderer_workspace(
    tmp_path, rf, suffix, mimetype, template_path
):
    path = tmp_path / ("test" + suffix)
    # use a csv as test data, it works for other types too
    path.write_text("a,b,c\n1,2,3")
    content_cache_id = "65e73ba8-b"

    time = 1709652904  # date this test was written
    os.utime(path, (time, time))

    renderer = renderers.get_renderer(path)
    assert renderer.last_modified == "Tue, 05 Mar 2024 15:35:04 GMT"

    if template_path:
        template_cache_id = renderers.filesystem_key(renderer.template.path.stat())
        assert renderer.cache_id == f"{content_cache_id}-{template_cache_id}"
    else:
        assert renderer.cache_id == content_cache_id

    response = renderer.get_response()
    if hasattr(response, "render"):
        # ensure template is actually rendered, for template coverage
        response.render()

    assert response.status_code == 200
    assert response.headers["Content-Type"].split(";")[0] == mimetype
    assert response.headers["Last-Modified"] == renderer.last_modified
    assert response.headers["ETag"] == renderer.etag
    assert response.headers["Cache-Control"] == "max-age=31536000, immutable"


@pytest.mark.parametrize("suffix,mimetype,template_path", RENDERER_TESTS)
@pytest.mark.django_db
def test_renderers_get_renderer_request(tmp_path, rf, suffix, mimetype, template_path):
    filepath = UrlPath("test" + suffix)
    grouppath = "group" / filepath
    request = factories.create_release_request("workspace")
    # use a csv as test data, it works for other types too
    factories.write_request_file(request, "group", filepath, "a,b,c\n1,2,3")

    time = 1709652904  # date this test was written
    abspath = request.abspath(grouppath)
    os.utime(abspath, (time, time))
    request_file = request.get_request_file(grouppath)

    renderer = renderers.get_renderer(
        abspath, request_file.relpath, request_file.file_id
    )
    assert renderer.last_modified == "Tue, 05 Mar 2024 15:35:04 GMT"

    if template_path:
        template_cache_id = renderers.filesystem_key(renderer.template.path.stat())
        assert renderer.cache_id == f"{request_file.file_id}-{template_cache_id}"
    else:
        assert renderer.cache_id == request_file.file_id

    response = renderer.get_response()
    if hasattr(response, "render"):
        # ensure template is actually rendered, for template coverage
        response.render()

    assert response.status_code == 200
    assert response.headers["Content-Type"].split(";")[0] == mimetype
    assert response.headers["Last-Modified"] == renderer.last_modified
    assert response.headers["ETag"] == renderer.etag
    assert response.headers["Cache-Control"] == "max-age=31536000, immutable"
