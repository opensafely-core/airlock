#!/bin/bash

set -euo pipefail

export PYTHONUNBUFFERED=TRUE  # make sure the log output lines don't clobber each other.

./manage.py check --deploy
./manage.py migrate

./manage.py run_file_uploader & exec "$@"
