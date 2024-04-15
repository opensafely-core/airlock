"""
Django settings for airlock project.

Generated by 'django-admin startproject' using Django 5.0.1.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/5.0/ref/settings/
"""

import logging
import os
import warnings
from pathlib import Path

import django.dispatch
import yaml
from django.contrib import messages
from django.db.backends.signals import connection_created


_missing_env_var_hint = """\
If you are running commands locally outside of `just` then you should
make sure that your `.env` file is being loaded into the environment,
which you can do in Bash using:

    set -a; source .env; set +a

If you are seeing this error when running via `just` (which should
automatically load variables from `.env`) then you should check that
`.env` contains all the variables listed in `dotenv-sample` (which may
have been updated since `.env` was first created).

If you are seeing this error in production then you haven't configured
things properly.
"""


def get_env_var(name):
    try:
        return os.environ[name]
    except KeyError:
        raise RuntimeError(
            f"Missing environment variable: {name}\n\n{_missing_env_var_hint}"
        )


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Working directory for application state. Note that this is not necessarily relative to
# BASE_DIR: if AIRLOCK_WORK_DIR is an absolute path it can point anywhere.
WORK_DIR = BASE_DIR / get_env_var("AIRLOCK_WORK_DIR")


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = get_env_var("DJANGO_SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = get_env_var("DJANGO_DEBUG") == "True"

if DEBUG:  # pragma: no cover
    DJANGO_DEBUG_TOOLBAR = os.environ.get("DJANGO_DEBUG_TOOLBAR", "false") == "True"
else:  # pragma: no cover
    DJANGO_DEBUG_TOOLBAR = False

ALLOWED_HOSTS = get_env_var("DJANGO_ALLOWED_HOSTS").split(",")


# Application definition

INSTALLED_APPS = [
    # ensure whitenoise serves files when using runserver
    # https://whitenoise.readthedocs.io/en/latest/django.html#using-whitenoise-in-development
    "whitenoise.runserver_nostatic",
    # our local applications
    "airlock",
    "assets",
    "local_db",  # TODO: not include this application if we're not configured to use it?
    # "django.contrib.auth",
    # "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_extensions",
    # requirements for assets library
    "django.contrib.humanize",
    "django_vite",
    "slippers",
    "django_htmx",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    # "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "airlock.middleware.UserMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]


if DJANGO_DEBUG_TOOLBAR:  # pragma: no cover
    INTERNAL_IPS = ["127.0.0.1"]
    INSTALLED_APPS.append("debug_toolbar")
    INSTALLED_APPS.append("template_profiler_panel")
    MIDDLEWARE.append("debug_toolbar.middleware.DebugToolbarMiddleware")
    DEBUG_TOOLBAR_PANELS = [
        "debug_toolbar.panels.history.HistoryPanel",
        "debug_toolbar.panels.versions.VersionsPanel",
        "debug_toolbar.panels.timer.TimerPanel",
        "debug_toolbar.panels.settings.SettingsPanel",
        "debug_toolbar.panels.headers.HeadersPanel",
        "debug_toolbar.panels.request.RequestPanel",
        "debug_toolbar.panels.sql.SQLPanel",
        "debug_toolbar.panels.staticfiles.StaticFilesPanel",
        "debug_toolbar.panels.templates.TemplatesPanel",
        "debug_toolbar.panels.cache.CachePanel",
        "debug_toolbar.panels.signals.SignalsPanel",
        "debug_toolbar.panels.redirects.RedirectsPanel",
        "debug_toolbar.panels.profiling.ProfilingPanel",
        "template_profiler_panel.panels.template.TemplateProfilerPanel",
    ]


ROOT_URLCONF = "airlock.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                # "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "airlock.nav.menu",
            ],
            "builtins": [
                "slippers.templatetags.slippers",  # required for assets library
            ],
            "debug": DEBUG,  # required for template coverage
        },
    },
]

WSGI_APPLICATION = "airlock.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": WORK_DIR / "db.sqlite3",
        "CONNECTION_INIT_QUERIES": [
            "PRAGMA journal_mode=wal",
        ],
    }
}


@django.dispatch.receiver(connection_created)
def run_connection_init_queries(*, connection, **kwargs):
    queries = connection.settings_dict.get("CONNECTION_INIT_QUERIES", ())
    with connection.cursor() as cursor:
        for query in queries:
            cursor.execute(query)


# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = "en-gb"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# Get the location of the built docs from the mkdocs config
# The build docs dir is added to STATICFILE_DIRS so that we
# can serve it within airlock
with (BASE_DIR / "mkdocs.yml").open() as mkdocs_config:
    DOCS_SITE_DIR = yaml.load(mkdocs_config, Loader=yaml.Loader)["site_dir"]
DOCS_DIR = BASE_DIR / DOCS_SITE_DIR


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = "static/"

ASSETS_DIST = BASE_DIR / "assets/dist"

STATICFILES_DIRS = [ASSETS_DIST, DOCS_DIR]

# Sessions

# Changing from the default allows us to share localhost port in developement
SESSION_COOKIE_NAME = "airlock-sessionid"

# login is painful, so reduce the frequency that users need to do it after inactivity.
SESSION_COOKIE_AGE = 8 * 7 * 24 * 60 * 60  # 8 weeks

# time before we refresh users authorisation
AIRLOCK_AUTHZ_TIMEOUT = 15 * 60  # 15 minutes

# Serve files from static dirs directly. This removes the need to run collectstatic
# https://whitenoise.readthedocs.io/en/latest/django.html#WHITENOISE_USE_FINDERS
WHITENOISE_USE_FINDERS = True

DJANGO_VITE = {
    "default": {
        # vite assumes collectstatic, so tell it where the manifest is directly
        "manifest_path": ASSETS_DIST / ".vite/manifest.json",
    },
}

#
# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# In production we'd expect AIRLOCK_WORKSPACE_DIR to be an absolute path pointing
# somewhere outside of WORK_DIR
WORKSPACE_DIR = WORK_DIR / get_env_var("AIRLOCK_WORKSPACE_DIR")

REQUEST_DIR = WORK_DIR / get_env_var("AIRLOCK_REQUEST_DIR")

AIRLOCK_API_ENDPOINT = os.environ.get(
    "AIRLOCK_API_ENDPOINT", "https://jobs.opensafely.org/api/v2"
)
assert not AIRLOCK_API_ENDPOINT.endswith("/")

AIRLOCK_API_TOKEN = os.environ.get("AIRLOCK_API_TOKEN")

if AIRLOCK_API_TOKEN:  # pragma: no cover
    AIRLOCK_DEV_USERS_FILE = None
elif dev_user_file := os.environ.get("AIRLOCK_DEV_USERS_FILE"):  # pragma: nocover
    AIRLOCK_DEV_USERS_FILE = WORK_DIR / dev_user_file
else:  # pragma: no cover
    raise RuntimeError(
        f"One of AIRLOCK_API_TOKEN or AIRLOCK_DEV_USERS_FILE environment "
        f"variables must be set.\n\n{_missing_env_var_hint}"
    )

AIRLOCK_DATA_ACCESS_LAYER = "local_db.data_access.LocalDBDataAccessLayer"


# BACKEND is global env var on backends
BACKEND = os.environ.get("BACKEND", "test")

# Messages
# https://docs.djangoproject.com/en/3.0/ref/contrib/messages/
MESSAGE_TAGS = {
    messages.DEBUG: "alert-info",
    messages.INFO: "alert-info",
    messages.SUCCESS: "alert-success",
    messages.WARNING: "alert-warning",
    messages.ERROR: "alert-danger",
}


class MissingVariableErrorFilter(logging.Filter):
    """
    Convert "missing template variable" log messages into warnings, whose presence will
    trip our zero warnings enforcement in test runs.

    Heavily inspired by Adam Johnson's work here:
    https://adamj.eu/tech/2022/03/30/how-to-make-django-error-for-undefined-template-variables/
    """

    ignored_prefixes = (
        # Some internal Django templates rely on the silent missing variable behaviour
        "admin/",
        "auth/",
        "django/",
        # As does our internal component library
        "_components",
        "_partials",
    )

    def filter(self, record):  # pragma: no cover
        if record.msg.startswith("Exception while resolving variable "):
            template_name = record.args[1]
            if (
                not template_name.startswith(self.ignored_prefixes)
                # This shows up when rendering Django's internal error pages
                and template_name != "unknown"
            ):
                # Use `warn_explicit` to raise the warning at whatever location the
                # original log message was raised
                warnings.warn_explicit(
                    record.getMessage(),
                    UserWarning,
                    record.pathname,
                    record.lineno,
                )
        # Remove from log output
        return False


# NOTE: This is the default minimal logging config from Django's docs. It is not
# intended to be the final word in how logging should be configured in Airlock.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "filters": {
        "missing_variable_error": {
            "()": f"{MissingVariableErrorFilter.__module__}.{MissingVariableErrorFilter.__name__}",
        },
    },
    "loggers": {
        "django.template": {
            "level": "DEBUG",
            "filters": ["missing_variable_error"],
        },
    },
}
