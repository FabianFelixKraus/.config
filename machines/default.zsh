# Default machine profile — loaded by .zshrc when no
# machines/<hostname>.zsh file exists for the current machine.
# Keep this conservative: Trade Republic tooling must be OFF unless a
# machine explicitly opts in. See README.md -> "Machine profiles".

export CONFIG_TRADE_REPUBLIC="no"   # yes | no   — enables dx / Trade Republic tooling
export CONFIG_PROFILE="desktop"      # desktop | cli — desktop apps vs. CLI-only setup
