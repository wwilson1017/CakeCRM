#!/usr/bin/env bash
#
# seed-labels.sh — create/normalize the GitHub labels the auto-issues loop uses.
#
# The /auto-issues loop reads and writes a small vocabulary of labels (eligibility,
# lane, park state, settle state, evidence state). This script makes that vocabulary
# reproducible on a fresh clone or fork and enforces the canonical color + description
# on labels that already exist, so the documented color scheme actually holds. It is
# idempotent and safe to re-run.
#
# Behavior per label:
#   - missing  -> created with the canonical color + description
#   - existing -> its color AND description are set to the canonical values (so the
#                 loop's color convention converges even on pre-existing labels)
#
# Target repo: inferred from the current directory's `gh` context (so running it inside
# a fork targets the fork), overridable with --repo <owner/name>.
#
# Requires: the GitHub CLI (`gh`), authenticated with repo scope.
#
# Usage:
#   scripts/seed-labels.sh                 # target the current repo
#   scripts/seed-labels.sh --repo owner/n  # target an explicit repo

set -euo pipefail

REPO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)
      if [ $# -lt 2 ]; then
        echo "error: --repo requires a value (e.g. --repo owner/name)" >&2
        exit 2
      fi
      REPO="$2"
      shift 2
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if ! command -v gh >/dev/null 2>&1; then
  echo "error: the GitHub CLI (gh) is required but not found on PATH." >&2
  exit 1
fi

# Infer the repo from the current gh context when not given explicitly.
if [ -z "$REPO" ]; then
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
fi
echo "Seeding auto-issues labels on: $REPO"

# label  color   description
# Colors are 6-hex without '#'. Kept close to GitHub conventions
# (green=go, yellow=in-flight, orange=parked, red=stop/failed).
LABELS=(
  "greenlit|2ea44f|Human-approved for the /auto-issues loop"
  "no-auto|b60205|Opt this issue OUT of the automated loop"
  "complex|fbca04|Complex lane — a deep planner plans it before implementation"
  "epic|5319e7|Epic lane — human reviews the plan before any code"
  "awaiting-answer|d93f0b|Parked: waiting on a human answer to a clarifying question"
  "awaiting-approval|d93f0b|Parked: waiting on human approval of the plan"
  "awaiting-question|d93f0b|Parked at settle: a review finding needs a human decision"
  "needs-settle|fbca04|PR open but not yet mergeable — re-queued for the settle lane"
  "ready-to-ship|0e8a16|Settled and verified — one click from merge (never auto-merged)"
  "needs-review|fbca04|Settle did not converge (standalone) — needs a human review"
  "auto-failed|b60205|Loop hit a technical dead-end after bounded retries"
  "evidence-posted|0e8a16|Verification evidence recorded on the PR"
  "evidence-failed|b60205|Verification found a bug that was not fixed this pass"
  "reporter-greenlit|0e8a16|Reporter approved the verification evidence"
  # Intake marker for the cake_os sync bot (docs/SYNC.md). The sync-intake
  # workflow also creates this label itself, so keep the color/description here
  # identical to the one in .github/workflows/sync-intake.yml — otherwise the two
  # would take turns overwriting each other.
  "sync-intake|fbca04|Automated cake_os merge intake — triage at first-look"
)

# One list call, reused for the create/update messaging. The high limit + `--force` on
# create below mean the script stays correct even if a repo has more labels than the
# snapshot returns (a snapshot miss just downgrades a label to a create --force, which
# upserts rather than erroring "already exists").
EXISTING="$(gh label list --repo "$REPO" --limit 1000 --json name -q '.[].name')"

created=0
updated=0
for entry in "${LABELS[@]}"; do
  IFS='|' read -r name color desc <<<"$entry"
  if printf '%s\n' "$EXISTING" | grep -Fxq "$name"; then
    gh label edit "$name" --repo "$REPO" --color "$color" --description "$desc" >/dev/null
    echo "  updated: $name"
    updated=$((updated + 1))
  else
    # --force = create-or-update, so a label missing from the snapshot doesn't crash.
    gh label create "$name" --force --repo "$REPO" --color "$color" --description "$desc" >/dev/null
    echo "  created: $name"
    created=$((created + 1))
  fi
done

echo "Done. ${created} created, ${updated} updated (of ${#LABELS[@]} labels)."
