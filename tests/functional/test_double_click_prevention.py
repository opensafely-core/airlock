from playwright.sync_api import expect

from airlock.enums import RequestStatus
from tests import factories
from tests.functional.conftest import login_as_user

from .conftest import click_and_htmx


def _make_pending_request(live_server, context):
    author = login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(username="author"),
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(group="group", path="file1.txt"),
            factories.request_file(group="group", path="file2.txt"),
        ],
    )
    return author, release_request


def test_traditional_form_button_disabled_after_click(live_server, context, page):
    """A submit button in a traditional POST form is disabled once clicked, so
    a second click while the response is in-flight can't trigger the action again."""
    _, release_request = _make_pending_request(live_server, context)

    page.goto(live_server.url + release_request.get_url("group/file1.txt"))

    button = page.locator("#withdraw-file-button")
    expect(button).to_be_visible()
    expect(button).to_be_enabled()

    # Respond with 204 No Content so the browser stays on the current page
    # rather than navigating to a response body.
    page.route("**/requests/withdraw/**", lambda route: route.fulfill(status=204))

    click_and_htmx(page, page.locator("#withdraw-file-button"))

    expect(button).to_be_disabled()
    expect(button).to_have_attribute("aria-busy", "true")


def test_traditional_form_double_click_only_submits_once(live_server, context, page):
    """Even when the user manages to click twice rapidly, only one POST is sent."""
    _, release_request = _make_pending_request(live_server, context)

    page.goto(live_server.url + release_request.get_url("group/file1.txt"))

    button = page.locator("#withdraw-file-button")
    expect(button).to_be_visible()

    # record the withdraw requests seen for /requests/withdraw/** route
    withdraw_requests = []

    def handler(route):
        withdraw_requests.append(route.request.url)
        route.fulfill(status=204)

    page.route("**/requests/withdraw/**", handler)

    # Two consecutive clicks via JS. The disable is scheduled via setTimeout(0)
    # so we sleep briefly between them to give the macrotask a chance to run.
    page.evaluate(
        """async () => {
            const btn = document.querySelector('#withdraw-file-button');
            btn.click();
            await new Promise((r) => setTimeout(r, 50));
            btn.click();
        }"""
    )

    # Wait until the button is disabled (i.e. the first submission fired and the
    # script ran), then assert only one request was recorded.
    expect(button).to_be_disabled()
    assert len(withdraw_requests) == 1


def test_htmx_form_button_disabled_during_request(live_server, context, page):
    """A submit button in an HTMX form is disabled while the request is in-flight."""
    _, release_request = _make_pending_request(live_server, context)

    page.goto(live_server.url + release_request.get_url("group/file1.txt"))

    button = page.locator("#update-file-modal-button")
    expect(button).to_be_visible()
    expect(button).to_be_enabled()

    # Hang the HTMX POST so the in-flight state is observable.
    page.route("**/requests/multiselect/**", lambda route: None)

    button.click(no_wait_after=True)

    expect(button).to_be_disabled()
    expect(button).to_have_attribute("aria-busy", "true")


def test_htmx_form_button_re_enabled_after_request(live_server, context, page):
    """A submit button in an HTMX form is re-enabled once the request completes."""
    _, release_request = _make_pending_request(live_server, context)

    page.goto(live_server.url + release_request.get_url("group/file1.txt"))

    button = page.locator("#update-file-modal-button")
    expect(button).to_be_visible()
    expect(button).to_be_enabled()

    # Return a minimal HTMX response so the request completes immediately.
    page.route(
        "**/requests/multiselect/**",
        lambda route: route.fulfill(status=200, body="<div></div>"),
    )

    button.click(no_wait_after=True)

    expect(button).to_be_enabled()
    expect(button).not_to_have_attribute("aria-busy", "true")
