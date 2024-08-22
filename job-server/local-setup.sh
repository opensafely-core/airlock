#!/bin/bash
set -euo pipefail

ghusername="$1"
workspace="${2:-airlock-test-workspace}"
backend="airlock-test-backend"
host="http://localhost:9000"

# load ensure_values function
# shellcheck disable=SC1091
. lib.sh

test -f .env.jobserver|| cp .env.jobserver.template .env.jobserver

# this is *horrid*, but the service won't start if the config has ADMIN_USERS that don't exist
ensure_value ADMIN_USERS "" .env.jobserver
ensure_value JOBSERVER_GITHUB_TOKEN "" .env.jobserver

# ensure we have a running db and up to date job-server instance we can run stuff in it 
test -z "${JOB_SERVER_IMAGE:-}" && docker compose pull job-server
docker compose up -d --wait db
docker compose up -d --wait job-server

# if first time, give some time for the initial migration to complete
echo "Checking service up..."
if ! curl -I "$host" -s --compressed --fail --retry 20 --retry-delay 1 --retry-all-errors >/dev/null; then
    echo "Service did not come up, likely a race condition. Re-run."
    echo "If that doesn't fix it, look at the logs with:"
    echo " - just job-server/logs"
    echo " - just job-server/logs db"
    exit 1
fi

# shellcheck disable=SC1091
. .env.jobserver

# setup github social logins
# this only needs to be done very rarely, and bw client is a faff, so add a check to only if needed
if test "$SOCIAL_AUTH_GITHUB_KEY" = "test" -o -z "$SOCIAL_AUTH_GITHUB_KEY"; then
    tmp=$(mktemp)
    if ! command -v bw > /dev/null; then
        echo "bitwarden client bw not found"
        exit 1
    fi
    if bw status | grep -q unauthenticated; then
        echo "You are not logged in to bitwarden (org id is bennettinstitute):"
        echo
        echo "   bw login --sso"
        echo
        exit 1
    fi
    docker compose exec job-server cat ./scripts/dev-env.sh > "$tmp"
    bash "$tmp" .env.jobserver
    echo "Restarting job-server with new configuration"
    docker compose up -d job-server
else
    echo "Skipping job-server SOCIAL_AUTH setup as it is already done"
fi

# ensure user exists
docker compose exec job-server ./manage.py create_user "$ghusername" --output-checker --core-developer

# create backend and store token
echo "Getting AIRLOCK_API_TOKEN for $backend backend"
token="$(docker compose exec job-server ./manage.py create_backend "$backend" --user "$ghusername" --quiet)"

# now we know the user definitely exists, we can add it to ADMIN_USERS
ensure_value ADMIN_USERS "$ghusername" .env.jobserver
# store token in job-server config, as an easy place to persist it
ensure_value AIRLOCK_API_TOKEN "$token" .env.jobserver

echo
echo "Local job-server instance has been set up running in docker at $host"
echo " - set up user $ghusername as staff and OutputChecker, with permissions on $workspace."
echo " - backend $backend set up, with user $ghusername"
echo 
echo "This state should persist until you prune the docker compose volume the db uses."
echo 
echo "You will need to create a workspace (default name is 'airlock-test-workspace')."
echo
echo "   just job-server/create-workspace [name]"
echo
echo "Your local airlock .env file has been setup to point to this local job-runner instance."
echo "To go back to normal the simplest way is to run, which will stop job-server and restore your .env config"
echo
echo "    just job-server/stop"
echo
echo "You can log in to the local job-server here: $host"
echo 
