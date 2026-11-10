#!/usr/bin/env bash
# Read-only health check for this machine's ~/.config. Never changes
# anything -- only reports pass/warn/fail and points at the script that
# fixes each issue (./setup.sh, ./cleanup.sh, ./auto-update.sh, or a plain
# git command). Safe to run any time, on any machine.
set -uo pipefail

CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
OS="$(uname)"
PROFILE_FILE="$CONFIG_DIR/machines/$HOST.zsh"

PASS=0; WARN=0; FAIL=0

# Packages and OS auto-updates are machine-wide: on a machine shared by
# several users, only one with sudo can fix them (a --user-only user can't).
MACHINE_FIX="./setup.sh, as a user with sudo (machine-wide)"

pass() { printf "  \033[32m✓\033[0m %s\n" "$1"; PASS=$((PASS + 1)); }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; [[ -n "${2:-}" ]] && printf "      -> %s\n" "$2"; WARN=$((WARN + 1)); }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; [[ -n "${2:-}" ]] && printf "      -> %s\n" "$2"; FAIL=$((FAIL + 1)); }

echo "=== Machine profile ($HOST) ==="
if [[ -f "$PROFILE_FILE" ]]; then
  pass "machines/$HOST.zsh exists"
  # shellcheck disable=SC1090
  source "$PROFILE_FILE"
else
  warn "no machines/$HOST.zsh yet" "./setup.sh"
  [[ -f "$CONFIG_DIR/machines/default.zsh" ]] && source "$CONFIG_DIR/machines/default.zsh"
fi
TRADE_REPUBLIC="${CONFIG_TRADE_REPUBLIC:-no}"
PROFILE="${CONFIG_PROFILE:-desktop}"
AUTO_UPDATE="${CONFIG_AUTO_UPDATE:-yes}"
AUTO_REBOOT="${CONFIG_AUTO_UPDATE_REBOOT:-yes}"
REBOOT_TIME="${CONFIG_AUTO_UPDATE_REBOOT_TIME:-04:00}"
echo "  trade_republic=$TRADE_REPUBLIC  profile=$PROFILE  auto_update=$AUTO_UPDATE"
echo

echo "=== Shell config ==="
if zsh -n "$CONFIG_DIR/.zshrc" 2>/dev/null; then
  pass ".zshrc parses cleanly"
else
  fail ".zshrc has a syntax error" "zsh -n $CONFIG_DIR/.zshrc"
fi
echo

echo "=== CLI dependencies ==="
for tool in eza fzf zoxide jq; do
  if command -v "$tool" >/dev/null 2>&1; then
    pass "$tool installed"
  else
    warn "$tool missing" "$MACHINE_FIX"
  fi
done
if command -v starship >/dev/null 2>&1; then
  pass "starship installed"
else
  warn "starship missing" "$MACHINE_FIX, or ./setup.sh --user-only for a private copy"
fi
if command -v bat >/dev/null 2>&1 || command -v batcat >/dev/null 2>&1; then
  pass "bat/batcat installed"
else
  warn "bat missing" "$MACHINE_FIX"
fi
for plugin in zsh-autosuggestions zsh-syntax-highlighting; do
  found="no"
  for p in \
    "/opt/homebrew/share/$plugin/$plugin.zsh" \
    "/usr/local/share/$plugin/$plugin.zsh" \
    "/usr/share/$plugin/$plugin.zsh" \
  ; do
    [[ -f "$p" ]] && found="yes"
  done
  if [[ "$found" == "yes" ]]; then pass "$plugin installed"; else warn "$plugin missing" "$MACHINE_FIX"; fi
done
echo

echo "=== Trade Republic / Docker context ==="
if [[ "$TRADE_REPUBLIC" != "yes" ]]; then
  if [[ -n "${DOCKER_CONTEXT:-}" ]]; then
    warn "DOCKER_CONTEXT=$DOCKER_CONTEXT is set in this shell but trade_republic=no" \
      "open a new terminal, or run: unset DOCKER_CONTEXT DOCKER_HOST"
  else
    pass "DOCKER_CONTEXT not set (correct for a non-TR machine)"
  fi
  if command -v docker >/dev/null 2>&1 && [[ -f "$HOME/.docker/config.json" ]]; then
    if command -v jq >/dev/null 2>&1; then
      persisted="$(jq -r '.currentContext // "default"' "$HOME/.docker/config.json" 2>/dev/null)"
    else
      persisted="$(command sed -n 's/.*"currentContext"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$HOME/.docker/config.json" | head -1)"
    fi
    if [[ "$persisted" == "dx" ]]; then
      fail "Docker's persisted context is stuck on 'dx'" "./cleanup.sh"
    else
      pass "Docker context is '$persisted'"
    fi
  fi
else
  if command -v dx >/dev/null 2>&1; then
    pass "dx CLI found"
  else
    warn "trade_republic=yes but dx CLI not found" "install dx/ai-kit per TR's own onboarding docs"
  fi
fi
echo

echo "=== Claude Code skills ==="
if [[ -d "$HOME/.claude/skills" ]]; then
  broken=0
  for link in "$HOME/.claude/skills"/*; do
    [[ -L "$link" && ! -e "$link" ]] && broken=$((broken + 1))
  done
  count="$(find "$HOME/.claude/skills" -maxdepth 1 -mindepth 1 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "$broken" -gt 0 ]]; then
    fail "$broken broken skill symlink(s) in ~/.claude/skills" "./setup.sh"
  else
    pass "$count skill(s) linked in ~/.claude/skills"
  fi
else
  warn "~/.claude/skills doesn't exist" "./setup.sh"
fi
echo

echo "=== OS auto-updates ==="
DROPIN=/etc/apt/apt.conf.d/51dotfiles-unattended-upgrades.conf
if [[ "$OS" == "Linux" ]]; then
  if [[ "$AUTO_UPDATE" == "yes" ]]; then
    if [[ -f "$DROPIN" ]] && (command -v unattended-upgrades >/dev/null 2>&1 || dpkg -l unattended-upgrades >/dev/null 2>&1); then
      pass "unattended-upgrades installed and configured"
    else
      warn "auto_update=yes but unattended-upgrades isn't set up" "./auto-update.sh, as a user with sudo (machine-wide)"
    fi
  else
    if [[ -f "$DROPIN" ]]; then
      warn "auto_update=no but the drop-in config still exists" "./auto-update.sh, as a user with sudo (machine-wide)"
    else
      pass "auto-updates correctly off"
    fi
  fi
  if [[ -f /var/run/reboot-required ]]; then
    warn "a reboot is pending on this machine right now" \
      "sudo reboot (or wait for the scheduled auto-reboot at $REBOOT_TIME if enabled)"
  else
    pass "no reboot pending"
  fi
elif [[ "$OS" == "Darwin" ]]; then
  if [[ "$AUTO_UPDATE" == "yes" ]]; then
    val="$(defaults read /Library/Preferences/com.apple.SoftwareUpdate CriticalUpdateInstall 2>/dev/null || echo 0)"
    if [[ "$val" == "1" ]]; then
      pass "macOS security auto-updates enabled"
    else
      warn "auto_update=yes but macOS security auto-updates aren't enabled" "./auto-update.sh, as a user with sudo (machine-wide)"
    fi
  else
    pass "auto_update=no (macOS Software Update settings left untouched)"
  fi
fi
echo

echo "=== Git sync ==="
cd "$CONFIG_DIR" || true
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [[ -z "$branch" ]]; then
  fail "$CONFIG_DIR doesn't look like a git repo" ""
else
  if ! git ls-files --error-unmatch "machines/$HOST.zsh" >/dev/null 2>&1; then
    warn "machines/$HOST.zsh isn't tracked by git yet" "git add machines/$HOST.zsh && git commit"
  elif ! git diff --quiet -- "machines/$HOST.zsh" 2>/dev/null || ! git diff --cached --quiet -- "machines/$HOST.zsh" 2>/dev/null; then
    warn "machines/$HOST.zsh has uncommitted changes" "git add machines/$HOST.zsh && git commit"
  else
    pass "machines/$HOST.zsh is committed"
  fi

  if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    ahead="$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
    behind="$(git rev-list --count 'HEAD..@{u}' 2>/dev/null || echo 0)"
    if [[ "$ahead" -gt 0 ]]; then
      warn "$ahead local commit(s) not pushed to origin" "git push"
    elif [[ "$behind" -gt 0 ]]; then
      warn "$behind commit(s) on origin not pulled yet" "git pull && ./setup.sh"
    else
      pass "up to date with origin/$branch"
    fi
  else
    warn "no upstream tracking branch configured" "git push -u origin $branch"
  fi
fi
echo

echo "=== Summary ==="
echo "  $PASS passed, $WARN warning(s), $FAIL failure(s)"
[[ "$FAIL" -gt 0 ]] && exit 1
exit 0
