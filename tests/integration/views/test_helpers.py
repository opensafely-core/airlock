import os

from airlock.views import helpers


def test_serve_file(tmp_path):
    path = tmp_path / "test.txt"
    path.write_text("hi")
    time = 1709652904  # date this test was written
    os.utime(path, (time, time))

    response = helpers.serve_file(path)
    assert response.headers["Last-Modified"] == "Tue, 05 Mar 2024 15:35:04 GMT"
    assert response.headers["Etag"] == '"65e73ba8-2"'
    assert response.headers["Content-Type"] == "text/plain"
    assert response.headers["Content-Length"] == "2"
