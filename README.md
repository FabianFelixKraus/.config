# .config — Dotfiles

## What this is

This is `~/.config`, synced via git across every machine I use: a personal
MacBook, a Trade Republic work laptop, home-lab Proxmox VMs, and a rented
VPS. It carries Neovim config, shell config (`.zshrc`), the Starship prompt,
and Claude Code skills identically everywhere — plus a few macOS-only app
configs (AeroSpace, a daily git-fetch launchd job).

Not everything *should* be identical across machines, though — Trade
Republic tooling only makes sense on a TR laptop, desktop apps only make
sense on a machine with a screen, and "always track the latest OS packages"
is a stronger stance on a server than on a laptop you're sitting at. A few
things exist specifically to handle that:

- **`machines/`** — per-machine decisions, tracked in git so they sync on
  every `git pull`. See "Machine profiles" below.
- **`setup.sh`** — run once per machine (and safe to re-run any time) to
  record its profile and apply it: install CLI dependencies, link Claude
  skills, configure OS auto-updates, wire up macOS desktop apps if
  relevant, and commit+push its own `machines/<hostname>.zsh` so the fleet
  stays in sync without a separate step.
- **`cleanup.sh`** / **`auto-update.sh`** — narrower helpers `setup.sh`
  calls into; see their sections below.
- **`doctor.sh`** — read-only health check; run it any time to see what's
  wrong on a machine and which of the above fixes it.
- **`CLAUDE.md`** — the non-obvious *why* behind these decisions, written
  for whichever Claude Code session (or future me) next needs to touch
  this repo, so it doesn't have to re-derive intent from reading every
  script from scratch.

## New machine checklist

```bash
git clone https://github.com/FabianFelixKraus/.config.git ~/.config
cd ~/.config && ./setup.sh
```

`setup.sh` asks (first run only — see "Machine profiles" for what each
answer controls):

1. **Is this a Trade Republic machine?** — almost always "no" outside an
   actual TR laptop.
2. **Profile: desktop or cli?** — `desktop` on a Mac you sit at with a
   screen; `cli` on anything you only SSH into (a VPS, a home-lab VM).
3. **Keep this machine on the latest OS packages automatically?** —
   defaults to "yes" if you just hit enter; see "OS auto-updates" below
   for exactly what that means before saying no.
4. *(Linux + auto-update yes only)* **Auto-reboot when an update needs
   it, and at what time?** — defaults to yes at `04:00`.

It records your answers in `machines/<hostname>.zsh`, then — every run,
first or repeat — it:

- installs the CLI tools `.zshrc` expects (`bat`, `starship`, `fzf`,
  `zoxide`, `zsh-autosuggestions`, `zsh-syntax-highlighting`, `jq`) via
  `brew bundle` on macOS or `apt` on Linux;
- links Claude Code skills (`ai/bridges/link-claude.sh`);
- configures OS auto-updates (`./auto-update.sh`);
- on a `desktop` profile (macOS only), wires up the daily git-fetch launchd
  job and IdeaVim, and prints the few steps it can't automate (font casks,
  granting AeroSpace Accessibility access — see "Option key" below too, on
  a German keyboard);
- commits `machines/<hostname>.zsh` if it's new or changed, and
  best-effort `git push`es it — see "How machine sync actually reaches
  the remote" below for exactly what that guarantees (and what it can't).

Then: open a new terminal (or `exec zsh`) so the shell picks up the new
profile. That's it — nvim, the prompt, aliases, and Claude skills all just
work from here. Run `./doctor.sh` any time afterward to double-check
everything landed correctly.

Re-run `./setup.sh` any time (e.g. after `git pull` adds a new dependency)
to re-sync a machine; it reuses the existing `machines/<hostname>.zsh`
instead of re-prompting, unless you pass `--force`. Already set up and just
pulled a big change? Check that change's own notes — e.g. `./cleanup.sh`
exists specifically to clear leftover `dx`/Docker-context state from before
the `machines/` system existed.

### Several users on one machine

Each user clones this repo into their own `~/.config` and runs `setup.sh`.
They share one `machines/<hostname>.zsh` (profiles are per machine, not
per user). Package installs and OS auto-updates are machine-wide, so only
one user with sudo runs a plain `./setup.sh`. Every other user runs:

```bash
./setup.sh --user-only
```

That skips everything needing sudo and only does the per-user parts:
Claude skill links, the `bat` symlink, a private `starship` in
`~/.local/bin` if there's no machine-wide one yet, and the profile git sync.
Their login shell must also be zsh for `.zshrc` to load
(`chsh -s "$(command -v zsh)"`).

If you want passwordless SSH into a server (not automated — see "SSH
access to servers" below), that's the last step on a server machine.

### Machine profiles

This repo is shared across every machine, but a few decisions are
per-machine, not universal:

- **Trade Republic tooling (`dx`)** — the `dx` CLI, `ai-kit`, and the forced
  `DOCKER_CONTEXT=dx` only make sense on a Trade Republic machine. They are
  **off by default** and must be opted into per machine.
- **`desktop` vs. `cli`** — whether this machine runs the full macOS desktop
  app set (AeroSpace, Karabiner, …) or is CLI-only (e.g. an Ubuntu VPS you
  only `nvim`/shell into). Desktop automation in `setup.sh` is macOS-only;
  Linux machines should generally use `cli`.
- **OS auto-updates** — whether this machine keeps itself on the latest OS
  packages unattended. **On by default everywhere**, because an unpatched
  kernel handing out known root exploits is a bigger risk than a scheduled
  unattended reboot. This matters most for servers (the home-lab Proxmox
  VMs and the rented VPS) but applies to any machine. See "OS auto-updates"
  below for exactly what it does per OS.

These decisions live in git as plain `zsh` files under `machines/`, named
by `scutil --get LocalHostName` (falls back to `hostname -s` on Linux), so
they sync to every machine on the next `git pull` — ask
Claude to add or flip one and commit it, no manual editing required:

```
machines/
  default.zsh                  # fallback when no file matches this hostname
  Fabians-MacBook-Pro-4.zsh     # trade_republic=no,  profile=desktop
```

Each file exports:

```zsh
export CONFIG_TRADE_REPUBLIC="no"              # yes | no
export CONFIG_PROFILE="desktop"                 # desktop | cli
export CONFIG_AUTO_UPDATE="yes"                 # yes | no
export CONFIG_AUTO_UPDATE_REBOOT="yes"          # yes | no — Linux only
export CONFIG_AUTO_UPDATE_REBOOT_TIME="04:00"   # HH:MM local time — Linux only
```

`CONFIG_TRADE_REPUBLIC` and `CONFIG_PROFILE` are read by `.zshrc` section 0;
the `CONFIG_AUTO_UPDATE*` ones are read by `./auto-update.sh`, which
`setup.sh` calls on every run, so flipping one and re-running `./setup.sh`
(or just `./auto-update.sh`) is enough to apply it.

To add a new machine (e.g. your Trade Republic laptop or a new VPS), run
`scutil --get LocalHostName` (or `hostname -s` on Linux) on it, create
`machines/<that-name>.zsh` with the exports above, and commit it — or just
run `./setup.sh` on that machine, which writes the file for you.

For AeroSpace: on first launch, grant **Accessibility** access when prompted (System Settings → Privacy & Security → Accessibility). Without it, AeroSpace can't manage windows.

### How machine sync actually reaches the remote

There's no separate "upload my decisions" script, and there doesn't need
to be: the last thing `./setup.sh` does is commit `machines/<hostname>.zsh`
(only that one file, never anything else lying around uncommitted) if it's
new or changed, then try `git push`. So running `./setup.sh` on a machine
is the whole loop — write the decision, commit it, push it — in one step.

What that *doesn't* cover, and why `./doctor.sh`'s last section exists:

- **Push can fail silently from your perspective** — a brand-new VPS that
  only has read-only clone access won't have push credentials yet.
  `setup.sh` reports this ("Committed locally, but couldn't push...") and
  carries on rather than aborting the rest of setup; you have to come back
  and `git push` once credentials exist. `./doctor.sh`'s "Git sync" section
  catches this (`N local commit(s) not pushed`) on any later run.
- **Pushing doesn't pull other machines' changes into your local checkout**
  — each machine still needs its own `git pull` (or re-cloning) to see
  another machine's profile. There's no cron job doing this automatically;
  the daily git-fetch-all launchd job (desktop, macOS only) only `fetch`es
  — it never merges — specifically so it can't surprise you with a working
  tree that changed underneath you.
- **So "are all my machines actually in sync" is answered by**: run
  `./doctor.sh` on each one. "up to date with origin" + no uncommitted
  `machines/<hostname>.zsh` means that machine's decision is both captured
  and visible to every other machine's next `git pull`.

### OS auto-updates

`./auto-update.sh` (called by `setup.sh`, or run standalone to re-apply
after flipping a flag) configures each OS's native mechanism — it doesn't
invent its own:

- **Linux (apt, i.e. the Ubuntu Server home-lab VMs and the VPS)**: installs
  `unattended-upgrades` and points it at the regular `-updates` pocket as
  well as `-security` (not security-only — the goal is "always latest", not
  just "always patched"). If `CONFIG_AUTO_UPDATE_REBOOT=yes` (default), it
  also sets `Automatic-Reboot "true"` at `CONFIG_AUTO_UPDATE_REBOOT_TIME`
  (default `04:00` local time), so a kernel update actually takes effect
  instead of sitting patched-on-disk-but-still-vulnerable-in-memory until
  someone remembers to reboot. Setting `CONFIG_AUTO_UPDATE=no` disables it
  again (`APT::Periodic::Unattended-Upgrade "0"`).
- **macOS**: enables automatic checking, downloading, and installing of
  *security* updates only (`CriticalUpdateInstall`, `ConfigDataInstall` via
  `softwareupdate`/`defaults`). It deliberately does **not** auto-install
  full macOS version upgrades and never forces a reboot — a laptop you're
  actively working on shouldn't restart itself without asking.
- The Proxmox *host* itself isn't covered — only the Ubuntu Server
  VMs/LXCs running on it. A host reboot takes every VM down at once, so
  that's deliberately left for you to manage by hand; set
  `CONFIG_AUTO_UPDATE="no"` in that machine's profile if you ever add one
  for the host itself.

### SSH access to servers

Not automated, and deliberately not part of `machines/` — on purpose, this
is **off by default** in the sense that nothing in this repo ever pushes a
key to a server on your behalf. Two reasons:

1. Private keys must never be committed to git — and this repo's GitHub
   remote is public. `~/.ssh` is a different directory from `~/.config`
   anyway, so there's no accidental overlap to worry about.
2. A machine profile is a property of *one* machine. SSH trust is a
   relationship between *one local key* and *one account on one server* —
   and you mentioned servers often have more than one account you use. That
   many-to-many shape doesn't fit a single per-machine yes/no toggle.

The actual workflow, using the two standard OpenSSH tools built for exactly
this, run once per new machine / once per new server account:

```bash
# 1. On the new machine, generate a key if you don't already have one
#    (skip this if ~/.ssh/id_ed25519 already exists):
ssh-keygen -t ed25519 -C "$(whoami)@$(hostname -s)"

# 2. For every user@host you want passwordless login to, copy the public
#    key over (prompts for that account's password once, then never again):
ssh-copy-id <user>@<host>
# e.g. two accounts on the same box both need their own run:
ssh-copy-id fabian@vps.example.com
ssh-copy-id root@vps.example.com
```

Optional but worth it once you have a few servers: a `Host` block per
server in `~/.ssh/config` pins the right username so you can just type
`ssh vps` instead of `ssh fabian@vps.example.com`. That file has no secrets
in it, so it *could* live in this repo and be symlinked by `setup.sh` if you
want it to sync — ask Claude to set that up if you'd like it; it isn't
there today.

### Option key on a German keyboard

On a German layout you type `@` with Option+L and `~` with Option+N. Terminal.app
treats Option as the Meta key by default, which swallows these keystrokes and
sends an escape sequence instead, so no character appears.

Open Settings → Profiles → your profile → Keyboard, and turn off "Use Option
as Meta Key". The stored key is `useOptionAsMetaKey` in `com.apple.Terminal`.
Quit Terminal.app before you write that key from a script, because
Terminal.app writes its own preferences back when it quits.

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

`setup.sh` doesn't run on native Windows (no WSL) — it's a bash script, and
there's no `machines/` profile or dependency install story for this path
yet. WSL (Ubuntu) is covered by the Linux path above instead.

## What's included

| Directory   | Purpose                        |
|-------------|--------------------------------|
| `nvim/`     | Neovim configuration           |
| `starship/` | Starship prompt theme (used in Terminal.app and over SSH alike) |
| `ai/`       | Claude Code custom skills + agent tooling (bridged into `~/.claude/skills` by `ai/bridges/link-claude.sh`) |
| `git/`      | Daily `git fetch` sweep of `~/Documents` repos (script + launchd job) |
| `aerospace/`| AeroSpace tiling window manager config (workspace switching, layouts) |
| `ideavim/`  | IdeaVim config (routes yank/delete/paste through the macOS clipboard, so Maccy captures them) |
| `machines/` | Per-machine decisions (Trade Republic tooling, desktop vs. CLI, OS auto-updates) — see "Machine profiles" above |
| `Brewfile`  | macOS CLI dependencies, installed by `setup.sh` via `brew bundle` (Linux installs the apt equivalents directly in `setup.sh`) |
| `setup.sh`  | Bootstraps (or re-syncs) a machine — see "New machine checklist" above |
| `cleanup.sh` | One-off: clears `dx`/Docker-context fallout from before `machines/` existed |
| `auto-update.sh` | Configures OS auto-updates per machine profile — see "OS auto-updates" above |
| `doctor.sh` | Read-only health check; points at what to run to fix anything it finds |
| `CLAUDE.md` | Non-obvious *why* behind these decisions, for whichever Claude Code session touches this repo next |
