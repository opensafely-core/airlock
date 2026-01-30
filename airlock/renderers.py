from __future__ import annotations

import csv
import hashlib
import mimetypes
import re
from dataclasses import dataclass
from email.utils import formatdate
from io import BytesIO, StringIO
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, ClassVar


if TYPE_CHECKING:
    from django.template.backends.base import _EngineTemplate

from ansi2html import Ansi2HTMLConverter
from django.conf import settings
from django.http import FileResponse, HttpResponseBase
from django.template import loader
from django.template.response import SimpleTemplateResponse
from django.utils.safestring import mark_safe

from airlock.types import UrlPath
from airlock.utils import is_valid_file_type, summarize_csv, truncate_log_stream


@dataclass
class RendererTemplate:
    name: str

    @property
    def template(self) -> _EngineTemplate:
        # This is cached by django, so is fast
        t = loader.get_template(self.name)

        # Calculate content hash if not already done, an attach it to the
        # django template instance. This is a little hacky, but its convenient
        # way to ensure that the content hash changes if the template has been
        # reloaded, which is really only when DEBUG=True. As our tests run with
        # DEBUG=True, this is marked as nocover.
        if not hasattr(t, "airlock_content_hash"):  # pragma: no cover
            t.airlock_content_hash = hashlib.md5(t.template.source.encode()).hexdigest()  # type: ignore
        return t

    @property
    def content_hash(self):
        # type ignore because its our own monkeypatched attribute
        return self.template.airlock_content_hash  # type: ignore


@dataclass
class Renderer:
    MAX_AGE = 365 * 24 * 60 * 60  # 1 year
    template: ClassVar[RendererTemplate | None] = None
    is_text: ClassVar[bool] = False

    stream: IO[Any]
    file_cache_id: str
    filename: str
    last_modified: str | None = None

    @classmethod
    def from_file(
        cls, abspath: Path, relpath: UrlPath | None = None, cache_id: str | None = None
    ) -> Renderer:
        stat = abspath.stat()
        path = relpath or abspath

        if cache_id is None:
            cache_id = filesystem_key(stat)

        if cls.is_text:
            stream: IO[Any] = abspath.open("r", errors="replace")
        else:
            stream = abspath.open("rb")

        return cls(
            stream=stream,
            file_cache_id=cache_id,
            last_modified=formatdate(stat.st_mtime, usegmt=True),
            filename=path.name,
        )

    @classmethod
    def from_contents(
        cls, contents: bytes, relpath: UrlPath, cache_id: str
    ) -> Renderer:
        if cls.is_text:
            stream: IO[Any] = StringIO(contents.decode("utf8", errors="replace"))
        else:
            stream = BytesIO(contents)

        return cls(
            stream=stream,
            file_cache_id=cache_id,
            filename=relpath.name,
        )

    def get_response(self):
        if self.template:
            context = self.context()
            context.setdefault("filename", self.filename)
            response: HttpResponseBase = SimpleTemplateResponse(
                self.template.template, context
            )
        else:
            response = FileResponse(self.stream, filename=self.filename)

        for k, v in self.headers().items():
            response.headers[k] = v

        return response

    def context(self):
        raise NotImplementedError()

    @property
    def cache_id(self):
        cache_id = self.file_cache_id
        if self.template:
            cache_id += "-" + self.template.content_hash

        return cache_id

    @property
    def etag(self):
        # quote as per spec
        return f'"{self.cache_id}"'

    def headers(self):
        return {
            "ETag": self.etag,
            "Last-Modified": self.last_modified,
            "Cache-Control": f"max-age={self.MAX_AGE}, immutable",
        }


class CSVRenderer(Renderer):
    template = RendererTemplate("file_browser/file_content/csv.html")
    is_text: ClassVar[bool] = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Temporary feature flag
        self.summarize = False

    def context(self):
        reader = csv.reader(self.stream)
        headers = next(reader, [])
        header_col_count = len(headers)
        rows = list(enumerate(reader, start=1))

        if self.summarize:
            summary = summarize_csv(headers, rows)
        else:
            summary = {}

        ctx = {
            "headers": headers,
            "rows": rows,
            "use_clusterize_table": True,
            "summary": summary,
        }
        if any(len(row) != header_col_count for _, row in rows):
            ctx["use_clusterize_table"] = False
        return ctx


class TextRenderer(Renderer):
    template = RendererTemplate("file_browser/file_content/text.html")
    is_text: ClassVar[bool] = True

    def context(self):
        return {
            "text": self.stream.read(),
            "class": Path(self.filename).suffix.lstrip("."),
            "truncated": False,
        }


class InvalidFileRenderer(Renderer):
    template = RendererTemplate("file_browser/file_content/text.html")

    def context(self):
        return {
            "text": f"{self.filename} is not a valid file type and cannot be displayed.",
            "class": "",
            "truncated": False,
        }


class LogRenderer(TextRenderer):
    def context(self):
        # Convert the text of the log file to HTML, converting ANSI colour codes to css classes
        # so we get the colour formatting from the original log.
        # We don't need the full HTML file that's produced, so just extract the <pre></pre>
        # tag which contains the log content and the inline styles.
        conv = Ansi2HTMLConverter()

        # truncate the logs if needed
        log, truncated = truncate_log_stream(self.stream, settings.MAX_LOG_BYTES)
        text = conv.convert(log)
        match = re.match(
            r".*(?P<style_tag><style.*</style>).*(?P<pre_tag><pre.*</pre>).*",
            text,
            flags=re.S,
        )
        if match:  # pragma: no branch
            # After conversion, we should always find a match. As a precaution, check
            # and render the plain text if we don't.
            style_tag = match.group("style_tag")
            pre_tag = match.group("pre_tag")
            text = mark_safe(f"{style_tag}{pre_tag}")

        return {
            "text": text,
            "class": Path(self.filename).suffix.lstrip("."),
            "truncated": truncated,
            "limit_kb": settings.MAX_LOG_BYTES // 1000,
        }


FILE_RENDERERS = {
    ".csv": CSVRenderer,
    ".log": LogRenderer,
    ".txt": TextRenderer,
    ".json": TextRenderer,
    ".md": TextRenderer,
    ".yaml": TextRenderer,
}


def get_renderer(relpath: UrlPath, plaintext=False) -> type[Renderer]:
    if is_valid_file_type(UrlPath(relpath)):
        if plaintext:
            return TextRenderer
        return FILE_RENDERERS.get(relpath.suffix, Renderer)
    return InvalidFileRenderer


def get_code_renderer(relpath: UrlPath, plaintext=False) -> type[Renderer]:
    """Guess correct renderer for code file."""
    if plaintext:
        return TextRenderer

    if relpath.suffix in FILE_RENDERERS:
        return FILE_RENDERERS[relpath.suffix]

    mtype, _ = mimetypes.guess_type(str(relpath), strict=False)

    if mtype is None:
        return TextRenderer

    if not mtype.startswith("text/"):
        return Renderer

    return TextRenderer


def filesystem_key(stat) -> str:
    # Like whitenoise, use filesystem metadata rather than hash as it's faster
    return f"{int(stat.st_mtime):x}-{stat.st_size:x}"
