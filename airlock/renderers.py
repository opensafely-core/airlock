from __future__ import annotations

import csv
from dataclasses import dataclass
from email.utils import formatdate
from functools import cached_property
from pathlib import Path

from django.http import FileResponse
from django.template import Template, loader
from django.template.response import SimpleTemplateResponse


@dataclass
class RendererTemplate:
    name: str
    path: Path = None
    template: Template = None

    def __post_init__(self):
        self.template = loader.get_template(self.name)
        self.path = Path(self.template.template.origin.name)

    def cache_id(self):
        return filesystem_key(self.path.stat())


@dataclass
class Renderer:
    MAX_AGE = 365 * 24 * 60 * 60  # 1 year
    template = None

    abspath: Path
    file_cache_id: str
    filename: str
    last_modified: str

    def get_response(self):
        if self.template:
            context = self.context()
            response = SimpleTemplateResponse(self.template.template, context)
        else:
            response = FileResponse(self.abspath.open("rb"), filename=self.filename)

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
    template = RendererTemplate("file_browser/csv.html")

    def context(self):
        reader = csv.reader(self.abspath.open())
        headers = next(reader)
        return {"headers": headers, "rows": reader}


class TextRenderer(Renderer):
    template = RendererTemplate("file_browser/text.html")

    def context(self):
        return {
            "text": self.abspath.read_text(),
            "class": Path(self.filename).suffix.lstrip("."),
        }


FILE_RENDERERS = {
    ".csv": CSVRenderer,
    ".log": TextRenderer,
    ".txt": TextRenderer,
    ".json": TextRenderer,
}


def get_renderer(abspath, request_file=None):
    stat = abspath.stat()

    if request_file:
        suffix = request_file.relpath.suffix
        filename = request_file.relpath.name
        file_cache_id = request_file.file_id
    else:
        suffix = abspath.suffix
        filename = abspath.name
        file_cache_id = filesystem_key(stat)

    renderer_class = FILE_RENDERERS.get(suffix, Renderer)

    return renderer_class(
        abspath=abspath,
        file_cache_id=file_cache_id,
        last_modified=formatdate(stat.st_mtime, usegmt=True),
        filename=filename,
    )


def filesystem_key(stat):
    # Like whitenoise, use filesystem metadata rather than hash as it's faster
    return f"{int(stat.st_mtime):x}-{stat.st_size:x}"
