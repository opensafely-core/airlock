from playwright.sync_api import expect

from tests import factories

from .conftest import login_as_user
from .utils import screenshot_element_with_padding


def test_login(auth_api_stubber, settings, page, live_server):
    settings.DEV_USERS = {}

    auth_api_stubber("authenticate", json=factories.create_api_user())

    page.goto(live_server.url + "/login/?next=/")
    page.locator("#id_user").fill("testuser")
    page.locator("#id_token").fill("dummy test token")

    # Scroll the button into view before screenshotting the form
    submit_button = page.locator("button[type=submit]")
    submit_button.scroll_into_view_if_needed()
    login_form = page.get_by_test_id("loginform")
    screenshot_element_with_padding(page, login_form, "login_form.png")

    submit_button.click()

    expect(page).to_have_url(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text("Logged in as: testuser")
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
