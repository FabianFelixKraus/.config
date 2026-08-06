# .config — Dotfiles

## Setup on macOS / WSL

```bash
git clone https://github.com/FabianFelixKraus/.config.git ~/.config

# Neovim (if not already at ~/.config/nvim)
# Nothing needed — nvim reads from ~/.config/nvim by default.

# Claude Code custom skills
ln -sf ~/.config/claude/commands ~/.claude/commands

# iTerm2 (only needed on machines where WezTerm isn't available)
brew install --cask font-jetbrains-mono font-symbols-only-nerd-font
mkdir -p ~/Library/"Application Support"/iTerm2/DynamicProfiles
ln -sf ~/.config/iterm2/DynamicProfiles/fabian.json ~/Library/"Application Support"/iTerm2/DynamicProfiles/fabian.json
```

Then in iTerm2: **Preferences → Profiles**, select **"Fabian (Catppuccin Macchiato)"**, right-click it → **Set as Default**.

## Setup on Windows

1. Clone to `C:\Users\<USER>\.config`
2. Run as Admin in PowerShell:
   ```powershell
   New-Item -ItemType SymbolicLink -Path "$env:LOCALAPPDATA\nvim" -Target "C:\Users\<USER>\.config\nvim"
   ```
3. Claude Code custom skills (no admin required):
   ```cmd
   mklink /J "%USERPROFILE%\.claude\commands" "%USERPROFILE%\.config\claude\commands"
   ```

## What's included

| Directory   | Purpose                        |
|-------------|--------------------------------|
| `nvim/`     | Neovim configuration           |
| `wezterm/`  | WezTerm terminal configuration |
| `iterm2/`   | iTerm2 Dynamic Profile + Catppuccin Macchiato color preset (fallback for machines without WezTerm) |
| `starship/` | Starship prompt theme          |
| `claude/`   | Claude Code custom skills      |
