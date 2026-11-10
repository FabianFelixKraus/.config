#!/usr/bin/env bash
# Configures OS-level automatic updates for this machine, driven by
# machines/<hostname>.zsh (see README.md -> "Machine profiles"). Safe to
# re-run any time -- e.g. after flipping CONFIG_AUTO_UPDATE and re-running
# ./setup.sh, or standalone.
#
# Linux (apt): installs unattended-upgrades, tracks the -updates pocket (not
# just -security) so the machine stays on the latest packages, and -- if
# CONFIG_AUTO_UPDATE_REBOOT=yes -- reboots itself at CONFIG_AUTO_UPDATE_REBOOT_TIME
# when a kernel/libc update needs it, so it's never patched-on-disk-but-still
# -vulnerable-in-memory.
#
# macOS: enables automatic checking/downloading and security-only installs
# (CriticalUpdateInstall, ConfigDataInstall). Does NOT auto-install full macOS
# version upgrades and does NOT force a reboot -- a workstation you're
# actively using shouldn't restart itself without asking.
set -euo pipefail

CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
PROFILE_FILE="$CONFIG_DIR/machines/$HOST.zsh"

if [[ -f "$PROFILE_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$PROFILE_FILE"
else
  echo "No machines/$HOST.zsh yet -- using defaults. Run ./setup.sh to record this machine."
  source "$CONFIG_DIR/machines/default.zsh"
fi

AUTO_UPDATE="${CONFIG_AUTO_UPDATE:-yes}"
AUTO_REBOOT="${CONFIG_AUTO_UPDATE_REBOOT:-yes}"
REBOOT_TIME="${CONFIG_AUTO_UPDATE_REBOOT_TIME:-04:00}"

echo "Machine: $HOST  (auto_update=$AUTO_UPDATE, reboot=$AUTO_REBOOT, reboot_time=$REBOOT_TIME)"
echo

configure_linux() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "No apt-get found -- auto-update.sh only knows how to drive unattended-upgrades on Debian/Ubuntu." >&2
    return 1
  fi

  local dropin=/etc/apt/apt.conf.d/51dotfiles-unattended-upgrades.conf
  local periodic=/etc/apt/apt.conf.d/20auto-upgrades

  if [[ "$AUTO_UPDATE" != "yes" ]]; then
    echo "CONFIG_AUTO_UPDATE=no -- disabling unattended upgrades."
    sudo rm -f "$dropin"
    if command -v unattended-upgrades >/dev/null 2>&1 || dpkg -l unattended-upgrades >/dev/null 2>&1; then
      sudo tee "$periodic" >/dev/null <<'EOF'
// Managed by ~/.config/auto-update.sh -- do not hand-edit, re-run ./auto-update.sh instead.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "0";
EOF
    fi
    return 0
  fi

  echo "Installing unattended-upgrades..."
  sudo apt-get update
  sudo apt-get install -y unattended-upgrades

  local reboot_bool="false"
  [[ "$AUTO_REBOOT" == "yes" ]] && reboot_bool="true"

  sudo tee "$dropin" >/dev/null <<EOF
// Managed by ~/.config/auto-update.sh -- do not hand-edit, re-run ./auto-update.sh instead.

// Track the regular -updates pocket too, not just -security: this machine
// is set to always stay on the latest packages rather than security-only.
#clear Unattended-Upgrade::Allowed-Origins;
Unattended-Upgrade::Allowed-Origins {
    "\${distro_id}:\${distro_codename}";
    "\${distro_id}:\${distro_codename}-security";
    "\${distro_id}:\${distro_codename}-updates";
    "\${distro_id}ESMApps:\${distro_codename}-apps-security";
    "\${distro_id}ESM:\${distro_codename}-infra-security";
};

Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "$reboot_bool";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
Unattended-Upgrade::Automatic-Reboot-Time "$REBOOT_TIME";
EOF

  sudo tee "$periodic" >/dev/null <<'EOF'
// Managed by ~/.config/auto-update.sh -- do not hand-edit, re-run ./auto-update.sh instead.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

  sudo systemctl enable --now apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service >/dev/null 2>&1 || true

  echo "unattended-upgrades enabled (all updates, not just security)."
  if [[ "$AUTO_REBOOT" == "yes" ]]; then
    echo "Automatic reboot enabled at $REBOOT_TIME local time when a kernel/libc update requires it."
  else
    echo "Automatic reboot disabled -- check /var/run/reboot-required yourself after updates land."
  fi
}

configure_macos() {
  if [[ "$AUTO_UPDATE" != "yes" ]]; then
    echo "CONFIG_AUTO_UPDATE=no -- leaving macOS Software Update settings untouched."
    return 0
  fi

  echo "Enabling automatic security updates (not full macOS version upgrades)..."
  sudo softwareupdate --schedule on
  sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticCheckEnabled -bool true
  sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticDownload -bool true
  sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate CriticalUpdateInstall -bool true
  sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate ConfigDataInstall -bool true
  # Deliberately NOT setting AutomaticallyInstallMacOSUpdates -- full OS
  # version upgrades stay manual, and nothing here forces a reboot.
  echo "Security updates will install automatically; full macOS upgrades stay manual."
}

OS="$(uname)"
case "$OS" in
  Darwin) configure_macos ;;
  Linux)  configure_linux ;;
  *) echo "auto-update.sh only knows macOS and Linux." >&2; exit 1 ;;
esac
