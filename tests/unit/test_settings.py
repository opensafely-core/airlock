import pytest
import yaml
from django.conf import settings

from airlock import settings as settings_funcs


# TODO: Stub test to get us started with
def test_secret_key():
    assert len(settings.SECRET_KEY) > 0


def test_get_env_var():
    with pytest.raises(
        RuntimeError, match="Missing environment variable: AINT_NO_SUCH_VAR"
    ):
        settings_funcs.get_env_var("AINT_NO_SUCH_VAR")


def test_docs_dir():
    with (settings.BASE_DIR / "mkdocs.yml").open() as mkdocs_config:
        DOCS_SITE_DIR = yaml.load(mkdocs_config, Loader=yaml.Loader)["site_dir"]
        assert DOCS_SITE_DIR == settings.DOCS_DIR.name
