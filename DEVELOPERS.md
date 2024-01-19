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

You'll probably find you need to run this twice. The first time it will
complain that you don't have a `.env` environment file and will create
one for you. Then you'll need to run the command again with the `.env`
file in place.

Check that Django is configured correctly with:
```
just manage check
```

You can run all the tests with:
```
just test
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


## Front-end Assets

All tooling and ui components are in the assets/ django application.

 - `assets/src` contains the js/css sources
 - `assets/templates` contains shared templates, include UI components
 - `assets/dist` contains the built assets

`just assets/` will list commands

### Developing assets

When developing css and js assets, you can use Vite's dev mode to do this w/o
needing to rebuild.


 * edit .env to set `ASSETS_DEV_MODE=true`
 * in a separate terminal, run `just assets/run`. This will run the Vite dev
   server.
 * run the app as normal, but now the assets will be loaded from the dev
   server, and dynamically update.

