from airlock import renderers
from airlock.views import helpers


def test_serve_file(tmp_path, rf):
    test_file = tmp_path / "test.foo"
    test_file.write_text("foo")

    renderer = renderers.Renderer(
        test_file,
        file_cache_id="cache_id",
        filename=test_file.name,
        last_modified="Tue, 05 Mar 2024 15:35:04 GMT",
    )

    request = rf.get("/")
    response = helpers.serve_file(request, renderer)
    assert response.status_code == 200
    assert list(response.streaming_content) == [b"foo"]

    request = rf.get("/", headers={"If-None-Match": renderer.etag})
    response = helpers.serve_file(request, renderer)
    assert response.status_code == 304

    request = rf.get(
        "/", headers={"If-Modified-Since": "Tue, 05 Mar 2024 15:35:04 GMT"}
    )
    response = helpers.serve_file(request, renderer)
    assert response.status_code == 304

    request = rf.get(
        "/", headers={"If-Modified-Since": "Tue, 05 Mar 2023 15:35:04 GMT"}
    )
    response = helpers.serve_file(request, renderer)
    assert response.status_code == 200
