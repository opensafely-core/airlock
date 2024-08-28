from django.conf import settings


def screenshot_element_with_padding(
    page, element_locator, filename, extra=None, crop=None
):
    """
    Take a screenshot with 10px padding around an element.

    Playwright allows screenshotting of a specific element
    (with element_locator.screenshot()) but it crops very close and makes
    ugly screenshots for including in docs.

    extra: optional dict, to add additional padding around the
    element (on top of the default 10px).

    crop: optional dict to crop height and/or width to a percentage of the
    original (from top left corner).
    """
    box = element_locator.bounding_box()

    clip = {
        "x": box["x"] - 10,
        "y": box["y"] - 10,
        "width": box["width"] + 20,
        "height": box["height"] + 20,
    }
    extra = extra or {}
    for key, extra_padding in extra.items():
        clip[key] += extra_padding

    crop = crop or {}
    for key, crop in crop.items():
        clip[key] *= crop

    page.screenshot(
        path=settings.SCREENSHOT_DIR / filename,
        clip=clip,
    )
