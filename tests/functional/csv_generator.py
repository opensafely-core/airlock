"""
A Hypothesis strategy for generating valid CSV files
"""

import functools
import string
from csv import writer
from io import StringIO

from hypothesis.strategies import (
    composite,
    floats,
    integers,
    lists,
    sampled_from,
    text,
)


def _records_to_csv(rows, lineterminator):
    """
    Convert the results into a csv string
    """
    f = StringIO()
    w = writer(f, lineterminator=lineterminator)
    for row in rows:
        w.writerow(row)

    return f.getvalue()


def _escape_csv_field(field) -> str:
    field = str(field)

    # Carriage return characters (\r) become newline (\n) characters in the html
    # This is fine, but annoying for the tests which then fail. So easier to just
    # exclude them as a possible csv character
    while "\r" in field:
        field = field.replace("\r", "_")
    return str(field)


@composite
def csv_row(draw, columns):
    """
    Strategy to produce a single csv row

    Args:
      columns: a list of hypothesis strategies, one per column

    Returns:
      tuple: the csv row
    """
    return tuple(_escape_csv_field(draw(column)) for column in columns)


valid_column_types = [
    integers,
    floats,
    functools.partial(text, min_size=1, max_size=20, alphabet=string.printable),
]


@composite
def csv_rows(draw, num_columns: int = 5, min_lines: int = 2, max_lines: int = 10):
    """
    Strategy to produce a list of csv rows

    Args:
      num_columns: The number of columns to generate
      min_lines: The minimum number of rows in the CSV
      max_lines: The maximum number of rows in the CSV

    Returns:
      list[tuple]: a list of csv rows as tuples
    """

    columns = [draw(sampled_from(valid_column_types))() for _ in range(num_columns)]
    rows = draw(
        lists(
            csv_row(columns=columns),
            min_size=min_lines,
            max_size=max_lines,
        )
    )
    return rows


@composite
def csv_file(draw, num_columns: int = 5, min_lines: int = 2, max_lines: int = 10):
    """
    Strategy to produce a CSV file as a string. Uses `csv_rows` strategy to
    generate the data.

    Args:
      num_columns: The number of columns to generate
      min_lines: The minimum number of rows in the CSV
      max_lines: The maximum number of rows in the CSV

    Returns:
      str: a string in CSV format
    """

    rows = list(
        draw(
            csv_rows(min_lines=min_lines, max_lines=max_lines, num_columns=num_columns)
        )
    )

    # The headers in our html table contain lots of whitespace because of the styling
    # and the presence of the sorting icons. If a header in the csv has any leading or
    # trailing whitespace then it's impossible to tell how much from the rendered table.
    # So to make life easier let's just generate csv files that don't have leading or
    # trailing spaces in the first row
    rows[0] = ["_" if field.strip() == "" else field.strip() for field in rows[0]]

    # Whether the line terminator is \r\n (excel, windows) or \n (linux/unix)
    lineterminator = draw(sampled_from(["\r\n", "\n"]))

    return _records_to_csv(
        rows=rows,
        lineterminator=lineterminator,
    )
