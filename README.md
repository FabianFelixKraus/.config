# .config — Dotfiles

## Setup on macOS / WSL
git clone https://github.com/FabianFelixKraus/.config.git ~/.config

## Setup on Windows
1. Clone to C:\Users\<USER>\.config
2. Run as Admin in PowerShell:
   New-Item -ItemType SymbolicLink -Path "$env:LOCALAPPDATA\nvim" -Target "C:\Users\<USER>\.config\nvim"
