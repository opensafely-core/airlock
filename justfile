set dotenv-load := true


export VIRTUAL_ENV  := env_var_or_default("VIRTUAL_ENV", ".venv")

export BIN := VIRTUAL_ENV + if os_family() == "unix" { "/bin" } else { "/Scripts" }
export PIP := BIN + if os_family() == "unix" { "/python -m pip" } else { "/python.exe -m pip" }

export DEFAULT_PYTHON := if os_family() == "unix" { "python3.11" } else { "python" }


# list available commands
default:
    @{{ just_executable() }} --list


# ensure that a '.env` file exists
ensure-env:
    #!/usr/bin/env bash
    set -euo pipefail

    if [[ ! -f .env ]]; then
      echo "No '.env' file found; creating a default '.env' from 'dotenv-sample'"
      cp dotenv-sample .env
      # Unfortunately if the '.env' file didn't exist at the start of the run I
      # don't see a way to get the variables loaded into the environment; so we
      # have to fail the task and force the user to run it again. This is
      # annoying but should only happen once.
      echo "If you re-attempt the previous command it should now pick up the default environment variables"
      exit 1
    fi


# ensure valid virtualenv
virtualenv: ensure-env
    #!/usr/bin/env bash
    set -euo pipefail

    # allow users to specify python version in .env
    PYTHON_VERSION=${PYTHON_VERSION:-python3.11}

    # create venv and upgrade pip
    if [[ ! -d $VIRTUAL_ENV ]]; then
      # Collapse output when running in Github Actions
      [[ -v CI ]] && echo "::group::Setting up venv (click to view)" || true

      $PYTHON_VERSION -m venv $VIRTUAL_ENV
      $PIP install --upgrade pip

      [[ -v CI ]]  && echo "::endgroup::" || true
    fi


# run pip-compile with our standard settings
pip-compile *args: devenv
    #!/usr/bin/env bash
    set -euo pipefail

    $BIN/pip-compile --allow-unsafe --generate-hashes --strip-extras {{ args }}


# ensure dev and prod requirements installed and up to date
devenv: virtualenv
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

        # Collapse output when running in Github Actions
        [[ -v CI ]] && echo "::group::Install $req_file (click to view)" || true
        # --no-deps is recommended when using hashes, and also works around a
        # bug with constraints and hashes. See:
        # https://pip.pypa.io/en/stable/topics/secure-installs/#do-not-use-setuptools-directly
        $PIP install --no-deps -r "$req_file"
        [[ -v CI ]]  && echo "::endgroup::" || true

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
    $BIN/python manage.py runserver {{ ARGS }}
    

# run Django's manage.py entrypoint
manage *ARGS: devenv
    $BIN/python manage.py {{ ARGS }}


# run tests
test *ARGS: devenv
    $BIN/python -m pytest {{ ARGS }}


# run tests as they will be in run CI (checking code coverage etc)
@test-all: devenv
    #!/usr/bin/env bash
    set -euo pipefail

    $BIN/python -m pytest \
      --cov=airlock \
      --cov=tests \
      --cov-report=html \
      --cov-report=term-missing:skip-covered
