import time
from typing import cast
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import auth
from django.shortcuts import redirect
from django.urls import reverse
from opentelemetry import trace

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
            span.set_attribute("user", request.user.username)
            time_since_authz = time.time() - request.user.last_refresh
            if time_since_authz > settings.AIRLOCK_AUTHZ_TIMEOUT:
                span.set_attribute("auth_refresh", True)
                user = self.backend.refresh(request)
                if user:  # refresh may have failed for some reason
                    request.user = user
        else:
            span.set_attribute("user", "")

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
