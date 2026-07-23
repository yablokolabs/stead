#!/usr/bin/env bash
# Create the private migration bundle. It contains secrets and household data.
set -euo pipefail

PROFILE="stead-kerstin-demo"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_HOME="${STEAD_DEMO_HOME:-${HOME}/.stead-demo}"
PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT="${1:-${HOME}/stead-backups/stead-private-${STAMP}.tar.gz}"

[[ -x "${REPO}/.venv/bin/python" ]] || {
    echo "ERROR: run scripts/setup.sh before exporting state" >&2
    exit 1
}

umask 077
exec "${REPO}/.venv/bin/python" -m stead_mcp.migration backup \
    --output "${OUTPUT}" \
    --demo-home "${DEMO_HOME}" \
    --profile-home "${PROFILE_HOME}" \
    --repo "${REPO}"
