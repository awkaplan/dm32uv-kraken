#!/bin/sh
# One-time GitHub Pages setup for the web installer, once the repository is public (Pages is not available
# on a private repository on a free plan). Needs the GitHub CLI, logged in as an admin of the repository.
#
#   tools/setup_pages.sh [OWNER/REPO]        (default: the repository of this checkout's origin)
#
# 1. Turns Pages on with GitHub Actions as its source (.github/workflows/pages.yml deploys it).
# 2. Lets release tags deploy to the github-pages environment: by default it only accepts the default
#    branch, and the workflow runs on the release's tag.
# 3. Protects release tags (v*) from being moved or deleted, so a release's tag keeps pointing at the
#    page it was published with.
# Also turn on Settings > General > Releases > "Enable release immutability": then a published release's
# files cannot be replaced either (this script does not change that setting).
# Then publish: Actions > Web installer > Run workflow (or publish a release).
set -eu
REPO=${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}
echo "Repository: $REPO"
if [ "$(gh repo view "$REPO" --json visibility --jq .visibility)" != "PUBLIC" ]; then
    echo "tools/setup_pages.sh: $REPO is not public yet; make it public first" >&2
    exit 1
fi
if gh api "repos/$REPO/pages" >/dev/null 2>&1; then
    gh api -X PUT "repos/$REPO/pages" -f build_type=workflow >/dev/null
    echo "Pages: already on; source set to GitHub Actions"
else
    gh api -X POST "repos/$REPO/pages" -f build_type=workflow >/dev/null
    echo "Pages: on, source GitHub Actions"
fi
# the environment exists once Pages is on; allow v* tags as well as the branches it already allows
gh api -X PUT "repos/$REPO/environments/github-pages" --input - >/dev/null <<'EOF'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
EOF
DEFAULT=$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)
allow() {  # TYPE NAME; an existing identical rule is fine
    gh api -X POST "repos/$REPO/environments/github-pages/deployment-branch-policies" \
        -f type="$1" -f name="$2" >/dev/null 2>&1 || true
    echo "github-pages environment: $1 $2 may deploy"
}
allow branch "$DEFAULT"
allow tag 'v*'
# release tags: no moving, no deleting
if gh api "repos/$REPO/rulesets" --jq '.[].name' 2>/dev/null | grep -qx "release tags"; then
    echo "ruleset: release tags already protected"
else
    printf '%s' '{"name": "release tags", "target": "tag", "enforcement": "active",
 "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
 "rules": [{"type": "deletion"}, {"type": "update"}, {"type": "non_fast_forward"}]}' |
        gh api -X POST "repos/$REPO/rulesets" --input - >/dev/null
    echo "ruleset: v* tags can be created, but not moved or deleted"
fi
echo "Next: Settings > General > Releases > Enable release immutability (published release files then cannot change)"
echo "Site: $(gh api "repos/$REPO/pages" --jq .html_url)"
