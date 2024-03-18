import os

import pytest

from airlock import renderers
from airlock.business_logic import UrlPath
from airlock.views import helpers
from tests import factories
from tests.unit.test_renderers import RENDERER_TESTS


@pytest.mark.parametrize("suffix,mimetype,template_path", RENDERER_TESTS)
def test_serve_file_not_modified_workspace_files(
    tmp_path, rf, suffix, mimetype, template_path
):
    abspath = tmp_path / ("test" + suffix)
    # use a csv as test data, it renders fine as text
    abspath.write_text("a,b,c\n1,2,3")

    time = 1709652904  # date this test was written
    os.utime(abspath, (time, time))

    renderer = renderers.get_renderer(abspath)

    request = rf.get("/", headers={"If-None-Match": renderer.etag})
    response = helpers.serve_file(request, abspath)
    assert response.status_code == 304

    request = rf.get(
        "/", headers={"If-Modified-Since": "Tue, 05 Mar 2024 15:35:04 GMT"}
    )
    response = helpers.serve_file(request, abspath)
    assert response.status_code == 304

    request = rf.get(
        "/", headers={"If-Modified-Since": "Tue, 05 Mar 2023 15:35:04 GMT"}
    )
    response = helpers.serve_file(request, abspath)
    assert response.status_code == 200
    assert response.headers["Content-Type"].split(";")[0] == mimetype


@pytest.mark.parametrize("suffix,mimetype,template_path", RENDERER_TESTS)
@pytest.mark.django_db
def test_serve_file_not_modified_request_files(
    tmp_path, rf, suffix, mimetype, template_path
):
    filepath = UrlPath("test" + suffix)
    grouppath = "group" / filepath
    request = factories.create_release_request("workspace")
    # use a csv as test data, it works for other types too
    factories.write_request_file(request, "group", filepath, "a,b,c\n1,2,3")

    time = 1709652904  # date this test was written
    abspath = request.abspath(grouppath)
    os.utime(abspath, (time, time))
    request_file = request.get_request_file(grouppath)

    renderer = renderers.get_renderer(abspath, request_file)

    request = rf.get("/", headers={"If-None-Match": renderer.etag})
    response = helpers.serve_file(request, abspath, request_file)
    assert response.status_code == 304

    request = rf.get(
        "/", headers={"If-Modified-Since": "Tue, 05 Mar 2024 15:35:04 GMT"}
    )
    response = helpers.serve_file(request, abspath, request_file)
    assert response.status_code == 304

    request = rf.get(
        "/", headers={"If-Modified-Since": "Tue, 05 Mar 2023 15:35:04 GMT"}
    )
    response = helpers.serve_file(request, abspath, request_file)
    assert response.status_code == 200
    assert response.headers["Content-Type"].split(";")[0] == mimetype
