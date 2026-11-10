# ~/.config — decisions this session shouldn't re-derive

This file exists so a future Claude Code session (or future me) doesn't
have to re-read every script to reconstruct *why* things are the way they
are. If you're about to grep through `setup.sh`/`auto-update.sh` to figure
out the intent behind something, check here first. Update this file
whenever a new non-obvious decision gets made — that's the whole point.

## What this repo is

`~/.config`, git-synced across every machine: a personal MacBook, a Trade
Republic work laptop, home-lab Proxmox VMs, and a rented VPS. Public GitHub
remote (`FabianFelixKraus/.config`) — **never commit secrets or private
keys here**, not even to a "private-looking" path.

## The three-script pattern

`setup.sh` (bootstrap/re-sync a machine) / `cleanup.sh` (one-off: clear
pre-`machines/` dx fallout) / `auto-update.sh` (apply the OS auto-update
policy) / `doctor.sh` (read-only health check, points at one of the other
three to fix anything it finds). All four are idempotent and safe to
re-run. `doctor.sh` never mutates state — if you're tempted to make it fix
something automatically, that's a sign it belongs in one of the other
three instead.

## Why `machines/<hostname>.zsh` exists at all

One `.zshrc` runs on every machine, but three things are genuinely
per-machine, not universal — each is a `CONFIG_*` env var exported from
`machines/<hostname>.zsh` (resolved via `scutil --get LocalHostName`,
falling back to `hostname -s` on Linux) and committed to git so it syncs on
`git pull`:

- `CONFIG_TRADE_REPUBLIC` (default **no**) — gates `dx`/`ai-kit`/forced
  `DOCKER_CONTEXT=dx` in `.zshrc`. History: this used to be unconditional,
  which broke Docker on every non-TR machine (forced a `DOCKER_CONTEXT=dx`
  that doesn't exist there). Fixed by making it opt-in per machine instead
  of trying to detect "is this a TR machine" automatically — there's no
  reliable signal for that, so a human has to say so once via `setup.sh`.
- `CONFIG_PROFILE` (`desktop`/`cli`, default `desktop`) — gates macOS-only
  desktop automation (AeroSpace reminder, git-fetch launchd job, IdeaVim
  symlink) in `setup.sh`. Linux machines should use `cli`; there's no
  Linux desktop automation written yet (not because it's hard, just
  because nothing needed it yet).
- `CONFIG_AUTO_UPDATE` (+ `_REBOOT`, `_REBOOT_TIME`), default **yes**
  everywhere — see below.

## OS auto-update policy (why these specific defaults)

Explicit user call: an unpatched kernel handing out known root exploits is
a bigger risk than a scheduled unattended reboot, so default is **on**
everywhere, and on Linux that includes auto-reboot (confirmed: yes,
default `04:00` local time) — not just patch-and-flag. Deliberately
asymmetric by OS:

- **Linux (apt)**: tracks the `-updates` pocket, not just `-security` —
  the goal is "always latest," not "only patched." Auto-reboots when a
  kernel/libc update needs it, because a patch sitting on disk but not
  loaded into the running kernel doesn't actually close anything.
- **macOS**: security-only auto-install, **never** auto-installs full
  macOS version upgrades, **never** forces a reboot. A laptop you're
  actively working on shouldn't restart itself without asking — the
  server reasoning (downtime is scheduled and acceptable) doesn't hold for
  a machine you're sitting at.
- **Proxmox host itself is explicitly out of scope** — only the Ubuntu
  guest VMs/LXCs on it are covered. A host reboot takes every VM down at
  once simultaneously; that blast radius is high enough it's left for
  manual judgment rather than a scheduled unattended reboot. If a host
  machine profile ever gets added, it should default
  `CONFIG_AUTO_UPDATE="no"` unless that judgment changes.

## WezTerm / iTerm2 are gone, on purpose

Removed entirely (not just unused) — user doesn't use either anymore, only
Terminal.app. If you see a reference to either reappear (e.g. in a stale
cached doc), it's drift to clean up, not a feature to restore.

## SSH access to servers: deliberately not automated here

Private keys never belong in this repo (public remote) and don't even live
under `~/.config` (they live in `~/.ssh`, a sibling directory). SSH trust
is also a many-to-many relationship (one local key ↔ one account on one
server, and servers here have multiple accounts), which doesn't fit the
one-machine-one-profile shape of `machines/`. Documented in README.md as a
manual `ssh-keygen` + `ssh-copy-id` workflow instead of scripted. A
`~/.ssh/config` host-alias file *could* be tracked here later (no secrets
in it) — intentionally not done yet, only on request.

## `setup.sh` auto-commits (and best-effort pushes) its own profile

The one file `setup.sh` writes (`machines/<hostname>.zsh`) is
auto-committed and `git push`ed at the end of a run, specifically so "did
every machine's decision actually make it into the shared repo" isn't a
separate manual step the user has to remember. Push is best-effort and
non-fatal (e.g. a brand-new VPS may not have git push credentials yet) —
it prints a reminder rather than failing the whole run. Nothing else in the
working tree is ever auto-added; only that one file, by name.
