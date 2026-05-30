#!/bin/sh
set -eu

env_file="${CEKURA_ENV_FILE:-$(dirname "$0")/.env}"

if [ -z "${CEKURA_API_KEY:-}" ] && [ -f "$env_file" ]; then
  CEKURA_API_KEY="$(
    awk -F= '$1 == "CEKURA_API_KEY" { sub(/^[^=]*=/, ""); print; exit }' "$env_file"
  )"
fi

if [ -z "${CEKURA_API_KEY:-}" ]; then
  echo "CEKURA_API_KEY is not set. Add it to $env_file or export it." >&2
  exit 1
fi

export CEKURA_API_KEY
exec npx -y mcp-remote https://api.cekura.ai/mcp --header "X-CEKURA-API-KEY:$CEKURA_API_KEY"
