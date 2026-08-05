#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OWNER="${PRAZYCRON_GITHUB_OWNER:-Prazynka}"
REPO_NAME="${PRAZYCRON_GITHUB_REPO:-prazycron}"
REPOSITORY="$OWNER/$REPO_NAME"
DESCRIPTION="A graphical and terminal manager for Linux Cron jobs and systemd timers. Cron made simple."
VERSION_ARG="${1:-}"

log() { printf '\n==> %s\n' "$*"; }
fail() { echo "Error: $*" >&2; exit 1; }

if [[ -n "$VERSION_ARG" ]]; then
  VERSION_ARG="${VERSION_ARG#v}"
  python3 scripts/set-version.py "$VERSION_ARG"
fi

VERSION="$(python3 -c 'from prazycron import __version__; print(__version__)')"
TAG="v$VERSION"
NOTES="RELEASE-NOTES-$VERSION.md"
[[ -f "$NOTES" ]] || fail "Missing $NOTES"

log "Preparing dependencies and GitHub authentication"
./scripts/first-run.sh --install

log "Running release checks and building assets"
./scripts/make-release.sh

log "Preparing local Git repository"
if [[ ! -d .git ]]; then
  git init
fi
git branch -M main

if ! git config user.name >/dev/null; then
  LOGIN="$(gh api user --jq .login)"
  git config user.name "$LOGIN"
fi
if ! git config user.email >/dev/null; then
  LOGIN="$(gh api user --jq .login)"
  USER_ID="$(gh api user --jq .id)"
  git config user.email "${USER_ID}+${LOGIN}@users.noreply.github.com"
fi

git add -A
if ! git diff --cached --quiet; then
  if git rev-parse --verify HEAD >/dev/null 2>&1; then
    git commit -m "Release PrazyCron $VERSION"
  else
    git commit -m "Initial public release of PrazyCron $VERSION"
  fi
fi

log "Creating or updating GitHub repository $REPOSITORY"
if gh repo view "$REPOSITORY" >/dev/null 2>&1; then
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "https://github.com/$REPOSITORY.git"
  else
    git remote add origin "https://github.com/$REPOSITORY.git"
  fi
  git push -u origin main
else
  gh repo create "$REPOSITORY" \
    --public \
    --source=. \
    --remote=origin \
    --description "$DESCRIPTION" \
    --disable-wiki \
    --push
fi

gh repo edit "$REPOSITORY" \
  --description "$DESCRIPTION" \
  --homepage "https://github.com/$REPOSITORY" \
  --add-topic cron \
  --add-topic crontab \
  --add-topic systemd \
  --add-topic scheduler \
  --add-topic linux \
  --add-topic python \
  --add-topic tkinter \
  --add-topic tui >/dev/null

# Best effort: configure repository features and let release workflows write assets.
gh api --method PATCH "repos/$REPOSITORY" \
  -F has_issues=true \
  -F has_projects=false \
  -F has_wiki=false >/dev/null 2>&1 || true

# Best effort: let release workflows write assets and enable private vulnerability reporting.
gh api --method PUT "repos/$REPOSITORY/actions/permissions/workflow" \
  -f default_workflow_permissions=write \
  -F can_approve_pull_request_reviews=false >/dev/null 2>&1 || true
gh api --method PUT "repos/$REPOSITORY/private-vulnerability-reporting" >/dev/null 2>&1 || true

log "Publishing tag $TAG"
if git rev-parse "$TAG" >/dev/null 2>&1; then
  git tag -d "$TAG" >/dev/null
fi
git tag -a "$TAG" -m "PrazyCron $VERSION"
git push origin main
git push origin "$TAG" --force

log "Waiting for GitHub Actions release workflow"
RUN_ID=""
for _ in $(seq 1 30); do
  RUN_ID="$(gh run list --repo "$REPOSITORY" --workflow release.yml --limit 10 \
    --json databaseId,headBranch,event,status \
    --jq '.[] | select(.headBranch == "'"$TAG"'") | .databaseId' | head -n1 || true)"
  [[ -n "$RUN_ID" ]] && break
  sleep 2
done

if [[ -n "$RUN_ID" ]]; then
  if ! gh run watch "$RUN_ID" --repo "$REPOSITORY" --exit-status; then
    echo "Release workflow failed; creating the release from local assets." >&2
  fi
fi

if ! gh release view "$TAG" --repo "$REPOSITORY" >/dev/null 2>&1; then
  gh release create "$TAG" \
    "dist/prazycron_${VERSION}_all.deb" \
    "dist/prazycron-${VERSION}-source.tar.gz" \
    "dist/SHA256SUMS" \
    --repo "$REPOSITORY" \
    --title "PrazyCron $VERSION" \
    --notes-file "$NOTES" \
    --verify-tag
fi

log "Publication complete"
echo "Repository: https://github.com/$REPOSITORY"
echo "Release:    https://github.com/$REPOSITORY/releases/tag/$TAG"
