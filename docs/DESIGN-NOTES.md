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
(`tarch tmux`, `tarch ubt`, etc. — done here as `tarch <name> -- <command>`).
