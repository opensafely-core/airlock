import os

import pytest
from django.test import RequestFactory

from airlock.views import helpers


@pytest.mark.parametrize(
    "suffix,mimetype,template_path",
    [
        (".html", "text/html", None),
        (".png", "image/png", None),
        (".csv", "text/html", "airlock/templates/file_browser/csv.html"),
        (".txt", "text/html", "airlock/templates/file_browser/text.html"),
    ],
)
def test_serve_file_rendered(tmp_path, suffix, mimetype, template_path):
    rf = RequestFactory()

    path = tmp_path / ("test" + suffix)
    # use a csv as test data, it works for other types too
    path.write_text("a,b,c\n1,2,3")

    time = 1709652904  # date this test was written
    os.utime(path, (time, time))

    etag = helpers.build_etag(path.stat(), template_path)

    response = helpers.serve_file(rf.get("/"), path)
    if hasattr(response, "render"):
        # ensure template is actually rendered, for template coverage
        response.render()

    assert response.status_code == 200
    assert response.headers["Last-Modified"] == "Tue, 05 Mar 2024 15:35:04 GMT"
    assert response.headers["Content-Type"].split(";")[0] == mimetype
    assert response.headers["Etag"] == etag


def test_serve_file_rendered_with_filename(tmp_path):
    rf = RequestFactory()

    path = tmp_path / "hash"
    path.write_text("data")

    time = 1709652904  # date this test was written
    os.utime(path, (time, time))

    response = helpers.serve_file(rf.get("/"), path, filename="test.html")
    assert response.status_code == 200
    assert response.headers["Last-Modified"] == "Tue, 05 Mar 2024 15:35:04 GMT"
    assert response.headers["Content-Type"].split(";")[0] == "text/html"
    assert response.headers["Etag"] == '"65e73ba8-4"'


@pytest.mark.parametrize(
    "suffix,template_path",
    [
        (".html", None),
        (".png", None),
        (".csv", "airlock/templates/file_browser/csv.html"),
        (".txt", "airlock/templates/file_browser/text.html"),
    ],
)
def test_serve_file_not_modified(tmp_path, suffix, template_path):
    rf = RequestFactory()
    path = tmp_path / ("test" + suffix)
    # use a csv as test data, it renders fine as text
    path.write_text("a,b,c\n1,2,3")
    time = 1709652904  # date this test was written
    os.utime(path, (time, time))

    etag = helpers.build_etag(path.stat(), template_path)

    request = rf.get("/", headers={"If-None-Match": etag})
    response = helpers.serve_file(request, path)
    assert response.status_code == 304

    request = rf.get(
        "/", headers={"If-Modified-Since": "Tue, 05 Mar 2024 15:35:04 GMT"}
    )
    response = helpers.serve_file(request, path)
    assert response.status_code == 304

    request = rf.get(
        "/", headers={"If-Modified-Since": "Tue, 05 Mar 2023 15:35:04 GMT"}
    )
    response = helpers.serve_file(request, path)
    assert response.status_code == 200


def test_serve_file_no_suffix(tmp_path):
    rf = RequestFactory()

    path = tmp_path / "nosuffix"
    path.touch()

    with pytest.raises(helpers.ServeFileException):
        helpers.serve_file(rf.get("/"), path)

    with pytest.raises(helpers.ServeFileException):
        helpers.serve_file(rf.get("/"), path, filename="nosuffix")
