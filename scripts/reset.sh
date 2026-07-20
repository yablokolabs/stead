#!/usr/bin/env bash
# Delete Stead demo DATA ONLY. Requires the exact database file path and typed
# confirmation. Refuses directories, refuses anything outside STEAD_DEMO_HOME.
# Touches no profile, no service, no Hermes configuration, nothing of Polaris.
set -euo pipefail

DEMO_HOME="${STEAD_DEMO_HOME:-${HOME}/.stead-demo}"
EXPECTED="${DEMO_HOME}/stead.sqlite"

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") <path-to-stead.sqlite>

Deletes ONLY the Stead demo database and its WAL/SHM sidecars.
The path must be exactly: ${EXPECTED}

This script will refuse a directory, a glob, or any other path.
EOF
    exit 2
}

[[ $# -eq 1 ]] || usage
TARGET="$1"

[[ -d "${TARGET}" ]] && { echo "REFUSED: '${TARGET}' is a directory." >&2; exit 1; }

TARGET_ABS="$(readlink -m -- "${TARGET}")"
EXPECTED_ABS="$(readlink -m -- "${EXPECTED}")"

if [[ "${TARGET_ABS}" != "${EXPECTED_ABS}" ]]; then
    echo "REFUSED: this script only deletes the Stead demo database." >&2
    echo "  you gave:  ${TARGET_ABS}" >&2
    echo "  expected:  ${EXPECTED_ABS}" >&2
    exit 1
fi

[[ -f "${TARGET_ABS}" ]] || { echo "Nothing to do: ${TARGET_ABS} does not exist."; exit 0; }

echo "About to permanently delete Stead demo data:"
for F in "${TARGET_ABS}" "${TARGET_ABS}-wal" "${TARGET_ABS}-shm"; do
    [[ -e "${F}" ]] && printf '  %s  (%s bytes)\n' "${F}" "$(stat -c %s "${F}")"
done
echo
echo "This erases Kerstin's household facts, goals, tasks, reminders and audit log."
echo "It does NOT touch the profile, the service, or any other agent."
echo
read -r -p "Type DELETE STEAD DEMO DATA to confirm: " REPLY_TEXT

if [[ "${REPLY_TEXT}" != "DELETE STEAD DEMO DATA" ]]; then
    echo "Aborted — nothing was deleted."
    exit 1
fi

rm -f -- "${TARGET_ABS}" "${TARGET_ABS}-wal" "${TARGET_ABS}-shm"
echo "Deleted. Run scripts/setup.sh to recreate an empty database."
