# syntax=docker/dockerfile:1
# check=error=true
#################################################
#
# Images are structured as shown in this diagram:
# ┌──────────────────┐            ┌──────────────────┐
# │   base-python    ├────────────►     builder      │
# └────────┬─────────┘            └──────────┬───────┘
#          │                                 │
#          │                                 │COPY FROM
#          │     ┌──────────────────┐        │
#          └─────► new-project-base ◄────────┘
#                └────────┬─┬───────┘
#                         │ │
#                         │ │
# ┌──────────────────┐    │ │     ┌──────────────────┐
# │ new-project-prod ◄────┘ └─────► new-project-dev  │
# └──────────────────┘            └──────────────────┘
# The goal of this stages structure is to a) minimise the size of the *prod* image by
# not including build dependencies and b) to make rebuilding the *dev* fast in local
# development, by avoiding invalidating the layer cache every time we change python
# code.
#
#################################################
#
# These build args have defaults, but are usually set from docker/.env. They
# are used in FROM lines, so need to be declared globally and have defaults
# set.
ARG NODE_VERSION=20
ARG UBUNTU_VERSION=22.04
ARG UV_VERSION=0.9

# Create base image with python installed.
# All Dockerfiles should start from the base-docker image
#
# DL3007 ignored because base-docker we specifically always want to build on
# the latest base image, by design.
#
# hadolint ignore=DL3007
FROM ghcr.io/opensafely-core/base-docker:${UBUNTU_VERSION} AS base-python

# we are going to use an apt cache on the host, so disable the default debian
# docker clean up that deletes that cache on every apt install
RUN rm -f /etc/apt/apt.conf.d/docker-clean

# use deadsnakes ppa to install a fully working base python installation
# see: https://gist.github.com/tiran/2dec9e03c6f901814f6d1e8dad09528e
# use space efficient utility from base image
RUN --mount=type=cache,target=/var/cache/apt <<'EOF'
UBUNTU_CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
KEY_PATH=/usr/share/keyrings/deadsnakes.asc
KEY_URL='https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xf23c5a6cf475977595c89f51ba6932366a755776'
/usr/lib/apt/apt-helper download-file "$KEY_URL" "$KEY_PATH"
echo "deb [signed-by=$KEY_PATH] https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu ${UBUNTU_CODENAME} main" > /etc/apt/sources.list.d/deadsnakes-ppa.list
EOF

# install any additional system dependencies
# NOTE: ensure .python-version is not excluded in .dockerignore
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=bind,source=docker/dependencies.txt,target=/dependencies.txt \
    --mount=type=bind,source=.python-version,target=/.python-version \
    /root/docker-apt-install.sh /dependencies.txt "python$(cat /.python-version)"

# uv env var documentation: https://docs.astral.sh/uv/reference/environment/
# copy files rather than symlink since the cache and the target are on different filesystems
ENV UV_LINK_MODE=copy
# compile at installation time, not import time
ENV UV_COMPILE_BYTECODE=1
# we are providing python via deadsnakes ppa
# (as we require dynamic linking of openssl for security reasons)
ENV UV_PYTHON_DOWNLOADS=never
# set the directory for the venv
ENV UV_PROJECT_ENVIRONMENT="/opt/venv"

##################################################
#
# Reusable uv CLI layer
#
# This allows us to specify the version in one place, and also works around
# a limitation that you cannot use build args directly in `COPY --from`.
#
# See: https://github.com/moby/buildkit/issues/2412 for more infor
#
#
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-cli

#################################################
#
# Create node image.
#
FROM node:${NODE_VERSION} AS node-builder

WORKDIR /usr/src/app

# copy just what npm ci needs
COPY package-lock.json package.json ./
RUN --mount=type=cache,target=/usr/src/app/.npm \
    npm set cache /usr/src/app/.npm && \
    npm ci

# just copy in the files `npm run build` needs
COPY vite.config.js tailwind.config.js postcss.config.js ./
COPY airlock/templates ./airlock/templates
COPY assets/templates ./assets/templates
COPY assets/src ./assets/src

RUN --mount=type=cache,target=./npm npm run build

##################################################
#
# Build image
#
# Ok, now we have local base image with python and our system dependencies on.
# We'll use this as the base for our builder image, where we'll build and
# install any python packages needed.
#
# We use a separate, disposable build image to avoid carrying the build
# dependencies into the production image.
FROM base-python AS builder

# Install any system build dependencies
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=bind,source=docker/dependencies-build.txt,target=/dependencies-build.txt \
    /root/docker-apt-install.sh /dependencies-build.txt

# Pull in the binaries from our pinned uv-cli stage
COPY --from=uv-cli /uv /uvx /usr/local/bin/

RUN uv venv

# The cache mount means a) /root/.cache is not in the image, and b) it's preserved
# between docker builds locally, for faster dev rebuild.
# Setting --directory /root lets `uv` detect the pyproject.toml, uv.lock etc.
RUN --mount=type=cache,target=/root/.cache \
    --mount=type=bind,source=pyproject.toml,target=/root/pyproject.toml \
    --mount=type=bind,source=uv.lock,target=/root/uv.lock \
    uv sync --frozen --no-dev --no-install-project --directory /root

##################################################
#
# Base project image
#
# Ok, we've built everything we need, build an image with all dependencies but
# no code.
#
# Not including the code at this stage has two benefits:
#
# 1) this image only rebuilds when the handlful of files needed to build
#    the base project image changes. If we do `COPY . /app` now, this will
#    rebuild when *any* file changes.
#
# 2) Ensures we *have* to mount the volume for dev image, as there's no embedded
#    version of the code. Otherwise, we could end up accidentally using the
#    version of the code included when the prod image was built.
FROM base-python AS airlock-base

RUN mkdir -p /app
WORKDIR /app

# copy venv over from builder image. These will have root:root ownership, but
# are readable by all.
COPY --from=builder /opt/venv /opt/venv

# copy node assets over from node-builder image. These will have root:root ownership, but
# are readable by all.
COPY --from=node-builder /usr/src/app/assets/out /opt/assets

# Asset and staticfile management
#
# We support two dev environment side-by-side. Docker-based, which is the same
# everywhere, and the same as production. And host-based, which can mean OSX
# or some version linux. Windows not supported, currently
#
# Both dev envs have tooling and config to generate js assets and to run
# django's collectstatic. In the host-based tooling, these are written to
# ./assets/dist and ./staticfiles respectively. When we run the dev image, we
# mount the host directory in, which means that by default, the tooling when
# run inside the dev container would write to those same directories, which
# would overwrite the host's files.
#
# This is problem because if the host is OSX, this will break some npm stuff,
# and the collectfiles will race between the host-based and docker-based to
# write those files. There can also be file permissions issues.
#
# To solve this we parameterize those locations via BUILT_ASSETS and
# STATIC_ROOT env vars, and within the docker image set them to paths outside
# of /app, so they do not clash in anyway with the host-based generated files.
#
# We do this in both dev and prod docker images. The ./staticfiles and
# ./assets/dist directories are excluded in .dockerignore, so the prod image
# only ever has the files it builds. As the app dir is mounted in the dev
# image, it can see them, but the use of these separate directories means it
# will never use them.
ENV VIRTUAL_ENV=/opt/venv/  \
    PATH="/opt/venv/bin:$PATH"  \
    PYTHONPATH=/app  \
    BUILT_ASSETS=/opt/assets  \
    STATIC_ROOT=/opt/staticfiles \
    DJANGO_SETTINGS_MODULE="airlock.settings" \
    ASSETS_DEV_MODE=False \
    OTEL_SERVICE_NAME="airlock"

##################################################
#
# Production image
#
# Copy code in, add proper metadata
FROM airlock-base AS airlock-prod

# Adjust this metadata to fit project. Note that the base-docker image does set
# some basic metadata.
LABEL org.opencontainers.image.title="airlock" \
      org.opencontainers.image.description="OpenSAFELY airlock" \
      org.opencontainers.image.source="https://github.com/opensafely-core/airlock"

# Expose the port
EXPOSE 8000

# copy application code
COPY . /app

# We use --init to run the docker cotnainer, which mounts tini binary, and uses it as PID 1,
# but only in single-process mode. Setting TINI_KILL_PROCESS_GROUP means it will forward signals
# to the background process (running the airlock file uploader) as well as the main process.
ENV TINI_KILL_PROCESS_GROUP=1

ENTRYPOINT ["/app/docker/entrypoints/prod.sh"]

# We set command rather than entrypoint, to make it easier to run different
# things from the cli
CMD ["gunicorn", "--config", "gunicorn.conf.py", "airlock.wsgi"]

# finally, tag with build information. These will change regularly, therefore
# we do them as the last action.
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.created=$BUILD_DATE
ARG GITREF=unknown
LABEL org.opencontainers.image.revision=$GITREF

# 10000 corresponds to jobrunner user in backend-server
ARG USERID=10000
ARG GROUPID=10000

# create workdir with permissions that the jobrunner user can access
# this can be used for prod-like tests, but persistent storage should be 
# mounted for actual production
RUN mkdir /workdir \
 && chown ${USERID}:${GROUPID} /workdir

USER ${USERID}:${GROUPID}

##################################################
#
# Dev image
#
# Now we build a dev image from our airlock-base image. This is basically
# installing dev dependencies and changing the entrypoint
#
FROM airlock-base AS airlock-dev

# Pull in the binaries from our pinned uv-cli stage
COPY --from=uv-cli /uv /uvx /bin/

# install development requirements
# we want to additionally sync dev dependencies into the venv;
# prod dependencies are already installed and we don't want to remove them.
RUN --mount=type=cache,target=/root/.cache \
    --mount=type=bind,source=pyproject.toml,target=/root/pyproject.toml \
    --mount=type=bind,source=uv.lock,target=/root/uv.lock \
    uv sync --frozen --no-install-project --directory /root

# Install playwright chromium dependencies. This needs to be done AFTER 
# the python playwright package is installed. Although we don't use the default playwright
# chromium browser, this gets us the system dependencies
RUN playwright install-deps chromium

# Override ENTRYPOINT rather than CMD so we can pass arbitrary commands to the entrypoint script
ENTRYPOINT ["/app/docker/entrypoints/dev.sh"]

# Run as non root user. Required when building image.
ARG USERID
ARG GROUPID
USER ${USERID}:${GROUPID}
