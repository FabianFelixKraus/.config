# Default machine profile — loaded by .zshrc when no
# machines/<hostname>.zsh file exists for the current machine.
# Keep this conservative: Trade Republic tooling must be OFF unless a
# machine explicitly opts in. See README.md -> "Machine profiles".

export CONFIG_TRADE_REPUBLIC="no"   # yes | no   — enables dx / Trade Republic tooling
export CONFIG_PROFILE="desktop"      # desktop | cli — desktop apps vs. CLI-only setup

# OS auto-updates (./auto-update.sh). Default ON everywhere — an unpatched
# kernel is a bigger risk than an unattended reboot. Linux auto-reboots when
# a kernel/libc update requires it; macOS only auto-installs security
# patches and never force-reboots.
export CONFIG_AUTO_UPDATE="yes"             # yes | no
export CONFIG_AUTO_UPDATE_REBOOT="yes"      # yes | no — Linux only
export CONFIG_AUTO_UPDATE_REBOOT_TIME="04:00"  # HH:MM local time — Linux only
