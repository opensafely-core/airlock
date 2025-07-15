import os

import services.tracing as tracing
from airlock.exceptions import RequestTimeout


# workers
workers = 4

# listen
bind = "0.0.0.0:8000"


# Because of Gunicorn's pre-fork web server model, we need to initialise opentelemetry
# in gunicorn's post_fork method in order to instrument our application process, see:
# https://opentelemetry-python.readthedocs.io/en/latest/examples/fork-process-model/README.html
def post_fork(server, worker):
    # opentelemetry initialisation needs this, so ensure its set
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "airlock.settings")
    server.log.info("Worker spawned (pid: %s)", worker.pid)
    tracing.setup_default_tracing()


# this hook is called when the gunicorn worker receives a SIGABRT from the
# master, because it has timed out. By default, it just kills the worker.
# However, we raise an Exception, so that our django stack will handle it as an
# error and emit logs and telemetry.
def worker_abort(worker):
    raise RequestTimeout(
        f"gunicorn worker {worker.pid} timed out (timeout={worker.cfg.timeout})"
    )
