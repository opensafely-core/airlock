import pytest
from playwright.sync_api import expect

from airlock.enums import RequestFileType, RequestStatus
from airlock.types import UrlPath
from tests import factories

from .conftest import login_as_user, wait_for_htmx


@pytest.fixture(autouse=True)
def workspaces():
    factories.create_workspace("test-dir1")
    factories.create_workspace("test-dir2")


def test_workspaces_index_as_ouput_checker(live_server, page, output_checker_user):
    # this should only list their workspaces, even though they can access all workspaces
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text(
        "Workspaces & Requests for Test Output Checker"
    )
    expect(page.locator("body")).not_to_contain_text("test-dir1")
    expect(page.locator("body")).to_contain_text("test-dir2")


def test_workspaces_index_as_researcher(live_server, page, researcher_user):
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text(
        "Workspaces & Requests for Test Researcher"
    )
    expect(page.locator("body")).to_contain_text("test-dir1")
    expect(page.locator("body")).not_to_contain_text("test-dir2")


def test_copiloted_workspaces_index_no_workspaces(live_server, page, researcher_user):
    # Copiloted workspaces nav item is not visible
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).not_to_contain_text("Copiloted Workspaces")

    page.goto(live_server.url + "/copiloted-workspaces/")
    expect(page.locator("body")).to_contain_text("No copiloted workspaces available")
    expect(page.locator("body")).to_contain_text(
        "You do not have access to any copiloted workspaces"
    )


def test_copiloted_workspaces_index_as_copilot(live_server, page, copilot_user):
    # Copiloted workspaces nav item is visible
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text("Copiloted Workspaces")

    page.goto(live_server.url + "/copiloted-workspaces/")
    expect(page.locator("body")).to_contain_text("test-dir1")


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
            (
                "There is currently a request under review for this workspace and you "
                "cannot modify it or start a new one until it is reviewed."
            ),
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
                "workspace": factories.create_api_workspace(
                    project="Project 1", ongoing=ongoing, archived=archived
                )
            },
            output_checker=False,
        ),
        "checker": factories.create_api_user(
            username="checker", workspaces={}, output_checker=True
        ),
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
    "request_status,released,updated,filetype,file_ext,is_enabled,tooltip",
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
            (
                "There is currently a request under review for this workspace and you "
                "cannot modify it or start a new one until it is reviewed."
            ),
        ),
    ],
)
def test_file_content_buttons(
    mock_old_api,
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
    user_data = factories.create_api_user(
        username="author",
        workspaces={"workspace": factories.create_api_workspace(project="Project 1")},
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

    # Table displays all data
    age_bands = {"0-20", "21-40", "41-60", "60+"}
    for age_band in age_bands:
        expect(page.locator("body")).to_contain_text(age_band)

    # Filter to rows with a "30"
    table_filter = page.get_by_role("searchbox")
    table_filter.fill("30")
    expect(page.locator("body")).to_contain_text("41-60")

    for age_band in age_bands - {"41-60"}:
        expect(page.locator("body")).not_to_contain_text(age_band)

    # Filter the mean column to a value that doesn't match anything
    table_filter.fill("foo")
    for age_band in age_bands:
        expect(page.locator("body")).not_to_contain_text(age_band)
    expect(page.locator("body")).to_contain_text("No results match")

    # Reset the filter by removing text
    table_filter.fill("")
    for age_band in age_bands:
        expect(page.locator("body")).to_contain_text(age_band)


def test_bug_rendering_datatable_in_combination_with_back_button(
    live_server, page, context
):
    """
    A combination of navigation, page refreshes and the back button created
    a state where the datatable failed to load. This was a failing test until
    the bug was fixed and acts as a regression test in case it happens again.
    """
    workspace = factories.create_workspace("my-workspace")

    file_1 = "outputs/file1.csv"
    file_2 = "outputs/file2.csv"
    factories.write_workspace_file(workspace, file_1)
    factories.write_workspace_file(workspace, file_2)
    login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="author",
            workspaces={
                "my-workspace": factories.create_api_workspace(project="Project 1")
            },
        ),
    )

    # goto folder view
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs")))
    # view file via treeview click
    page.locator('.tree a[href*="file1.csv"]').click()
    # ensure the file has loaded
    expect(page.locator("#file1csv-title")).to_be_visible()
    # refresh the page
    page.reload()
    # go back to the folder view
    page.go_back()
    # should load the table
    expect(page.locator(".clusterized")).to_be_visible()

    # refresh the folder view by clicking on the folder in the tree view
    page.locator('.tree a[href$="outputs/"]').click()

    # The above click triggers an htmx request. This waits for that to
    # complete
    wait_for_htmx(page)
    # should load the table but previously didn't
    expect(page.locator(".clusterized")).to_be_visible()


def test_checkbox_caching(live_server, page, context):
    workspace = factories.create_workspace("my-workspace")

    file_1 = "outputs/file1.csv"
    file_2 = "outputs/file2.csv"
    factories.write_workspace_file(workspace, file_1)
    factories.write_workspace_file(workspace, file_2)
    login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": {
                "my-workspace": {
                    "project_details": {"name": "Project 1", "ongoing": True},
                    "archived": False,
                },
            },
        },
    )
    # goto page with files
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs")))

    file_1_selector = f"input[type=checkbox][value='{file_1}']"
    file_2_selector = f"input[type=checkbox][value='{file_2}']"

    # checkboxes start unchecked
    expect(page.locator(file_1_selector)).not_to_be_checked()
    expect(page.locator(file_2_selector)).not_to_be_checked()

    # check the first box
    page.check(file_1_selector)

    # go to a different page and then come back
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs/file1.csv")))
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs")))

    # the first checkbox state has persisted
    expect(page.locator(file_1_selector)).to_be_checked()
    expect(page.locator(file_2_selector)).not_to_be_checked()


def test_checkbox_caching_same_file_name_but_different_workspace(
    live_server, page, context
):
    workspace_1 = factories.create_workspace("my-workspace-1")
    workspace_2 = factories.create_workspace("my-workspace-2")

    file_1 = "outputs/file1.txt"
    factories.write_workspace_file(workspace_1, file_1)
    factories.write_workspace_file(workspace_2, file_1)
    login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": {
                "my-workspace-1": {
                    "project_details": {"name": "Project 1", "ongoing": True},
                    "archived": False,
                },
                "my-workspace-2": {
                    "project_details": {"name": "Project 1", "ongoing": True},
                    "archived": False,
                },
            },
        },
    )

    # goto page with files in workspace 1
    page.goto(live_server.url + workspace_1.get_url(UrlPath("outputs")))

    file_1_selector = f"input[type=checkbox][value='{file_1}']"

    # checkboxes start unchecked
    expect(page.locator(file_1_selector)).not_to_be_checked()

    # check the first box
    page.check(file_1_selector)

    # go to different workspace
    page.goto(live_server.url + workspace_2.get_url(UrlPath("outputs")))

    # file with same name will not be checked
    expect(page.locator(file_1_selector)).not_to_be_checked()

    # go back to original workspace
    page.goto(live_server.url + workspace_1.get_url(UrlPath("outputs")))

    # original file should still be checked
    expect(page.locator(file_1_selector)).to_be_checked()


def test_checkbox_caching_appears_after_back_button(live_server, page, context):
    """
    View a folder > check a box > view a file > click back > ensure checkbox is still checked
    """
    workspace = factories.create_workspace("my-workspace")

    file_1 = "outputs/file1.csv"
    file_2 = "outputs/file2.csv"
    factories.write_workspace_file(workspace, file_1)
    factories.write_workspace_file(workspace, file_2)
    login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": {
                "my-workspace": {
                    "project_details": {"name": "Project 1", "ongoing": True},
                    "archived": False,
                },
            },
        },
    )

    file_1_selector = f"input[type=checkbox][value='{file_1}']"
    file_2_selector = f"input[type=checkbox][value='{file_2}']"

    # goto folder view
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs")))
    # checkboxes start unchecked
    expect(page.locator(file_1_selector)).not_to_be_checked()
    expect(page.locator(file_2_selector)).not_to_be_checked()
    # Check a box
    page.check(file_1_selector)
    expect(page.locator(file_1_selector)).to_be_checked()
    # view file via treeview click
    page.locator('.tree a[href*="file1.csv"]').click()
    # ensure the file has loaded
    expect(page.locator("#file1csv-title")).to_be_visible()
    # go back to the folder view
    page.go_back()
    # checkbox should still be checked
    expect(page.locator(file_1_selector)).to_be_checked()


def test_checkbox_caching_works_following_back_button(live_server, page, context):
    """
    View a folder > view a file > click back > check a box > refresh > box state persists
    """
    workspace = factories.create_workspace("my-workspace")

    file_1 = "outputs/file1.csv"
    file_2 = "outputs/file2.csv"
    factories.write_workspace_file(workspace, file_1)
    factories.write_workspace_file(workspace, file_2)
    login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": {
                "my-workspace": {
                    "project_details": {"name": "Project 1", "ongoing": True},
                    "archived": False,
                },
            },
        },
    )

    file_1_selector = f"input[type=checkbox][value='{file_1}']"
    file_2_selector = f"input[type=checkbox][value='{file_2}']"

    # goto folder view
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs")))
    # view file via treeview click
    page.locator('.tree a[href*="file1.csv"]').click()
    # ensure the file has loaded
    expect(page.locator("#file1csv-title")).to_be_visible()
    # go back to the folder view
    page.go_back()
    # checkboxes start unchecked
    expect(page.locator(file_1_selector)).not_to_be_checked()
    expect(page.locator(file_2_selector)).not_to_be_checked()
    # Check a box
    page.check(file_1_selector)
    expect(page.locator(file_1_selector)).to_be_checked()
    # Refresh page
    page.reload()
    expect(page.locator(file_1_selector)).to_be_checked()
    expect(page.locator(file_2_selector)).not_to_be_checked()


def test_select_all(live_server, page, context):
    workspace = factories.create_workspace("my-workspace")

    file_1 = "outputs/file1.csv"
    file_2 = "outputs/file2.csv"
    factories.write_workspace_file(workspace, file_1)
    factories.write_workspace_file(workspace, file_2)
    login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": {
                "my-workspace": {
                    "project_details": {"name": "Project 1", "ongoing": True},
                    "archived": False,
                },
            },
        },
    )

    # goto page with files
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs")))

    # confirm selectall is unchecked
    expect(page.locator("input.selectall")).not_to_be_checked()

    # select 1 checkbox and expect selectall to remain unchecked
    page.check(f"input[type=checkbox][value='{file_1}']")
    expect(page.locator("input.selectall")).not_to_be_checked()

    # select the other checkbox, now expect select all to be checked
    page.check(f"input[type=checkbox][value='{file_2}']")
    expect(page.locator("input.selectall")).to_be_checked()

    # uncheck 1 checkbox, now select all should be unchecked again
    page.uncheck(f"input[type=checkbox][value='{file_1}']")
    expect(page.locator("input.selectall")).not_to_be_checked()
