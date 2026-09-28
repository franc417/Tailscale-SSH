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
curl -fsSL https://raw.githubusercontent.com/franc417/Tailscale-SSH/main/install.sh | bash
```

Run that exact command on every device (laptop, phone, another laptop, whatever). It
installs one Python file to `~/.local/share/tailscale-ssh/` (`$PREFIX/share` on
Termux) and one thin wrapper command, `mesh`, to `~/.local/bin` (`$PREFIX/bin` on
Termux). The name is purely cosmetic — every install runs the identical picker — but a
shared name keeps the habit simple. Want a different word? `TSSH_NAME=hop bash
install.sh` — every help string and hint in the tool adapts to whatever name it's
invoked as.

*(If you ever make the repo private again: add `| TSSH_TOKEN=<a github token with
read access> bash` — or an already-logged-in `gh` works too.)*

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

## More than one Tailscale account on the same device

```
mesh accounts             # see the accounts set up on this device
mesh accounts use work    # switch to one
mesh accounts add [nickname]   # sign in to another, without disturbing the current one
```

On a machine with the real Tailscale CLI (Arch, other Linux, macOS), this is a thin
wrapper around Tailscale's own [fast user switching](https://tailscale.com/kb/1225/fast-user-switching)
(`tailscale switch`) — `mesh accounts` really does change which tailnet this device can
reach.

Fast user switching isn't available on Android, so on Termux there's no CLI to switch
in the first place. `mesh setup --account <name>` stores an additional named API key
instead, and `mesh accounts use <name>` switches which one `mesh` reads for its device
list. Important distinction: **this only changes which devices `mesh` shows you — it
doesn't change which tailnet the phone can actually reach.** A device can only ever be
routing packets on one tailnet at a time (true on every platform — Tailscale's own
docs are explicit about this), and on Android that's whichever account the Tailscale
app itself is signed in to. So switching profiles here only shows a usefully different
device list if the Tailscale app is *also* signed in to that account.

## Remote desktop (experimental)

```
mesh gui            # pick a device, get its full desktop
mesh gui arch       # or name it
```

Builds on [Sunshine](https://github.com/LizardByte/Sunshine) (a self-hosted streaming
host) and [Moonlight](https://moonlight-stream.org) (its client). `mesh gui` does the
part that's scriptable: over the SSH connection mesh already has, it checks whether
Sunshine is installed and running on the target, installs it if not (**Arch only for
now**, via LizardByte's own pacman repo -- the AUR package isn't one they support),
starts it, and confirms it's up. On Termux it then copies the target's address to your
clipboard and opens Moonlight. Because it all rides the tailnet, there's no relay or
rendezvous server to run, unlike RustDesk -- Tailscale already solves that part.

Two limits, both checked rather than assumed:

- **The first connection to each device is a manual step inside Moonlight.** Moonlight
  has no documented way to be launched pre-pointed at a host (someone asked its
  maintainers exactly this; it went unanswered), so you do "+ Add PC", paste the address
  mesh copied, and enter the PIN once. After that Moonlight remembers the device.
- **Sunshine captures a real, logged-in display.** A closed laptop lid or no active
  session means there's nothing to capture. A virtual/dummy display would fix that; it
  isn't built yet.

Status: the SSH orchestration is tested against a real `sshd`, and the branching logic
is covered too. What has *not* been run against real hardware is the Arch install/start
itself and the Android side (`termux-clipboard-set`, launching Moonlight) -- there's no
Arch box or phone in the test environment. Treat it as experimental until it's been run
for real.

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
in the repo or in the installer. Now that the repo is public, `install.sh`/`mesh
update` don't need a token at all. Still worth rotating the one you pasted here once
you've reviewed the code — a token that's appeared in plaintext chat is best treated
as burned, regardless of whether it's still needed for anything.
