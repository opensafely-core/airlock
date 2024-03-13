#!/bin/bash

set -euo pipefail

./manage.py check --deploy
./manage.py migrate
./manage.py backpopulate_file_id

exec "$@"
