#!/usr/bin/env bash
# Reports whether each required Stead value is PRESENT or MISSING.
# Never prints, echoes or logs a value. Read-only.
set -euo pipefail

DEMO_HOME="${STEAD_DEMO_HOME:-${HOME}/.stead-demo}"
ENV_FILE="${DEMO_HOME}/.env"

REQUIRED=(
    STEAD_TELEGRAM_BOT_TOKEN
    STEAD_ALLOWED_TELEGRAM_IDS
    STEAD_MODEL_PROVIDER
    STEAD_MODEL_NAME
)

echo "Environment file: ${ENV_FILE}"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "  MISSING — run scripts/setup.sh first"
    exit 1
fi

PERMS="$(stat -c '%a %U' "${ENV_FILE}")"
echo "  permissions: ${PERMS} (expected: 600 ${USER})"
[[ "${PERMS%% *}" == "600" ]] || echo "  WARNING: expected mode 600"
echo

present() { grep -qE "^[[:space:]]*(export[[:space:]]+)?$1=[^[:space:]]" "${ENV_FILE}"; }

MISSING=0
for VAR in "${REQUIRED[@]}"; do
    if present "${VAR}"; then
        printf '  %-32s PRESENT\n' "${VAR}"
    else
        printf '  %-32s MISSING\n' "${VAR}"
        MISSING=1
    fi
done

# The provider credential depends on which provider was chosen. Read only the
# provider name — it is not a secret.
PROVIDER="$(grep -E '^[[:space:]]*(export[[:space:]]+)?STEAD_MODEL_PROVIDER=' "${ENV_FILE}" 2>/dev/null \
            | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
case "${PROVIDER}" in
    anthropic) CRED=ANTHROPIC_API_KEY ;;
    gemini)    CRED=GEMINI_API_KEY ;;
    "")        CRED="" ;;
    *)         echo; echo "  Unknown provider '${PROVIDER}' — expected anthropic or gemini"; MISSING=1; CRED="" ;;
esac

if [[ -n "${CRED}" ]]; then
    if present "${CRED}"; then
        printf '  %-32s PRESENT\n' "${CRED}"
    else
        printf '  %-32s MISSING\n' "${CRED}"
        MISSING=1
    fi
fi

# Validate the model name against the installed Hermes catalogue, so a typo or a
# model this Hermes build does not know is caught before the gateway ever starts.
MODEL="$(grep -E '^[[:space:]]*(export[[:space:]]+)?STEAD_MODEL_NAME=' "${ENV_FILE}" 2>/dev/null \
         | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
if [[ -n "${MODEL}" ]]; then
    CATALOGUE="${HERMES_MODELS_CATALOGUE:-${HOME}/.hermes/hermes-agent/hermes_cli/models.py}"
    if [[ -f "${CATALOGUE}" ]] && grep -qF "\"${MODEL}\"" "${CATALOGUE}"; then
        printf '  %-32s VALID (in Hermes catalogue)\n' "model:${MODEL}"
    else
        printf '  %-32s NOT IN CATALOGUE\n' "model:${MODEL}"
        echo "        This Hermes build does not list that model. Check the exact id."
        MISSING=1
    fi
fi

echo
if [[ "${MISSING}" -eq 0 ]]; then
    echo "SECRET GATE: READY"
else
    echo "SECRET GATE: NOT READY — fill the MISSING values above"
    exit 1
fi
