import contextlib
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
