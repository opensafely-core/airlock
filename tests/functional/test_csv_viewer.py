import csv
import sys
from io import StringIO

from hypothesis import given, settings

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
# So this is why we instead pass the csv file into pyright.evaluate() and let
# the evaluated javascript do the comparison, before returning an array of any
# failures


@given(csv_file=csv_file(min_lines=2, max_lines=10, num_columns=5))
@settings(deadline=None)
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
            const headerRow = document.querySelector('thead tr');
            const headerRowCells = Array.from(headerRow.querySelectorAll('th'));
            // This assumes the first column are the row numbers so we ignore them
            const headerValues = headerRowCells.map(cell => cell.textContent.trim()).slice(1);

            // Next we get the table rows
            const bodyRows = Array.from(document.querySelector('tbody').querySelectorAll('tr'));
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

    # In theory we should tidy up by closing the context. But there is a bug which
    # causes this test to hang when run with coverage turned on. So we check to see
    # if coverage is on, and if not we tidy up.
    # This (https://github.com/HypothesisWorks/hypothesis/issues/4052) is possibly the
    # same bug. But it's not clear whether it's hypothesis, pytest or coverage at fault
    # and it seeems to get fixed in higher versions of python. Maybe can test if it's
    # still a problem if we move higher than python 3.11
    if "coverage" not in sys.modules.keys():  # pragma: no cover
        context.close()
