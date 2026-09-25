# Tailscale SSH

One command — installed under whatever name you like (default: `mesh`) — on every
device on your tailnet (laptop, phone, another laptop, a server). Each one can reach
any of the others: a live, color-coded list of who's online, who answers SSH, and one
keypress to connect. No IPs, ports or usernames are hardcoded anywhere; there's nothing
to edit when your network changes, because nothing is stored except what Tailscale
already knows.

```
◈ mesh  ·  franc@example.com
this device: arch  ·  100.101.102.1
────────────────────────────────────────────────────────────────
   #  DEVICE          OS       ADDRESS        STATUS        PATH     LATENCY
▸  1  ● pixel-7        android  100.x.x.x      ready :8022   direct     8 ms
   2  ● mint-desktop   linux    100.x.x.x      ready :22     relay     41 ms
   3  ◐ old-server     linux    100.x.x.x      online·no ssh
   4  ○ work-laptop    macos    100.x.x.x      offline · 2d ago
────────────────────────────────────────────────────────────────
2 ready · 3 online · 4 total                                    ● live
↑↓ select  ⏎ connect  r rescan  q quit
```

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/franc417/Tailscale-SSH/main/install.sh \
  | TSSH_TOKEN=<a github token with repo read access> bash
```

The repo is private, so the token is only needed for that first download (or use `gh`
if you're already logged in — the installer picks up `gh auth token` automatically).
It installs one Python file to `~/.local/share/tailscale-ssh/` (`$PREFIX/share` on
Termux) and one thin wrapper command, `mesh`, to `~/.local/bin` (`$PREFIX/bin` on
Termux). Install it under the same name on every device — the name is purely cosmetic
(every install runs the identical picker), but a shared name keeps the habit simple.
Want a different word? `TSSH_NAME=hop bash install.sh` — every help string and hint
in the tool adapts to whatever name it's invoked as.

First run walks new devices through setup automatically (or run `mesh setup` any
time):
- **Installs and signs in to Tailscale** if it isn't already (`pacman`/`apt`/`dnf` on
  Linux, the Play Store link on Android/Termux, a prompt to grab the macOS app).
  Sign-in opens a browser link — use the *same* account (Google/Microsoft/GitHub/Apple)
  on every device so they land on one tailnet. That first sign-in is what creates your
  Tailscale account if you don't have one yet.
- **Starts an SSH server** on the device if none is running, so your other devices can
  reach it (`openssh` on Linux, `sshd` in Termux).
- **Generates an SSH key** (ed25519) if you don't have one, and offers to copy it to a
  device the first time you connect to it, so later connections don't need a password.

Update any install later with `mesh update` (pulls the latest engine from GitHub).

## Using it

```
mesh                   # arrow-key list of every reachable device — pick one, hit enter
mesh pixel              # connect straight to a device by name (prefix match is fine)
mesh u0_a327@pixel      # ...or override the username
mesh laptop -- tmux attach  # pass a remote command after --
mesh list               # print the list once and exit (add --json for scripts)
mesh watch              # live dashboard, no connecting
mesh doctor             # diagnose "why can't I connect"
```

Every device shows the identical picker — a laptop and a phone see each other
symmetrically. Add a third device (a Mint desktop, say) and it just appears in
everyone's list once it's signed in to the same Tailscale account.

## Why there's no config file to edit

The original design (see `docs/DESIGN-NOTES.md`) used `sshph.conf` / `tarch.conf` files
with hardcoded IPs and a "ping the local IP, else fall back to Tailscale" check. That
stops working the moment a device changes networks in a way that isn't "home WiFi vs.
not" — a new WiFi, a different phone hotspot, a laptop at a coffee shop. So instead:

- **No IP is ever stored.** Every run asks `tailscale status` (or the Tailscale API on
  Android, which has no CLI) for the current address of every device, live. A device's
  Tailscale IP is stable *for that device* regardless of which physical network it's
  on — that's what Tailscale is for — so this is simultaneously simpler and more
  correct than a local/Tailscale fallback.
- **What *is* remembered** (in `~/.config/tailscale-ssh/`) is just the small stuff
  that's genuinely stable: your probe ports, your refresh interval, and — once you've
  connected to a device — the username and port you used, so you're not asked again.
  `mesh forget <device>` clears one; `mesh config` shows the file.
- Multiple devices aren't a config-file list you maintain by hand — they're just
  "everyone signed in to your tailnet," discovered automatically.

## Repo layout

```
tailscale_ssh.py   the whole engine (single file, no dependencies beyond the stdlib)
install.sh         one-line installer — creates the `mesh` command (name is configurable)
tests/              unit + integration tests (mock tailscale/ssh, real local TCP listeners)
```

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests fake `tailscale` and `ssh` (see `tests/mockbin/`) and spin up real local TCP
listeners that speak an SSH banner, so the whole pipeline — status parsing, port
probing, the rendered table at several terminal widths, and the arrow-key picker driven
through a real pseudo-terminal — runs for real without needing an actual tailnet.

## Notes on the token you gave Claude

The grained PAT you shared was used only to push this code and is not stored anywhere
in the repo or in the installer. For `install.sh`/`mesh update` to pull from a private
repo going forward, whoever runs them needs their own token (`TSSH_TOKEN`/`GITHUB_TOKEN`
env var, or a logged-in `gh` CLI) — consider rotating the one you shared here once
you've reviewed the code, since pasted tokens are best treated as burned.
