set dotenv-load := true
set positional-arguments := true

# list available commands
default:
    @{{ just_executable() }} --list

# Create a valid .env if none exists
_dotenv:
    #!/usr/bin/env bash
    set -euo pipefail

    if [[ ! -f .env ]]; then
      echo "No '.env' file found; creating a default '.env' from 'dotenv-sample'"
      cp dotenv-sample .env
    fi

# Check if a .env exists
# Use this (rather than _dotenv or devenv) for recipes that require that a .env file exists.
# just will not pick up environment variables from a .env that it's just created,
# and there isn't an easy way to load those into the environment, so we just

# prompt the user to run just devenv to set up their local environment properly.
_checkenv:
    #!/usr/bin/env bash
    set -euo pipefail

    if [[ ! -f .env ]]; then
        echo "No '.env' file found; run 'just devenv' to create one"
        exit 1
    fi

# Clean up temporary files
clean:
    rm -rf .venv

# Install production requirements into and remove extraneous packages from venv
prodenv:
    uv sync --no-dev

# update to the latest version of the internal pipeline library
update-pipeline:
    ./scripts/upgrade-pipeline.sh pyproject.toml

# && dependencies are run after the recipe has run. Needs just>=0.9.9. This is
# a killer feature over Makefiles.
#

# Install dev requirements into venv without removing extraneous packages
devenv: _dotenv
    uv sync --inexact

# Fetch and extract the chromium version we need for testing
get-chromium:
    #!/bin/bash
    # We use chromium v108 for consistency with backends
    # https://www.chromium.org/getting-involved/download-chromium/#downloading-old-builds-of-chrome-chromium
    # 
    # Instructions on the link are not entirely up to date, so, to find another version
    # 1) Go to https://chromiumdash.appspot.com/releases
    # 2) Select the relevant platform 
    # 3) In the "Stable" table, click Load more until you get to the version you're after
    #    Make a note of the base position (e.g. 1368529 @ Nov 12 2024)
    # 4) Go to https://commondatastorage.googleapis.com/chromium-browser-snapshots/index.html
    # 5) Select your platform (Mac/Linux_x64). Filter for the base position (there might not be
    #    an exact match, so filter for the first 5 numbers or so and find the closest)

    # Exit with an error if the playwright env variable isn't set; we want to ensure that
    # functional tests are always run with the custom chrome version
    if [[ -z ${PLAYWRIGHT_BROWSER_EXECUTABLE_PATH} ]]; then
      echo "ERROR: PLAYWRIGHT_BROWSER_EXECUTABLE_PATH environment variable is not set"
      exit 1
    fi

    # If this is being fetched for docker, the platform is passed in and we bypass whatever the
    # local OS is. Otherwise, get the right chrome for the detected platform.
    PLATFORM="${PLATFORM:-$OSTYPE}"

    chrome_linux_executable=.playwright-browsers/chrome-linux/chrome
    chrome_mac_executable=.playwright-browsers/chrome-mac/Chromium.app/Contents/MacOS/Chromium

    if [[ "$PLATFORM" == "linux-gnu"* ]]; then
        chrome_executable=$chrome_linux_executable
        # The chrome executable after unzipping is at ../.playwright-browsers/chrome-linux/chrome
        if [[ ! -f $chrome_executable ]]; then
            mkdir -p .playwright-browsers
            curl -o .playwright-browsers/chrome-linux.zip https://commondatastorage.googleapis.com/chromium-browser-snapshots/Linux_x64/1058929/chrome-linux.zip
            unzip .playwright-browsers/chrome-linux.zip -d .playwright-browsers && rm .playwright-browsers/chrome-linux.zip
        fi
    elif [[ "$PLATFORM" == "darwin"* ]]; then
        # Mac OSX
        chrome_executable=$chrome_mac_executable
        # The chrome executable after unzipping is at ../.playwright-browsers/chrome-mac/chrome
        if [[ ! -f $chrome_executable ]]; then
            mkdir -p .playwright-browsers
            curl -o .playwright-browsers/chrome-mac.zip https://commondatastorage.googleapis.com/chromium-browser-snapshots/Mac/1058919/chrome-mac.zip
            unzip .playwright-browsers/chrome-mac.zip -d .playwright-browsers && rm .playwright-browsers/chrome-mac.zip
        fi

        if [[ ${PLAYWRIGHT_BROWSER_EXECUTABLE_PATH} == ${chrome_linux_executable} ]]; then
            echo "" 
            echo "WARNING: Detected MacOS but PLAYWRIGHT_BROWSER_EXECUTABLE_PATH is set to $chrome_linux_executable"
        fi
    else
        echo "Unsupported OS $PLATFORM found"
    fi

# Upgrade a single package to the latest version as of the cutoff in pyproject.toml
upgrade-package package: && uvmirror devenv
    uv lock --upgrade-package {{ package }}

# Upgrade all packages to the latest versions as of the cutoff in pyproject.toml
upgrade-all: && uvmirror devenv
    uv lock --upgrade

# update the uv mirror requirements file
uvmirror file="requirements.uvmirror.txt":
    rm -f {{ file }}
    uv export --format requirements-txt --frozen --no-hashes --all-groups --all-extras > {{ file }}

# Move the cutoff date in pyproject.toml to N days ago (default: 7) at midnight UTC
bump-uv-cutoff days="7":
    #!/usr/bin/env -S uvx --with tomlkit python3

    import datetime
    import tomlkit

    with open("pyproject.toml", "rb") as f:
        content = tomlkit.load(f)

    new_datetime = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=int("{{ days }}"))
    ).replace(hour=0, minute=0, second=0, microsecond=0)
    new_timestamp = new_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")
    if existing_timestamp := content["tool"]["uv"].get("exclude-newer"):
        if new_datetime < datetime.datetime.fromisoformat(existing_timestamp):
            print(
                f"Existing cutoff {existing_timestamp} is more recent than {new_timestamp}, not updating."
            )
            exit(0)
    content["tool"]["uv"]["exclude-newer"] = new_timestamp

    with open("pyproject.toml", "w") as f:
        tomlkit.dump(content, f)

# This is the default input command to update-dependencies action
# https://github.com/bennettoxford/update-dependencies-action

# Bump the timestamp cutoff to midnight UTC 7 days ago and upgrade all dependencies
update-dependencies: bump-uv-cutoff upgrade-all

format *args:
    uv run ruff format --diff --quiet {{ args }} .
    uv run djhtml --tabwidth 2 --check airlock/

lint *args:
    uv run ruff check {{ args }} .

lint-actions:
    docker run --rm -v $(pwd):/repo:ro --workdir /repo rhysd/actionlint:1.7.8 -color

# run mypy type checker
mypy *ARGS:
    uv run mypy airlock/ local_db/ tests/ "$@"

shellcheck:
    #!/usr/bin/env bash
    set -euo pipefail

    find docker/ airlock/ job-server/ scripts/ -name \*.sh -print0 | xargs -0 docker run --rm -v "$PWD:/mnt" koalaman/shellcheck:v0.9.0

# Run the various dev checks but does not change any files
check:
    #!/usr/bin/env bash
    set -euo pipefail

    failed=0

    check() {
      echo -e "\e[1m=> ${1}\e[0m"
      rc=0
      # Run it
      eval $1 || rc=$?
      # Increment the counter on failure
      if [[ $rc != 0 ]]; then
        failed=$((failed + 1))
        # Add spacing to separate the error output from the next check
        echo -e "\n"
      fi
    }

    check "just check-lockfile"
    check "just format"
    check "just mypy"
    check "just lint"
    check "just lint-actions"
    check "just shellcheck"
    check "just state-diagram /tmp/airlock-states.md && diff -u /tmp/airlock-states.md docs/reference/request-states.md"
    test -d docker/ && check "just docker/lint"

    if [[ $failed > 0 ]]; then
      echo -en "\e[1;31m"
      echo "   $failed checks failed"
      echo -e "\e[0m"
      exit 1
    fi

# validate uv.lock
check-lockfile:
    #!/usr/bin/env bash
    set -euo pipefail
    # Make sure dates in pyproject.toml and uv.lock are in sync
    unset UV_EXCLUDE_NEWER
    rc=0
    uv lock --check || rc=$?
    if test "$rc" != "0" ; then
        echo "Timestamp cutoffs in uv.lock must match those in pyproject.toml. See DEVELOPERS.md for details and hints." >&2
        exit $rc
    fi

# fix the things we can automate: linting, formatting, import sorting, diagrams
fix: && state-diagram
    uv run ruff format .
    uv run ruff check --fix .
    uv run djhtml --tabwidth 2 airlock/
    just --fmt --unstable --justfile justfile
    just --fmt --unstable --justfile docker/justfile

# run airlock with django dev server
run *ARGS: docs-build
    uv run python manage.py runserver "$@"

# run airlock with gunicorn, like in production
run-gunicorn *args: _checkenv
    uv run gunicorn --config gunicorn.conf.py airlock.wsgi {{ args }}

run-uploader:
    just manage run_file_uploader

run-all:
    { just run-uploader & just run 7000; }

# run Django's manage.py entrypoint
manage *ARGS: _checkenv
    uv run python manage.py "$@"

# run tests
test *ARGS: _checkenv get-chromium
    uv run python -m pytest "$@"

# run tests as they will be in run CI (checking code coverage etc)
@test-all: _checkenv docs-build get-chromium
    #!/usr/bin/env bash
    set -euo pipefail

    uv run python -m pytest \
      --cov=airlock \
      --cov=assets \
      --cov=local_db \
      --cov=users \
      --cov=tests \
      --cov=old_api \
      --cov=services \
      --cov-report=html \
      --cov-report=term-missing:skip-covered

load-dev-users:
    #!/usr/bin/env bash
    set -euo pipefail
    # Configure user details for local login
    # But don't clobber any existing dev users file
    echo "Dev users file located at ${AIRLOCK_WORK_DIR%/}/${AIRLOCK_DEV_USERS_FILE}"
    if [[ -e "${AIRLOCK_WORK_DIR%/}/${AIRLOCK_DEV_USERS_FILE}" ]]; then
        echo "File already exists, skipping"
    else
        cp example-data/dev_users.json "${AIRLOCK_WORK_DIR%/}/${AIRLOCK_DEV_USERS_FILE}"
    fi

# load example data so there's something to look at in development
load-example-data: _checkenv load-dev-users && manifests
    #!/usr/bin/env bash
    set -euo pipefail

    if [[ "$DJANGO_DEBUG" != "True" ]]; then
      echo "DJANGO_DEBUG env var is not set to 'True'."
      echo "Exiting in case this is a production environment."
      exit 1
    fi

    # This is where we'd set up the database, load fixtures etc. if we had any

    # Use a loop to create a bunch of workspace files. In future we'll probably
    # bundle a more sensible set of example files which we can copy over.
    workspace="${AIRLOCK_WORK_DIR%/}/${AIRLOCK_WORKSPACE_DIR%/}/example-workspace"
    for i in {1..2}; do
      workspace_dir="${workspace}_$i"
      for j in {1..2}; do
        subdir="$workspace_dir/sub_dir_$j"
        mkdir -p "$subdir"
        for k in {1..5}; do
          echo "I am file $j$k" > "$subdir/file_$j$k.txt"
        done
      done
      mkdir -p "$workspace_dir/sub_dir_empty"
    done

    # Make a deep directory and long file names
    workspace_dir="${workspace}_1/sub_dir_deep"
    mkdir -p "${workspace_dir}"
    echo "A large file name to test wrapping behaviour" > "${workspace_dir}/a_very_long_file_name_without_any_spaces_at_all.txt"
    for i in {1..9}; do
      workspace_dir="${workspace_dir}/another_sub_dir"
      mkdir -p "${workspace_dir}"
    done

    mkdir -p "$workspace/sub_dir_empty"

    tmp=$(mktemp)
    # grab published database outputs for example csv and html data
    curl -s https://jobs.opensafely.org/opensafely-internal/tpp-database-schema/outputs/85/download/ --output "$tmp"
    unzip -u "$tmp" -d "$workspace"

    cp example-data/bennett.svg $workspace/output/sample.svg

    # Make a large csv file
    cp $workspace/output/rows.csv $workspace/output/rows_LARGE.csv
    for i in {1..4}; do cat $workspace/output/rows.csv >> $workspace/output/rows_LARGE.csv; done

    # Make a large workspace
    workspace_dir="${AIRLOCK_WORK_DIR%/}/${AIRLOCK_WORKSPACE_DIR%/}/large-workspace/10k-files"
    mkdir -p "${workspace_dir}"
    for i in {1..9999}; do echo "I am but one file $i of 10000" > "$workspace_dir/file_$i.txt"; done
    workspace_dir="${AIRLOCK_WORK_DIR%/}/${AIRLOCK_WORKSPACE_DIR%/}/large-workspace/400-files"
    mkdir -p "${workspace_dir}"
    for i in {1..399}; do echo "I am but one file $i of 400" > "$workspace_dir/file_$i.txt"; done

    request_dir="${AIRLOCK_WORK_DIR%/}/${AIRLOCK_REQUEST_DIR%/}/example-workspace/test-request"
    mkdir -p $request_dir
    cp -a $workspace/output $request_dir

# generate or update manifests and git repos for local test workspaces
manifests: _checkenv
    cat scripts/manifests.py | uv run python manage.py shell

# generate the automated state diagrams from code
state-diagram file="docs/reference/request-states.md": _checkenv
    {{ just_executable() }} manage statemachine {{ file }}

# Run the documentation server: to configure the port, append: ---dev-addr localhost:<port>
docs-serve *ARGS:
    uv run mkdocs serve --clean {{ ARGS }}

# Build the documentation
docs-build *ARGS:
    uv run mkdocs build --clean {{ ARGS }}

# Remove built assets and node_modules
assets-clean:
    rm -rf assets/out
    rm -rf node_modules

# Install the Node.js dependencies
assets-install *args="":
    #!/usr/bin/env bash
    set -euo pipefail


    # exit if lock file has not changed since we installed them. -nt == "newer than",
    # but we negate with || to avoid error exit code
    test package-lock.json -nt node_modules/.written || exit 0

    npm ci {{ args }}
    touch node_modules/.written

# Build the Node.js assets
assets-build:
    #!/usr/bin/env bash
    set -euo pipefail

    # find files which are newer than dist/.written in the src directory. grep
    # will exit with 1 if there are no files in the result.  We negate this
    # with || to avoid error exit code
    # we wrap the find in an if in case dist/.written is missing so we don't
    # trigger a failure prematurely
    if test -f assets/out/.written; then
        find assets/src -type f -newer assets/out/.written | grep -q . || exit 0
    fi

    npm run build
    touch assets/out/.written

# Install npm toolchain, build and collect assets
assets: assets-install assets-build

# Rebuild all npm/static assets
assets-rebuild: assets-clean assets

# Run the npm development server and watch for changes
assets-run: assets-install
    #!/usr/bin/env bash
    set -euo pipefail

    if [ "$ASSETS_DEV_MODE" == "False" ]; then
        echo "Set ASSETS_DEV_MODE to a truthy value to run this command"
        exit 1
    fi

    npm run dev
