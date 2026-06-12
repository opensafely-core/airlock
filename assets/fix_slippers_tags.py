"""Rewrite hyphenated names in {% attrs %} and slippers component tags to use underscores.

Django 6.0 disallows hyphens in template variable names. Slippers' {% attrs %} tag
uses each listed name as both the HTML attribute key and the context variable to
look up, and slippers component invocations ({% #name ... %}) resolve each kwarg
name the same way, so hyphenated names raise TemplateSyntaxError at compile time.
Slippers converts underscores back to hyphens in the rendered HTML, so this
transformation is invisible to the browser.

Only names are rewritten; values are left alone (e.g. data-table-pagination=id
becomes data_table_pagination=id, but data_table_pagination="previous-page" keeps
its hyphenated string value).
"""

import re
import sys
from pathlib import Path


TAG_RE = re.compile(
    r"\{%\s*(attrs|#[A-Za-z_][A-Za-z0-9_]*)\s+([^%]+?)\s*%\}", re.DOTALL
)
TOKEN_RE = re.compile(r"""([^\s=]+)(=(?:"[^"]*"|'[^']*'|[^\s]+))?""")


def fix_token(match):
    name = match.group(1).replace("-", "_")
    value = match.group(2) or ""
    return name + value


def fix_tag(match):
    tag_name = match.group(1)
    new_content = TOKEN_RE.sub(fix_token, match.group(2)).strip()
    return "{% " + tag_name + " " + new_content + " %}"


def fix_file(path):
    text = path.read_text()
    new_text = TAG_RE.sub(fix_tag, text)
    if new_text == text:
        return False
    path.write_text(new_text)
    return True


def main(root):
    print("Updating slippers components for Django 6 compatibility...")
    for path in sorted(Path(root).rglob("*.html")):
        if fix_file(path):
            print(f"Fixed: {path}")


if __name__ == "__main__":
    main(sys.argv[1])
