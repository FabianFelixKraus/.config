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
