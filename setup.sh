#!/usr/bin/env bash
# Bootstraps (or re-syncs) ~/.config on a machine: macOS or Ubuntu/Debian Linux.
#
# First run: writes machines/<hostname>.zsh (see README.md -> "Machine
# profiles") and installs the CLI tools .zshrc expects (via Homebrew on
# macOS, apt on Linux). Safe to re-run any time to re-sync dependencies --
# it reuses an existing machine profile instead of re-prompting, unless
# --force is passed.
set -euo pipefail

CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACHINES_DIR="$CONFIG_DIR/machines"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--trade-republic=yes|no] [--profile=desktop|cli]
                 [--auto-update=yes|no] [--auto-update-reboot=yes|no]
                 [--auto-update-reboot-time=HH:MM] [--force] [--user-only]

Creates machines/<hostname>.zsh for this machine (skipped if it already
exists, unless --force), then always installs CLI dependencies, links
Claude Code skills, and configures OS auto-updates (./auto-update.sh); on a
desktop profile also wires up the daily git-fetch launchd job and IdeaVim
(macOS only).

  --trade-republic=yes|no        Enable the dx / Trade Republic tooling in .zshrc.
  --profile=desktop|cli          desktop: also run the macOS desktop steps above.
                                   cli: skip them (e.g. a remote box you only SSH into).
  --auto-update=yes|no           Keep this machine on the latest OS packages. Default yes.
  --auto-update-reboot=yes|no    Linux only: reboot itself when an update needs it. Default yes.
  --auto-update-reboot-time=HH:MM  Linux only: local-time maintenance window. Default 04:00.
  --force                        Re-ask and overwrite an existing machines/<hostname>.zsh.
  --user-only                    Skip machine-wide steps that need sudo (apt packages,
                                   auto-update.sh) -- for a second, non-sudo user on a
                                   machine a sudo user has already set up.

With no flags, prompts interactively the first time. Non-interactively (no
TTY) on a machine with no existing profile, --trade-republic and --profile
are required (auto-update flags default sensibly if omitted).
EOF
}

TRADE_REPUBLIC=""
PROFILE=""
AUTO_UPDATE=""
AUTO_UPDATE_REBOOT=""
AUTO_UPDATE_REBOOT_TIME=""
FORCE="no"
USER_ONLY="no"

for arg in "$@"; do
  case "$arg" in
    --trade-republic=*) TRADE_REPUBLIC="${arg#*=}" ;;
    --profile=*) PROFILE="${arg#*=}" ;;
    --auto-update=*) AUTO_UPDATE="${arg#*=}" ;;
    --auto-update-reboot=*) AUTO_UPDATE_REBOOT="${arg#*=}" ;;
    --auto-update-reboot-time=*) AUTO_UPDATE_REBOOT_TIME="${arg#*=}" ;;
    --force) FORCE="yes" ;;
    --user-only) USER_ONLY="yes" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; usage; exit 1 ;;
  esac
done

OS="$(uname)"
case "$OS" in
  Darwin|Linux) ;;
  *) echo "setup.sh only automates macOS and Linux. See README.md for Windows." >&2; exit 1 ;;
esac

# Machine-wide steps (package installs, auto-update.sh) need sudo. Check up
# front and stop before doing anything if it can't work. Otherwise every sudo
# call fails on its own (e.g. when run via `!` inside Claude Code, which has
# no TTY) and the run still ends with "Done." --user-only skips those steps,
# so it skips this check too.
require_sudo() {
  [[ $EUID -eq 0 ]] && return 0
  sudo -n true 2>/dev/null && return 0
  # sudo reads the password from /dev/tty, not stdin, so test that it opens.
  if { : </dev/tty; } 2>/dev/null; then
    echo "Some steps need root -- asking for your sudo password once up front..."
    sudo -v && return 0
  fi
  echo "setup.sh needs sudo, but can't get a password prompt (no terminal)." >&2
  echo "Run it from a normal terminal instead: cd \"$CONFIG_DIR\" && ./setup.sh" >&2
  echo "Or, as a user without sudo, skip the machine-wide steps: ./setup.sh --user-only" >&2
  exit 1
}
[[ "$USER_ONLY" == "yes" ]] || require_sudo

HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
PROFILE_FILE="$MACHINES_DIR/$HOST.zsh"

if [[ -f "$PROFILE_FILE" && "$FORCE" != "yes" ]]; then
  echo "Using existing machines/$HOST.zsh:"
  echo "---"
  cat "$PROFILE_FILE"
  echo "---"
  # shellcheck disable=SC1090
  source "$PROFILE_FILE"
  TRADE_REPUBLIC="${CONFIG_TRADE_REPUBLIC:-no}"
  PROFILE="${CONFIG_PROFILE:-desktop}"
  AUTO_UPDATE="${CONFIG_AUTO_UPDATE:-yes}"
  AUTO_UPDATE_REBOOT="${CONFIG_AUTO_UPDATE_REBOOT:-yes}"
  AUTO_UPDATE_REBOOT_TIME="${CONFIG_AUTO_UPDATE_REBOOT_TIME:-04:00}"
  echo "(pass --force to reconfigure this machine instead)"
  echo
else
  if [[ -z "$TRADE_REPUBLIC" ]]; then
    if [[ -t 0 ]]; then
      read -r -p "Is this a Trade Republic machine? [y/N] " ans
      case "$ans" in
        y|Y|yes|YES) TRADE_REPUBLIC="yes" ;;
        *) TRADE_REPUBLIC="no" ;;
      esac
    else
      echo "Not running interactively and --trade-republic was not given." >&2
      usage
      exit 1
    fi
  fi

  case "$TRADE_REPUBLIC" in
    yes|no) ;;
    *) echo "--trade-republic must be yes or no, got: $TRADE_REPUBLIC" >&2; exit 1 ;;
  esac

  if [[ -z "$PROFILE" ]]; then
    if [[ -t 0 ]]; then
      read -r -p "Profile for this machine? [desktop/cli] (desktop) " ans
      PROFILE="${ans:-desktop}"
    else
      echo "Not running interactively and --profile was not given." >&2
      usage
      exit 1
    fi
  fi

  case "$PROFILE" in
    desktop|cli) ;;
    *) echo "--profile must be desktop or cli, got: $PROFILE" >&2; exit 1 ;;
  esac

  if [[ -z "$AUTO_UPDATE" ]]; then
    if [[ -t 0 ]]; then
      read -r -p "Keep this machine on the latest OS packages automatically? [Y/n] " ans
      case "$ans" in
        n|N|no|NO) AUTO_UPDATE="no" ;;
        *) AUTO_UPDATE="yes" ;;
      esac
    else
      AUTO_UPDATE="yes"
    fi
  fi

  case "$AUTO_UPDATE" in
    yes|no) ;;
    *) echo "--auto-update must be yes or no, got: $AUTO_UPDATE" >&2; exit 1 ;;
  esac

  if [[ "$AUTO_UPDATE" == "yes" && "$OS" == "Linux" ]]; then
    if [[ -z "$AUTO_UPDATE_REBOOT" ]]; then
      if [[ -t 0 ]]; then
        read -r -p "Auto-reboot when an update needs it (e.g. new kernel)? [Y/n] " ans
        case "$ans" in
          n|N|no|NO) AUTO_UPDATE_REBOOT="no" ;;
          *) AUTO_UPDATE_REBOOT="yes" ;;
        esac
      else
        AUTO_UPDATE_REBOOT="yes"
      fi
    fi
    if [[ -z "$AUTO_UPDATE_REBOOT_TIME" ]]; then
      if [[ -t 0 && "$AUTO_UPDATE_REBOOT" == "yes" ]]; then
        read -r -p "Maintenance window for that reboot? [HH:MM] (04:00) " ans
        AUTO_UPDATE_REBOOT_TIME="${ans:-04:00}"
      else
        AUTO_UPDATE_REBOOT_TIME="04:00"
      fi
    fi
  else
    AUTO_UPDATE_REBOOT="${AUTO_UPDATE_REBOOT:-yes}"
    AUTO_UPDATE_REBOOT_TIME="${AUTO_UPDATE_REBOOT_TIME:-04:00}"
  fi

  case "$AUTO_UPDATE_REBOOT" in
    yes|no) ;;
    *) echo "--auto-update-reboot must be yes or no, got: $AUTO_UPDATE_REBOOT" >&2; exit 1 ;;
  esac

  mkdir -p "$MACHINES_DIR"
  cat > "$PROFILE_FILE" <<EOF
# Generated by setup.sh on $(date +%Y-%m-%d).
export CONFIG_TRADE_REPUBLIC="$TRADE_REPUBLIC"
export CONFIG_PROFILE="$PROFILE"
export CONFIG_AUTO_UPDATE="$AUTO_UPDATE"
export CONFIG_AUTO_UPDATE_REBOOT="$AUTO_UPDATE_REBOOT"
export CONFIG_AUTO_UPDATE_REBOOT_TIME="$AUTO_UPDATE_REBOOT_TIME"
EOF
  echo "Wrote machines/$HOST.zsh (trade_republic=$TRADE_REPUBLIC, profile=$PROFILE, auto_update=$AUTO_UPDATE)"
  echo
fi

# --- Dependencies (OS-specific) --------------------------------------------

install_dependencies_macos() {
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew not found -- install it from https://brew.sh, then re-run ./setup.sh." >&2
    return 1
  fi
  brew bundle --file="$CONFIG_DIR/Brewfile"
}

install_dependencies_linux() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "No apt-get found -- install eza, bat, starship, fzf, zoxide," >&2
    echo "zsh-autosuggestions, zsh-syntax-highlighting and jq yourself for your distro." >&2
    return 1
  fi

  sudo apt-get update
  sudo apt-get install -y zsh fzf zoxide bat jq zsh-autosuggestions zsh-syntax-highlighting

  # eza isn't in every release's default apt repos yet -- fall back to eza's
  # own apt repo if the plain install fails.
  if ! command -v eza >/dev/null 2>&1 && ! sudo apt-get install -y eza 2>/dev/null; then
    echo "eza not available via the default apt repos -- adding eza's official apt repo..."
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://raw.githubusercontent.com/eza-community/eza/main/deb.asc \
      | sudo gpg --dearmor -o /etc/apt/keyrings/gierens.gpg
    echo "deb [signed-by=/etc/apt/keyrings/gierens.gpg] http://deb.gierens.de stable main" \
      | sudo tee /etc/apt/sources.list.d/gierens.list >/dev/null
    sudo chmod 644 /etc/apt/keyrings/gierens.gpg /etc/apt/sources.list.d/gierens.list
    sudo apt-get update
    sudo apt-get install -y eza
  fi

  # starship has no apt package -- use its official installer (to
  # /usr/local/bin, so every user on the machine gets it).
  if ! command -v starship >/dev/null 2>&1; then
    curl -sS https://starship.rs/install.sh | sh -s -- -y
  fi
}

# Per-user pieces that need no sudo. Runs for every user, --user-only or not.
install_user_tools_linux() {
  mkdir -p "$HOME/.local/bin"

  # Debian/Ubuntu ship bat's binary as `batcat` (name clash with another package).
  if ! command -v bat >/dev/null 2>&1 && command -v batcat >/dev/null 2>&1; then
    ln -sf "$(command -v batcat)" "$HOME/.local/bin/bat"
  fi

  # No machine-wide starship (a sudo user hasn't run setup.sh yet): install a
  # private copy into ~/.local/bin rather than leaving the prompt broken.
  if ! command -v starship >/dev/null 2>&1; then
    curl -sS https://starship.rs/install.sh | sh -s -- -y -b "$HOME/.local/bin"
  fi
}

# Machine-wide steps: done once per machine, by a sudo user. A second user on
# the same machine runs --user-only and relies on what the sudo user set up.
if [[ "$USER_ONLY" == "yes" ]]; then
  echo "--user-only: skipping machine-wide steps (CLI packages, OS auto-updates)."
  echo "A user with sudo on this machine runs those with a plain ./setup.sh."
else
  echo "Installing CLI dependencies..."
  if [[ "$OS" == "Darwin" ]]; then
    install_dependencies_macos || echo "Dependency install had issues -- see above."
  else
    install_dependencies_linux || echo "Dependency install had issues -- see above."
  fi
fi
echo

if [[ "$OS" == "Linux" ]]; then
  install_user_tools_linux || echo "Per-user tool setup had issues -- see above."
fi

# --- Steps that apply to every machine -------------------------------------

if [[ -x "$CONFIG_DIR/ai/bridges/link-claude.sh" ]]; then
  echo "Linking Claude Code skills..."
  "$CONFIG_DIR/ai/bridges/link-claude.sh"
  echo
fi

if [[ "$USER_ONLY" != "yes" ]]; then
  echo "Configuring OS auto-updates..."
  "$CONFIG_DIR/auto-update.sh" || echo "auto-update.sh had issues -- see above."
  echo
fi

# --- Desktop-only steps (macOS) ----------------------------------------------

if [[ "$PROFILE" == "desktop" && "$OS" == "Darwin" ]]; then
  echo "Installing daily git-fetch-all launchd job..."
  mkdir -p "$HOME/Library/LaunchAgents"
  ln -sf "$CONFIG_DIR/git/launchd/com.fabiankraus.git-fetch-all.plist" \
    "$HOME/Library/LaunchAgents/com.fabiankraus.git-fetch-all.plist"
  launchctl bootout "gui/$(id -u)" \
    "$HOME/Library/LaunchAgents/com.fabiankraus.git-fetch-all.plist" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" \
    "$HOME/Library/LaunchAgents/com.fabiankraus.git-fetch-all.plist"

  if [[ -f "$CONFIG_DIR/ideavim/ideavimrc" ]]; then
    echo "Linking IdeaVim config..."
    ln -sf "$CONFIG_DIR/ideavim/ideavimrc" "$HOME/.ideavimrc"
  fi

  echo
  cat <<'EOF2'
Remaining manual desktop steps (see README.md for details):
  - brew install --cask font-jetbrains-mono font-symbols-only-nerd-font
    then set it as your terminal's font (needed for the Nerd Font glyph in starship.toml)
  - brew install --cask nikitabobko/tap/aerospace
    open -a AeroSpace   # grant Accessibility access when prompted
EOF2
elif [[ "$PROFILE" == "desktop" ]]; then
  echo "desktop profile on Linux: no extra automation yet (this repo's desktop integrations are macOS-only)."
else
  echo "cli profile: skipping git-fetch-all launchd / IdeaVim (desktop-only, macOS)."
fi

if [[ "$TRADE_REPUBLIC" == "yes" ]]; then
  echo
  echo "Trade Republic tooling is enabled. Install dx/ai-kit per TR's own onboarding docs"
  echo "if you haven't already -- this repo only wires .zshrc to use them once present."
fi

# --- Sync this machine's profile to git -------------------------------------
# This is the one file that's actually specific to this machine, so make
# sure the fleet (and GitHub) actually has it -- that's the whole point of
# tracking decisions in git instead of leaving them as local-only state.

sync_machine_profile_to_git() {
  cd "$CONFIG_DIR"
  git rev-parse --git-dir >/dev/null 2>&1 || { echo "Not a git repo?!" >&2; return 1; }

  local needs_commit="no"
  if ! git ls-files --error-unmatch "machines/$HOST.zsh" >/dev/null 2>&1; then
    needs_commit="yes"
  elif ! git diff --quiet -- "machines/$HOST.zsh" || ! git diff --cached --quiet -- "machines/$HOST.zsh"; then
    needs_commit="yes"
  fi

  if [[ "$needs_commit" != "yes" ]]; then
    echo "machines/$HOST.zsh already committed and in sync."
    return 0
  fi

  echo "Committing machines/$HOST.zsh..."
  git add "machines/$HOST.zsh"
  if ! git commit -m "chore(machines): sync profile for $HOST" >/dev/null; then
    echo "Could not commit (check 'git config user.name'/'user.email' on this machine) -- commit it yourself:" >&2
    echo "  git add machines/$HOST.zsh && git commit" >&2
    return 1
  fi

  if git push >/dev/null 2>&1; then
    echo "Pushed machines/$HOST.zsh to origin -- every other machine will see it on their next 'git pull'."
  else
    echo "Committed locally, but couldn't push (no network/credentials yet?) -- run 'git push' once you can." >&2
  fi
}

echo
echo "Syncing this machine's profile to git..."
sync_machine_profile_to_git || echo "Git sync had issues -- see above."

echo
echo "Done. Open a new terminal (or run 'exec zsh') to load the new profile."
echo "Run ./doctor.sh any time to check this machine's health and get pointed at what to fix."
