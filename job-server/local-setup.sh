#!/bin/bash
set -euo pipefail

ghusername="$1"
workspace="${2:-airlock-test-workspace}"
backend="airlock-test-backend"
host="http://localhost:9000"

# make sure a value is set in a .env file
ensure_value() {
    local name="$1"
    local value="$2"
    local file="$3"

    echo "Setting $name=$value in $file"

    # set naked value
    if grep -q "^$name=" "$file" 2>/dev/null; then
        # use '|' sed delimiter as we use '/' in values
        sed -i "s|^$name=.*|$name=\"$value\"|" "$file"
    # set and uncomment commented line
    elif grep -q "^#$name=" "$file" 2>/dev/null; then
        sed -i "s|^#$name=.*|$name=\"$value\"|" "$file"
    # append the line as it does not exist
    else
        echo "$name=\"$value\"" >> "$file"
    fi
}


test -f .env.jobserver|| cp .env.jobserver.template .env.jobserver

# this is *horrid*, but the service won't start if the config has ADMIN_USERS that don't exist
ensure_value ADMIN_USERS "" .env.jobserver

# ensure we have a running db and job-server instance we can run stuff in it 
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
if test "$SOCIAL_AUTH_GITHUB_KEY" = "test"; then
    tmp=$(mktemp)
    docker compose exec job-server cat ./scripts/dev-env.sh > "$tmp"
    bash "$tmp" .env.jobserver
    echo "Restarting job-server with new configuration"
    docker compose up -d job-server
else
    echo "Skipping job-server SOCIAL_AUTH setup as it is already done"
fi

# ensure user exists
docker compose exec job-server ./manage.py create_user "$ghusername" --output-checker --core-developer

# now we know the user definitely exists, we can add it to ADMIN_USERS
ensure_value ADMIN_USERS "$ghusername" .env.jobserver
# restart to pick up change, sigh
docker compose up -d

# ensure our local airlock config has the correct backend token and endpoint
echo "Getting backend token"
token="$(docker compose exec job-server ./manage.py create_backend "$backend" --user "$ghusername" --quiet)"
ensure_value "AIRLOCK_API_TOKEN" "$token" ../.env
ensure_value "AIRLOCK_API_ENDPOINT" "http://localhost:9000/api/v2" ../.env

# trigger reload of any running dev server
touch ../airlock/settings.py

echo
echo "Local job-server instance has been set up running in docker at $host"
echo " - set up user $ghusername as staff and OutputChecker, with permissions on $workspace."
echo " - backend $backend set up, with user $ghusername"
echo " - workspace $workspace set up, with user $ghusername"
echo 
echo "This state should persist until you prune the docker compose volume the db uses."
echo
echo "Your local airlock .env file has been setup to point to this local job-runner instance."
echo "To undo, comment out or edit the lines that set AIRLOCK_API_*. in your .env file."
echo
echo "You can log in to the local job-server here: $host"
