import csv
import sys
from io import StringIO
from os import environ

from hypothesis import given, settings
from playwright.sync_api import expect

from airlock.types import UrlPath
from tests import factories

from .conftest import login_as_user
from .csv_generator import csv_file


# An attempt to check that when a csv file is displayed in airlock, it contains
# the same content as the underlying file.
#
# I initially tried to use playwright.evaluate() to pull out the table contents
# and then compare it to the csv file in the python test. However string
# escaping was problematic e.g. a csv containing a carriage return (\r) would
# end up as a newline (\n) by the time it had come back to the test. Similarly
# escaped strings like hex (\x00) and unicode (\u0000) would turn from raw
# strings into their corresponding hex/unicode characters.
#
# So this is why we instead pass the csv file into page.evaluate() and let
# the evaluated javascript do the comparison, before returning an array of any
# failures

# NB There is a bug which causes these tests to hang when run with coverage turned
# on e.g. `just test-all` if a hypothesis test calls `context.close()`. This
# (https://github.com/HypothesisWorks/hypothesis/issues/4052) is possibly the same
# bug. But it's not clear whether it's hypothesis, pytest or coverage at fault and
# it seeems to get fixed in higher versions of python. So this might not be true if
# we migrate to higher than python 3.11. The code to add to the final hypothesis
# test in this file is:
#
# if "coverage" not in sys.modules.keys():  # pragma: no cover
#   context.close()


@given(csv_file=csv_file(min_lines=2, max_lines=10, num_columns=5))
@settings(deadline=None, max_examples=int(environ.get("HYPOTHESIS_MAX_EXAMPLES", 5)))
def test_csv_renders_all_text(live_server, browser, csv_file):
    # Normally we pass "context" and "page" as per function fixtures.
    # However hypothesis would then use the same context/page for each
    # test run and occasionally caching causes tests to fail. So instead
    # we create a new context and page for each test run
    context = browser.new_context()
    page = context.new_page()
    workspace = factories.create_workspace("my-workspace")

    factories.write_workspace_file(
        workspace,
        "outputs/file1.csv",
        csv_file,
    )
    login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="author",
            workspaces={
                "my-workspace": factories.create_api_workspace(project="Project 2"),
            },
        ),
    )

    page.goto(
        live_server.url + workspace.get_contents_url(UrlPath("outputs/file1.csv"))
    )

    reader = csv.reader(StringIO(csv_file), delimiter=",")
    csv_list = list(reader)

    failures = page.evaluate(
        """(csv_list) => {
            let failures = [];

            // First we get the contents of the header row
            const headerRow = document.querySelector('#airlock-table thead tr');
            const headerRowCells = Array.from(headerRow.querySelectorAll('th'));
            // This assumes the first column are the row numbers so we ignore them
            const headerValues = headerRowCells.map(cell => cell.textContent.trim()).slice(1);

            // Next we get the table rows
            const bodyRows = Array.from(document.querySelector('#airlock-table tbody').querySelectorAll('tr'));
            const rowValues = bodyRows.map((row) => {
                return Array.from(row.querySelectorAll('td')).map(cell => cell.textContent).slice(1)
            });

            // We add the headers back to the rowValues array as the first row
            rowValues.unshift(headerValues);

            // For each row in the actual csv file, we check that it is the same
            // as what is displayed in the table.
            csv_list.forEach((row, i) => {
              if(JSON.stringify(row) !== JSON.stringify(rowValues[i])) {
                failures.push(`The csv row ${JSON.stringify(row)} does not match the table row ${JSON.stringify(rowValues[i])}. In case it's useful the table row is ${rowValues[i]} before passed to JSON.strinfigy.`)
              }
            })

            // Finally we check that the number of rows in the csv file matches the html table
            if(csv_list.length !== rowValues.length) {
              failures.push(`CSV has ${csv.length} rows, but HTML table has ${rowValues.length}`);
            }
            return failures;
          }
        """,
        csv_list,
    )

    assert len(failures) == 0, print(repr(failures))

    # We can tidy up here without hanging because there is a hypothesis test
    # after this one. See the comment at the top of the file for more detail
    context.close()


# The current table virtualization implementation only shows 200 rows, plus
# the header. So we simluate at least a 210 row csv file.
@given(csv_file=csv_file(min_lines=210, max_lines=250, num_columns=5, just_text=True))
@settings(deadline=None, max_examples=int(environ.get("HYPOTHESIS_MAX_EXAMPLES", 5)))
def test_csv_sorting(live_server, browser, csv_file):
    # Normally we pass "context" and "page" as per function fixtures.
    # However hypothesis would then use the same context/page for each
    # test run and occasionally caching causes tests to fail. So instead
    # we create a new context and page for each test run
    context = browser.new_context()
    page = context.new_page()
    workspace = factories.create_workspace("my-workspace")

    factories.write_workspace_file(
        workspace,
        "outputs/file1.csv",
        csv_file,
    )
    login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="author",
            workspaces={
                "my-workspace": factories.create_api_workspace(project="Project 2"),
            },
        ),
    )

    page.goto(
        live_server.url + workspace.get_contents_url(UrlPath("outputs/file1.csv"))
    )

    column_sort_index = 2

    table_locator = page.locator("#airlock-table")

    # We get a handle on the sort button for the n and (n+1)th column
    sort_button_1 = table_locator.get_by_role("button").nth(column_sort_index + 1)
    sort_button_2 = table_locator.get_by_role("button").nth(column_sort_index + 2)

    reader = csv.reader(StringIO(csv_file), delimiter=",")
    csv_list = list(reader)

    # We don't want the first row as that is treated as a header
    rows = csv_list[1:]
    rows_sorted_1 = sorted(rows, key=lambda x: (x[column_sort_index]))
    rows_sorted_2 = sorted(rows, key=lambda x: (x[column_sort_index + 1]))

    first_row_sorted_asc = rows_sorted_1[0]
    last_row_sorted_column_value = rows_sorted_1[-1][column_sort_index]
    first_row_second_sort_column_value = rows_sorted_2[0][column_sort_index + 1]

    # There seems to be an edge case where if every value in the column
    # is already sorted, a javascript sort will not do anything, while the
    # python sort shifts some rows about. If the value in the first row
    # is the same after sorting, then we assume the first row stayed in
    # place
    if first_row_sorted_asc[column_sort_index] == rows[0][column_sort_index]:
        first_row_sorted_asc = rows[0]

    # sort ascending by nth column
    sort_button_1.click()

    # wait for sorting to finish
    expect(
        table_locator.locator(
            f"thead th:nth-child({column_sort_index + 2}) .icon.datatable-icon--ascending"
        )
    ).to_be_visible()

    # check first row is as expected
    for item in first_row_sorted_asc:
        expect(table_locator.locator("tbody tr:nth-child(1)").first).to_contain_text(
            item, timeout=1
        )

    # sort descending by nth column
    sort_button_1.click()

    # wait for sorting to finish
    expect(
        table_locator.locator(
            f"thead th:nth-child({column_sort_index + 2}) .icon.datatable-icon--descending"
        )
    ).to_be_visible()

    # Check cell in sorted column first row is as expected.
    # The sort behaviour in python/javascript is sufficiently different that
    # it's hard to get this correct after the first sort. So rather than adding
    # unnecessary complexity to the test, we just check that the first value in
    # the sorted column is as expected
    expect(
        table_locator.locator("tbody tr:nth-child(1)").first.locator(
            f"td:nth-child({column_sort_index + 2})"
        )
    ).to_contain_text(last_row_sorted_column_value, timeout=1)

    # sort ascending by (n+1)th column
    sort_button_2.click()

    # wait for sorting to finish
    expect(
        table_locator.locator(
            f"thead th:nth-child({column_sort_index + 3}) .icon.datatable-icon--ascending"
        )
    ).to_be_visible()

    expect(
        table_locator.locator("tbody tr:nth-child(1)").first.locator(
            f"td:nth-child({column_sort_index + 3})"
        )
    ).to_contain_text(first_row_second_sort_column_value, timeout=1)

    # See note at the top of file
    if "coverage" not in sys.modules.keys():  # pragma: no cover
        context.close()


# Ensure every element in a large csv table is eventually visible with scolling
def test_csv_scroll(live_server, page, context):
    workspace = factories.create_workspace("my-workspace")
    num_rows = 1000

    factories.write_workspace_file(
        workspace,
        "outputs/file1.csv",
        "\r\n".join([f"value,value,value{i}" for i in range(num_rows)]),
    )
    login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="author",
            workspaces={
                "my-workspace": factories.create_api_workspace(project="Project 2"),
            },
        ),
    )

    page.goto(
        live_server.url + workspace.get_contents_url(UrlPath("outputs/file1.csv"))
    )

    table_locator = page.locator("#airlock-table")
    table_locator.locator("tbody").hover()
    for i in range(num_rows):
        row_locator = table_locator.get_by_text(f"value{i}", exact=True)
        if not row_locator.is_visible():  # pragma: no branch (if we ever revert to non-virtualized tables then this is never True)
            # We need to scroll the table sufficiently so the next row appears.
            # This scrolls 6000 pixels which seems to be about right. NB "visible"
            # in the playwright context means it is potentially visible, even if
            # currently off screen. So we need to scroll by a long way because
            # initially there are 200 visible rows, and we need to scroll until
            # the virtualized html table generates the next batch of rows.
            page.mouse.wheel(0, 6000)
            expect(row_locator).to_be_visible()
