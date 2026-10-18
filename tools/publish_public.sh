#!/bin/bash
# Push main to the PUBLIC GitHub repo, with Aiden's phone number scrubbed from all history.
#
# Why a script: old commits contain a phone number, so the public repo gets a rewritten copy
# of main instead of a plain `git push`. The rewrite is deterministic, so every run produces
# the same commit IDs for old commits and the push is a normal fast-forward.
#
# Run from the project folder:   bash tools/publish_public.sh
set -euo pipefail

REPO_URL="https://github.com/aidenkaro10/redline.git"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. Copy only main into a temporary bare repo.
git clone -q --no-local --single-branch --branch main --bare "$(pwd)" "$WORK/src.git"
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
if git ls-tree -r main --name-only | grep -qE '(^|/)\.env$'; then
  echo "STOPPED: a .env file is tracked. Nothing was pushed." >&2
  exit 1
fi

# 4. Push.
git push -q "$REPO_URL" main:main
echo "Published main to $REPO_URL"
