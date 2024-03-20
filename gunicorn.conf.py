import services.tracing as tracing


# workers
workers = 4

# listen
port = 8000
bind = "0.0.0.0"


# Because of Gunicorn's pre-fork web server model, we need to initialise opentelemetry
# in gunicorn's post_fork method in order to instrument our application process, see:
# https://opentelemetry-python.readthedocs.io/en/latest/examples/fork-process-model/README.html
def post_fork(server, worker):
    server.log.info("Worker spawned (pid: %s)", worker.pid)
    tracing.setup_default_tracing()
