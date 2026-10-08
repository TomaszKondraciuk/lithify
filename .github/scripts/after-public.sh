#!/bin/sh
# Turn on what GitHub offers only for public repositories, once, right after going public
# (docs/releasing.md). Needs the GitHub CLI, signed in as an owner of the repository.
#
#   sh .github/scripts/after-public.sh <owner>/<repository>
set -eu
R=${1:?usage: after-public.sh <owner>/<repository>}

# Secrets pushed by mistake are found, and refused at push time.
gh api -X PATCH "repos/$R" \
  -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled' >/dev/null
# main can be neither rewritten nor deleted (direct pushes stay possible: CI checks them after).
gh api -X POST "repos/$R/rulesets" --input - >/dev/null <<'JSON'
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "rules": [{"type": "non_fast_forward"}, {"type": "deletion"}]
}
JSON
echo "done. By hand: Settings -> General -> Social preview -> upload docs/images/social-preview.png"
