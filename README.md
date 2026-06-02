# .config — Dotfiles

## Setup on macOS / WSL

```bash
git clone https://github.com/FabianFelixKraus/.config.git ~/.config

# Neovim (if not already at ~/.config/nvim)
# Nothing needed — nvim reads from ~/.config/nvim by default.

# Claude Code custom skills
ln -sf ~/.config/claude/commands ~/.claude/commands
```

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
| `starship/` | Starship prompt theme          |
| `claude/`   | Claude Code custom skills      |
