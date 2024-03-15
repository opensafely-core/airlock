from django.conf import settings
from django.forms import Form
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from airlock import login_api
from airlock.forms import TokenLoginForm

from .helpers import login_exempt


@login_exempt
def login(request):
    default_next_url = reverse("workspace_index")

    if request.method != "POST":
        next_url = request.GET.get("next", default_next_url)
        if request.user is not None:
            return redirect(next_url)
        token_login_form = TokenLoginForm()
    else:
        next_url = request.POST.get("next", default_next_url)
        token_login_form = TokenLoginForm(request.POST)
        user_data = get_user_data_or_set_form_errors(token_login_form)
        # If `user_data` is None then the form object will have the relevant errors
        if user_data is not None:
            # TODO: the current code expects an `id` field but the API doesn't return
            # one; we should work out what we're doing here
            user_data["id"] = user_data["username"]
            request.session["user"] = user_data
            return redirect(next_url)

    return TemplateResponse(
        request,
        "login.html",
        {
            "next_url": next_url,
            "token_login_form": token_login_form,
            "dev_users_file": settings.AIRLOCK_DEV_USERS_FILE,
        },
    )


def get_user_data_or_set_form_errors(form: Form):
    if not form.is_valid():
        return
    try:
        return login_api.get_user_data(
            user=form.cleaned_data["user"],
            token=form.cleaned_data["token"],
        )
    except login_api.LoginError as exc:
        form.add_error("token", str(exc))


def logout(request):
    """
    User information is held in the session. On logout, remove
    session data and redirect to the home page.
    """
    request.session.flush()
    return redirect(reverse("home"))
