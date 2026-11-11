# meshd: Mesh Host, the computer's side as one command

Download the folder (or the zip), open a terminal in it, and:

```
python3 meshd.py install
```

That detects your distribution, installs what is missing (asking before every step), puts a `meshd` command on your PATH,
registers the background services, sets up Sunshine's web account, and prints what to type on the phone. Afterwards just
type `meshd` anywhere to see the state of everything.

| Command | What it does |
|---|---|
| `meshd` / `meshd status` | one screen: which processes run, and whether each answers (`--watch` refreshes, `--json` for scripts) |
| `meshd install` | install for your distribution, then `enable`, `login`, `pair` (`--yes` asks nothing, `--install-only` stops after packages) |
| `meshd login` | Sunshine's web account: **new** Sunshine → choose a username and password; **existing** account → enter it, it is checked |
| `meshd enable` / `disable` | run the input agent, Sunshine and the supervisor in the background / stop and unregister them |
| `meshd start\|stop\|restart [agent\|sunshine\|supervisor\|all]` | control them |
| `meshd logs [-f]` | what they printed |
| `meshd pair` | what to type on the phone (and the pairing info, copied to the clipboard, for other cases) |

## One install for both tools

`meshd install` also offers (as optional steps, from this same folder, nothing downloaded) to install **`mesh`**, the
shell-access tool, and to prepare this computer for it (SSH server and key). Tailscale is already signed in by then, so
`mesh` does not ask you to sign in a second time. `mesh` is only needed for a terminal on this computer from your phone's
Termux; the Mesh app does not use it.

On the phone, `meshd pair` prints the exact lines:

```
pkg install python curl
curl -fsSL https://raw.githubusercontent.com/franc417/Tailscale-SSH/main/install.sh | bash
mesh add mkz        # or: mesh add 100.67.163.21
```

`mesh add` needs no Tailscale API key: it finds the computer by name or address (asking Tailscale's own resolver at
100.100.100.100 when the phone's resolver does not know a tailnet name) and lists it with the others.

## What runs in the background

Three systemd **user** services (no root after the packages are installed):

- **mesh-input.service**: the input agent (`mesh_input.py run`): phone keyboard, trackpad, pairing, Tailscale trust.
- **sunshine.service**: Sunshine, with your desktop session.
- **meshd.service**: the supervisor. systemd already restarts a process that exits; the supervisor also catches one that is
  alive but stuck (its port does not answer), starts one that stopped, and checks both again a few seconds after the network
  comes back or changes (Wi-Fi switch, hotspot, wake from sleep). It restarts a service at most 4 times in 10 minutes, so
  something that cannot work does not loop; `meshd status` then shows it as a problem.

**On boot:** `meshd enable` turns on "linger", so the agent and the supervisor start with the computer, before anyone logs in.
Sunshine captures your screen, so it can only start once the desktop session exists: log in, or turn on your desktop's
auto-login and it becomes part of boot. `meshd status` shows "waiting" for Sunshine until then; that is not a fault.

## Distributions

| | Tailscale | Sunshine | input permission | video driver |
|---|---|---|---|---|
| Arch and derivatives | installed for you | installed for you (LizardByte pacman repo; verified on real hardware) | set up for you | Intel/AMD VA-API driver installed for you |
| Fedora | installed for you (Tailscale's installer) | via Sunshine's COPR (from its documentation, **not tested by Mesh**) | set up for you | you install it |
| Debian, Ubuntu, others | installed for you (Tailscale's installer) | **manual**: the tool tells you where to get the package | set up for you | you install it |

`meshd install` can be run again at any time; it only does what is still missing.

## Ways to add a computer on the phone, from least to most typing

1. **Same Tailscale account** (what `meshd pair` shows first): type only the computer's name, leave the pairing box empty.
2. **Paste the pairing info** (`meshd pair` copies it to the clipboard when `wl-copy`, `xclip` or `xsel` exists).
3. Ideas not built yet: a QR code the app scans, and finding computers on the local network with a short one-time code
   instead of the long pairing info.

Not verified on real hardware yet: the supervisor's behaviour across a real network change, boot start with linger, and every
non-Arch install step.
