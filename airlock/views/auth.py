from django.conf import settings
from django.contrib import auth, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from airlock.forms import TokenLoginForm

from .helpers import login_exempt


@login_exempt
def login(request):
    default_next_url = reverse("workspace_index")

    if request.method != "POST":
        next_url = request.GET.get("next", default_next_url)
        if request.user.is_authenticated:
            return redirect(next_url)
        token_login_form = TokenLoginForm()
    else:
        next_url = request.POST.get("next", default_next_url)
        token_login_form = TokenLoginForm(request.POST)
        user = get_user_or_set_form_errors(request, token_login_form)
        # If `user` is None then the form object will have the relevant errors
        if user is not None:
            auth.login(request, user)
            messages.success(request, f"Logged in as {user.username}")
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


def get_user_or_set_form_errors(request, form):
    if not form.is_valid():
        return

    user = auth.authenticate(
        request,
        username=form.cleaned_data["user"],
        token=form.cleaned_data["token"],
    )

    if user:
        return user

    form.add_error("token", "Invalid user or token")


def logout(request):
    """
    User information is held in the session. On logout, remove
    session data and redirect to the home page.
    """
    auth.logout(request)
    return redirect(reverse("login"))
