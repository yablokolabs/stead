#!/usr/bin/env bash
# Clone-to-running bootstrap for a new Linux VM with Hermes Agent installed.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE=""
START=1
SYSTEMD_ARGS=()
HERMES_BIN="${STEAD_HERMES_BIN:-$(command -v hermes 2>/dev/null || true)}"
UV_BIN="${STEAD_UV_BIN:-$(command -v uv 2>/dev/null || true)}"

usage() {
    cat <<'EOF'
Usage: scripts/bootstrap-vm.sh [--restore BUNDLE] [--no-start] [--no-systemd]

Prerequisites: Hermes Agent and uv installed for the current user.
The private BUNDLE is produced by scripts/export-state.sh on the old VM.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --restore)
            [[ $# -ge 2 ]] || { echo "ERROR: --restore needs a path" >&2; exit 2; }
            BUNDLE="$2"; shift ;;
        --no-start) START=0 ;;
        --no-systemd) SYSTEMD_ARGS=(--no-systemd); START=0 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

[[ -n "${HERMES_BIN}" && -x "${HERMES_BIN}" ]] || {
    echo "ERROR: Hermes Agent is not installed." >&2
    echo "Install it from https://hermes-agent.nousresearch.com/docs, then rerun." >&2
    exit 1
}
[[ -n "${UV_BIN}" && -x "${UV_BIN}" ]] || {
    echo "ERROR: uv is not installed (the Hermes installer normally provides it)." >&2
    exit 1
}

"${REPO}/scripts/setup.sh" "${SYSTEMD_ARGS[@]}"
if [[ -n "${BUNDLE}" ]]; then
    "${REPO}/scripts/restore-state.sh" --force "${BUNDLE}"
fi

# Re-render after restore so model/provider pins and all absolute paths match
# the new host rather than values from the old VM.
"${REPO}/scripts/setup.sh" "${SYSTEMD_ARGS[@]}"

if [[ "${START}" -eq 1 ]]; then
    if grep -qE '^[[:space:]]*(export[[:space:]]+)?SEARXNG_URL=' \
        "${STEAD_DEMO_HOME:-${HOME}/.stead-demo}/.env" 2>/dev/null; then
        "${REPO}/scripts/setup-searxng.sh" --start
    fi
    "${REPO}/scripts/check-secrets.sh"
    "${REPO}/scripts/setup.sh" --start
    "${REPO}/scripts/verify.sh"
else
    echo "Bootstrap complete without starting Stead."
fi
