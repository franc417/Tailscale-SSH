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
