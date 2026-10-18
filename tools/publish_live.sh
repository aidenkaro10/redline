#!/bin/bash
# Push a branch to the PUBLIC GitHub repo, with Aiden's phone number scrubbed from all history.
# Same as main's tools/publish_public.sh, but you pick the branches. Render deploys "live".
#
# Why a script: old commits contain a phone number, so the public repo gets a rewritten copy
# of main instead of a plain `git push`. The rewrite is deterministic, so every run produces
# the same commit IDs for old commits and the push is a normal fast-forward.
#
# Run from the project folder:   bash tools/publish_live.sh [local-branch] [remote-branch]
# Default: this checkout's current branch -> live
set -euo pipefail

SRC_BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
DST_BRANCH="${2:-live}"

REPO_URL="https://github.com/aidenkaro10/planit-control.git"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. Copy only that branch into a temporary bare repo.
git clone -q --no-local --single-branch --branch "$SRC_BRANCH" --bare "$(pwd)" "$WORK/src.git"
git init -q --bare "$WORK/clean.git"

# 2. Rewrite history: same-length replacement keeps git's byte counts valid.
(cd "$WORK/src.git" && git fast-export --all --signed-tags=strip --tag-of-filtered-object=drop) \
  | LC_ALL=C sed 's/3525550199/3525550199/g' \
  | (cd "$WORK/clean.git" && git fast-import --quiet)

# 3. Refuse to publish if anything sensitive is left.
cd "$WORK/clean.git"
if git log -p --all | grep -qE '3525550199|sk-ant-[A-Za-z0-9_-]{20,}|sk_[a-f0-9]{40,}|AC[a-f0-9]{32}|phnum_[a-z0-9]{20,}'; then
  echo "STOPPED: a phone number or key is still in the history. Nothing was pushed." >&2
  exit 1
fi
if git ls-tree -r "$SRC_BRANCH" --name-only | grep -qE '(^|/)\.env$'; then
  echo "STOPPED: a .env file is tracked. Nothing was pushed." >&2
  exit 1
fi

# 4. Push (DRY_RUN=1 stops here, after all the checks).
if [ "${DRY_RUN:-0}" = "1" ]; then echo "Dry run: checks passed, nothing pushed."; exit 0; fi
git push -q "$REPO_URL" "$SRC_BRANCH:$DST_BRANCH"
echo "Published $SRC_BRANCH to $REPO_URL as $DST_BRANCH"
