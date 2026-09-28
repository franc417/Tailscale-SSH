# Design notes / history

This project started as two separate shell aliases:

- **`sshph`** (on the Arch laptop) — SSH into the phone's Termux over local WiFi only,
  with the phone's IP, port, and username hardcoded in the alias.
- **`tarch`** (on the phone, in Termux) — SSH into the laptop over Tailscale, which
  works from anywhere and gets around Kenyan ISPs putting devices behind CGNAT (no
  public IP to connect to directly).

The first iteration of "make these installable" kept that local/Tailscale split and
moved the hardcoded values into a config file per device:

```bash
# sshph.conf (on the laptop)
PHONE_USER="u0_a327"
PHONE_PORT="8022"
PHONE_LOCAL_IP="192.168.100.8"
PHONE_TAILSCALE_IP="100.x.x.x"
SSH_KEY="$HOME/.ssh/id_ed25519"

# tarch.conf (on the phone)
LAPTOP_USER="mkz"
LAPTOP_PORT="22"
LAPTOP_LOCAL_IP="192.168.100.4"
LAPTOP_TAILSCALE_IP="100.x.x.x"
SSH_KEY="$HOME/.ssh/id_ed25519"
POST_CONNECT=""
```

with a "ping the local IP, fall back to Tailscale" check to pick the faster path when
available:

```bash
if ping -c 1 -W 1 "$LAPTOP_LOCAL_IP" &>/dev/null; then
    TARGET="$LAPTOP_LOCAL_IP"
else
    TARGET="$LAPTOP_TAILSCALE_IP"
fi
```

That's a reasonable design, but it has a gap: the "local IP" is only right on *one*
specific WiFi network. Move to a different WiFi, a phone hotspot, or a coffee shop and
the local-IP branch either fails outright or (worse) connects to whatever unrelated
device happens to hold that IP on the new network. The fix in this repo is to drop the
local/Tailscale distinction entirely — every device's Tailscale IP is already stable
regardless of which physical network it's on, so there's no "local" fast path left to
maintain, and no IP ever needs to be typed into a config file. See the main README for
how devices, users and ports are discovered and remembered instead.

The rest of the original wishlist carried forward as-is: a private GitHub repo, a
one-line installer, self-update, multiple-device support, and post-connect commands
(`tarch tmux`, `tarch ubt`, etc. — done here as `<command> <device> -- <remote command>`).

**Second change: one command name instead of two.** `sshph`/`tarch` made sense when
they were two different asymmetric scripts pointed at two specific machines. Once the
tool became symmetric — every device runs the identical picker and sees the identical
live list — the two-name split stopped meaning anything (what would a third device, a
Mint desktop say, even call itself: `sshph`? `tarch`? neither fits). So the installer
now creates a single command, named whatever you like (`install.sh` defaults to
`mesh`, override with `TSSH_NAME=whatever`). Every hint and help string in the tool
adapts to whatever name it was invoked as — there's nothing hardcoded to `sshph` or
`tarch` left anywhere.

**Third fix: a stray local `tailscale` binary on Termux was treated as authoritative.**
Termux's Android Tailscale app has no CLI at all — it's a VPN service, not a shell
command — which is exactly why device discovery there uses the Tailscale HTTP API
instead (see the README). But it's possible to separately `pkg install tailscale`
*inside* Termux, which gets you a real `tailscale`/`tailscaled` pair — except that
instance lives entirely inside Termux's own sandbox, has never been signed in, and has
nothing to do with the Android app that's actually connected. The engine's detection
logic only checked "does a `tailscale` binary exist on PATH", found that stray unrelated
binary, and used it — producing "Can't read Tailscale status" even while the real
(Android app) connection was fine. Fix: `ts_cli()` now unconditionally returns `None` on
Termux, so device discovery there only ever goes through the API/local-IP path,
regardless of what happens to be on PATH. (As a second layer of defense for other
platforms too, `fetch_nodes()` now also falls back to the API if the CLI path exists but
errors and an API key is configured, rather than hard-failing.)

**Fourth fix: the API key prompt echoed the real key to the screen.** The Termux
clipboard-shortcut flow asked the person to "press Enter once it's copied" before
reading the system clipboard -- but that prompt was a plain, visible `input()`, and its
return value was simply discarded. In practice, people naturally paste the key directly
into whatever prompt is on screen right after copying it, rather than reading the
instructions closely -- and when they did, the real key got echoed in full, in
plaintext, to the terminal (and into anything that captured that terminal, like a
screenshot). Fixed two ways: that prompt now uses `getpass` like every other secret
entry here, so nothing typed into it is ever echoed regardless of what the person types;
and if what they typed looks like a real key (starts with `tskey`), it's used directly
instead of being discarded, which also means a person who pastes there no longer has to
paste a second time at the following hidden prompt.

**Fifth fix: the suggested SSH username made sense for laptop-to-laptop but not
phone-to-laptop.** When no username is known for a target yet, the tool suggests the
account running it as a reasonable guess (people often use the same username across
their own machines). But that guess was based on `getpass.getuser()` unconditionally
whenever the *target* wasn't Android -- which on Termux returns an Android app UID
string like `u0_a327`, meaningless as a suggestion for an unrelated Linux or macOS
target. Fixed: that default is now only offered when the *calling* device isn't Termux
either; calling from Termux toward a non-Android target now asks with no default at all
rather than suggesting something that's certain to be wrong.

**Sixth fix: `ssh-copy-id` doesn't work on Termux.** Its build there has a long-standing
bug in the step that checks which keys are already installed (a "scratch directory"
setup that fails on Android's filesystem), causing it to either hang or exit with
`Assertion failure: in filter_ids()...` -- so the tool's own "install your key for
password-less logins?" step just didn't work on the device it matters most for. Fixed
by dropping the dependency on `ssh-copy-id` entirely: `install_pubkey()` does the same
job directly over a plain `ssh` connection (read the local pubkey, ssh over, `mkdir -p
~/.ssh`, append the key if it's not already there, fix permissions), which needs
nothing but `ssh` itself and so works identically on every platform. Verified against a
real local `sshd` and a real throwaway system account (not a mock) -- confirms the key
actually gets appended, that installing it twice doesn't duplicate the line, and that
the newly-installed key can genuinely log in unassisted afterward.

**New in 0.3.0: more than one Tailscale account on the same device.** Real Tailscale
already has "fast user switching" (`tailscale switch`) for this on Linux/macOS -- one
account active at a time, switch on demand -- so `mesh accounts` is mostly a thin,
consistently-named wrapper around that (`list`, `use <name>`, `add [nickname]`) rather
than a reimplementation. Android isn't in Tailscale's supported platform list for fast
user switching, and Termux has no CLI to switch in the first place, so the same `mesh
accounts` command does something different there under the hood: named API-key
profiles of our own, stored in config (`mesh setup --account <name>` adds one, `mesh
accounts use <name>` switches which one `mesh` reads for its device list). The config
schema changed from one flat `api_key` to `accounts: {name: {api_key}}` +
`active_account`; existing configs migrate automatically on next load.

The one thing this can't paper over: a device can only ever route packets on one
tailnet at a time, on every platform -- switching which named profile `mesh` reads on
Termux only shows a *usefully different* device list if the Tailscale app itself is
also signed in to that same account, since our profile switch and the Android app's
active session are two independent things. Real network-level switching only exists
where Tailscale's own fast user switching exists (Linux, macOS, iOS, Windows -- not
Android), which is why the CLI-based path can honestly claim more than the Termux path.

**New in 0.4.0 (experimental): `mesh gui`, remote desktop.** The idea was RustDesk's
experience without RustDesk's infrastructure. RustDesk needs its own ID and relay servers
because it can't assume anything about how two devices reach each other; here Tailscale
already answers that, so the design reduces to orchestrating two existing, mature
projects -- Sunshine on the target (capture + hardware encode) and Moonlight on the
client (decode + render + input) -- instead of writing a capture/codec pipeline.

Things checked before building, because the first sketch assumed them:

- *Can Moonlight be launched pre-pointed at a host?* No documented way exists; the one
  upstream issue asking is unanswered. The first sketch drew "launch Moonlight via
  intent, pre-filled" -- that was wrong. What mesh can honestly do is copy the address
  and bring the app forward; the first connection per device stays a manual "Add PC" +
  PIN, after which Moonlight remembers it.
- *How to install Sunshine on Arch?* It's AUR-only in the official ecosystem, and its
  maintainers explicitly don't support the AUR package. They publish a pacman repo
  instead, which needs no AUR helper and no compiling.

Two bugs found in my own first version while testing it, both fixed: a clean exit from
the installer was trusted as proof Sunshine was installed (a missing package would then
have been blamed on "no graphical session"), and the running-check ran instantly after
`systemctl --user enable --now`, before the process necessarily existed.

Not verified against real hardware: the Arch install/start, and the Android handoff.
Still open: capture needs a real display session, so a closed lid means no stream.
