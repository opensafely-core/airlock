import pytest
from playwright.sync_api import expect

from airlock.enums import RequestFileType, RequestStatus
from airlock.types import UrlPath
from tests import factories

from .conftest import login_as_user


@pytest.fixture(autouse=True)
def workspaces():
    factories.create_workspace("test-dir1")
    factories.create_workspace("test-dir2")


def test_workspaces_index_as_ouput_checker(live_server, page, output_checker_user):
    # this should only list their workspaces, even though they can access all workspaces
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text("Workspaces for test_output_checker")
    expect(page.locator("body")).not_to_contain_text("test-dir1")
    expect(page.locator("body")).to_contain_text("test-dir2")


def test_workspaces_index_as_researcher(live_server, page, researcher_user):
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text("Workspaces for test_researcher")
    expect(page.locator("body")).to_contain_text("test-dir1")
    expect(page.locator("body")).not_to_contain_text("test-dir2")


@pytest.mark.parametrize(
    "archived,ongoing,login_as,request_status,is_visible,is_enabled,tooltip",
    [
        # archived workspace, button not shown
        (True, False, "researcher", None, False, False, ""),
        # inactive project, button not shown
        (True, True, "researcher", None, False, False, ""),
        # active project, checker with no role, shown disabled
        (
            False,
            True,
            "checker",
            None,
            True,
            False,
            "You do not have permission to add files to a request.",
        ),
        # active project, user with role, shown enabled
        (False, True, "researcher", None, True, True, ""),
        # active project, current request editable, shown enabled
        (False, True, "researcher", RequestStatus.PENDING, True, True, ""),
        # active project, current request under review, shown disabled
        (
            False,
            True,
            "researcher",
            RequestStatus.SUBMITTED,
            True,
            False,
            "The current request is under review and cannot be modified.",
        ),
    ],
)
def test_content_buttons(
    live_server,
    page,
    context,
    archived,
    ongoing,
    login_as,
    request_status,
    is_visible,
    is_enabled,
    tooltip,
):
    user_data = {
        "researcher": dict(
            username="researcher",
            workspaces={
                "workspace": {
                    "project_details": {"name": "Project 1", "ongoing": ongoing},
                    "archived": archived,
                }
            },
            output_checker=False,
        ),
        "checker": dict(username="checker", workspaces={}, output_checker=True),
    }
    user = login_as_user(live_server, context, user_data[login_as])
    workspace = factories.create_workspace("workspace", user)
    factories.write_workspace_file("workspace", path="subdir/file.txt")

    if request_status:
        factories.create_request_at_status(
            "workspace",
            author=user,
            files=[factories.request_file(path="subdir/another_file.txt")],
            status=request_status,
        )

    page.goto(live_server.url + workspace.get_url(UrlPath("subdir")))

    # On the directory page, the update button is only visible if the
    # buttons are also enabled
    # If we can't act on the multiselect, we just show the disabled
    # add files button
    update_is_visible = is_enabled
    assert_button_status(
        page,
        add_is_visible=is_visible,
        add_is_enabled=is_enabled,
        update_is_visible=update_is_visible,
        update_is_enabled=is_enabled,
        tooltip=tooltip,
    )

    page.goto(live_server.url + workspace.get_url(UrlPath("subdir/file.txt")))
    assert_button_status(
        page,
        add_is_visible=is_visible,
        add_is_enabled=is_enabled,
        update_is_visible=False,
        update_is_enabled=False,
        tooltip=tooltip,
    )


@pytest.mark.parametrize(
    "request_status,released,updated,filetype,file_ext," "is_enabled,tooltip",
    [
        # no current request, button enabled
        (None, False, False, None, "txt", True, ""),
        # no current request, previously released file, button disabled
        (None, True, False, None, "txt", False, "This file has already been released"),
        # no current request, invalid file type, button disabled
        (
            None,
            False,
            False,
            None,
            "foo",
            False,
            "This file type cannot be added to a request",
        ),
        # current request, file not currently added, button enabled
        (RequestStatus.PENDING, False, False, None, "txt", True, ""),
        # current request, file not currently added, invalid file type, button disabled
        (
            RequestStatus.PENDING,
            False,
            False,
            None,
            "foo",
            False,
            "This file type cannot be added to a request",
        ),
        # current request, file not currently added, previously released, button disabled
        (
            RequestStatus.PENDING,
            True,
            False,
            None,
            "txt",
            False,
            "This file has already been released",
        ),
        # current request, file already added, button disabled
        (
            RequestStatus.PENDING,
            False,
            False,
            RequestFileType.OUTPUT,
            "txt",
            False,
            "This file has already been added to the current request",
        ),
        # current request, suporting file already added, button disabled
        (
            RequestStatus.PENDING,
            False,
            False,
            RequestFileType.SUPPORTING,
            "txt",
            False,
            "This file has already been added to the current request",
        ),
        # current request, file previously added and withdrawn, button enabled
        (
            RequestStatus.RETURNED,
            False,
            False,
            RequestFileType.WITHDRAWN,
            "txt",
            True,
            "",
        ),
        # current request, file already added and since updated, update button enabled
        (RequestStatus.PENDING, False, True, RequestFileType.OUTPUT, "txt", True, ""),
        # current request under-review, file already added and since updated, update button disabled
        (
            RequestStatus.SUBMITTED,
            False,
            True,
            RequestFileType.OUTPUT,
            "txt",
            False,
            "The current request is under review and cannot be modified.",
        ),
    ],
)
def test_file_content_buttons(
    live_server,
    page,
    context,
    bll,
    request_status,
    released,
    updated,
    filetype,
    file_ext,
    is_enabled,
    tooltip,
):
    user_data = dict(
        username="author",
        workspaces={
            "workspace": {
                "project_details": {"name": "Project 1", "ongoing": True},
                "archived": False,
            }
        },
        output_checker=False,
    )
    user = login_as_user(live_server, context, user_data)
    workspace = factories.create_workspace("workspace", user)
    filepath = f"subdir/file.{file_ext}"
    factories.write_workspace_file("workspace", path=filepath, contents="test")

    if released:
        # create a previous release for this file
        factories.create_request_at_status(
            "workspace",
            files=[
                factories.request_file(path=filepath, contents="test", approved=True)
            ],
            status=RequestStatus.RELEASED,
        )

    if request_status:
        # create a current request
        # default file so that the request can be created as submitted, if necessary
        files = [factories.request_file(approved=True)]
        test_filetype = (
            filetype
            if filetype != RequestFileType.WITHDRAWN
            else RequestFileType.OUTPUT
        )
        if test_filetype:
            files.append(
                factories.request_file(
                    path=filepath,
                    contents="test",
                    filetype=test_filetype,
                    approved=True,
                )
            )
        release_request = factories.create_request_at_status(
            "workspace",
            author=user,
            files=files,
            status=request_status,
        )
        if filetype == RequestFileType.WITHDRAWN:
            bll.withdraw_file_from_request(
                release_request, UrlPath(f"group/{filepath}"), user
            )

    if updated:
        # update the workspace file content
        factories.write_workspace_file("workspace", path=filepath, contents="changed")

    page.goto(live_server.url + workspace.get_url(UrlPath(filepath)))

    add_is_visible = not updated
    update_is_visible = updated
    add_is_enabled = add_is_visible and is_enabled
    update_is_enabled = update_is_visible and is_enabled

    assert_button_status(
        page,
        add_is_visible,
        add_is_enabled,
        update_is_visible,
        update_is_enabled,
        tooltip=tooltip,
    )


def assert_button_status(
    page, add_is_visible, add_is_enabled, update_is_visible, update_is_enabled, tooltip
):
    add_file_modal_button = page.locator("#add-file-modal-button")
    update_file_modal_button = page.locator("#update-file-modal-button")

    if add_is_visible:
        expect(add_file_modal_button).to_be_visible()
        if add_is_enabled:
            expect(add_file_modal_button).to_be_enabled()
        if tooltip:
            add_file_modal_button.hover()
            expect(add_file_modal_button).to_contain_text(tooltip)
    else:
        expect(add_file_modal_button).not_to_be_visible()

    if update_is_visible:
        expect(update_file_modal_button).to_be_visible()
        if update_is_enabled:
            expect(update_file_modal_button).to_be_enabled()
        else:
            expect(update_file_modal_button).to_be_disabled()
        if tooltip:  # pragma: no cover
            update_file_modal_button.hover()
            expect(update_file_modal_button).to_contain_text(tooltip)
    else:
        expect(update_file_modal_button).not_to_be_visible()


def test_csv_filtering(live_server, page, context, bll):
    workspace = factories.create_workspace("my-workspace")

    factories.write_workspace_file(
        workspace,
        "outputs/file1.csv",
        "Age Band,Mean\n0-20,10\n21-40,20\n41-60,30\n60+,40",
    )
    login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": {
                "my-workspace": {
                    "project_details": {"name": "Project 2", "ongoing": True},
                    "archived": False,
                },
            },
        },
    )

    page.goto(
        live_server.url + workspace.get_contents_url(UrlPath("outputs/file1.csv"))
    )

    # Table displays all data
    age_bands = {"0-20", "21-40", "41-60", "60+"}
    for age_band in age_bands:
        expect(page.locator("body")).to_contain_text(age_band)

    # Filter the mean column to value 20 (in age band 21-40)
    column_filter = page.get_by_placeholder("Filter mean")
    column_filter.fill("20")
    expect(page.locator("body")).to_contain_text("21-40")

    for age_band in age_bands - {"21-40"}:
        expect(page.locator("body")).not_to_contain_text(age_band)

    # Filter the mean column to a value that doesn't match anything
    column_filter.fill("foo")
    for age_band in age_bands:
        expect(page.locator("body")).not_to_contain_text(age_band)
    expect(page.locator("body")).to_contain_text("No results match")

    # Reset the filter by removing text
    column_filter.fill("")
    for age_band in age_bands:
        expect(page.locator("body")).to_contain_text(age_band)
