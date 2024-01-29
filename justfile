set dotenv-load := true
set positional-arguments


export VIRTUAL_ENV  := env_var_or_default("VIRTUAL_ENV", ".venv")

export BIN := VIRTUAL_ENV + if os_family() == "unix" { "/bin" } else { "/Scripts" }
export PIP := BIN + if os_family() == "unix" { "/python -m pip" } else { "/python.exe -m pip" }

export DEFAULT_PYTHON := if os_family() == "unix" { "python3.11" } else { "python" }


# list available commands
default:
    @{{ just_executable() }} --list


# ensure valid virtualenv
virtualenv:
    #!/usr/bin/env bash
    set -euo pipefail

    # allow users to specify python version in .env
    PYTHON_VERSION=${PYTHON_VERSION:-python3.11}

    # create venv and upgrade pip
    if [[ ! -d $VIRTUAL_ENV ]]; then
      $PYTHON_VERSION -m venv $VIRTUAL_ENV
      $PIP install --upgrade pip
    fi


# run pip-compile with our standard settings
pip-compile *args: devenv
    #!/usr/bin/env bash
    set -euo pipefail

    $BIN/pip-compile --allow-unsafe --generate-hashes --strip-extras {{ args }}


# create a valid .env if none exists
_dotenv:
    #!/usr/bin/env bash
    set -euo pipefail

    if [[ ! -f .env ]]; then
      echo "No '.env' file found; creating a default '.env' from 'dotenv-sample'"
      cp dotenv-sample .env
    fi


# ensure dev and prod requirements installed and up to date
devenv: virtualenv _dotenv
    #!/usr/bin/env bash
    set -euo pipefail

    for req_file in requirements.dev.txt requirements.prod.txt; do
      # If we've installed this file before and the original hasn't been
      # modified since then bail early
      record_file="$VIRTUAL_ENV/$req_file"
      if [[ -e "$record_file" && "$record_file" -nt "$req_file" ]]; then
        continue
      fi

      if cmp --silent "$req_file" "$record_file"; then
        # If the timestamp has been changed but not the contents (as can happen
        # when switching branches) then just update the timestamp
        touch "$record_file"
      else
        # Otherwise actually install the requirements

        # --no-deps is recommended when using hashes, and also works around a
        # bug with constraints and hashes. See:
        # https://pip.pypa.io/en/stable/topics/secure-installs/#do-not-use-setuptools-directly
        $PIP install --no-deps -r "$req_file"

        # Make a record of what we just installed
        cp "$req_file" "$record_file"
      fi
    done


# lint and check formatting but don't modify anything
check: devenv
    #!/usr/bin/env bash

    failed=0

    check() {
      # Display the command we're going to run, in bold and with the "$BIN/"
      # prefix removed if present
      echo -e "\e[1m=> ${1#"$BIN/"}\e[0m"
      # Run it
      eval $1
      # Increment the counter on failure
      if [[ $? != 0 ]]; then
        failed=$((failed + 1))
        # Add spacing to separate the error output from the next check
        echo -e "\n"
      fi
    }

    check "$BIN/ruff format --diff --quiet ."
    check "$BIN/ruff check --show-source ."
    check "docker run --rm -i ghcr.io/hadolint/hadolint:v2.12.0-alpine < Dockerfile"

    if [[ $failed > 0 ]]; then
      echo -en "\e[1;31m"
      echo "   $failed checks failed"
      echo -e "\e[0m"
      exit 1
    fi


# fix formatting and import sort ordering
fix: devenv
    $BIN/ruff format .
    $BIN/ruff --fix .


run *ARGS: devenv
    $BIN/python manage.py runserver "$@"


# run Django's manage.py entrypoint
manage *ARGS: devenv
    $BIN/python manage.py "$@"


# run tests
test *ARGS: devenv
    $BIN/python -m pytest "$@"


# run tests as they will be in run CI (checking code coverage etc)
@test-all: devenv
    #!/usr/bin/env bash
    set -euo pipefail

    $BIN/python -m pytest \
      --cov=airlock \
      --cov=assets \
      --cov=tests \
      --cov-report=html \
      --cov-report=term-missing:skip-covered


# load example data so there's something to look at in development
load-example-data: devenv
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
      tmp=$(mktemp)
      # grab published database outputs for example csv and html data
      curl -s https://jobs.opensafely.org/opensafely-internal/tpp-database-schema/outputs/85/download/ --output "$tmp"
      unzip "$tmp" -d "$workspace"
    done
