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
