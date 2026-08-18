#!/usr/bin/env bash
# Offline verification for Stead Preview. Read-only: starts nothing, sends nothing.
# Never prints a secret value.
set -uo pipefail

PROFILE="stead-kerstin-demo"
PROFILE_HOME="${HOME}/.hermes/profiles/${PROFILE}"
DEMO_HOME="${STEAD_DEMO_HOME:-${HOME}/.stead-demo}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="hermes-gateway-${PROFILE}.service"
POLARIS_UNIT="hermes-gateway.service"
HERMES_BIN="${STEAD_HERMES_BIN:-$(command -v hermes 2>/dev/null || true)}"
SEARXNG_CONFIG_DIR="${SEARXNG_CONFIG_DIR:-${DEMO_HOME}/searxng}"
SEARXNG_IMAGE="searxng/searxng@sha256:b8ca38ba06eea544d7555e88321e212ddc0d5c3c7de055419cfb2e5c6bf30812"

PASS=0; FAIL=0
ok()   { printf '  PASS  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  FAIL  %s\n' "$1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }

echo "== Polaris is untouched =="
check "Polaris unit is a different unit"    "[[ ${UNIT} != ${POLARIS_UNIT} ]]"
check "Stead does not reference Polaris home" \
      "! grep -q 'HERMES_HOME=${HOME}/.hermes\"' ${HOME}/.config/systemd/user/${UNIT}"
if systemctl --user cat "${POLARIS_UNIT}" >/dev/null 2>&1; then
    check "existing Polaris service still active" \
          "[[ \$(systemctl --user is-active ${POLARIS_UNIT}) == active ]]"
else
    ok "no default/Polaris gateway exists on this host"
fi

echo; echo "== Profile isolation =="
check "profile directory exists"            "[[ -d ${PROFILE_HOME} ]]"
for SUB in .env config.yaml memories sessions cron skills; do
    check "own ${SUB}"                      "[[ -e ${PROFILE_HOME}/${SUB} ]]"
done
check "sticky default not switched"         "[[ ! -f ${HOME}/.hermes/active_profile ]]"

echo; echo "== Forbidden tools are absent =="
for TOOLSET in terminal file code_execution browser computer_use skills delegation cronjob x_search; do
    for PLAT in cli telegram; do
        check "${TOOLSET} disabled (${PLAT})" \
              "${HERMES_BIN} --profile ${PROFILE} tools list --platform ${PLAT} | grep -q '✗ disabled  ${TOOLSET} '"
    done
done

echo; echo "== Web search =="
# The pin is asserted whether or not search is switched on. Without it, Hermes'
# fallback order can resolve a provider this repository never configured — ddgs
# needs only an importable package, no key and no config — and Stead would gain
# search with nothing here changing.
check "web backend pinned to searxng" \
      "grep -qE '^[[:space:]]*(search_)?backend:[[:space:]]*\"?searxng' ${PROFILE_HOME}/config.yaml"

if grep -qE '^[[:space:]]*(export )?SEARXNG_URL=[^[:space:]]' "${DEMO_HOME}/.env" 2>/dev/null; then
    for PLAT in cli telegram; do
        check "web toolset enabled (${PLAT})" \
              "${HERMES_BIN} --profile ${PROFILE} tools list --platform ${PLAT} | grep -q '✓ enabled  web '"
    done
    check "SEARXNG_URL points at loopback" \
          "grep -qE '^[[:space:]]*(export )?SEARXNG_URL=https?://(127\.0\.0\.1|localhost)(:[0-9]+)?/?\$' ${DEMO_HOME}/.env"
    # Checked separately so an absent container fails rather than vacuously
    # satisfying the loopback assertion below.
    check "searxng container is running" \
          "docker ps --filter name=stead-searxng --format '{{.Names}}' | grep -q stead-searxng"
    check "searxng publishes on loopback only" \
          "! docker ps --filter name=stead-searxng --format '{{.Ports}}' | tr ',' '\n' | grep -- '->' | grep -qv '^ *127\.0\.0\.1:'"
    check "searxng uses the pinned image digest" \
          "[[ \$(docker inspect --format '{{.Config.Image}}' stead-searxng) == ${SEARXNG_IMAGE} ]]"
    if [[ "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/searxng"}}{{.Source}}|{{.RW}}{{end}}{{end}}' stead-searxng)" == "${SEARXNG_CONFIG_DIR}|true" ]]; then
        ok "searxng uses the expected config mount"
    else
        bad "searxng uses the expected config mount"
    fi
    check "searxng restart policy is constrained" \
          "[[ \$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' stead-searxng) == unless-stopped ]]"
else
    check "no web tools are exposed while search is off" \
          "! ${HERMES_BIN} --profile ${PROFILE} tools list --platform telegram | grep -q '✓ enabled  web '"
fi

echo; echo "== Scheduling goes only through the trusted path =="
check "no raw cron tool on the agent surface" \
      "! ${HERMES_BIN} --profile ${PROFILE} tools list --platform telegram | grep -q '✓ enabled  cronjob'"
check "trusted scheduler exists"            "[[ -f ${REPO}/stead_mcp/scheduler.py ]]"
check "scheduler pins the Stead profile"    "grep -q 'PROFILE = \"stead-kerstin-demo\"' ${REPO}/stead_mcp/scheduler.py"
check "scheduler uses argv, never a shell"  "grep -q 'shell=False' ${REPO}/stead_mcp/scheduler.py"
check "no fallback provider configured"     "${HERMES_BIN} --profile ${PROFILE} fallback list | grep -q 'No fallback providers'"

echo; echo "== Credential enforcement =="
check "launcher exists and is executable"   "[[ -x ${REPO}/scripts/stead-launch.sh ]]"
# Hermes rewrites its own unit on gateway startup (refresh_systemd_unit_if_needed),
# so the launcher is enforced by a drop-in, which that self-heal does not touch.
check "launcher drop-in exists" \
      "[[ -f ${HOME}/.config/systemd/user/${UNIT}.d/override.conf ]]"
check "effective ExecStart is the launcher" \
      "systemctl --user show -p ExecStart --value ${UNIT} | grep -q 'stead-launch.sh'"
check "service persists the private workspace path" \
      "grep -Fq 'Environment=\"STEAD_DEMO_HOME=${DEMO_HOME}\"' ${HOME}/.config/systemd/user/${UNIT}.d/override.conf"
check "service persists the Hermes executable path" \
      "grep -Fq 'Environment=\"STEAD_HERMES_BIN=${HERMES_BIN}\"' ${HOME}/.config/systemd/user/${UNIT}.d/override.conf"
check "unit will not restart a misconfig"   "grep -q 'RestartPreventExitStatus=78' ${HOME}/.config/systemd/user/${UNIT}"
check "launcher scrubs ambient keys"        "grep -q 'CLAUDE_CODE_OAUTH_TOKEN' ${REPO}/scripts/stead-launch.sh"
check "launcher never reads Claude Code creds" \
      "! grep -q 'credentials.json' ${REPO}/scripts/stead-launch.sh"
check "inherited copilot credential suppressed" \
      "! ${HERMES_BIN} --profile ${PROFILE} auth list | grep -q '^copilot'"
check "inherited qwen credential suppressed" \
      "! ${HERMES_BIN} --profile ${PROFILE} auth list | grep -q '^qwen-oauth'"

echo; echo "== Required tools are present =="
for TOOLSET in memory clarify; do
    check "${TOOLSET} enabled (telegram)" \
          "${HERMES_BIN} --profile ${PROFILE} tools list --platform telegram | grep -q '✓ enabled  ${TOOLSET} '"
done

echo; echo "== Service definition =="
check "unit exists"                         "[[ -f ${HOME}/.config/systemd/user/${UNIT} ]]"
check "unit is bound to the Stead profile"  "grep -q '${PROFILE}' ${HOME}/.config/systemd/user/${UNIT}"
check "launcher pins the Stead profile"     "grep -q -- '--profile \"\${PROFILE}\"' ${REPO}/scripts/stead-launch.sh"
check "launcher hardcodes the profile name" "grep -q 'PROFILE=\"${PROFILE}\"' ${REPO}/scripts/stead-launch.sh"
check "unit restarts on failure"            "grep -q 'Restart=always' ${HOME}/.config/systemd/user/${UNIT}"
check "unit contains no secret material" \
      "! grep -qEi '(TOKEN|API_KEY|SECRET|PASSWORD)=' ${HOME}/.config/systemd/user/${UNIT}"
check "live drop-in has no unresolved placeholder" \
      "! grep -q '@STEAD_LAUNCHER@' ${HOME}/.config/systemd/user/${UNIT}.d/override.conf"

echo; echo "== Workspace and state =="
check "workspace is owner-only"             "[[ \$(stat -c %a ${DEMO_HOME}) == 700 ]]"
check "database is owner-only"              "[[ ! -f ${DEMO_HOME}/stead.sqlite ]] || [[ \$(stat -c %a ${DEMO_HOME}/stead.sqlite) == 600 ]]"
check "env file is owner-only"              "[[ ! -f ${DEMO_HOME}/.env ]] || [[ \$(stat -c %a ${DEMO_HOME}/.env) == 600 ]]"
check "workspace is outside the repo"       "[[ ${DEMO_HOME} != ${REPO}* ]]"

echo; echo "== Git hygiene =="
cd "${REPO}"
check "no .env tracked"                     "! git ls-files --error-unmatch .env"
check "no database tracked"                 "[[ -z \$(git ls-files '*.sqlite*') ]]"
check "no venv tracked"                     "[[ -z \$(git ls-files '.venv*') ]]"
check "Python dependencies are locked"      "[[ -f pyproject.toml && -f uv.lock ]]"
check "runtime has no old-VM home literal" \
      "! grep -Rqs '/home/azureuser' scripts/stead-launch.sh stead_mcp/scheduler.py systemd/override.conf .env.example"
check "private backup tooling is tracked" \
      "[[ -x scripts/export-state.sh && -x scripts/restore-state.sh && -x scripts/bootstrap-vm.sh ]]"
check "production code never names personal creds" \
      "! git grep -qI 'credentials.json' -- stead_mcp scripts ':!scripts/verify.sh'"

echo; echo "== Test suite =="
if "${REPO}/.venv/bin/python" -m pytest "${REPO}/tests" -q >/tmp/stead_pytest.log 2>&1; then
    ok "pytest ($(tail -1 /tmp/stead_pytest.log | tr -d '\n'))"
else
    bad "pytest — see /tmp/stead_pytest.log"
fi

echo; echo "== Secret gate =="
if EXEC_GUARD=1 "${REPO}/scripts/stead-launch.sh" >/dev/null 2>&1; then
    ok "all required values present — gateway may be started"
else
    printf '  WAIT  secrets incomplete — gateway must NOT be started\n'
fi

echo; echo "-----------------------------------------"
printf 'PASS: %d   FAIL: %d\n' "${PASS}" "${FAIL}"
[[ "${FAIL}" -eq 0 ]]
