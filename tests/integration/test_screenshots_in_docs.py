import os
import re
from pathlib import Path

import pytest
from django.conf import settings


SCREENSHOT_RE = re.compile(r"\/([^\/]+\.png\b)")


def test_docs_screenshots_are_used():
    screenshots = [
        filepath.name for filepath in Path(settings.SCREENSHOT_DIR).glob("**/*.png")
    ]

    docs = Path(settings.BASE_DIR / "docs").glob("**/*.md")
    screenshots_in_docs = []
    for filepath in docs:
        matches = SCREENSHOT_RE.findall(filepath.read_text())
        screenshots_in_docs.extend(matches)

    unused_screenshots = set(screenshots) - set(screenshots_in_docs)
    assert not unused_screenshots, (
        f"Found screenshot files that are not used in docs: {unused_screenshots}"
        f"{'Have new screenshots been taken in this test run?' if os.environ.get('TAKE_SCREENSHOTS') else ''}"
    )

    not_found_screenshots = set(screenshots_in_docs) - set(screenshots)
    assert not not_found_screenshots, (
        f"Missing screenshot files referenced in docs: {not_found_screenshots}"
    )


@pytest.mark.parametrize(
    "text_snippet,found",
    [
        ("../screenshots/screenshot.png", ["screenshot.png"]),
        ("../screenshots/a-screenshot.png", ["a-screenshot.png"]),
        ("../screenshots/a.test.screenshot.png", ["a.test.screenshot.png"]),
        ("../screenshots/screenshot(1).png", ["screenshot(1).png"]),
        ("../screenshots/subdir/1screenshot.png", ["1screenshot.png"]),
        ("/screenshots/subdir/subsubdir/screenshot.png", ["screenshot.png"]),
        (
            "some text dir/screenshot.png some text another/dir/screenshot1.png",
            ["screenshot.png", "screenshot1.png"],
        ),
        (
            """            Markdown                               |  Rendered
            :-------------------------:                            |:-------------------------:
            ![Markdown](../screenshots/mkd_screenshot.png) | ![Rendered](../screenshots/mkd_screenshot1.png))
                    )
            """,
            ["mkd_screenshot.png", "mkd_screenshot1.png"],
        ),
        ("/dir/screenshot.png", ["screenshot.png"]),
        ("screenshot.png", []),
        ("/screenshots/subdir/screenshot.png1", []),
    ],
)
def test_screenshot_png_detection(text_snippet, found):
    assert SCREENSHOT_RE.findall(text_snippet) == found
