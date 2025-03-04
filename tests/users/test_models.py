from tests import factories
from users.models import User


def test_user_model_fullname_fallback():
    api_data = factories.create_api_user(username="username", fullname="fullname")
    assert User.from_api_data(api_data).fullname == "fullname"
    api_data["fullname"] = ""
    assert User.from_api_data(api_data).fullname == "username"
    api_data.pop("fullname")
    assert User.from_api_data(api_data).fullname == "username"
