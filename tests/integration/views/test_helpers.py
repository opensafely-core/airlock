import os

import pytest
from django.test import RequestFactory
from django.template.response import TemplateResponse

from airlock.views import helpers


@pytest.mark.parametrize(
    "suffix,mimetype",
    [
        (".html", "text/html"),
        (".csv", "text/html"),
        (".png", "image/png"),
        (".txt", "text/html"),
    ],
)
def test_serve_file_rendered(tmp_path, suffix, mimetype):
    rf = RequestFactory()
    path = tmp_path / ("test" + suffix)
    # use a csv as test data, it renders fine as text
    path.write_text("a,b,c\n1,2,3")
    time = 1709652904  # date this test was written
    os.utime(path, (time, time))

    # test rendered content for iframe
    response = helpers.serve_file(rf.get("/"), path)
    if isinstance(response, TemplateResponse):
        # ensure template is actually rendered, for template coverage
        response.render()

    assert response.headers["Last-Modified"] == "Tue, 05 Mar 2024 15:35:04 GMT"
    assert response.headers["Etag"] == '"65e73ba8-b"'
    assert response.headers["Content-Type"].split(";")[0] == mimetype


@pytest.mark.parametrize(
    "filename,mimetype",
    [
        ("foo.html", "text/html"),
        ("foo.csv", "text/html"),
        ("foo.png", "image/png"),
        ("foo.txt", "text/html"),
    ],
)
def test_serve_file_filename(tmp_path, filename, mimetype):
    rf = RequestFactory()
    path = tmp_path / "hashed_file"
    path.write_text("data")
    time = 1709652904  # date this test was written
    os.utime(path, (time, time))

    response = helpers.serve_file(rf.get("/"), path, filename=filename)
    assert response.headers["Last-Modified"] == "Tue, 05 Mar 2024 15:35:04 GMT"
    assert response.headers["Etag"] == '"65e73ba8-4"'
    assert response.headers["Content-Type"].split(";")[0] == mimetype


def test_serve_file_no_suffix(tmp_path):
    rf = RequestFactory()

    path = tmp_path / "nosuffix"
    path.touch()

    with pytest.raises(Exception):
        helpers.serve_file(rf.get("/"), path)

    with pytest.raises(Exception):
        helpers.serve_file(rf.get(), path, filename="nosuffix")
