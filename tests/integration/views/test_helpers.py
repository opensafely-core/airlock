import os
from email.utils import formatdate

from django.contrib.messages.api import get_messages
from django.contrib.messages.storage.session import SessionStorage
from django.contrib.sessions.backends.db import SessionStore
from django.template import (  # type: ignore
    Context,
    Template,
    autoreload,
)
from django.test import RequestFactory

from airlock import forms, renderers
from airlock.views import helpers


def test_serve_file(tmp_path, rf):
    test_file = tmp_path / "test.foo"
    test_file.write_text("foo")

    renderer = renderers.Renderer.from_file(test_file)
    renderer.last_modified = "Tue, 05 Mar 2024 15:35:04 GMT"

    request = rf.get("/")
    response = helpers.serve_file(request, renderer)
    assert response.status_code == 200
    assert list(response.streaming_content) == [b"foo"]

    request = rf.get("/", headers={"If-None-Match": renderer.etag})
    response = helpers.serve_file(request, renderer)
    assert response.status_code == 304

    # update file content
    test_file.write_text("newfoo")
    new_renderer = renderers.Renderer.from_file(test_file)
    assert new_renderer.etag != renderer.etag
    request = rf.get("/", headers={"If-None-Match": renderer.etag})
    response = helpers.serve_file(request, new_renderer)
    assert response.status_code == 200

    request = rf.get("/", headers={"If-None-Match": new_renderer.etag})
    response = helpers.serve_file(request, new_renderer)
    assert response.status_code == 304


def test_serve_file_template_reloads(tmp_path, rf, settings):
    settings.TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [tmp_path],
            "APP_DIRS": False,
            "OPTIONS": {"debug": True},
        },
    ]

    template_file = tmp_path / "test_template.html"
    template_file.write_text("{{ text }}")

    class TestRenderer(renderers.TextRenderer):
        template = renderers.RendererTemplate("test_template.html")

    test_file = tmp_path / "test.foo"
    test_file.write_text("foo")

    # set the time on both the test and template file to 03 Jan 2025
    time = 1735900200
    os.utime(test_file, (time, time))
    os.utime(template_file, (time, time))

    renderer = TestRenderer.from_file(test_file)
    assert renderer.last_modified == formatdate(time, usegmt=True)

    request = rf.get("/")
    response = helpers.serve_file(request, renderer)
    assert response.status_code == 200
    assert response.rendered_content == "foo"
    previous_etag = response.headers["etag"]
    previous_last_modified = response.headers["last-modified"]

    # update the template
    template_file.write_text("{{ text }}123")

    # Clear the template cache. In a development environment, django's template
    # autoreloader would do this for us
    # https://github.com/django/django/blob/5.1.7/django/template/autoreload.py#L55
    # In production, it's irrelevant, because the template cache is in-memory, and
    # templates won't change without a deploy and app restart
    autoreload.reset_loaders()

    # serve_file() is called from a workspace or request content view, where the
    # renderer is instantiated again from the workspace or request file
    # A new call to serve_file() will be called with a new renderer, so it will
    # include any changes to the renderer's cache ID or last_modified date
    new_renderer = TestRenderer.from_file(test_file)

    # The etag is the combined cache ID based on both file and template; it has
    # changed because the template has been updated
    assert previous_etag != renderer.etag
    # last_modified is based on the file content only, so has not changed
    assert new_renderer.last_modified == renderer.last_modified

    request = rf.get(
        "/",
        # headers with etag and last_modified from the renderer used in the
        # first request
        headers={
            "If-None-Match": previous_etag,
            "If-Modified-Since": previous_last_modified,
        },
    )

    # The template has changed, so we expect the file to be reloaded, and
    # the content to reflect the updated template
    response = helpers.serve_file(request, new_renderer)
    assert response.status_code == 200
    assert response.rendered_content == "foo123"


def test_display_form_errors():
    request = RequestFactory().get("/")
    request.session = SessionStore()
    messages = SessionStorage(request)
    request._messages = messages  # type: ignore

    form = forms.TokenLoginForm(request.POST)
    form.is_valid()
    helpers.display_form_errors(request, form.errors)

    ctx = Context({"messages": get_messages(request)})
    template = Template("{% for message in messages %}{{ message }}{% endfor %}")
    content = template.render(ctx)

    # assert the <br/> has not been escaped
    assert content == "user: This field is required.<br/>token: This field is required."
