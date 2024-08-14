from django.conf import settings


def screenshot_element_with_padding(page, element_locator, filename):
    """
    Take a screenshot with 10px padding around an element.

    Playwright allows screenshotting of a specific element
    (with element_locator.screenshot()) but it crops very close and makes
    ugly screenshots for including in docs.
    """
    box = element_locator.bounding_box()
    page.screenshot(
        path=settings.SCREENSHOT_DIR / filename,
        clip={
            "x": box["x"] - 10,
            "y": box["y"] - 10,
            "width": box["width"] + 20,
            "height": box["height"] + 20,
        },
    )
