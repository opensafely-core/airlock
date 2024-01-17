from django.conf import settings


# TODO: Stub test to get us started with
def test_secret_key():
    assert len(settings.SECRET_KEY) > 0
