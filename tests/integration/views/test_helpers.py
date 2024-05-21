from django.contrib.messages.api import get_messages
from django.contrib.messages.storage.session import SessionStorage
from django.contrib.sessions.backends.db import SessionStore
from django.template import Context, Template
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


def test_display_form_errors():
    request = RequestFactory().get("/")
    request.session = SessionStore()
    messages = SessionStorage(request)
    request._messages = messages

    form = forms.TokenLoginForm(request.POST)
    form.is_valid()
    helpers.display_form_errors(request, form.errors)

    ctx = Context({"messages": get_messages(request)})
    template = Template("{% for message in messages %}{{ message }}{% endfor %}")
    content = template.render(ctx)

    # assert the <br/> has not been escaped
    assert content == "user: This field is required.<br/>token: This field is required."
