#!/usr/bin/env bash
# Idempotent setup for the Stead Preview demo profile.
# Safe to re-run. Touches only the stead-kerstin-demo profile and STEAD_DEMO_HOME.
# Never prints a secret value.
set -euo pipefail

PROFILE="stead-kerstin-demo"
PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"
DEMO_HOME="${STEAD_DEMO_HOME:-${HOME}/.stead-demo}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '  %s\n' "$1"; }

if [[ ! -d "${PROFILE_HOME}" ]]; then
    echo "ERROR: profile ${PROFILE} does not exist." >&2
    echo "Create it first:  hermes profile create ${PROFILE} --no-skills" >&2
    exit 1
fi

echo "Stead Preview setup"
echo "  profile:   ${PROFILE_HOME}"
echo "  workspace: ${DEMO_HOME}"
echo

# 1. Workspace, owner-only.
install -d -m 700 "${DEMO_HOME}"
say "workspace ready (mode 700)"

# 2. Protected environment file — created empty, never populated by this script.
if [[ ! -f "${DEMO_HOME}/.env" ]]; then
    install -m 600 /dev/null "${DEMO_HOME}/.env"
    say "created empty ${DEMO_HOME}/.env (mode 600) — fill it in manually"
else
    chmod 600 "${DEMO_HOME}/.env"
    say "environment file present (mode 600)"
fi

# 3. Stead identity.
install -m 600 "${REPO}/identity/SOUL.md" "${PROFILE_HOME}/SOUL.md"
say "installed Stead identity"

# 4. Household skill.
install -d -m 700 "${PROFILE_HOME}/skills/stead-household-chief-of-staff"
install -m 600 "${REPO}/skills/stead-household-chief-of-staff/SKILL.md" \
    "${PROFILE_HOME}/skills/stead-household-chief-of-staff/SKILL.md"
say "installed stead-household-chief-of-staff skill"

# 5. Database schema (idempotent).
STEAD_DEMO_HOME="${DEMO_HOME}" PYTHONPATH="${REPO}" \
    "${REPO}/.venv/bin/python" -c "from stead_mcp.server import build_store; build_store().close()"
chmod 600 "${DEMO_HOME}/stead.sqlite"
say "database migrated (mode 600)"

echo
echo "Setup complete. Next: fill in ${DEMO_HOME}/.env, then run scripts/verify.sh"
