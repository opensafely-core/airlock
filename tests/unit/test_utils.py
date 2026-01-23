import pytest

from airlock.utils import summarize_column, summarize_csv


@pytest.mark.parametrize("headers,rows", [(["a header"], []), ([], [])])
def test_summarize_csv_no_data(headers, rows):
    assert summarize_csv(headers, rows) is None


def test_summarize_csv():
    headers = ["Col1", "Col2", "Col3"]
    rows = [
        # whitespace ignored, column type text, int, float
        (1, ("foo", " 1 ", "3.0")),
        (2, ("bar", "1", " 0.5")),
        (3, ("foo", "2 ", "1.0 ")),
    ]
    summary = summarize_csv(headers, rows)

    assert summary["headers"] == [
        "Column name",
        "Column type",
        "Total rows",
        "Null / missing",
        "Redacted",
        "Min value",
        "Min non-zero",
        "Max value",
        "Sum",
        "Divisible by 5",
        "Divisible by 6",
        "Midpoint 6 rounded",
    ]
    assert summary["rows"] == [
        ["Col1", "text", 3, 0, "-", "-", "-", "-", "-", "-", "-", "-"],
        ["Col2", "integer", 3, 0, 0, 1, 1, 2, 4, False, False, False],
        ["Col3", "float", 3, 0, 0, 0.5, 0.5, 3.0, 4.5, False, False, False],
    ]


def test_summarize_csv_uneven_columns():
    headers = ["Col1", "Col2", "Col3"]
    rows = [
        # row 1 has values for first 2 cols only
        (1, ("foo", "1")),
        # row 1 has values for an extra col with no matching header, ignored
        (2, ("bar", "1", "0.5", "x")),
        (3, ("foo", "2 ", "1.0")),
    ]
    summary = summarize_csv(headers, rows)

    assert summary["rows"] == [
        ["Col1", "text", 3, 0, "-", "-", "-", "-", "-", "-", "-", "-"],
        ["Col2", "integer", 3, 0, 0, 1, 1, 2, 4, False, False, False],
        ["Col3", "float", 3, 0, 0, 0.5, 0.5, 1.0, 1.5, False, False, False],
    ]


@pytest.mark.parametrize(
    "col_data",
    [
        ("1", "2", ""),
        ("1", "None", "2"),
        ("1", "null", "2"),
        ("1", "none", "2"),
    ],
)
def test_summarize_column_missing_values(col_data):
    column_summary = summarize_column("col_name", col_data)
    assert column_summary["missing_values"] == 1
    assert column_summary["type"] == "integer"


@pytest.mark.parametrize(
    "col_data,expected_type,expected_redacted",
    [
        (("1", "2", "[REDACTED]"), "integer", 1),
        (("1.3", "2", "Redacted "), "float", 1),
        (("1", "2", "3"), "integer", 0),
        (("1", "2", "<4"), "text", "-"),
    ],
)
def test_summarize_csv_redacted_values(col_data, expected_type, expected_redacted):
    column_summary = summarize_column("col_name", col_data)
    assert column_summary["missing_values"] == 0
    assert column_summary["type"] == expected_type
    assert column_summary["redacted"] == expected_redacted


@pytest.mark.parametrize(
    "col_data,divisible_by_5",
    [
        (("5", "10", "15", "0", "500", "35"), True),
        (("50", "10", "0", "-10", "-20"), True),
        (("5", "10", "15", "[REDACTED]", ""), True),
        (("5.0", "10.0", "15.0"), True),
        (("5", "10", "15", "16"), False),
    ],
)
def test_summarize_rounded_5(col_data, divisible_by_5):
    column_summary = summarize_column("col_name", col_data)
    assert column_summary["divisible_by_5"] is divisible_by_5


@pytest.mark.parametrize(
    "col_data,divisible_by_6",
    [
        (
            (
                "0",
                "6",
                "24",
            ),
            True,
        ),
        (("48", "-12", "0"), True),
        (("6", "12", "18", "[REDACTED]", ""), True),
        (("6.0", "12.0", "18.0"), True),
        (("6", "12", "13"), False),
    ],
)
def test_summarize_rounded_6(col_data, divisible_by_6):
    column_summary = summarize_column("col_name", col_data)
    assert column_summary["divisible_by_6"] is divisible_by_6


@pytest.mark.parametrize(
    "col_data,midpoint6_rounded",
    [
        (("0", "3", "9", "15", "21"), True),
        (("9", "27", "-3"), True),
        (("3", "9", "15", "[REDACTED]", ""), True),
        (("3.0", "9.0", "0.0"), True),
        (("9", "15", "18"), False),
    ],
)
def test_summarize_midpoint6_rounded(col_data, midpoint6_rounded):
    column_summary = summarize_column("col_name", col_data)
    assert column_summary["midpoint6_rounded"] is midpoint6_rounded
