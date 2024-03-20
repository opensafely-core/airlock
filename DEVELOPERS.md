# Notes for developers


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

### Python

You'll need an appropriate version of Python on your PATH. Check the
`.python-version` file for the required version.

### Docker

Possibly something API compatibile with Docker would also work, but we
don't officially support that.


## Getting started

Set up a local development environment with:
```
just devenv
```

Check that Django is configured correctly with:
```
just manage check
```

You can run all the tests with:
```
just test
```

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

## Opentelemetry

To log opentelemetry traces to the console in local environments,
set the `OTEL_EXPORTER_CONSOLE` environment variable in your `.env` file. 

## Testing

### Test categories

Tests are divided into the following categories.

<dl>
   <dt>unit</dt><dd>fast tests of small code units</dd>
   <dt>integration</dt><dd>tests of components working together (e.g. views)</dd>
   <dt>functional</dt><dd>end-to-end [Playwright](https://playwright.dev/docs/intro) tests</dd>
</dl>

Each category lives in its own directory (for example `tests/unit`) and can be run with
`just test -k <category>` (for example `just test -k unit`).

Additional arguments passed to `just test` are passed on to pytest. For example, to
run all the tests _except_ the functional tests, run `just test -k 'not functional'`,
or to run a single test, run e.g. `just test tests/unit/test_urls.py::test_urls`.


### Functional tests

#### Debugging

Functional tests run headless by default. To see what's going on, they
can be run in headed mode. The follwoing command will run just the
functional tests, in headed mode, slowed down by 500ms. See the
[playwright docs](https://playwright.dev/python/docs/test-runners#cli-arguments) for additional cli arguments that may be
useful.

### Django Debug Toolbar

We include DDT as a dev dependency, as it is useful for inspecting django
specific things.  However, it is not enabled by default.  It is only enabled if
both `DJANGO_DEBUG` and `DJANGO_DEBUG_TOOLBAR` env vars are set to `"True"`.


```
just test -k functional --headed --slowmo 500
```

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


# Local job-server for integration.

## First time set up

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

## Create workspace

You will need at least one workspace set up in job-server and locally in airlock to test integration:


```
just job-server/create-workspace NAME  # defaults to "airlock-test-workspace"

```

IMPORTANT GOTCHA: The current release API is awkward, and will refuse to upload a file
that's already been uploaded. This will change, but for now, you can clear the
state of all releases for a workspace with:

```
just job-server/remove-releases workspace
```
## Running a custom job-server build

This is useful to test against an development version of job-server.

1. Build the prod image in your job-server checkout: `just docker/build prod`
2. `export JOB_SERVER_IMAGE=job-server`

Now the configure and run command will use the local job-server image, rather
than the published one.


## Undoing

To go back to normal, you can use `just job-server/stop`. This will comment out
the `AIRLOCK_API_*` lines in .env.  `just job-server/run` will uncomment them.


## Cleaning up

By default, the local job-server maintains db and file on a couple of volumes.
To reset back to a clean slate, you can kill and re-configure, and then add
workspaces again.

```
just job-server/clean
just job-server/configure GHUSERNAME
just job-server/create-workspace 
```


## Deployment

New versions should be deployed automatically on merge to `main` after a
short delay. Github Actions should build a new Docker image, and then
the backends poll regularly for updated images. See:
https://github.com/opensafely-core/backend-server/tree/main/services/airlock
