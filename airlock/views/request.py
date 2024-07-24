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

from airlock.business_logic import (
    ROOT_PATH,
    CommentVisibility,
    RequestFileType,
    RequestFileVote,
    RequestStatus,
    bll,
)
from airlock.file_browser_api import get_request_tree
from airlock.forms import GroupCommentDeleteForm, GroupCommentForm, GroupEditForm
from airlock.types import UrlPath
from services.tracing import instrument

from .helpers import (
    display_form_errors,
    download_file,
    get_path_item_from_tree_or_404,
    get_release_request_or_raise,
    serve_file,
)


tracer = trace.get_tracer_provider().get_tracer("airlock")


@instrument
def request_index(request):
    authored_requests = bll.get_requests_authored_by_user(request.user)

    outstanding_requests = []
    returned_requests = []
    approved_requests = []

    def get_reviewer_progress(release_request):
        progress = f"Your review: {release_request.files_reviewed_by_reviewer_count(request.user)}/{len(release_request.output_files())} files"
        if request.user.username not in release_request.completed_reviews:
            progress += " (incomplete)"
        return progress

    if request.user.output_checker:
        outstanding_requests = [
            (outstanding_request, get_reviewer_progress(outstanding_request))
            for outstanding_request in bll.get_outstanding_requests_for_review(
                request.user
            )
        ]
        returned_requests = bll.get_returned_requests(request.user)
        approved_requests = bll.get_approved_requests(request.user)

    return TemplateResponse(
        request,
        "requests.html",
        {
            "authored_requests": authored_requests,
            "outstanding_requests": outstanding_requests,
            "returned_requests": returned_requests,
            "approved_requests": approved_requests,
        },
    )


# we return different content if it is a HTMX request.
@vary_on_headers("HX-Request")
@require_http_methods(["GET"])
@instrument(func_attributes={"release_request": "request_id"})
def request_view(request, request_id: str, path: str = ""):
    release_request = get_release_request_or_raise(request.user, request_id)

    relpath = UrlPath(path)
    template = "file_browser/index.html"
    selected_only = False

    if request.htmx:
        template = "file_browser/contents.html"
        selected_only = True

    tree = get_request_tree(release_request, request.user, relpath, selected_only)
    path_item = get_path_item_from_tree_or_404(tree, relpath)

    is_directory_url = path.endswith("/") or relpath == ROOT_PATH

    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    is_author = release_request.author == request.user.username

    file_withdraw_url = None
    code_url = None

    if release_request.is_editing() and not is_directory_url:
        # A file can only be withdrawn from a request that is currently
        # editable by the author
        file_withdraw_url = reverse(
            "file_withdraw",
            kwargs={"request_id": request_id, "path": path},
        )

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

    group_edit_form = None
    group_edit_url = None
    comments = []
    group_comment_form = None
    group_comment_create_url = None
    group_comment_delete_url = None

    can_edit_group = (
        # no-one can edit context/controls for final requests
        not release_request.is_final()
        # only authors can edit context/controls
        and is_author
        # and only if the request is in draft i.e. in an author-owned turn
        and release_request.is_editing()
    )

    can_comment = (
        # no-one can comment on final requests
        not release_request.is_final()
        # user who can review can comment if the request is under review
        and (
            release_request.user_can_review(request.user)
            and release_request.is_under_review()
        )
        or
        # any user with access to the workspace can comment if the request is in draft
        (
            request.user.can_access_workspace(release_request.workspace)
            and release_request.is_editing()
        )
    )

    activity = []
    group_activity = []

    if relpath == ROOT_PATH:
        # viewing the root
        activity = bll.get_audit_log(request=release_request.id, exclude_readonly=True)

    # if we are viewing a group page, load the specific group data and forms
    elif len(relpath.parts) == 1:
        group = relpath.parts[0]
        filegroup = release_request.filegroups.get(group)

        # defense in depth: get_request_tree should prevent this branch, but
        # just in case it changes.
        if filegroup is None:  # pragma: no cover
            raise Http404()

        group_edit_form = GroupEditForm.from_filegroup(filegroup)
        group_edit_url = reverse(
            "group_edit",
            kwargs={"request_id": request_id, "group": group},
        )

        comments = release_request.get_visible_comments_for_group(group, request.user)
        visibilities = release_request.get_writable_comment_visibilities_for_user(
            request.user
        )
        group_comment_form = GroupCommentForm(visibilities=visibilities)

        group_comment_create_url = reverse(
            "group_comment_create",
            kwargs={"request_id": request_id, "group": group},
        )
        group_comment_delete_url = reverse(
            "group_comment_delete",
            kwargs={"request_id": request_id, "group": group},
        )

        group_activity = bll.get_audit_log(
            request=release_request.id, group=group, exclude_readonly=True
        )

    if not is_author:
        user_has_submitted_review = (
            request.user.username in release_request.completed_reviews
        )
        user_has_reviewed_all_files = (
            release_request.output_files()
            and release_request.all_files_reviewed_by_reviewer(request.user)
        )
    else:
        user_has_submitted_review = False
        user_has_reviewed_all_files = False

    request_submit_url = reverse(
        "request_submit",
        kwargs={"request_id": request_id},
    )
    request_review_url = reverse(
        "request_review",
        kwargs={"request_id": request_id},
    )
    request_withdraw_url = reverse(
        "request_withdraw",
        kwargs={"request_id": request_id},
    )
    request_reject_url = reverse(
        "request_reject",
        kwargs={"request_id": request_id},
    )
    request_return_url = reverse(
        "request_return",
        kwargs={"request_id": request_id},
    )
    release_files_url = reverse(
        "request_release_files",
        kwargs={"request_id": request_id},
    )

    if (
        is_directory_url
        or not release_request.is_under_review()
        or release_request.request_filetype(path)
        in [RequestFileType.SUPPORTING, RequestFileType.WITHDRAWN]
    ):
        file_approve_url = None
        file_reject_url = None
        file_reset_review_url = None
    else:
        file_approve_url = reverse(
            "file_approve",
            kwargs={"request_id": request_id, "path": path},
        )
        file_reject_url = reverse(
            "file_reject",
            kwargs={"request_id": request_id, "path": path},
        )
        file_reset_review_url = reverse(
            "file_reset_review",
            kwargs={"request_id": request_id, "path": path},
        )

        request_file = release_request.get_request_file_from_urlpath(relpath)
        existing_review = request_file.reviews.get(request.user.username)

        if existing_review and existing_review.status != RequestFileVote.UNDECIDED:
            if existing_review.status == RequestFileVote.APPROVED:
                file_approve_url = None
            elif existing_review.status == RequestFileVote.REJECTED:
                file_reject_url = None
            else:
                assert False, "Invalid RequestFileVote value"
        else:
            file_reset_review_url = None

    if (
        release_request.is_under_review()
        and user_has_reviewed_all_files
        and not user_has_submitted_review
    ):
        request_action_required = (
            "You have reviewed all files. You can now complete your review."
        )
    elif release_request.status.name == "REVIEWED" and release_request.user_can_review(
        request.user
    ):
        if release_request.can_be_released():
            request_action_required = "Two independent reviews have been completed. You can now return, reject or release this request."
        else:
            request_action_required = "Two independent reviews have been completed. You can now return or reject this request."
    else:
        request_action_required = None

    context = {
        "workspace": bll.get_workspace(release_request.workspace, request.user),
        "release_request": release_request,
        "root": tree,
        "path_item": path_item,
        "context": "request",
        "title": f"Request for {release_request.workspace} by {release_request.author}",
        # TODO file these in from user/models
        "is_author": is_author,
        "is_output_checker": request.user.output_checker,
        "file_approve_url": file_approve_url,
        "file_reject_url": file_reject_url,
        "file_reset_review_url": file_reset_review_url,
        "file_withdraw_url": file_withdraw_url,
        "request_submit_url": request_submit_url,
        "request_review_url": request_review_url,
        "request_reject_url": request_reject_url,
        "request_return_url": request_return_url,
        "request_withdraw_url": request_withdraw_url,
        "release_files_url": release_files_url,
        "user_has_submitted_review": user_has_submitted_review,
        "user_has_reviewed_all_files": user_has_reviewed_all_files,
        "activity": activity,
        "group_edit_form": group_edit_form,
        "group_edit_url": group_edit_url,
        "group_comments": comments,
        "group_comment_form": group_comment_form,
        "group_comment_create_url": group_comment_create_url,
        "group_readonly": not can_edit_group,
        "can_comment": can_comment,
        "group_activity": group_activity,
        "show_c3": settings.SHOW_C3,
        "request_action_required": request_action_required,
        # TODO, but for now stops template variable errors
        "multiselect_url": "",
        "code_url": code_url,
        "return_url": "",
        "group_comment_delete_url": group_comment_delete_url,
    }

    return TemplateResponse(request, template, context)


@instrument(func_attributes={"release_request": "request_id"})
@xframe_options_sameorigin
@require_http_methods(["GET"])
def request_contents(request, request_id: str, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        abspath = release_request.abspath(path)
    except bll.FileNotFound:
        raise Http404()

    download = "download" in request.GET
    # Downloads are only allowed for output checkers
    # Downloads are not allowed for request authors (including those that are also
    # output checkers)
    if download:
        if not request.user.output_checker or (
            release_request.author == request.user.username
        ):
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
    except bll.IncompleteContextOrControls as exc:
        messages.error(request, str(exc))
        return redirect(release_request.get_url())
    except bll.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    messages.success(request, "Request has been submitted")
    return redirect(release_request.get_url())


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_review(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        bll.review_request(release_request, request.user)
    except bll.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))
    except bll.RequestReviewDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Your review has been completed")

    return redirect(release_request.get_url())


def _action_request(request, request_id, new_status):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        bll.set_status(release_request, new_status, request.user)
    except bll.RequestPermissionDenied as exc:
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
    except bll.RequestPermissionDenied as exc:
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
    except bll.FileNotFound:
        raise Http404()

    try:
        bll.withdraw_file_from_request(release_request, grouppath, request.user)
    except bll.RequestPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    try:
        release_request.get_request_file_from_urlpath(grouppath)
    except bll.FileNotFound:
        # its been removed - redirect to group that contained
        redirect_url = release_request.get_url(grouppath.parts[0])
    else:
        # its been set to withdrawn - redirect to it directly
        redirect_url = release_request.get_url(grouppath)

    messages.error(request, f"The file {grouppath} has been withdrawn from the request")
    return redirect(redirect_url)


@instrument
def requests_for_workspace(request, workspace_name: str):
    requests_for_workspace = bll.get_requests_for_workspace(
        workspace_name, request.user
    )

    return TemplateResponse(
        request,
        "requests_for_workspace.html",
        {
            "workspace": workspace_name,
            "requests_for_workspace": requests_for_workspace,
        },
    )


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_approve(request, request_id, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        request_file = release_request.get_request_file_from_urlpath(path)
    except bll.FileNotFound:
        raise Http404()

    try:
        bll.approve_file(release_request, request_file, request.user)
    except bll.ApprovalPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    return redirect(release_request.get_url(path))


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_reject(request, request_id, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        request_file = release_request.get_request_file_from_urlpath(path)
    except bll.FileNotFound:
        raise Http404()

    try:
        bll.reject_file(release_request, request_file, request.user)
    except bll.ApprovalPermissionDenied as exc:
        raise PermissionDenied(str(exc))

    return redirect(release_request.get_url(path))


@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def file_reset_review(request, request_id, path: str):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        relpath = release_request.get_request_file_from_urlpath(path).relpath
    except bll.FileNotFound:
        raise Http404()

    try:
        bll.reset_review_file(release_request, relpath, request.user)
    except bll.ApprovalPermissionDenied as exc:
        raise PermissionDenied(str(exc))
    except bll.FileReviewNotFound:
        raise Http404()

    messages.success(request, "File review has been reset")
    return redirect(release_request.get_url(path))


@vary_on_headers("HX-Request")
@instrument(func_attributes={"release_request": "request_id"})
@require_http_methods(["POST"])
def request_release_files(request, request_id):
    release_request = get_release_request_or_raise(request.user, request_id)

    try:
        # For now, we just implicitly approve when release files is requested
        # If the status is already approved, don't approve again. An error from
        # job-server when releasing files can result in a request being stuck in
        # approved status
        if release_request.status != RequestStatus.APPROVED:
            bll.set_status(release_request, RequestStatus.APPROVED, request.user)
        bll.release_files(release_request, request.user)
    except bll.RequestPermissionDenied as exc:
        messages.error(request, f"Error releasing files: {str(exc)}")
    except bll.InvalidStateTransition as exc:
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
        messages.success(request, "Files have been released to jobs.opensafely.org")

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
        except bll.RequestPermissionDenied as exc:  # pragma: nocover
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
                visibility=CommentVisibility[form.cleaned_data["visibility"]],
                user=request.user,
            )
        except bll.RequestPermissionDenied as exc:  # pragma: nocover
            # currently, we can't hit this because of get_release_request_or_raise above.
            # However, that may change, so handle it anyway.
            raise PermissionDenied(str(exc))
        except bll.FileNotFound:
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
        except bll.RequestPermissionDenied as exc:  # pragma: nocover
            # currently, we can't hit this because of get_release_request_or_raise above.
            # However, that may change, so handle it anyway.
            raise PermissionDenied(str(exc))
        except bll.FileNotFound:
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
