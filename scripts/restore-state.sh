#!/usr/bin/env bash
# Restore a private Stead migration bundle into an existing profile.
set -euo pipefail

PROFILE="stead-kerstin-demo"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_HOME="${STEAD_DEMO_HOME:-${HOME}/.stead-demo}"
PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"
FORCE_ARGS=()

if [[ "${1:-}" == "--force" ]]; then
    FORCE_ARGS=(--force)
    shift
fi
[[ $# -eq 1 ]] || {
    echo "Usage: scripts/restore-state.sh [--force] /private/path/stead-private-*.tar.gz" >&2
    exit 2
}
[[ -x "${REPO}/.venv/bin/python" ]] || {
    echo "ERROR: run scripts/setup.sh before restoring state" >&2
    exit 1
}

exec "${REPO}/.venv/bin/python" -m stead_mcp.migration restore "$1" \
    --demo-home "${DEMO_HOME}" \
    --profile-home "${PROFILE_HOME}" \
    "${FORCE_ARGS[@]}"
