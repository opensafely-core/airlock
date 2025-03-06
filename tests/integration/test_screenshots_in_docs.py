import os
import re
from pathlib import Path

from django.conf import settings


SCREENSHOT_RE = re.compile(r"\b[\w-]+.png\b")


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
