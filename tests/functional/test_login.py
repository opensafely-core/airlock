from unittest import mock

from playwright.sync_api import expect


@mock.patch("airlock.login_api.requests.post", autospec=True)
def test_login(requests_post, settings, page, live_server):
    settings.AIRLOCK_API_TOKEN = "test_api_token"

    api_response = requests_post.return_value
    api_response.status_code = 200
    api_response.json.return_value = {
        "username": "test_user",
        "output_checker": False,
    }

    page.goto(live_server.url + "/login/?next=/")
    page.locator("#id_user").fill("test_user")
    page.locator("#id_token").fill("foo bar baz")
    page.locator("button[type=submit]").click()

    requests_post.assert_called_with(
        "https://jobs.opensafely.org/api/v2/releases/auth",
        headers={"Authorization": "test_api_token"},
        json={"user": "test_user", "token": "foo bar baz"},
    )

    expect(page).to_have_url(live_server.url + "/")
    expect(page.locator("body")).to_contain_text("Logged in as: test_user")
