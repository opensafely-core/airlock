import requests
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods
from django.views.decorators.vary import vary_on_headers
from opentelemetry import trace

from airlock import exceptions, permissions
from airlock.business_logic import bll
from airlock.enums import (
    PathType,
    RequestFileVote,
    RequestStatus,
    Visibility,
)
from airlock.file_browser_api import get_request_tree
from airlock.forms import (
    AddFileForm,
    FileFormSet,
    GroupCommentDeleteForm,
    GroupCommentForm,
    GroupEditForm,
    MultiselectForm,
)
from airlock.types import ROOT_PATH, UrlPath
from airlock.views.workspace import add_or_update_form_is_valid
from services.tracing import instrument

from .helpers import (
    ButtonContext,
    display_form_errors,
    display_multiple_messages,
    download_file,
    get_next_url_from_form,
    get_path_item_from_tree_or_404,
    get_release_request_or_raise,
    serve_file,
)


tracer = trace.get_tracer_provider().get_tracer("airlock")


@instrument
def requests_for_output_checker(request):
    outstanding_requests = []
    returned_requests = []
    approved_requests = []

    def get_reviewer_progress(release_request):
        progress = f"Your review: {release_request.files_reviewed_by_reviewer_count(request.user)}/{len(release_request.output_files())} files"
        if request.user.username not in release_request.submitted_reviews:
            progress += " (incomplete)"
        return progress

    outstanding_requests = [
        (outstanding_request, get_reviewer_progress(outstanding_request))
        for outstanding_request in bll.get_outstanding_requests_for_review(request.user)
    ]
    returned_requests = bll.get_returned_requests(request.user)
    approved_requests = bll.get_approved_requests(request.user)

    return TemplateResponse(
        request,
        "requests_for_output_checker.html",
        {
            "outstanding_requests": outstanding_requests,
            "returned_requests": returned_requests,
            "approved_requests": approved_requests,
        },
    )


@instrument
def requests_for_researcher(request):
    authored_requests = bll.get_requests_authored_by_user(request.user)

    return TemplateResponse(
        request,
        "requests_for_researcher.html",
        {
            "authored_requests": authored_requests,
        },
    )


def _get_request_button_context(user, release_request):
    # default context for request-level actions with everything hidden
    # author actions
    req_id = release_request.id
    submit_btn = ButtonContext.with_request_defaults(req_id, "request_submit")
    resubmit_btn = ButtonContext.with_request_defaults(req_id, "request_submit")
    withdraw_btn = ButtonContext.with_request_defaults(req_id, "request_withdraw")
    # output checker actions
    submit_review_btn = ButtonContext.with_request_defaults(req_id, "request_review")
    reject_btn = ButtonContext.with_request_defaults(req_id, "request_reject")
    return_btn = ButtonContext.with_request_defaults(req_id, "request_return")
    release_files_btn = ButtonContext.with_request_defaults(
        req_id, "request_release_files"
    )

    # Buttons shown to authors for requests in editable state
    if permissions.user_can_edit_request(user, release_request):
        withdraw_btn.show = True

        # we show the submit modal for initial submissions and
        # just a submit button for re-submission
        if release_request.status == RequestStatus.PENDING:
            submit_btn.show = True
        else:
            resubmit_btn.show = True

        try:
            permissions.check_user_can_submit_request(user, release_request)
        except exceptions.RequestPermissionDenied as err:
            for button in submit_btn, resubmit_btn:
                button.tooltip = str(err)
        else:
            for button in submit_btn, resubmit_btn:
                button.disabled = False

    # Buttons shown to output-checkers for requests in reviewable state
    elif permissions.user_can_currently_review_request(user, release_request):
        # All output-checker actions are visible (but not necessarily enabled)
        for button in [
            submit_review_btn,
            reject_btn,
            return_btn,
            release_files_btn,
        ]:
            button.show = True

        # We allow output checkers to return at any time
        return_btn.disabled = False

        try:
            permissions.check_user_can_submit_review(user, release_request)
        except exceptions.RequestReviewDenied as err:
            submit_review_btn.tooltip = str(err)
        else:
            submit_review_btn.disabled = False
            submit_review_btn.tooltip = "Submit Review"

        if release_request.status == RequestStatus.REVIEWED:
            reject_btn.disabled = False
            reject_btn.tooltip = "Reject request as unsuitable for release"
            return_btn.tooltip = "Return request for changes/clarification"
            return_btn.modal_confirm_message = "All reviews have been submitted. Are you ready to return the request to the original author?"
        else:
            reject_btn.tooltip = "Rejecting a request is disabled until review has been submitted by two reviewers"
            return_btn.tooltip = "Return request before full review"
            return_btn.modal_confirm_message = (
                "Are you sure you want to return this request before both reviews "
                "are submitted? Typically you would only do this if the request "
                "author has asked for an early return to make changes."
            )

        # A request can be released if it is in REVIEWED status and all files are
        # approved
        # If something goes wrong during the release process, not all files may be
        # released - but in this case, the request will NOT have move to APPROVED yet,
        # and the request is still in under-review state.
        # If a request is in APPROVED status, all its files have been released and
        # are in the process to uploading; uploads will be tried until they
        # succeed, no further interaction from the user is possible.
        if release_request.can_be_released():
            release_files_btn.show = True
            release_files_btn.disabled = False
            release_files_btn.tooltip = "Release files to jobs.opensafely.org"
        else:
            release_files_btn.tooltip = "Releasing to jobs.opensafely.org is disabled until all files have been approved by by two reviewers"

    return {
        "submit": submit_btn,
        "resubmit": resubmit_btn,
        "withdraw": withdraw_btn,
        "submit_review": submit_review_btn,
        "reject": reject_btn,
        "return": return_btn,
        "release_files": release_files_btn,
    }


def _get_dir_button_context(user, release_request):
    multiselect_withdraw_btn = ButtonContext.with_request_defaults(
        release_request.id, "request_multiselect"
    )
    if permissions.user_can_edit_request(user, release_request):
        multiselect_withdraw_btn.show = True
        multiselect_withdraw_btn.disabled = False
    return {"multiselect_withdraw": multiselect_withdraw_btn}


def _get_file_button_context(user, release_request, workspace, path_item):
    group_relpath = path_item.relpath
    relpath = UrlPath(*group_relpath.parts[1:])

    # author buttons
    req_id = release_request.id
    withdraw_btn = ButtonContext.with_request_defaults(
        req_id, "file_withdraw", path=group_relpath
    )
    # output-checker voting buttons
    user_vote = path_item.request_status.vote
    voting_buttons = {
        "approve": ButtonContext.with_request_defaults(
            req_id, "file_approve", path=group_relpath, label="Approve file"
        ),
        "request_changes": ButtonContext.with_request_defaults(
            req_id, "file_request_changes", path=group_relpath, label="Request changes"
        ),
    }

    reset_review_url = reverse(
        "file_reset_review",
        kwargs={"request_id": release_request.id, "path": group_relpath},
    )

    move_file_button = ButtonContext.with_request_defaults(
        req_id, "request_multiselect"
    )
    if permissions.user_can_withdraw_file_from_request(
        user, release_request, workspace, relpath
    ):
        withdraw_btn.show = True
        withdraw_btn.disabled = False
        withdraw_btn.tooltip = "Withdraw this file from this request"

    if permissions.user_can_change_request_file_group(
        user, release_request, workspace, relpath
    ):
        move_file_button.show = True
        move_file_button.disabled = False
        move_file_button.tooltip = "Move this file to different group"

    # Show the voting buttons for output files to any user who can review
    # for requests in currently reviewable status
    if (
        permissions.user_can_currently_review_request(user, release_request)
        and path_item.is_output()
    ):
        for button in voting_buttons.values():
            button.show = True
    # Determine whether any of the voting buttons should be enabled
    if permissions.user_can_review_file(user, release_request, relpath):
        can_reset_review = permissions.user_can_reset_file_review(
            user, release_request, relpath
        )
        # check what the current vote is
        #  - make that button selected
        #  - change its url to the reset-review url so it can be toggled off
        #  - if resetting votes is not allowed, disable the button
        #  - enable the OTHER option; when voting buttons are shown, you can always
        #    change your vote to the opposite, even if you can't reset
        match user_vote:
            case RequestFileVote.APPROVED:
                voting_buttons["approve"].label = user_vote.description()
                voting_buttons["approve"].selected = True
                voting_buttons["approve"].url = reset_review_url
                voting_buttons["approve"].disabled = not can_reset_review
                voting_buttons["request_changes"].disabled = False
            case RequestFileVote.CHANGES_REQUESTED:
                voting_buttons["request_changes"].label = user_vote.description()
                voting_buttons["request_changes"].selected = True
                voting_buttons["request_changes"].url = reset_review_url
                voting_buttons["request_changes"].disabled = not can_reset_review
                voting_buttons["approve"].disabled = False
            case RequestFileVote.UNDECIDED | None:
                voting_buttons["approve"].disabled = False
                voting_buttons["request_changes"].disabled = False
            case _:  # pragma: no cover
                assert False, "Invalid RequestFileVote value"

    return {
        "move_file_button": move_file_button,
        "withdraw_file": withdraw_btn,
        "voting": voting_buttons,
    }


def get_button_context(path_item, user, release_request, workspace):
    """
    Return a context dict defining the status of the buttons
    shown at the top of the content panel
    """
    match path_item.type:
        case PathType.REQUEST:
            return _get_request_button_context(user, release_request)

        case PathType.FILE:
            return _get_file_button_context(user, release_request, workspace, path_item)
        case PathType.DIR:
            return _get_dir_button_context(user, release_request)
        case _:
            return {}


# we return different content if it is a HTMX request.
@vary_on_headers("HX-Request")
@require_http_methods(["GET"])
@instrument(func_attributes={"release_request": "request_id"})
def request_view(request, request_id: str, path: str = ""):
    release_request = get_release_request_or_raise(request.user, request_id)

    relpath = UrlPath(path)
    template_dir = "file_browser/request/"
    template = template_dir + "index.html"
    selected_only = False

    if request.htmx:
        template = "file_browser/request/contents.html"
        selected_only = True

    tree = get_request_tree(release_request, request.user, relpath, selected_only)
    path_item = get_path_item_from_tree_or_404(tree, relpath)

    is_directory_url = path.endswith("/") or relpath == ROOT_PATH

    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    workspace = bll.get_workspace(release_request.workspace, request.user)
    # button context
    # get the information that the template needs in order to
    # generate the buttons shown at the top of the content panel
    # Buttons differ depending on the type of path_item
    # FILE, DIR, REQUEST, FILEGROUP
    button_context = get_button_context(
        path_item, request.user, release_request, workspace
    )

    is_author = release_request.author == request.user

    code_url = None

    if not is_directory_url:
        code_url = (
            reverse(
                "code_view",
                kwargs={
                    "workspace_name": release_request.workspace,
                    "commit": release_request.get_request_file_from_urlpath(
                        path
                    ).commit,
                    "path": "project.yaml",
                },
            )
            + f"?return_url={release_request.get_url(path)}"
        )

    activity = []

    if relpath == ROOT_PATH:
        # viewing the root
        activity = bll.get_request_audit_log(
            user=request.user,
            request=release_request,
            exclude_readonly=True,
        )
        group_context = None
    else:
        group_context = group_presenter(release_request, relpath, request)

    if permissions.user_can_submit_review(request.user, release_request):
        if relpath == ROOT_PATH:
            request_action_required = (
                "You have reviewed all files. Go to individual file groups to add "
                "comments, or submit your review now."
            )
        else:
            request_action_required = mark_safe(
                "You have reviewed all files. Go to individual file groups to add "
                f"comments, or go to the <a class='text-oxford-600' href='{release_request.get_url()}'>request overview</a> to submit your review."
            )
    elif (
        release_request.status.name == "REVIEWED"
        and permissions.user_can_review_request(request.user, release_request)
    ):
        if relpath == ROOT_PATH:
            request_action_required = (
                "Two independent reviews have been submitted. You can now "
            )
        else:
            request_action_required = (
                "Two independent reviews have been submitted. Go to the "
                f"<a class='text-oxford-600' href='{release_request.get_url()}'>request overview</a> to "
            )

        if release_request.can_be_released():
            request_action_required += "return, reject or release this request."
        else:
            request_action_required += "return or reject this request."
        request_action_required = mark_safe(request_action_required)

    else:
        request_action_required = None

    context = {
        "template_dir": template_dir,
        "workspace": workspace,
        "release_request": release_request,
        "root": tree,
        "path_item": path_item,
        "title": f"Request for {release_request.workspace} by {release_request.author}",
        "content_buttons": button_context,
        "activity": activity,
        "group": group_context,
        "request_action_required": request_action_required,
        "code_url": code_url,
        "include_code": code_url is not None,
        "include_download": not is_author,
        "upload_in_progress": release_request.upload_in_progress(),
        "is_request_root": release_request.get_url() == request.path,
    }

    return TemplateResponse(request, template, context)


def group_presenter(release_request, relpath, request):
    """Present to build group template context, which is needed in most request views."""

    assert relpath != ROOT_PATH

    group = relpath.parts[0]
    filegroup = release_request.filegroups.get(group)
    visibilities = release_request.get_writable_comment_visibilities_for_user(
        request.user
    )
    # are we on the group page?
    if len(relpath.parts) == 1:
        inline = False
    else:
        inline = True

    # are context and controls readonly?
    c2_readonly = inline or not permissions.user_can_edit_request(
        request.user, release_request
    )

    return {
        "name": group,
        "title": f"{group} group",
        "inline": inline,
        # context/controls editing
        "c2_readonly": c2_readonly,
        "c2_edit_form": GroupEditForm.from_filegroup(filegroup, inline=inline),
        "c2_edit_url": reverse(
            "group_edit",
            kwargs={"request_id": release_request.id, "group": group},
        ),
        # group comments
        "user_can_comment": permissions.user_can_comment_on_group(
            request.user, release_request
        ),
        "comments": release_request.get_visible_comments_for_group(group, request.user),
        "comment_form": GroupCommentForm(visibilities=visibilities),
        "comment_create_url": reverse(
            "group_comment_create",
            kwargs={"request_id": release_request.id, "group": group},
        ),
        "comment_delete_url": reverse(
            "group_comment_delete",
            kwargs={"request_id": release_request.id, "group": group},
        ),
        "comment_visibility_public_url": reverse(
            "group_comment_visibility_public",
            kwargs={"request_id": release_request.id, "group": group},
        ),
        # group activity
        "activity": bll.get_request_audit_log(
            user=request.user,
            request=release_request,
            group=group,
            exclude_readonly=True,
        ),
    }


@instrument(func_attributes={"release_request": "request_id"})
@xframe_options_sameorigin
@require_http_methods(["GET"])
def request_contents(request, request_id: str, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        abspath = release_request.abspath(path)
    except exceptions.FileNotFound:
        raise Http404()

    download = "download" in request.GET
    # Downloads are only allowed for output checkers
    # Downloads are not allowed for request authors (including those that are also
    # output checkers)
    if download:
        if not request.user.output_checker or (release_request.author == request.user):
            raise PermissionDenied()

        bll.audit_request_file_download(release_request, UrlPath(path), request.user)
        return download_file(abspath, filename=path)

    bll.audit_request_file_access(release_request, UrlPath(path), request.user)
    plaintext = request.GET.get("plaintext", False)
    renderer = release_request.get_renderer(UrlPath(path), plaintext=plaintext)
    return serve_file(request, renderer)


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_submit(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        bll.submit_request(release_request, request.user)
    except exceptions.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.success(request, "Request has been submitted")
    return redirect(release_request.get_url())


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_review(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        bll.review_request(release_request, request.user)
    except exceptions.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))
    except exceptions.RequestReviewDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Your review has been submitted")

    return redirect(release_request.get_url())


def _action_request(request, request_id, new_status):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        bll.set_status(release_request, new_status, request.user)
    except exceptions.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.error(request, f"Request has been {new_status.name.lower()}")
    return redirect(release_request.get_url())


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_reject(request, request_id):
    return _action_request(request, request_id, RequestStatus.REJECTED)


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_withdraw(request, request_id):
    return _action_request(request, request_id, RequestStatus.WITHDRAWN)


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_return(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)
    try:
        bll.return_request(release_request, request.user)
    except exceptions.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.success(request, "Request has been returned to author")
    return redirect(release_request.get_url())


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_withdraw(request, request_id, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)
    grouppath = UrlPath(path)

    try:
        release_request.get_request_file_from_urlpath(grouppath)
    except exceptions.FileNotFound:
        raise Http404()

    try:
        bll.withdraw_file_from_request(release_request, grouppath, request.user)
    except exceptions.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    try:
        release_request.get_request_file_from_urlpath(grouppath)
    except exceptions.FileNotFound:
        # its been removed - redirect to group that contained
        redirect_url = release_request.get_url(grouppath.parts[0])
    else:
        # its been set to withdrawn - redirect to it directly
        redirect_url = release_request.get_url(grouppath)

    messages.error(request, f"The file {grouppath} has been withdrawn from the request")
    return redirect(redirect_url)


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_multiselect(request, request_id: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    multiform = MultiselectForm(request.POST)

    if not multiform.is_valid():
        display_form_errors(request, multiform.errors)
        url = get_next_url_from_form(release_request, multiform)
        return HttpResponse(headers={"HX-Redirect": url})

    action = multiform.cleaned_data["action"]
    match action:
        case "withdraw_files":
            return multiselect_withdraw_files(request, multiform, release_request)
        case "update_files":
            return multiselect_update_files(request, multiform, release_request)

        case _:
            raise Http404(f"Invalid action {action}")


def multiselect_withdraw_files(request, multiform, release_request):
    errors = []
    successes = []
    for path_str in multiform.cleaned_data["selected"]:
        path = UrlPath(path_str)
        try:
            bll.withdraw_file_from_request(release_request, path, request.user)
            successes.append(f"The file {path} has been withdrawn from the request")
        except exceptions.RequestPermissionDenied as exc:
            errors.append(str(exc))

    display_multiple_messages(request, errors, "error")
    display_multiple_messages(request, successes, "success")

    url = get_next_url_from_form(release_request, multiform)
    return HttpResponse(headers={"HX-Redirect": url})


def multiselect_update_files(request, multiform, release_request):
    files_to_add = []
    files_ignored = {}
    # validate which files can be added
    for f in multiform.cleaned_data["selected"]:
        workspace = bll.get_workspace(release_request.workspace, request.user)
        relpath = release_request.get_request_file_from_urlpath(f).relpath
        if permissions.user_can_change_request_file_group(
            request.user, release_request, workspace, relpath
        ):
            files_to_add.append(f)
        else:
            files_ignored[f] = "file cannot be moved"

    move_file_form = AddFileForm(
        release_request=release_request,
        initial={"next_url": f"/requests/view/{release_request.id}/"},
    )

    filetype_formset = FileFormSet(
        initial=[{"file": f} for f in files_to_add],
    )
    return TemplateResponse(
        request,
        template="change_file_group.html",
        context={
            "form": move_file_form,
            "formset": filetype_formset,
            "files_ignored": files_ignored,
            "no_valid_files": len(files_to_add) == 0,
            # "update": True,
            "move_file_url": reverse(
                "file_move_group",
                kwargs={"request_id": release_request.id},
            ),
        },
    )


@instrument
def requests_for_workspace(request, workspace_name: str):
    requests_for_workspace = bll.get_requests_for_workspace(
        workspace_name, request.user
    )
    request_filter = request.GET.get("status")
    if request_filter:
        requests_for_workspace = [
            r for r in requests_for_workspace if r.status.name == request_filter
        ]
        request_filter = RequestStatus[request_filter].description()

    status_list = [(status.name, status.description()) for status in RequestStatus]

    filter_url = request.path

    return TemplateResponse(
        request,
        "requests_for_workspace.html",
        {
            "workspace": workspace_name,
            "requests_for_workspace": requests_for_workspace,
            "request_filter": request_filter,
            "status_list": status_list,
            "filter_url": filter_url,
        },
    )


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_approve(request, request_id, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        request_file = release_request.get_request_file_from_urlpath(path)
    except exceptions.FileNotFound:
        raise Http404()

    try:
        bll.approve_file(release_request, request_file, request.user)
    except exceptions.RequestReviewDenied as exc:
        raise PermissionDenied(str(exc))

    return redirect(release_request.get_url(path))


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_move_group(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)
    form = AddFileForm(request.POST, release_request=release_request)
    formset = FileFormSet(request.POST)
    errors = add_or_update_form_is_valid(request, form, formset)

    next_url = get_next_url_from_form(release_request, form)

    if errors:
        return redirect(next_url)

    group_name = (
        form.cleaned_data.get("new_filegroup")
        or form.cleaned_data.get("filegroup")
        or ""
    )
    error_msgs = []
    success_msgs = []
    for formset_form in formset:
        path = formset_form.cleaned_data["file"]
        relpath = release_request.get_request_file_from_urlpath(path).relpath
        try:
            bll.move_file_to_new_group_in_request(
                release_request, relpath, request.user, group_name
            )
            success_msgs.append(
                f"The file {relpath} has been moved to new group {group_name}"
            )
            next_url = release_request.get_url(group_name)
        except exceptions.RequestPermissionDenied as exc:
            error_msgs.append(str(exc))

    display_multiple_messages(request, error_msgs, "error")
    display_multiple_messages(request, success_msgs, "success")

    return redirect(next_url)


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_request_changes(request, request_id, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        request_file = release_request.get_request_file_from_urlpath(path)
    except exceptions.FileNotFound:
        raise Http404()

    try:
        bll.request_changes_to_file(release_request, request_file, request.user)
    except exceptions.RequestReviewDenied as exc:
        raise PermissionDenied(str(exc))

    return redirect(release_request.get_url(path))


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_reset_review(request, request_id, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        relpath = release_request.get_request_file_from_urlpath(path).relpath
    except exceptions.FileNotFound:
        raise Http404()

    try:
        bll.reset_review_file(release_request, relpath, request.user)
    except exceptions.RequestReviewDenied as exc:
        raise PermissionDenied(str(exc))
    except exceptions.FileReviewNotFound:
        raise Http404()

    messages.success(request, "File review has been reset")
    return redirect(release_request.get_url(path))


@vary_on_headers("HX-Request")
@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_release_files(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        bll.release_files(release_request, request.user)
    except exceptions.RequestPermissionDenied as exc:
        messages.error(request, f"Error releasing files: {str(exc)}")
    except exceptions.InvalidStateTransition as exc:
        messages.error(request, f"Error releasing files: {str(exc)}")
    except requests.HTTPError as err:
        if settings.DEBUG:
            response_type = err.response.headers["Content-Type"]
            if response_type == "application/json":
                message = err.response.json()
            else:
                message = err.response.content

            messages.error(
                request,
                mark_safe(
                    "Error releasing files<br/>"
                    f"Content:<pre>{message}</pre>"
                    f"Type: {err.response.headers['Content-Type']}"
                ),
            )
        else:
            if err.response.status_code == 403:
                messages.error(request, "Error releasing files: Permission denied")
            else:
                messages.error(
                    request, "Error releasing files; please contact tech-support."
                )
    else:
        messages.success(
            request,
            "Files have been released and will be uploaded to jobs.opensafely.org",
        )

    if request.htmx:
        return HttpResponse(headers={"HX-Redirect": release_request.get_url()})
    else:
        return redirect(release_request.get_url())


@instrument(func_attributes={"release_request": "request_id", "group": "group"})
@require_http_methods(["POST"])
def group_edit(request, request_id, group):
    release_request = get_release_request_or_raise(request.user, request_id)

    filegroup = release_request.filegroups.get(group)

    if filegroup is None:
        raise Http404(f"bad group {group}")

    form = GroupEditForm(
        request.POST,
        initial={
            "context": filegroup.context,
            "controls": filegroup.controls,
        },
    )

    if form.has_changed():
        form.is_valid()  # force validation - the form currently cannot fail to validate

        try:
            bll.group_edit(
                release_request,
                group=group,
                context=form.cleaned_data["context"],
                controls=form.cleaned_data["controls"],
                user=request.user,
            )
        except exceptions.RequestPermissionDenied as exc:  # pragma: nocover
            # currently, we can't hit this because of get_release_request_or_raise above.
            # However, that may change, so handle it anyway.
            raise PermissionDenied(str(exc))
        else:
            messages.success(request, f"Updated group {group}")
    else:
        messages.success(request, f"No changes made to group {group}")

    return redirect(release_request.get_url(group))


@instrument(func_attributes={"release_request": "request_id", "group": "group"})
@require_http_methods(["POST"])
def group_comment_create(request, request_id, group):
    release_request = get_release_request_or_raise(request.user, request_id)

    visibilities = release_request.get_writable_comment_visibilities_for_user(
        request.user
    )
    form = GroupCommentForm(visibilities, request.POST)

    if form.is_valid():
        try:
            bll.group_comment_create(
                release_request,
                group=group,
                comment=form.cleaned_data["comment"],
                visibility=Visibility[form.cleaned_data["visibility"]],
                user=request.user,
            )
        except exceptions.RequestPermissionDenied as exc:  # pragma: nocover
            # currently, we can't hit this because of get_release_request_or_raise above.
            # However, that may change, so handle it anyway.
            raise PermissionDenied(str(exc))
        except exceptions.FileNotFound:
            messages.error(request, f"Invalid group: {group}")
        else:
            messages.success(request, "Comment added")

    else:
        display_form_errors(request, form.errors)

    return redirect(release_request.get_url(group))


@instrument(func_attributes={"release_request": "request_id", "group": "group"})
@require_http_methods(["POST"])
def group_comment_delete(request, request_id, group):
    release_request = get_release_request_or_raise(request.user, request_id)

    form = GroupCommentDeleteForm(request.POST)

    if form.is_valid():
        comment_id = form.cleaned_data["comment_id"]
        try:
            bll.group_comment_delete(
                release_request,
                group=group,
                comment_id=comment_id,
                user=request.user,
            )
        except exceptions.RequestPermissionDenied as exc:  # pragma: nocover
            # currently, we can't hit this because of get_release_request_or_raise above.
            # However, that may change, so handle it anyway.
            raise PermissionDenied(str(exc))
        except exceptions.FileNotFound:
            raise Http404(
                request, f"Comment not found in group {group} with id {comment_id}"
            )
        else:
            messages.success(request, "Comment deleted")

    else:
        for field, error_list in form.errors.items():
            for error in error_list:
                messages.error(request, f"{field}: {error}")

    return redirect(release_request.get_url(group))


@instrument(func_attributes={"release_request": "request_id", "group": "group"})
@require_http_methods(["POST"])
def group_comment_visibility_public(request, request_id, group):
    release_request = get_release_request_or_raise(request.user, request_id)

    form = GroupCommentDeleteForm(request.POST)

    if form.is_valid():
        comment_id = form.cleaned_data["comment_id"]
        try:
            bll.group_comment_visibility_public(
                release_request,
                group=group,
                comment_id=comment_id,
                user=request.user,
            )
        except exceptions.RequestPermissionDenied as exc:  # pragma: nocover
            # currently, we can't hit this because of get_release_request_or_raise above.
            # However, that may change, so handle it anyway.
            raise PermissionDenied(str(exc))
        except exceptions.FileNotFound:
            raise Http404(
                request, f"Comment not found in group {group} with id {comment_id}"
            )
        else:
            messages.success(request, "Comment made public")

    else:
        for field, error_list in form.errors.items():
            for error in error_list:
                messages.error(request, f"{field}: {error}")

    return redirect(release_request.get_url(group))


def uploaded_files_count(request, request_id):
    """
    This view is called with htmx when a request is in the process of
    uploading files, so we can update the file count displayed on the request
    overview page.
    """
    release_request = get_release_request_or_raise(request.user, request_id)
    if release_request.upload_in_progress():
        return TemplateResponse(
            request,
            "file_browser/_includes/uploaded_files_count.html",
            {"release_request": release_request, "upload_in_progress": True},
        )
    # If the request is no longer uploading, it's been released and we just reload
    # the whole page as we'll need need to update other elements on the page
    # (e.g. the request status). We could do this with hx-swap-oob for the various
    # different elements, but it's simpler just to reload.
    return HttpResponse(headers={"HX-Redirect": release_request.get_url()})
