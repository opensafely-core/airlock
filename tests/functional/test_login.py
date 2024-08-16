from unittest import mock

from playwright.sync_api import expect

from .conftest import login_as_user
from .utils import screenshot_element_with_padding


@mock.patch("airlock.login_api.session.post", autospec=True)
def test_login(requests_post, settings, page, live_server):
    settings.AIRLOCK_API_TOKEN = "test_api_token"
    settings.DEV_USERS = {}

    api_response = requests_post.return_value
    api_response.status_code = 200
    api_response.json.return_value = {
        "username": "test_user",
        "output_checker": False,
    }

    page.goto(live_server.url + "/login/?next=/")
    page.locator("#id_user").fill("test_user")
    page.locator("#id_token").fill("dummy test token")

    # Scroll the button into view before screenshotting the form
    submit_button = page.locator("button[type=submit]")
    submit_button.scroll_into_view_if_needed()
    login_form = page.get_by_test_id("loginform")
    screenshot_element_with_padding(page, login_form, "login_form.png")

    submit_button.click()

    requests_post.assert_called_with(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/authenticate",
        headers={"Authorization": "test_api_token"},
        json={"user": "test_user", "token": "dummy test token"},
    )

    expect(page).to_have_url(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text("Logged in as: test_user")
    expect(page.get_by_test_id("switch-user")).not_to_be_attached()


def test_switch_users(dev_users, settings, page, context, live_server):
    settings.DEV_USERS = {
        "researcher": "researcher",
        "output_checker": "output_checker",
        "output_checker_1": "output_checker_1",
    }
    login_as_user(
        live_server,
        context,
        {"username": "researcher", "workspaces": {}, "output_checker": False},
    )
    page.goto(live_server.url)
    expect(page.locator("body")).to_contain_text("Logged in as: researcher")

    menu_locator = page.get_by_test_id("switch-user")
    expect(menu_locator).to_be_attached()
    # Click on the menu item to show the available users
    menu_locator.click()
    # Switch user to checker1
    user_switch_button = page.locator('button:text("output_checker_1")')
    expect(user_switch_button).to_be_visible()
    user_switch_button.click()
    expect(page.locator("body")).to_contain_text("Logged in as: output_checker_1")
