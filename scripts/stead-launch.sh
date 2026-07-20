#!/usr/bin/env bash
# Trusted launcher for the Stead gateway.
#
# Enforcement, not warning: every model-provider credential that might be
# hanging around in the environment is removed BEFORE Stead configuration is
# loaded, so nothing belonging to Claude Code, Polaris, or another profile can
# be inherited. The only credentials Stead ever sees are the ones in its own
# protected env file.
#
# Fails closed. Never prints a credential value.
# Safe whether launched by systemd or by hand.
set -euo pipefail

PROFILE="stead-kerstin-demo"
DEMO_HOME="${STEAD_DEMO_HOME:-/home/azureuser/.stead-demo}"
ENV_FILE="${DEMO_HOME}/.env"
HERMES_PY="/home/azureuser/.hermes/hermes-agent/venv/bin/python"
PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"

die() { echo "stead-launch: FATAL: $*" >&2; exit 78; }   # 78 = do not restart

# --- 1. Scrub ambient provider credentials ----------------------------------
# Anything a launching shell, a parent service, or another agent might have set.
for VAR in \
    ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL \
    CLAUDE_API_KEY CLAUDE_CODE_OAUTH_TOKEN CLAUDECODE CLAUDE_CODE_ENTRYPOINT \
    OPENAI_API_KEY OPENAI_BASE_URL OPENAI_ORGANIZATION \
    GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_APPLICATION_CREDENTIALS \
    XAI_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY MISTRAL_API_KEY \
    GROQ_API_KEY TOGETHER_API_KEY FIREWORKS_API_KEY NOUS_API_KEY \
    MINIMAX_API_KEY QWEN_API_KEY DASHSCOPE_API_KEY KIMI_API_KEY \
    COPILOT_GITHUB_TOKEN GH_TOKEN GITHUB_TOKEN \
    AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AZURE_OPENAI_API_KEY \
    SEARXNG_URL FIRECRAWL_API_KEY FIRECRAWL_API_URL PARALLEL_API_KEY \
    TAVILY_API_KEY EXA_API_KEY BRAVE_SEARCH_API_KEY
do
    unset "${VAR}" 2>/dev/null || true
done

# --- 2. Load ONLY the Stead env file ----------------------------------------
[[ -f "${ENV_FILE}" ]] || die "no Stead environment file at ${ENV_FILE}"

PERM="$(stat -c '%a' "${ENV_FILE}")"
[[ "${PERM}" == "600" ]] || die "${ENV_FILE} is mode ${PERM}, expected 600"

OWNER="$(stat -c '%U' "${ENV_FILE}")"
[[ "${OWNER}" == "$(id -un)" ]] || die "${ENV_FILE} is owned by ${OWNER}, expected $(id -un)"

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

# --- 3. Pin provider and model ----------------------------------------------
PROVIDER="${STEAD_MODEL_PROVIDER:-}"
MODEL="${STEAD_MODEL_NAME:-}"
[[ -n "${PROVIDER}" ]] || die "STEAD_MODEL_PROVIDER is not set"
[[ -n "${MODEL}"    ]] || die "STEAD_MODEL_NAME is not set"

case "${PROVIDER}" in
    anthropic) NEEDED="ANTHROPIC_API_KEY"; OTHER="GEMINI_API_KEY" ;;
    gemini)    NEEDED="GEMINI_API_KEY";    OTHER="ANTHROPIC_API_KEY" ;;
    *) die "STEAD_MODEL_PROVIDER='${PROVIDER}' is not supported (anthropic|gemini)" ;;
esac

# --- 4. Fail closed on a missing or ambiguous credential ---------------------
[[ -n "${!NEEDED:-}" ]] || die "${NEEDED} is required for provider '${PROVIDER}' but is not set in ${ENV_FILE}"

if [[ -n "${!OTHER:-}" ]]; then
    die "both ${NEEDED} and ${OTHER} are set — refusing to guess which provider to use"
fi

# Reject a Claude Code subscription token masquerading as an application key.
if [[ "${PROVIDER}" == "anthropic" && "${ANTHROPIC_API_KEY}" != sk-ant-* ]]; then
    die "ANTHROPIC_API_KEY is not an application API key (expected sk-ant- prefix)"
fi

# --- 5. Validate the model against the installed catalogue ------------------
CATALOGUE="${HERMES_MODELS_CATALOGUE:-/home/azureuser/.hermes/hermes-agent/hermes_cli/models.py}"
if [[ -f "${CATALOGUE}" ]] && ! grep -qF "\"${MODEL}\"" "${CATALOGUE}"; then
    die "STEAD_MODEL_NAME='${MODEL}' is not in this Hermes build's catalogue"
fi

# --- 5b. Enforce the pinned model, don't just validate it -------------------
# Validating STEAD_MODEL_NAME without applying it is worthless: Hermes falls
# back to its built-in default (observed: claude-fable-5) when the profile has
# no model key, so the pinned value is silently ignored. Reconcile the profile
# config with the env file on every start.
CONFIGURED_MODEL="$(sed -nE 's/^model:[[:space:]]*//p' "${PROFILE_HOME}/config.yaml" 2>/dev/null | head -1 | tr -d '"'"'"' ')"
CONFIGURED_PROVIDER="$(sed -nE 's/^provider:[[:space:]]*//p' "${PROFILE_HOME}/config.yaml" 2>/dev/null | head -1 | tr -d '"'"'"' ')"

if [[ "${CONFIGURED_MODEL}" != "${MODEL}" || "${CONFIGURED_PROVIDER}" != "${PROVIDER}" ]]; then
    die "profile config (model='${CONFIGURED_MODEL}' provider='${CONFIGURED_PROVIDER}') does not match ${ENV_FILE} (model='${MODEL}' provider='${PROVIDER}'). Reconcile with: stead-kerstin-demo config set model ${MODEL} && stead-kerstin-demo config set provider ${PROVIDER}"
fi

# --- 6. Telegram destination -------------------------------------------------
[[ -n "${STEAD_TELEGRAM_BOT_TOKEN:-}"   ]] || die "STEAD_TELEGRAM_BOT_TOKEN is not set"
[[ -n "${STEAD_ALLOWED_TELEGRAM_IDS:-}" ]] || die "STEAD_ALLOWED_TELEGRAM_IDS is not set"

# Hermes reads these names; map from the Stead-prefixed ones.
export TELEGRAM_BOT_TOKEN="${STEAD_TELEGRAM_BOT_TOKEN}"
export TELEGRAM_ALLOWED_USERS="${STEAD_ALLOWED_TELEGRAM_IDS}"
export HERMES_HOME="${PROFILE_HOME}"
export TZ="${STEAD_TIMEZONE:-Europe/London}"

echo "stead-launch: provider=${PROVIDER} model=${MODEL} profile=${PROFILE} — credentials loaded from ${ENV_FILE}"

# Verification hook: run every check, then stop without starting anything.
if [[ -n "${EXEC_GUARD:-}" ]]; then
    echo "stead-launch: EXEC_GUARD set — configuration valid, gateway NOT started"
    exit 0
fi

exec "${HERMES_PY}" -m hermes_cli.main --profile "${PROFILE}" gateway run
