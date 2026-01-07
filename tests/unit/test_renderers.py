import os

import pytest
from opentelemetry import trace

from airlock import renderers
from airlock.types import UrlPath
from tests import factories


RENDERER_TESTS = [
    (".html", "text/html", False, None),
    (".png", "image/png", False, None),
    (".csv", "text/html", False, "airlock/templates/file_browser/csv.html"),
    (".txt", "text/html", False, "airlock/templates/file_browser/text.html"),
    (".log", "text/html", False, "airlock/templates/file_browser/text.html"),
    (".html", "text/html", True, "airlock/templates/file_browser/text.html"),
    (".png", "text/html", True, "airlock/templates/file_browser/text.html"),
    (".csv", "text/html", True, "airlock/templates/file_browser/text.html"),
    (".txt", "text/html", True, "airlock/templates/file_browser/text.html"),
    (".log", "text/html", True, "airlock/templates/file_browser/text.html"),
]


@pytest.mark.parametrize("suffix,mimetype,plaintext,template_path", RENDERER_TESTS)
def test_renderers_get_renderer_workspace(
    tmp_path, rf, suffix, mimetype, plaintext, template_path
):
    path = tmp_path / ("test" + suffix)
    # use a csv as test data, it works for other types too
    path.write_text("a,b,c\n1,2,3")
    content_hash = "65e73ba8-b"

    time = 1709652904  # date this test was written
    os.utime(path, (time, time))

    renderer_class = renderers.get_renderer(path, plaintext=plaintext)
    renderer = renderer_class.from_file(path)

    assert renderer.last_modified == "Tue, 05 Mar 2024 15:35:04 GMT"

    if template_path:
        assert isinstance(renderer.template, renderers.RendererTemplate)
        assert renderer.cache_id == f"{content_hash}-{renderer.template.content_hash}"
    else:
        assert renderer.cache_id == content_hash

    response = renderer.get_response()
    if hasattr(response, "render"):
        # ensure template is actually rendered, for template coverage
        response.render()

    assert response.status_code == 200
    assert response.headers["Content-Type"].split(";")[0] == mimetype
    assert response.headers["Last-Modified"] == renderer.last_modified
    assert response.headers["ETag"] == renderer.etag
    assert response.headers["Cache-Control"] == "max-age=31536000, immutable"


@pytest.mark.parametrize("suffix,mimetype,plaintext,template_path", RENDERER_TESTS)
@pytest.mark.django_db
def test_renderers_get_renderer_request(
    tmp_path, rf, suffix, mimetype, plaintext, template_path
):
    filepath = UrlPath("test" + suffix)
    grouppath = "group" / filepath
    request = factories.create_release_request("workspace")
    # use a csv as test data, it works for other types too
    request = factories.add_request_file(request, "group", filepath, "a,b,c\n1,2,3")

    time = 1709652904  # date this test was written
    abspath = request.abspath(grouppath)
    os.utime(abspath, (time, time))
    request_file = request.get_request_file_from_urlpath(grouppath)

    renderer_class = renderers.get_renderer(request_file.relpath, plaintext=plaintext)
    renderer = renderer_class.from_file(
        abspath, request_file.relpath, request_file.file_id
    )
    assert renderer.last_modified == "Tue, 05 Mar 2024 15:35:04 GMT"

    if template_path:
        assert isinstance(renderer.template, renderers.RendererTemplate)
        assert (
            renderer.cache_id
            == f"{request_file.file_id}-{renderer.template.content_hash}"
        )
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


@pytest.mark.parametrize("suffix,mimetype,plaintext,template_path", RENDERER_TESTS)
def test_code_renderer_from_contents(suffix, mimetype, plaintext, template_path):
    path = UrlPath("test." + suffix)

    renderer_class = renderers.get_code_renderer(path, plaintext=plaintext)
    renderer = renderer_class.from_contents(b"test", path, "cache_id")
    response = renderer.get_response()

    assert response.status_code == 200
    assert response.headers["Content-Type"].split(";")[0] == mimetype
    assert response.headers["ETag"] == renderer.etag
    assert response.headers["Cache-Control"] == "max-age=31536000, immutable"


def test_csv_renderer_handles_empty_file(tmp_path):
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("")
    relpath = empty_csv.relative_to(tmp_path)
    Renderer = renderers.get_renderer(relpath)
    renderer = Renderer.from_file(empty_csv, relpath)
    response = renderer.get_response()
    response.render()
    assert response.status_code == 200


def test_csv_renderer_handles_uneven_columns(tmp_path):
    # CSVs without the equal numbers of columns per row
    # can be rendered in a plain html table, but not with datatables
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("Foo Bar\nfoo,bar")
    relpath = bad_csv.relative_to(tmp_path)
    Renderer = renderers.get_renderer(relpath)
    renderer = Renderer.from_file(bad_csv, relpath)
    response = renderer.get_response()
    response.render()
    assert response.status_code == 200
    assert response.context_data["use_clusterize_table"] is False


def test_csv_renderer_uses_faster_csv_renderer(tmp_path):
    good_csv = tmp_path / "good.csv"
    good_csv.write_text("Foo,Bar\nfoo,bar")
    relpath = good_csv.relative_to(tmp_path)
    Renderer = renderers.get_renderer(relpath)
    renderer = Renderer.from_file(good_csv, relpath)
    response = renderer.get_response()
    response.render()
    assert response.status_code == 200
    assert response.context_data["use_clusterize_table"] is True


def test_plaintext_renderer_handles_invalid_utf8(tmp_path):
    invalid_file = tmp_path / "invalid.txt"
    invalid_file.write_bytes(b"invalid \xf0\xa4\xad continuation byte")
    relpath = invalid_file.relative_to(tmp_path)
    Renderer = renderers.get_renderer(relpath, plaintext=True)
    renderer = Renderer.from_file(invalid_file, relpath)
    response = renderer.get_response()
    response.render()
    assert response.status_code == 200
    assert "invalid � continuation byte" in response.rendered_content


def test_log_renderer_handles_ansi_colors(tmp_path):
    log_file = tmp_path / "test.log"
    # in ansi codes:
    # \x1B[32m = foregrouund green
    # \x1b[1m bold
    # \x1b[0m resets formatting
    log_file.write_bytes(
        b"No ansi here \x1b[32m\x1b[1mThis is green and bold.\x1b[0m This is not."
    )
    relpath = log_file.relative_to(tmp_path)
    Renderer = renderers.get_renderer(relpath)
    renderer = Renderer.from_file(log_file, relpath)
    response = renderer.get_response()
    response.render()
    assert response.status_code == 200

    assert "ansi1 { font-weight: bold; }" in response.rendered_content
    assert "ansi32 { color: #00aa00; }" in response.rendered_content
    assert (
        '<span class="ansi1 ansi32">This is green and bold.</span>'
        in response.rendered_content
    )


@pytest.mark.parametrize(
    "log,limit,expected,truncated",
    [
        ("LINE1\nLINE2\nLINE3\n", 12, "LINE3\n", True),  # line 2 striped
        ("LINE1\nLINE2\nLINE3\n", 13, "LINE2\nLINE3\n", True),  # line 2 not stripped
        ("LINE1LINE3\n", 6, "LINE3\n", True),  # single line
        ("LINE2\nLINE3\n", 30, "LINE2\nLINE3\n", False),  # under limit
    ],
)
def test_log_renderer_handles_truncation(
    tmp_path, settings, log, limit, expected, truncated
):
    tracer = trace.get_tracer("tests")
    settings.MAX_LOG_BYTES = limit
    log_file = tmp_path / "test.log"
    log_file.write_text(log)
    relpath = log_file.relative_to(tmp_path)
    Renderer = renderers.get_renderer(relpath)
    renderer = Renderer.from_file(log_file, relpath)

    with tracer.start_as_current_span("test") as span:
        response = renderer.get_response()

    response.render()
    assert response.status_code == 200
    assert expected in response.rendered_content
    assert "LINE1" not in response.rendered_content
    assert span.attributes["job.log_truncated"] == truncated  # type: ignore
    assert span.attributes["job.log_size"] == len(log)  # type: ignore

    if truncated:
        assert "Log truncated" in response.rendered_content
    else:
        assert "Log truncated" not in response.rendered_content
