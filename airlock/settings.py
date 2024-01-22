"""
Django settings for airlock project.

Generated by 'django-admin startproject' using Django 5.0.1.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/5.0/ref/settings/
"""

import os
from pathlib import Path


def get_env_var(name):
    try:
        return os.environ[name]
    except KeyError:
        raise RuntimeError(
            f"Missing environment variable: {name}\n"
            f"\n"
            f"If you are running commands locally outside of `just` then you should\n"
            f"make sure that your `.env` file is being loaded into the environment,\n"
            f"which you can do in Bash using:\n"
            f"\n"
            f"    set -a; source .env; set +a\n"
            f"\n"
            f"If you are seeing this error when running via `just` (which should\n"
            f"automatically load variables from `.env`) then you should check that\n"
            f"`.env` contains all the variables listed in `dotenv-sample` (which may\n"
            f"have been updated since `.env` was first created).\n"
            f"\n"
            f"If you are seeing this error in production then you haven't configured\n"
            f"things properly."
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

ALLOWED_HOSTS = get_env_var("DJANGO_ALLOWED_HOSTS").split(",")


# Application definition

INSTALLED_APPS = [
    # ensure whitenoise serves files when using runserver
    # https://whitenoise.readthedocs.io/en/latest/django.html#using-whitenoise-in-development
    "whitenoise.runserver_nostatic",
    "airlock",
    # "django.contrib.auth",
    # "django.contrib.contenttypes",
    # "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_extensions",
    # requirements for assets library
    "django.contrib.humanize",
    "django_vite",
    "slippers",
    "assets",
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
            ],
            "builtins": [
                "slippers.templatetags.slippers",  # required for assets library
            ],
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
    }
}


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


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = "static/"

ASSETS_DIST = Path(os.environ.get("ASSETS_DIST", BASE_DIR / "assets/dist"))
STATICFILES_DIRS = [str(ASSETS_DIST)]

# Serve files from static dirs directly. This removes the need to run collectstatic
# https://whitenoise.readthedocs.io/en/latest/django.html#WHITENOISE_USE_FINDERS
WHITENOISE_USE_FINDERS = True

DJANGO_VITE = {
    "default": {
        # vite assumes collectstatic, so tell it where the manifest is directly
        "manifest_path": "assets/dist/.vite/manifest.json",
    },
}

#
# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
