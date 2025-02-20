import time
from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from opentelemetry import trace

from airlock import login_api
from airlock.users import User


class UserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.login_url = reverse("login")

    def __call__(self, request):
        """Add the session user to the request"""
        user = User.from_session(request.session)
        span = trace.get_current_span()

        if user:
            time_since_authz = time.time() - user.last_refresh
            if time_since_authz > settings.AIRLOCK_AUTHZ_TIMEOUT:
                span.set_attribute("auth_refresh", True)
                try:
                    details = login_api.get_user_authz(user)
                    details["last_refresh"] = time.time()
                except login_api.LoginError:
                    # TODO: log this, but we should have telemetry for the requests call anyway
                    pass
                else:
                    request.session["user"] = details
                    user = User.from_session(request.session)

        request.user = user
        span.set_attribute("user", user.username if user else "")
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if getattr(view_func, "login_exempt", False):
            return

        if request.user is not None:
            return

        qs = urlencode({"next": request.get_full_path()}, safe="/")
        return redirect(self.login_url + f"?{qs}")
