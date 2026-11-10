# .config — Dotfiles

## Setup on macOS / WSL

```bash
git clone https://github.com/FabianFelixKraus/.config.git ~/.config
cd ~/.config && ./setup.sh
```

`setup.sh` asks two questions — see "Machine profiles" below — records the
answer in `machines/<hostname>.zsh`, links Claude Code skills
(`ai/bridges/link-claude.sh`), and, on a `desktop` profile, wires up the
iTerm2 Dynamic Profile and the daily git-fetch launchd job. It prints the
few steps it can't automate (font casks, setting the iTerm2 profile as
default, granting AeroSpace Accessibility access) at the end. Re-run it any
time; it won't overwrite an existing profile unless you pass `--force`.

Already set up and just pulled this change? Run `./cleanup.sh` once to
check for and clear any leftover `dx`/Docker-context state from before the
`machines/` profile system existed.

Neovim needs nothing extra — it reads from `~/.config/nvim` by default.

### Machine profiles

This repo is shared across every machine, but a couple of decisions are
per-machine, not universal:

- **Trade Republic tooling (`dx`)** — the `dx` CLI, `ai-kit`, and the forced
  `DOCKER_CONTEXT=dx` only make sense on a Trade Republic machine. They are
  **off by default** and must be opted into per machine.
- **`desktop` vs. `cli`** — whether this machine runs the full desktop app
  set (AeroSpace, iTerm2, Karabiner, …) or is CLI-only (e.g. a remote box
  you only `nvim`/shell into).

These decisions live in git as plain `zsh` files under `machines/`, named
by `scutil --get LocalHostName` (falls back to `hostname -s` on
non-macOS), so they sync to every machine on the next `git pull` — ask
Claude to add or flip one and commit it, no manual editing required:

```
machines/
  default.zsh                  # fallback when no file matches this hostname
  Fabians-MacBook-Pro-4.zsh     # trade_republic=no,  profile=desktop
```

Each file just exports two variables, read by `.zshrc` section 0:

```zsh
export CONFIG_TRADE_REPUBLIC="no"   # yes | no
export CONFIG_PROFILE="desktop"      # desktop | cli
```

To add a new machine (e.g. your Trade Republic laptop), run
`scutil --get LocalHostName` on it, create
`machines/<that-name>.zsh` with the two exports above, and commit it.

For AeroSpace: on first launch, grant **Accessibility** access when prompted (System Settings → Privacy & Security → Accessibility). Without it, AeroSpace can't manage windows.

### Option key on a German keyboard

On a German layout you type `@` with Option+L and `~` with Option+N. A terminal
that treats Option as the Meta key swallows these keystrokes and sends an escape
sequence instead, so no character appears.

Terminal.app: open Settings → Profiles → your profile → Keyboard, and turn off
"Use Option as Meta Key". The stored key is `useOptionAsMetaKey` in
`com.apple.Terminal`. Quit Terminal.app before you write that key from a script,
because Terminal.app writes its own preferences back when it quits.

iTerm2: open Settings → Profiles → Keys, and set "Left Option key" to `Normal`.

WezTerm needs no change. `wezterm/wezterm.lua` sets
`send_composed_key_when_left_alt_is_pressed`, which keeps Option+L as `@`.

## Setup on Windows

1. Clone to `C:\Users\<USER>\.config`
2. Run as Admin in PowerShell:
   ```powershell
   New-Item -ItemType SymbolicLink -Path "$env:LOCALAPPDATA\nvim" -Target "C:\Users\<USER>\.config\nvim"
   ```
3. Claude Code custom skills (no admin required):
   ```cmd
   mklink /J "%USERPROFILE%\.claude\skills" "%USERPROFILE%\.config\ai\skills"
   ```

## What's included

| Directory   | Purpose                        |
|-------------|--------------------------------|
| `nvim/`     | Neovim configuration           |
| `wezterm/`  | WezTerm terminal configuration |
| `iterm2/`   | iTerm2 Dynamic Profile + Catppuccin Macchiato color preset (fallback for machines without WezTerm) |
| `starship/` | Starship prompt theme          |
| `ai/`       | Claude Code custom skills + agent tooling (bridged into `~/.claude/skills` by `ai/bridges/link-claude.sh`) |
| `git/`      | Daily `git fetch` sweep of `~/Documents` repos (script + launchd job) |
| `aerospace/`| AeroSpace tiling window manager config (workspace switching, layouts) |
| `ideavim/`  | IdeaVim config (routes yank/delete/paste through the macOS clipboard, so Maccy captures them) |
| `machines/` | Per-machine decisions (Trade Republic tooling on/off, desktop vs. CLI profile) — see "Machine profiles" above |
