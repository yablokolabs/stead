#!/usr/bin/env bash
# Prepare a local SearXNG instance for Stead's web search.
#
# By default this writes configuration only. Pass --start during an explicit
# VM bootstrap to create/start the pinned loopback-only container.
#
# SearXNG is a metasearch proxy. It does NOT keep queries on this machine:
# it forwards them to upstream engines and aggregates the results. What it
# avoids is a search-vendor account, an API key, and a billed subscription
# tied to Kerstin. Say that plainly to anyone who asks.
set -euo pipefail

CONFIG_DIR="${SEARXNG_CONFIG_DIR:-${HOME}/.stead-demo/searxng}"
PORT="${SEARXNG_PORT:-8080}"
CONTAINER="stead-searxng"
IMAGE="${SEARXNG_IMAGE:-searxng/searxng@sha256:b8ca38ba06eea544d7555e88321e212ddc0d5c3c7de055419cfb2e5c6bf30812}"
START=0

if [[ "${1:-}" == "--start" ]]; then
    START=1
elif [[ $# -gt 0 ]]; then
    echo "Usage: scripts/setup-searxng.sh [--start]" >&2
    exit 2
fi

say() { printf '  %s\n' "$1"; }

echo "SearXNG configuration for Stead"

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is not installed." >&2
    exit 1
fi

mkdir -p "${CONFIG_DIR}"
chmod 700 "${CONFIG_DIR}"

SETTINGS="${CONFIG_DIR}/settings.yml"
if [[ -f "${SETTINGS}" ]]; then
    say "settings.yml already exists — left untouched"
else
    # SearXNG ships with only 'html' in search.formats. Hermes calls
    # /search?format=json, which 404s until json is enabled here.
    SECRET="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    cat > "${SETTINGS}" <<EOF
use_default_settings: true

server:
  secret_key: "${SECRET}"
  limiter: false
  image_proxy: false

search:
  formats:
    - html
    - json
EOF
    chmod 600 "${SETTINGS}"
    say "wrote settings.yml with a generated secret_key (mode 600)"
fi

cat <<EOF

Review ${SETTINGS}, then start it yourself:

  docker run -d \\
    --name ${CONTAINER} \\
    --restart=unless-stopped \\
    -p 127.0.0.1:${PORT}:8080 \\
    -v "${CONFIG_DIR}:/etc/searxng:rw" \\
    -e "SEARXNG_BASE_URL=http://localhost:${PORT}/" \\
    ${IMAGE}

The 127.0.0.1 prefix is load-bearing. Without it Docker publishes the port on
every interface and this VM becomes an open search proxy.

Then confirm it answers JSON:

  curl -s 'http://127.0.0.1:${PORT}/search?q=test&format=json' | head -c 200

Then add to \${STEAD_DEMO_HOME}/.env:

  SEARXNG_URL=http://127.0.0.1:${PORT}

EOF

if [[ "${START}" -eq 1 ]]; then
    if docker inspect "${CONTAINER}" >/dev/null 2>&1; then
        docker start "${CONTAINER}" >/dev/null
        say "existing ${CONTAINER} container started"
    else
        docker run -d \
            --name "${CONTAINER}" \
            --restart=unless-stopped \
            -p "127.0.0.1:${PORT}:8080" \
            -v "${CONFIG_DIR}:/etc/searxng:rw" \
            -e "SEARXNG_BASE_URL=http://localhost:${PORT}/" \
            "${IMAGE}" >/dev/null
        say "created ${CONTAINER} from pinned image"
    fi
fi
