#!/bin/bash
set -euo pipefail

workspace="$1"

docker compose exec -T job-server ./manage.py shell << EOF
from jobserver.models import *
for release in Release.objects.filter(workspace__name="$workspace"):
    release.files.all().delete()
    release.delete()
EOF
