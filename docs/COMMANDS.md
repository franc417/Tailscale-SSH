# Mesh commands on the computer

Run them in a terminal inside the desktop, from the Tailscale-SSH folder (`cd ~/Downloads/Tailscale-SSH`).
Update first: `git pull https://github.com/franc417/Tailscale-SSH.git main`

## The easy way: `meshd` (new)

`python3 meshd.py install` does the whole first-time setup for your distribution; afterwards `meshd` shows the state of the
agent, Sunshine and the supervisor, and `meshd enable|disable|start|stop|restart|logs|login|pair` manage them. See
[MESHD.md](MESHD.md). The commands below are the lower-level pieces it is built from.

## First time on a computer (or to repair one)

| Command | What it does |
|---|---|
| `python3 mesh_input.py doctor` | A checklist, changes nothing: Tailscale, input permission, the agent's identity and service, Sunshine and its service, Sunshine's login for the agent, the video encoder and driver, Sunshine's secure port. Each line says what is fine and what to do. |
| `python3 mesh_input.py setup` | The same checklist, then fixes what it can (asks before each fix; `--yes` asks nothing): creates the agent's identity, installs the agent as a service, installs Sunshine as a service (instead of a terminal), gives Sunshine its login and saves it for the agent, picks the fastest encoder. Ends by telling you what to type on the phone. |

Then on the phone: install Tailscale and sign in with the same account, open Mesh, **Add desktop**, type the computer's Tailscale name (or its 100.x address), leave the pairing box empty, press **Add**. Nothing to paste: the computer lets in phones signed in to the same Tailscale account.

## Every day

| Command | What it does |
|---|---|
| `python3 mesh_input.py run` | Starts the Mesh agent (input from the phone, and automatic pairing). Keep it running, in its own terminal, or install it once with `install-service`. |
| `python3 mesh_input.py screen-normal` | Streams your real screen: Sunshine's own settings back, virtual monitor removed, Sunshine restarted. **The safe default, and the fix when something is stuck.** |
| `python3 mesh_input.py screen-phone --size 1920x864` | The phone-shaped screen: starts a virtual monitor, points Sunshine at it, restarts Sunshine. Use the size the app's **Phone screen** quality shows. The first time GNOME asks which screen to share: choose MetaVendor, press Share. |
| `python3 mesh_input.py screen-status` | One paste that shows everything about the setup: the virtual monitor, monitors, Sunshine settings and log, saved approvals, agent, paired phones. **Run it right after something fails and paste it.** |

## Setting up or repairing

| Command | What it does |
|---|---|
| `python3 mesh_input.py sunshine-login` | Once: saves Sunshine's web username and password (`https://localhost:47990`) so the phone pairs with no PIN. |
| `python3 mesh_input.py sunshine-unpair-all` | Forgets every paired phone in Sunshine (backup kept as `sunshine_state.json.mesh-backup`) and restarts Sunshine. Use when the phone keeps asking to pair, or Sunshine says "identity is not enabled". |
| `python3 mesh_input.py encoder-bench` | Times the video encoders and recommends one. `--apply` writes it into `sunshine.conf`. |
| `python3 mesh_input.py install-service` / `uninstall-service` | Runs the agent in the background from login (`--no-start` writes it without starting). |
| `python3 mesh_input.py pair-info` | Prints what the phone needs to find this computer (addresses, certificate). `reset-token` makes a new secret. |

## Finding out what is wrong

| Command | What it does |
|---|---|
| `python3 mesh_input.py diagnose` | Desktop, monitors, GNOME screen-capture abilities, Sunshine settings and log, paired phones, hardware. |
| `python3 mesh_input.py spike-virtual-monitor` | Tries creating a virtual monitor and reports what GNOME did. `--size 2400x1080`, `--seconds 600` holds it open (Ctrl+C removes it). |

## When something fails

| What you see | Do this |
|---|---|
| App says error code 503, or is stuck on the "Desktop" panel | `python3 mesh_input.py screen-status`, paste it. To stream now: `python3 mesh_input.py screen-normal`. |
| Anything seems off after an update or a reinstall | `python3 mesh_input.py doctor`, then `python3 mesh_input.py setup` to fix what it lists. |
| App says "Pairing failed" / asks for a PIN again | Make sure `run` is running, then `python3 mesh_input.py sunshine-unpair-all`, then Open desktop once. In the app tap **Copy details** and paste it. |
| Sunshine "did NOT start" | `python3 mesh_input.py screen-normal`; if it still fails, `python3 mesh_input.py diagnose`. |
| "Address already in use" | `pkill -9 -x sunshine`, then `python3 mesh_input.py screen-normal`. |
| The app crashed | Open it again: the "Mesh crashed last time" window has a **Copy and close** button. |

## Files it keeps (in `~/.config/sunshine/`)

`mesh-sunshine.log` (Sunshine's output when started by these commands), `sunshine.conf.mesh-normal` and `portal_token.mesh-normal` (your normal setup while the phone screen is on), `portal_token.mesh-phone` (the saved GNOME approval for the virtual screen), `sunshine_state.json.mesh-backup` (before the last unpair).
