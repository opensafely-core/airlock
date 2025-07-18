import os

import services.tracing as tracing
from airlock.exceptions import RequestTimeout


# workers
workers = 4

# listen
bind = "0.0.0.0:8000"


# track if this worker is currently handling a request
_http_request = False


# Because of Gunicorn's pre-fork web server model, we need to initialise opentelemetry
# in gunicorn's post_fork method in order to instrument our application process, see:
# https://opentelemetry-python.readthedocs.io/en/latest/examples/fork-process-model/README.html
def post_fork(server, worker):
    # opentelemetry initialisation needs this, so ensure its set
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "airlock.settings")
    server.log.info("Worker spawned (pid: %s)", worker.pid)
    tracing.setup_default_tracing()


# track this worker is currently handling an actual request
def pre_request(worker, req):
    global _http_request
    _http_request = True


# track this worker is no longer handling a request
def post_request(worker, req, environ, resp):
    global _http_request
    _http_request = False


# This hook is called when the gunicorn worker receives a SIGABRT from the
# master, because it has timed out. By default, it just kills the worker, we do
# something different.
#
# Firstly, we check if we are currently handing an acutal HTTP request. If not,
# it means we just got a connection with no data, so we exit w/o error. This
# happens with airlock specifically as we have no proxy infront of it.
#
# If not, we raise RequestTimeout, so that our django stack will handle it as
# an error and emit logs and telemetry.
def worker_abort(worker):
    if not _http_request:
        worker.log.info(f"No request sent timeout, exiting (pid: {worker.pid})")
        # do not raise SystemExit, just quit
        os._exit(0)

    raise RequestTimeout(
        f"gunicorn worker {worker.pid} timed out (timeout={worker.cfg.timeout})"
    )
