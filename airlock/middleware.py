import logging
from typing import cast
from urllib.parse import urlencode

from django.contrib import auth
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.defaults import server_error
from opentelemetry import trace

from airlock.exceptions import RequestTimeout
from users.auth import Level4AuthenticationBackend


class UserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.login_url = reverse("login")

        self.backend = cast(
            Level4AuthenticationBackend,
            auth.get_backends()[0],
        )

    def __call__(self, request):
        """Handle custom user authentication.

        We refresh user data from job-server periodically, and also record some
        telemetry.
        """
        span = trace.get_current_span()

        if request.user.is_authenticated:
            span.set_attribute("username", request.user.username)
            span.set_attribute("user_id", request.user.user_id)
            if self.backend.needs_refresh(request.user):
                span.set_attribute("auth_refresh", True)
                user = self.backend.refresh(request)
                if user:  # refresh may have failed for some reason
                    request.user = user
        else:
            span.set_attribute("username", "anonymous")
            span.set_attribute("user_id", "anonymous")

        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Implement our custom global version of LoginRequiredMiddleware

        It is inverted from djangos - we require login nearly everywhere,
        except for /login and assets.
        """

        if getattr(view_func, "login_exempt", False):
            return

        if request.user.is_authenticated:
            return

        qs = urlencode({"next": request.get_full_path()}, safe="/")
        return redirect(self.login_url + f"?{qs}")


class TimeoutExceptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, RequestTimeout):
            return None

        trace.get_current_span().set_attribute("timeout", True)

        logging.getLogger("django.request").error(
            f"Timeout for {request.path}",
            exc_info=(type(exception), exception, exception.__traceback__),
        )

        default_response = server_error(request)
        return HttpResponse(
            default_response.content, status=504, content_type="text/html"
        )
