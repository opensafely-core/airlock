import csv

from django import template


register = template.Library()


@register.simple_tag
def as_csv_data(file_content):
    reader = csv.reader(file_content.splitlines(keepends=True))
    return {
        "headers": next(reader),
        "rows": list(reader),
    }
