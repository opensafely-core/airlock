from __future__ import annotations

import csv
import mimetypes
from dataclasses import dataclass
from email.utils import formatdate
from functools import cached_property
from io import BytesIO, StringIO
from pathlib import Path
from typing import IO, Any, ClassVar, Self, cast

from django.http import FileResponse, HttpResponseBase
from django.template import Template, loader
from django.template.response import SimpleTemplateResponse

from airlock.types import UrlPath
from airlock.utils import is_valid_file_type


@dataclass
class RendererTemplate:
    name: str
    path: Path
    template: Template

    @classmethod
    def from_name(cls, name: str) -> Self:
        template = cast(Template, loader.get_template(name))
        return cls(name, template=template, path=Path(template.origin.name))

    def cache_id(self):
        return filesystem_key(self.path.stat())


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

    @cached_property
    def cache_id(self):
        cache_id = self.file_cache_id
        if self.template:
            cache_id += "-" + self.template.cache_id()

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
    template = RendererTemplate.from_name("file_browser/file_content/csv.html")
    is_text: ClassVar[bool] = True

    def context(self):
        reader = csv.reader(self.stream)
        headers = next(reader, [])
        header_col_count = len(headers)
        rows = list(reader)
        ctx = {"headers": headers, "rows": rows, "use_datatables": True}
        if any(len(row) != header_col_count for row in rows):
            ctx["use_datatables"] = False
        return ctx


class TextRenderer(Renderer):
    template = RendererTemplate.from_name("file_browser/file_content/text.html")
    is_text: ClassVar[bool] = True

    def context(self):
        return {
            "text": self.stream.read(),
            "class": Path(self.filename).suffix.lstrip("."),
        }


class PlainTextRenderer(TextRenderer):
    template = RendererTemplate.from_name("file_browser/file_content/plaintext.html")


class InvalidFileRenderer(Renderer):
    template = RendererTemplate.from_name("file_browser/file_content/text.html")

    def context(self):
        return {
            "text": f"{self.filename} is not a valid file type and cannot be displayed.",
            "class": "",
        }


FILE_RENDERERS = {
    ".csv": CSVRenderer,
    ".log": TextRenderer,
    ".txt": TextRenderer,
    ".json": TextRenderer,
    ".md": TextRenderer,
}


def get_renderer(relpath: UrlPath, plaintext=False) -> type[Renderer]:
    if is_valid_file_type(UrlPath(relpath)):
        if plaintext:
            return PlainTextRenderer
        return FILE_RENDERERS.get(relpath.suffix, Renderer)
    return InvalidFileRenderer


def get_code_renderer(relpath: UrlPath, plaintext=False) -> type[Renderer]:
    """Guess correct renderer for code file."""
    if plaintext:
        return PlainTextRenderer

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
