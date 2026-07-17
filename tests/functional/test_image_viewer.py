from pathlib import Path

from playwright.sync_api import expect

from airlock.types import UrlPath
from tests import factories

from .conftest import login_as_user


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def test_large_png_fits_in_iframe(live_server, page, context):
    workspace = factories.create_workspace("image-workspace")

    # 600x600 test file is larger than the 400px iframe height
    png_bytes = (FIXTURE_DIR / "600x600.png").read_bytes()
    img_path = workspace.root() / "outputs" / "large.png"
    img_path.parent.mkdir(parents=True, exist_ok=True)
    img_path.write_bytes(png_bytes)
    factories.update_manifest(workspace, ["outputs/large.png"])

    login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="author",
            workspaces={
                "image-workspace": factories.create_api_workspace(
                    project="Test Project"
                ),
            },
        ),
    )

    page.goto(live_server.url + workspace.get_url(UrlPath("outputs/large.png")))

    # Wait for the iframe's style.height to be updated from its initial value, indicating
    # that the content resizer (see resizer.js) has fired. Otherwise we may end up checking
    # the iframe/image before they've finished reszing. Note that we are of course assuming
    # that the iframe height will always be adjusted to something that isn't _exactly_ the
    # inital 400px value, but that should be unlikely enough that this check is good enough
    # for the test (and alternative are either more complicated, or use a blunt wait_for_timeout).
    page.wait_for_function(
        "() => document.querySelector('#content-iframe').style.height !== '400px'"
    )

    iframe_locator = page.locator("#content-iframe")
    img_locator = page.frame_locator("#content-iframe").locator("img")

    expect(img_locator).to_be_visible()

    iframe_box = iframe_locator.bounding_box()
    img_box = img_locator.bounding_box()
    assert iframe_box is not None
    assert img_box is not None
    assert img_box["width"] <= iframe_box["width"]
    assert img_box["height"] <= iframe_box["height"]
