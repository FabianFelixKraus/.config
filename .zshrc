# =============================================================================
# 1. GLOBAL ENVIRONMENT & PATH BUILDER
# =============================================================================
# Start with base paths and build up systematically

export PATH="$HOME/.local/bin:$PATH"                     # User binaries (nvim, pipx, etc)
export PATH="/usr/local/opt/inetutils/libexec/gnubin:$PATH"
export PATH="$PATH:$HOME/.local/bin"                     # pipx
export PATH="/opt/homebrew/opt/ruby/bin:$PATH"           # Ruby

# Docker CLI & Compose

export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
export DOCKER_CLI_PLUGIN_HINTS="$HOME/.docker/cli-plugins"
export PATH="$HOME/.docker/cli-plugins:$PATH"

# Bun

export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"

# PNPM

export PNPM_HOME="$HOME/Library/pnpm"
case ":$PATH:" in
":$PNPM_HOME:") ;;
*) export PATH="$PNPM_HOME:$PATH" ;;
esac

# Tailscale

export PATH="/usr/bin/local/tailscale:$PATH"

# Gradle properties -> environment variables
# (dots aren't valid in shell var names, so e.g. gpr.user becomes gpr_user)

if [ -f "$HOME/.gradle/gradle.properties" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    varname="${key//./_}"
    export "$varname=$value"
  done < "$HOME/.gradle/gradle.properties"
fi

# =============================================================================
# 2. TOOL INITIALIZATIONS
# =============================================================================
# Python (pyenv)

if command -v pyenv >/dev/null 2>&1; then
eval "$(pyenv init --path)"
fi


# Node (nvm)
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# Haskell (ghcup)

[ -f "$HOME/.ghcup/env" ] && . "$HOME/.ghcup/env"

# Java (jenv)
if command -v jenv >/dev/null 2>&1; then
eval "$(jenv init -)"
fi

# =============================================================================
# 3. ALIASES
# =============================================================================
alias python=python3
alias pip=pip3

# Modern CLI replacements
alias ls='eza'
alias ll='eza -l --git'
alias la='eza -la --git'
alias tree='eza --tree'
alias cat='bat --paging=never'

# Fetch all git repos in a directory (defaults to ~/Documents)
fetchall() {
  local dir="${1:-$HOME/Documents}"
  local d
  for d in "$dir"/*/; do
    if [ -d "$d/.git" ]; then
      echo "== ${d} =="
      git -C "$d" fetch --all --prune
    fi
  done
}

# =============================================================================
# 4. COMPLETIONS (Executed exactly once for maximum speed)
# =============================================================================
fpath=($HOME/.docker/completions $fpath)
[ -s "$NVM_DIR/bash_completion" ] && . "$NVM_DIR/bash_completion"
[ -s "$HOME/.bun/_bun" ] && source "$HOME/.bun/_bun"

autoload -Uz compinit
compinit

# =============================================================================
# 5. ZSH VI MODE (Neovim keybindings)
# =============================================================================
# Enable Vi mode
bindkey -v

# Remove the delay when pressing ESC so Normal mode is instantaneous
export KEYTIMEOUT=1

# Ensure basic navigation like backspace still works perfectly in Insert mode
bindkey '^?' backward-delete-char
bindkey '^h' backward-delete-char
bindkey '^w' backward-kill-word

# =============================================================================
# 6. TERMINAL & PROMPT HOOKS
# =============================================================================
# Report current working directory changes cleanly to WezTerm
if [ -n "$WEZTERM_PANE" ]; then
precmd() {
print -Pn "\e]7;file://%m%d\a"
}
fi

# Initialize Starship prompt
if command -v starship >/dev/null 2>&1; then
eval "$(starship init zsh)"
fi

# Fish-style autosuggestions (ghost-text completion from history)
[ -f /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh ] && \
source /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh

# fzf (Ctrl+R history search, Ctrl+T file search, **<Tab> completion)
command -v fzf >/dev/null 2>&1 && eval "$(fzf --zsh)"

# zoxide (smarter `cd` — use `z <partial dir>` to jump)
command -v zoxide >/dev/null 2>&1 && eval "$(zoxide init zsh)"

# zsh-syntax-highlighting (must be sourced last, after other widget-wrapping plugins)
[ -f /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ] && \
source /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh

echo -e "\n✅ Profile loaded successfully."

### MANAGED BY RANCHER DESKTOP START (DO NOT EDIT)
export PATH="/Users/fabiankraus/.rd/bin:$PATH"
### MANAGED BY RANCHER DESKTOP END (DO NOT EDIT)

# Prefer Homebrew's kubectl (kubernetes-cli) over Rancher Desktop's bundled copy
export PATH="/opt/homebrew/bin:$PATH"

# dx shell completion
eval "$(dx completion zsh)"

# >>> dx ai-kit (managed — do not edit) >>>
[ -r /Users/fabiankraus/.traderepublic/ai-kit/ai-kit-env.sh ] && source /Users/fabiankraus/.traderepublic/ai-kit/ai-kit-env.sh
# <<< dx ai-kit <<<

# >>> dx docker (managed — do not edit) >>>
[ -r /Users/fabiankraus/.traderepublic/dx-docker/env.sh ] && source /Users/fabiankraus/.traderepublic/dx-docker/env.sh
# <<< dx docker <<<

# Always use the dx Docker context. dx-docker/env.sh exports DOCKER_HOST, which makes the
# docker CLI ignore contexts and report "default". DOCKER_CONTEXT also wins over a
# currentContext that Rancher Desktop rewrites. Testcontainers reads ~/.testcontainers.properties.
unset DOCKER_HOST
export DOCKER_CONTEXT=dx
