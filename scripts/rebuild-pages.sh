#!/usr/bin/env bash
# Rebuild djev-patched/ (server.py and the four pages) from the pinned upstream djev-run commit.
# The result should be byte-identical to what is committed; `git diff djev-patched` should show nothing.
set -euo pipefail
cd "$(dirname "$0")/.."

UPSTREAM_REPO="https://github.com/taeold/djev-run.git"
UPSTREAM_COMMIT="35a6ff02edcc8fbb1079142a16170405a957ef0e"   # the djev-run source the pages were built from

WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT
git clone -q "$UPSTREAM_REPO" "$WORK/djev-run"
git -C "$WORK/djev-run" checkout -q "$UPSTREAM_COMMIT"
python3 -I patch_demos.py "$WORK/djev-run" djev-patched
echo "rebuilt djev-patched/ from djev-run@${UPSTREAM_COMMIT:0:7}"
