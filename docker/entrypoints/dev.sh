#!/bin/bash

set -euo pipefail

./manage.py migrate
./manage.py backpopulate_file_id

exec "$@"
