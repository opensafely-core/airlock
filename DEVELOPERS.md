# Notes for developers

## Diagrams

 * [Request State Machine](docs/reference/request-states.md) (auto-generated)


## Prerequisites for local development

### Just

We use [`just`](https://github.com/casey/just) as our command runner. It's
a single file binary available for many platforms so should be easy to
install.

```sh
# macOS
brew install just

# Linux
# Install from https://github.com/casey/just/releases

# Add completion for your shell. E.g. for bash:
source <(just --completions bash)

# Show all available commands
just #  shortcut for just --list
```

### uv

Follow installation instructions from the [uv documentation](https://docs.astral.sh/uv/getting-started/installation/) for your OS.

### Python

You'll need an appropriate version of Python on your PATH. Check the
`.python-version` file for the required version.

### Docker

Possibly something API compatible with Docker would also work, but we
don't officially support that.



## Dependency management
Dependencies are managed with `uv`.

### Overview
See the [uv documentation](https://docs.astral.sh/uv/concepts/projects/dependencies) for details on usage.
Commands for adding, removing or modifying constraints of dependencies will automatically respect the
global timestamp cutoff specified in the `pyproject.toml`:
```toml
[tool.uv]
exclude-newer = "YYYY-MM-DDTHH:MM:SSZ"
```
Changes to dependencies should be made via `uv` commands, or by modifying `pyproject.toml` directly followed by
[locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/) via `uv` or `just` commands like
`just devenv` or `just upgrade-all`. You should not modify `uv.lock` manually.

Note that `uv.lock` must be reproducible from `pyproject.toml`. Otherwise, `just check` will fail.
If `just check` errors saying that the timestamps must match, you might have modified one file but not the other:
  - If you modified `pyproject.toml`, you must update `uv.lock` via `uv lock` / `just upgrade-all` or similar.
  - If you did not modify `pyproject.toml` but have changes in `uv.lock`, you should revert the changes to `uv.lock`,
  modify `pyproject.toml` as you require, then run `uv lock` to update `uv.lock`.

The timestamp cutoff should usually be set to midnight UTC of a past date.
In general, the date is expected to be between 7 and 14 days ago as a result of automated weekly dependency updates.

If you require a package version that is newer than the cutoff allows, you can either manually bump the global cutoff
date or add a package-specific timestamp cutoff. Both options are described below.

### Manually bumping the cutoff date
The cutoff timestamp can be modified to a more recent date either manually in the `pyproject.toml`
or with `just bump-uv-cutoff <days-ago>`.
For example, to set the cutoff to today's date and upgrade all dependencies, run:
```
just bump-uv-cutoff 0
just upgrade-all
```

### Adding a package-specific timestamp cutoff
It is possible to specify a package-specific timestamp cutoff in addition to the global cutoff.
This should be done in the `pyproject.toml` to ensure reproducible installs;
see the [uv documentation](https://docs.astral.sh/uv/reference/settings/#exclude-newer-package) for details.
If set, the package-specific cutoff will take precedence over the global cutoff regardless of which one is more recent.

You should not set a package-specific cutoff that is older than the global cutoff - use a version
constraint instead.
If there is good reason to set a package-specific cutoff that is more recent than the global cutoff,
**care should be taken to ensure that the package-specific cutoff is manually removed once it is over 7 days old**,
as otherwise future automated updates of that package will be indefinitely blocked.
Currently no automated tooling is in place to enforce removal of stale package-specific cutoffs.


## Getting started

Set up a local development environment with:
```
just devenv
```

Check that Django is configured correctly with:
```
just manage check
```

Create a local database with:
```
just manage migrate
```

Set up the frontend assets with:
```
just assets
```

Build the docs with
```
just docs-build
```

You can run all the tests with:
```
just test
```
Note that you will need to run `just assets` and `just docs-build` on a clean checkout in order for the tests to run.

To load some initial data for playing with the app locally use:
```
just load-example-data
```

To start the app use:
```
just run
```


### Running commands without using `just`

`just` automatically takes care of a few things:

 * ensuring commands are run using the correct Python virtual
   environment;
 * ensuring that the installed packages match what's specified in the
   `requirements.*.txt` files;
 * ensuring that the variables specified in `.env` are loaded into the
   environment.

Running commands outside of `just` is a reasonable and supported
workflow, but you will need to handle the above tasks yourself e.g. by
activating a virtual environment and running something like:
```bash
set -a; source .env; set +a
```

## Assets

The asset build tooling and component library is currently extracted
from job-server for use in airlock.

We also add the components browser view at the /ui-components. This acts
as a test to see everything works, and a helpful builtin reference for
using the slippers components.

To update the upstream assets, first remove any existing built assets:
```bash
just assets/clean
```
And then update with the latest upstream assets:
```bash
just assets/update
```

### Testing upstream assets locally

By default, `just assets/update` with fetch the job-server repo from
GitHub. You can optionally use a local job-server checkout. This is
useful if you are making changes to the job-server assets and want to
test how they will be applied in Airlock.

```bash
just assets/update /absolute/path/to/local/job-server
```

Note: do not commit assets updated using a local job-server checkout. Merge
your job-server changes first, then run `just assets/update` to update from
the upstream repo.


## Opentelemetry

To log opentelemetry traces to the console in local environments,
set the `OTEL_EXPORTER_CONSOLE` environment variable in your `.env` file.

To reduce some of the noise for local development, some instrumentations
can be turned off; to run a local server and disable everything except
tracing we explicitly add in Airlock code, run:

```
OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=django,sqlite3,requests
```

## Testing

### Test categories

Tests are divided into the following categories.

<dl>
   <dt>unit</dt><dd>fast tests of small code units</dd>
   <dt>integration</dt><dd>tests of components working together (e.g. views)</dd>
   <dt>functional</dt><dd>end-to-end <a href="https://playwright.dev/docs/intro">Playwright</a> tests</dd>
</dl>

Each category lives in its own directory (for example `tests/unit`) and can be run with
`just test -k <category>` (for example `just test -k unit`).

Additional arguments passed to `just test` are passed on to pytest. For example, to
run all the tests _except_ the functional tests, run `just test -k 'not functional'`,
or to run a single test, run e.g. `just test tests/unit/test_urls.py::test_urls`.


### Functional tests

#### Debugging

##### Headed mode

Functional tests run headless by default. To see what's going on, they
can be run in headed mode. The following command will run just the
functional tests, in headed mode, slowed down by 500ms. See the
[playwright docs](https://playwright.dev/python/docs/test-runners#cli-arguments) for additional cli arguments that may be
useful.

```
just test -k functional --headed --slowmo 500
```

To leave the browser instance open after a test failure you can make
pytest drop into the debugger using the `--pdb` argument:
```
just test --headed --pdb ... <path/to/test.py>
```

##### The Playwright Inspector

To use the [Playwright Inspector debugging tool](https://playwright.dev/python/docs/running-tests#debugging-tests), run with:
```
PWDEBUG=1 just test ...
```

This will run the tests and open up a browser window as well as the Playwright Inspector.
In the inspector you can step throught the test and investigate element locators.

##### Tracing tests

[Record a trace](https://playwright.dev/python/docs/trace-viewer-intro#recording-a-trace)
for each test with:

```
just test ... --tracing on
```

This will record the trace and place it into the file named trace.zip in the `test-results`
directory.

You can load the trace using Playwright's trace viewer, and see the state of the page
at each action in each test:
```
playwright show-trace /path/to/trace.zip
```

#### Hypothesis tests

We use hypothesis in some functional tests (e.g. [test_csv_viewer.py](tests/functional/test_csv_viewer.py).
These can be slow, so by default only run with 5 examples. Scheduled CI tests run them regularly with
the default 100 examples.  To run the tests with a different number of examples, use:

```
HYPOTHESIS_MAX_EXAMPLES=200 just test ...
```


### Django Debug Toolbar

We include DDT as a dev dependency, as it is useful for inspecting django
specific things.  However, it is not enabled by default.  It is only enabled if
both `DJANGO_DEBUG` and `DJANGO_DEBUG_TOOLBAR` env vars are set to `"True"`.


#### Browser configuration

By default, the functional tests run with the latest chromium browser only (the
Playwright default). In order to test older/different browser version, you can
pass an environment variable specifying a path to browser executable. E.g. to
run with the system chrome at /usr/bin/google-chrome:

```
PLAYWRIGHT_BROWSER_EXECUTABLE_PATH=/usr/bin/google-chrome-stable just test -k functional
```

(To verify the custom browser executable, run with `-s` to print an info message to
the console, or with `--headed` for headed mode.)


## Local job-server for integration.

### First time set up

This needs some first time setup, but after that is fairly simple to use. You
will need the Bitwarden cli `bw` installed to pull the dev Github auth
credentials. You need to run the following command with your github username.

```
just job-server/configure GHUSERNAME
```

This will configure and run the latest job-server image at
http://localhost:9000 to use in integration testing airlock. It will
automatically point your current .env config to this local instance.

This command is idempotent, and can be safely re-run.


In future you can just do the following to start it up:

```
just job-server/run
```

### Create workspace

You will need at least one workspace set up in job-server and locally in airlock to test integration:


```
just job-server/create-workspace NAME  # defaults to "airlock-test-workspace"

```

You can clear the state of all releases for a workspace with:

```
just job-server/remove-releases workspace
```

### Running a custom job-server build

This is useful to test against an development version of job-server.

1. Build the prod image in your job-server checkout: `just docker/build prod`
2. `export JOB_SERVER_IMAGE=job-server`

Now the configure and run command will use the local job-server image, rather
than the published one.


### Undoing

To go back to normal, you can use `just job-server/stop`. This will comment out
the `AIRLOCK_API_*` lines in .env.  `just job-server/run` will uncomment them.


### Cleaning up

By default, the local job-server maintains db and file on a couple of volumes.
To reset back to a clean slate, you can kill and re-configure, and then add
workspaces again.

```
just job-server/clean
just job-server/configure GHUSERNAME
just job-server/create-workspace
```


### Running Airlock with a local job-server and job-runner integration

job-runner (RAP controller and RAP agent) can be run with a local job-server using the instructions
[here](https://github.com/opensafely-core/job-runner/blob/main/DEVELOPERS.md#running-locally-with-a-local-job-server).

This runs job-server on localhost:8000 and the RAP controller web-app on localhost:3000.

To use all 4 local components together (RAP controller, RAP agent, job-server, Airlock), make the following
changes to your .env file:

```
# point workdir at your local job-runner repo's workdir
AIRLOCK_WORK_DIR=/absolute/path/to/local/job-runner/workdir/
# Set workspace dir to the medium_privacy/workspaces dir, which will be  located relative to AIRLOCK_WORK_DIR
# (i.e. in you local job-runner repo - these are the workspaces files airlock should be allowed to access)
AIRLOCK_WORKSPACE_DIR=medium_privacy/workspaces/

# change endpoint to port local job-server is running on 
AIRLOCK_API_ENDPOINT="http://localhost:8000/api/v2"

# Set AIRLOCK_API_TOKENT to a valid backend token from your locally running jobserver (find it at staff/backends - any
# valid backend token is accepted)
AIRLOCK_API_TOKEN="token-from-job-server-backend"
```

In separate terminal windows, run:
1) job-server (with `just run`)
2) job-runner (with `just run` - this runs the RAP agent, RAP controller and the controller webapp all together)
3) airlock (`just run 7000` - run on any port that doesn't clash with job-server, which is using 8000)
4) airlock file uploader (`just manage run_file_uploader`)

Go to job-server at localhost:8000 and login with GitHub. Create at least one job request in the workspace
that you set up in your local job-server and let it run to completion (this ensures you have at least one
workspace available to view in Airlock).

Obtain a 3 word token from http://localhost:8000/settings/
and use it to log into airlock at localhost:7000. You should now see any workspaces that you have run jobs
for with your local job-runner.

Note that in order to actually release from Airlock to your local job-server, you will need a different user
who is a valid job-server user (second approvals can be done by a dummy dev user). In order to test releases, you
can use dummy dev users to create the release and perform the first output checker review, and then switch to your
real GitHub user for the second review and release (ensure that your user in your local job-server has the Output
Checker role).

First ensure you have a dev users file available. The following command will create a dev users file if it doesn't
already exist, and will print the location of the file.

```
just load-dev-users
```

Update the researcher_1 user's "workspaces" to add an entry that matches your job-server workspace so that
researcher_1 can create a release request for this workspace:

```
...

"researcher_1": {
    ...
      "workspaces": {
        "example-workspace": {
          "project_details": {"name": "Project 1", "ongoing": true},
          "archived": false
        },
        ...
        "my-jobserver-test-workspace": {
          "project_details": {"name": "Test Project", "ongoing": true},
          "archived": false
        }
      }
    ...
  }
...  

```

To switch Airlock to using dev users, stop the server, comment out `AIRLOCK_API_TOKEN=` in your `.env` file, and restart. 

To switch back to using prod-like 3 word token logins, uncomment `AIRLOCK_API_TOKEN=` again and restart (both django
server and file uploader).


## Deployment

New versions should be deployed automatically on merge to `main` after a
short delay. Github Actions should build a new Docker image, and then
the backends poll regularly for updated images. See:
https://github.com/opensafely-core/backend-server/tree/main/services/airlock


## Documentation

The documentation in this repository forms part of the main [OpenSAFELY documentation](https://github.com/opensafely/documentation). It is also available within Airlock itself in order to be
accessible from within the OpenSAFELY backends.

To build the docs as a standalone documentation site with MkDocs to preview content changes, run:

    just docs-serve


:warning: In order to maintain a similar look to the main OpenSAFELY documentation, we duplicate the
custom css from the main documentation repo (`docs/stylesheets/extra.css`).

When the main OpenSAFELY documentation is built, it imports the airlock `docs/` directory
and builds it within the main documentation site.

### Documentation redirects

These are handled in the main [OpenSAFELY documentation repository](https://github.com/opensafely/documentation).
If you need to redirect URLs —
and this should be fairly infrequent —
make any changes to the `_redirects` file in the main documentation repository,
and test them in a preview there.

### Structure

Airlock documentation is located in the [docs](docs/) directory. Local configuration is
specified in the `mkdocs.yml` located at the repo root.

Note that most of the config in the `mkdocs.yml` is specific to the within-airlock docs and
will be ignored when the docs are imported into the main OpenSAFELY documentation.

In order to serve the docs within Airlock, the directory built by mkdocs (with
`just docs-build`) is specified as a static files directory in the `STATICFILES_DIRS`
setting. A simple view makes them available within Airlock, using the `django.static.serve`
view (see `airlock/views/docs.py`)

#### Process for updating Airlock documentation

1. Developer makes changes to documentation files
1. PR opened; CI builds a cloudflare pages preview
1. PR merged; CI triggers a deploy of the main OpenSAFELY documentation site

To check how local changes appear within the main OpenSAFELY docs, first push a
branch with your Airlock changes. Then, in a local checkout of the documentation repo:

- In `mkdocs.yml`, update the import line in the `nav` section with your branch:
  ```
  - Releasing with Airlock: '!import https://github.com/opensafely-core/airlock?branch=<YOUR BRANCH>'
  ```
- Run the main docs with:
  ```
  MKDOCS_MULTIREPO_CLEANUP=true just run
  ```
  (`MKDOCS_MULTIREPO_CLEANUP=true` ensures that the external repos are re-fetched)


### Updating the main OpenSAFELY documentation repository

Merges to the main branch in this repo trigger a [deployment of the main OpenSAFELY documentation via a Github Action](https://github.com/opensafely-core/airlock/actions/workflows/deploy-documentation.yml).
