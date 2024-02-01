from airlock.users import User


def user_middleware(get_response):
    """Add the session user to the request"""

    def middleware(request):
        request.user = User.from_session(request.session)
        response = get_response(request)
        return response

    return middleware
