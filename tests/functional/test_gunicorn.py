import contextlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest


@contextlib.contextmanager
def run_gunicorn(args, timeout, check_url="/", env=None):
    """Run a gunicorn server on a unix socket.

    Waits and checks for it to come up properly, and fails if it does not.
    Returns a tuple of the unix socket and the running process.

    The unix socket will be unique to every test, and we know it ahead of time.
    This avoids a whole bunch of complexity around clashing TCP ports, and
    using and communicating random TCP ports.
    """

    with tempfile.TemporaryDirectory() as tmpdir:
        socket = str(Path(tmpdir) / "gunicorn.sock")
        # use -m to use python import system to find gunicorn, rather than
        # requiring it be on the PATH, which gets messy with venv.
        cmd = [sys.executable, "-m", "gunicorn", "--bind", f"unix:{socket}"] + args

        # redirect stderr to stdout, so we can see the order in which things
        # happened when debugging.
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
        )

        # we use httpx as it supports unix sockets
        client = httpx.Client(transport=httpx.HTTPTransport(uds=socket))

        def kill():
            if process.poll() is None:  # pragma: no branch
                print(f"terminating gunicorn process {process.pid}")
                process.terminate()
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    print("force killing gunicorn process {process.pid}")
                    process.kill()
                    process.wait()

        # Wait for startup with multiple checks
        start_time = time.time()
        while time.time() - start_time < timeout:
            # check if process died
            if process.poll() is not None:
                stdout, _ = process.communicate()
                print(stdout)
                raise AssertionError("gunicorn failed to start correctly")

            # check it is up
            try:
                client.get("http://localhost/{check_url.lstrip('/')}")
                break
            except Exception:
                pass

            time.sleep(0.5)
        else:
            kill()
            stdout, _ = process.communicate()
            print(stdout)
            raise AssertionError(f"gunicorn failed to start within {timeout}s")

        yield (client, process)

        client.close()
        kill()


def test_run_gunicorn_failure():
    with pytest.raises(AssertionError) as exc:
        # we use preload to force an early error and avoid race conditions
        with run_gunicorn(["doesnotexist", "-w", "1", "--preload"], timeout=5) as (
            _,
            process,
        ):
            # should not get here, so if we do, print some debugging info
            stdout, stderr = process.communicate()  # pragma: nocover
            print(stdout)  # pragma: nocover
            print(stderr)  # pragma: nocover

    assert "gunicorn failed to start correctly" in str(exc)


def test_run_gunicorn_timeout():
    with pytest.raises(AssertionError) as exc:
        with run_gunicorn(
            ["airlock.wsgi:application"], check_url="/login", timeout=0
        ) as (_, process):
            # should not get here, so if we do, print some debugging info
            stdout, stderr = process.communicate()  # pragma: nocover
            print(stdout)  # pragma: nocover
            print(stderr)  # pragma: nocover

    assert "gunicorn failed to start within" in str(exc)


def create_test_wsgi_application():
    """Create a test WSGI app

    It adds an additional url/view to the existing airlock app, to enable
    testing timeouts.
    """
    # we defer all these import as they need doing in the gunicorn worker
    # process
    import django

    django.setup()

    from django.conf import settings
    from django.core.wsgi import get_wsgi_application
    from django.urls import path

    # Need login_exempt to not get redirected to login
    # Need to import this here as it requires django.setup()
    from airlock.views.helpers import login_exempt

    @login_exempt
    def slow_test_view(request):
        """View that intentionally times out"""
        time.sleep(5)
        raise Exception("view did not timeout")  # pragma: nocover

    # Add test URL pattern to the existing urlpatterns
    urlconf_module = __import__(settings.ROOT_URLCONF, fromlist=[""])
    test_pattern = path("test-timeout/", slow_test_view, name="test-timeout")
    urlconf_module.urlpatterns.append(test_pattern)

    # use default application
    return get_wsgi_application()


# module level so we can using as gunicorn wsgi app
application = create_test_wsgi_application()


def test_gunicorn_timeout():
    cmd = [
        "--config",
        "gunicorn.conf.py",
        "--timeout",
        "1",
        "--workers",
        "1",
        "--access-logfile",
        "-",
        "tests.functional.test_gunicorn:application",
    ]

    env = os.environ.copy()
    # this will export otel spans to stdout
    env["OTEL_EXPORTER_CONSOLE"] = "true"

    with run_gunicorn(cmd, timeout=5, env=env) as (client, process):
        response = client.get("http://localhost/test-timeout/")
        # leave some for gunicorn to clean up
        time.sleep(1)

    stdout, stderr = process.communicate()
    print(stdout)
    print(stderr)  # should be empty

    assert "GET /test-timeout/" in stdout
    assert "airlock.exceptions.RequestTimeout" in stdout
    assert response.status_code == 504
    # check otel json is emitted for timedout request
    assert '"name": "GET test-timeout/"' in stdout
