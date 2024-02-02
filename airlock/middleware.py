from urllib.parse import urlencode

from django.shortcuts import redirect, reverse

from airlock.users import User


class UserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.login_url = reverse("login")

    def __call__(self, request):
        """Add the session user to the request"""
        request.user = User.from_session(request.session)
        response = self.get_response(request)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if getattr(view_func, "login_exempt", False):
            return

        if request.user is not None:
            return

        qs = urlencode({"next": request.get_full_path()}, safe="/")
        return redirect(self.login_url + f"?{qs}")
