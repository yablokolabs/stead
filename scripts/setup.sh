#!/usr/bin/env bash
# Reproducible setup for Stead on a fresh Linux VM.
# Safe to re-run. Never reads another profile or prints a secret value.
set -euo pipefail

PROFILE="stead-kerstin-demo"
PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"
DEMO_HOME="${STEAD_DEMO_HOME:-${HOME}/.stead-demo}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="hermes-gateway-${PROFILE}.service"
HERMES_BIN="${STEAD_HERMES_BIN:-$(command -v hermes 2>/dev/null || true)}"
UV_BIN="${STEAD_UV_BIN:-$(command -v uv 2>/dev/null || true)}"
INSTALL_SYSTEMD=1
START_SERVICE=0

usage() {
    cat <<'EOF'
Usage: scripts/setup.sh [--no-systemd] [--start]

  --no-systemd  Render profile/workspace only (for clean-room testing).
  --start       Validate private config and start Stead after installation.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-systemd) INSTALL_SYSTEMD=0 ;;
        --start) START_SERVICE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

say() { printf '  %s\n' "$1"; }
require() {
    [[ -n "$2" && -x "$2" ]] || {
        echo "ERROR: $1 is required but was not found." >&2
        exit 1
    }
}

require "Hermes Agent (https://hermes-agent.nousresearch.com/docs)" "${HERMES_BIN}"
require "uv (normally installed with Hermes Agent)" "${UV_BIN}"

echo "Stead Preview setup"
echo "  checkout:  ${REPO}"
echo "  profile:   ${PROFILE_HOME}"
echo "  workspace: ${DEMO_HOME}"
echo

# 1. Recreate the exact repository Python environment.
(cd "${REPO}" && "${UV_BIN}" sync --frozen --quiet)
say "Python environment synchronized from uv.lock"

# 2. Create the isolated profile and wrapper without cloning another agent.
if [[ ! -d "${PROFILE_HOME}" ]]; then
    "${HERMES_BIN}" profile create "${PROFILE}" --no-skills
    say "created isolated Hermes profile"
else
    say "isolated Hermes profile already exists"
fi
install -d -m 700 "${PROFILE_HOME}"

# 3. Workspace and protected environment.
install -d -m 700 "${DEMO_HOME}"
if [[ ! -f "${DEMO_HOME}/.env" ]]; then
    install -m 600 /dev/null "${DEMO_HOME}/.env"
    say "created empty ${DEMO_HOME}/.env (mode 600) — fill it or restore a backup"
else
    chmod 600 "${DEMO_HOME}/.env"
    say "protected environment file present (mode 600)"
fi

# 4. Declarative profile surfaces. config.yaml is generated for this checkout,
# so stale absolute paths from the old VM are never restored.
install -m 600 "${REPO}/identity/SOUL.md" "${PROFILE_HOME}/SOUL.md"
install -d -m 700 "${PROFILE_HOME}/skills/stead-household-chief-of-staff"
install -m 600 "${REPO}/skills/stead-household-chief-of-staff/SKILL.md" \
    "${PROFILE_HOME}/skills/stead-household-chief-of-staff/SKILL.md"
PYTHONPATH="${REPO}" "${REPO}/.venv/bin/python" -m stead_mcp.install profile-config \
    --profile-home "${PROFILE_HOME}" \
    --repo "${REPO}" \
    --demo-home "${DEMO_HOME}" \
    --env-file "${DEMO_HOME}/.env" >/dev/null
say "identity, skill, and portable profile config installed"

# 5. Database schema (idempotent, including after a state restore).
STEAD_DEMO_HOME="${DEMO_HOME}" PYTHONPATH="${REPO}" \
    "${REPO}/.venv/bin/python" -c \
    "from stead_mcp.server import build_store; store = build_store(); store.close()"
chmod 600 "${DEMO_HOME}/stead.sqlite"
say "household database migrated (mode 600)"

# 6. Ask Hermes to generate the host-specific base unit, then install our
# credential-enforcing drop-in with this checkout's path.
if [[ "${INSTALL_SYSTEMD}" -eq 1 ]]; then
    "${HERMES_BIN}" --profile "${PROFILE}" gateway install \
        --force --no-start-now --start-on-login
    DROPIN="${HOME}/.config/systemd/user/${UNIT}.d/override.conf"
    PYTHONPATH="${REPO}" "${REPO}/.venv/bin/python" -m stead_mcp.install systemd-dropin \
        --output "${DROPIN}" \
        --launcher "${REPO}/scripts/stead-launch.sh" >/dev/null
    systemctl --user daemon-reload
    systemctl --user enable "${UNIT}" >/dev/null
    say "user service and credential-enforcing drop-in installed"
else
    say "systemd installation skipped"
fi

if [[ "${START_SERVICE}" -eq 1 ]]; then
    [[ "${INSTALL_SYSTEMD}" -eq 1 ]] || {
        echo "ERROR: --start cannot be combined with --no-systemd" >&2
        exit 2
    }
    EXEC_GUARD=1 "${REPO}/scripts/stead-launch.sh" >/dev/null
    systemctl --user restart "${UNIT}"
    say "Stead service started"
fi

echo
echo "Setup complete."
echo "  Fresh install: fill ${DEMO_HOME}/.env, rerun this script, then use --start."
echo "  Migration:     restore the private bundle, rerun this script, then use --start."
