# Notes for developers


## Local development environment

We use [just](https://github.com/casey/just) as our command runner.

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

Set up a local development environment with:
```
just devenv
```
