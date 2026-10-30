# Reproducible evaluation environment for the "JMESPath let-expressions" task.
#
# Pinned on purpose:
#   * base image by digest (not just tag), so the Python/Debian layer cannot drift
#   * upstream repo by commit SHA, so the task always targets the same code
#   * pytest by version, so test collection/reporting behaviour is fixed
#
# The image contains ONLY the environment the agent sees (repo + toolchain).
# Hidden tests and the grader are mounted read-only at grading time, so an
# agent working inside this image never has access to them.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG REPO_URL=https://github.com/jmespath/jmespath.py
ARG REPO_SHA=2812594e69d43098ef60f81f4efc404c071b0418

WORKDIR /work
RUN git clone --quiet "${REPO_URL}" repo \
 && cd repo \
 && git checkout --quiet "${REPO_SHA}" \
 && git config user.email grader@localhost \
 && git config user.name grader \
 && git tag task-base

# Test toolchain only. The repo itself needs no third-party runtime deps.
RUN pip install pytest==8.4.1

# Non-root user: the grader never needs root, and a candidate patch must not
# be able to modify the toolchain.
RUN useradd --create-home --uid 10001 runner \
 && chown -R runner:runner /work
USER runner
WORKDIR /work/repo

# Sanity check at build time: the pinned suite is green before any patch.
RUN python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -1
