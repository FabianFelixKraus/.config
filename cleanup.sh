#!/usr/bin/env bash
# Verifies a machine is clear of fallout from the pre-v2.0.0 .zshrc, which
# unconditionally forced `export DOCKER_CONTEXT=dx` for everyone, Trade
# Republic machine or not. Safe to re-run anytime; only touches state it
# positively identifies as stale.
set -uo pipefail

CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
PROFILE_FILE="$CONFIG_DIR/machines/$HOST.zsh"

if [[ -f "$PROFILE_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$PROFILE_FILE"
else
  echo "No machines/$HOST.zsh yet -- using the default (off) profile. Run ./setup.sh to record this machine."
  source "$CONFIG_DIR/machines/default.zsh"
fi

echo "Machine: $HOST  (trade_republic=${CONFIG_TRADE_REPUBLIC:-no}, profile=${CONFIG_PROFILE:-desktop})"
echo

fixed_anything="no"

persisted_docker_context() {
  local cfg="$HOME/.docker/config.json"
  [[ -f "$cfg" ]] || { echo "default"; return; }
  if command -v jq >/dev/null 2>&1; then
    jq -r '.currentContext // "default"' "$cfg" 2>/dev/null
  else
    command sed -n 's/.*"currentContext"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$cfg" | head -1
  fi
}

# 1. Docker's *persisted* context (~/.docker/config.json), independent of any
#    shell env var -- only something an explicit `docker context use dx` could
#    have set.
if [[ "${CONFIG_TRADE_REPUBLIC:-no}" != "yes" ]] && command -v docker >/dev/null 2>&1; then
  persisted="$(persisted_docker_context)"
  if [[ "$persisted" == "dx" ]]; then
    fallback="default"
    env -u DOCKER_CONTEXT -u DOCKER_HOST docker context inspect desktop-linux >/dev/null 2>&1 && fallback="desktop-linux"
    echo "Docker's persisted context is stuck on 'dx' -- switching to '$fallback'."
    env -u DOCKER_CONTEXT -u DOCKER_HOST docker context use "$fallback" >/dev/null
    fixed_anything="yes"
  fi
fi

# 2. Env vars exported into *this* shell by an old .zshrc. A script running as
#    a subprocess can't unset these in your interactive shell -- print the
#    copy-pasteable fix instead.
if [[ "${CONFIG_TRADE_REPUBLIC:-no}" != "yes" ]] && [[ -n "${DOCKER_CONTEXT:-}" || -n "${DOCKER_HOST:-}" ]]; then
  echo "This shell still has DOCKER_CONTEXT=${DOCKER_CONTEXT:-} DOCKER_HOST=${DOCKER_HOST:-} exported."
  echo "  -> run: unset DOCKER_CONTEXT DOCKER_HOST   (or just open a new terminal)"
fi

# 3. Testcontainers config some dx tooling may have pointed at a TR docker
#    host. Not auto-edited -- surfaced for manual review only.
if [[ "${CONFIG_TRADE_REPUBLIC:-no}" != "yes" && -f "$HOME/.testcontainers.properties" ]]; then
  echo "Found ~/.testcontainers.properties -- review it manually, it is not auto-edited:"
  command sed 's/^/    /' "$HOME/.testcontainers.properties"
fi

echo
if [[ "$fixed_anything" == "no" ]]; then
  echo "Nothing to fix on disk. This machine is clean."
fi
