# Trying the Mesh input channel

This is the first end-to-end test of the Mesh phone app: a trackpad and keyboard on your phone driving your
computer over the encrypted, certificate-pinned input channel (no video yet).

## On the computer (Arch or any Linux)

1. Get the code: `git pull` in your Tailscale-SSH checkout (or `./install.sh`, which also installs the agent).
2. Make sure your user can use `/dev/uinput`. If Sunshine already types for you, you are done. If not, the agent says
   so and what to do (a udev rule plus the `input` group, then log out and in once).
3. Start it. Either:
   - `python3 mesh_input.py run` in a terminal (you will see each connection), or
   - `python3 mesh_input.py install-service` to start at every login and restart by itself.
4. Print what the phone needs: `python3 mesh_input.py pair-info` and copy the whole line it prints.
5. If you run a firewall, allow TCP port **47600** (Tailscale traffic is usually already allowed).

## On the phone

1. Install the app. In the GitHub repository open **Actions**, pick the newest green run, download the
   **mesh-debug** artifact, unzip it and install the APK.
2. Open **Mesh**. Under **Add a computer** tap **Paste from clipboard** (or paste into the box) and tap **Save**.
   The pairing info already lists the computer's addresses (Tailscale first, then LAN), and the phone tries them all
   at once, so you do not need to type one. The address box is only for a computer whose addresses are not listed.
   Never use `0.0.0.0`: it only means "every address" in the agent's log, and the app refuses it.
3. Tap the computer. The top line shows `Connecting`, then `Connected  ·  12 ms` (the round-trip time).

## What to try

- Move one finger: the cursor moves. Tap: click. Two fingers: scroll. Tap-and-drag: select text.
- Type on the keyboard, including Ctrl, Alt and the fn row. Try `ctrl` then `c`, and `alt` then `tab`.
- Turn Wi-Fi off and on, or walk out of range: the top line says what happened and **Reconnect** appears.
- Connect a second phone: the first one is told another phone took over.
- Put the phone down for a minute: it stays connected (a ping runs every 5 seconds).

## If something goes wrong

The messages are meant to be readable. The common ones:

| Message | Meaning |
|---|---|
| Could not reach … at …. Is the Mesh agent running… | no address answered: the agent is not running, a firewall blocks TCP 47600, or the phone is on neither the same Wi-Fi nor Tailscale |
| the computer did not answer (is the Mesh agent running?) | something answered the port but not as the agent |
| the computer rejected this phone's pairing token (pair again) | the token was reset on the computer; paste fresh `pair-info` |
| the computer's certificate does not match the one paired with this phone | the agent's certificate changed (reinstall) or someone is in the middle; pair again only if you know why |
| no permission to open /dev/uinput | the agent cannot type on this computer yet; see step 2 |

To revoke every phone: `python3 mesh_input.py reset-token`, then pair again.

## What is not done yet

- A held key sends a quick press and release (no hold-to-repeat yet).
- The pairing token is stored in the app's private storage (backups are off); wrapping it with an Android Keystore key is planned.
- No video: the streamed desktop (Sunshine and Moonlight) is the next milestone.
