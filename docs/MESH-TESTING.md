# Trying the Mesh input channel

This is the first end-to-end test of the Mesh phone app: a trackpad and keyboard on your phone driving your
computer over the encrypted, certificate-pinned input channel. The second half of this page covers the **desktop view**
(the computer's screen on your phone), which is new and has only ever been compiled, never run on a phone.

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
- See the desktop view section below for what is and is not done there.

# Adding and editing desktops

The home screen has **+ Add desktop** at the top. Each saved computer has **Open desktop**, **Input only** and **Edit**.
**Edit** changes the name, the addresses (one per line; all are tried at once), and the port. If the agent on the computer
was reinstalled or its pairing reset, paste its new `pair-info` there to replace the saved pairing. **Remove this
computer** is at the bottom of the edit screen.

# Trying the desktop view

Mesh shows the computer's screen by talking to **Sunshine** (the same program Moonlight uses), using Moonlight's
streaming engine inside the app. Typing and the trackpad still go over the Mesh input channel above, laid on top of the picture.

## One-time setup on the computer

1. Install Sunshine (Arch: the `sunshine` package) and start it. Open its web page, `https://localhost:47990`, accept
   the self-signed certificate warning, and create the Sunshine username and password when it asks.
2. Make sure the application list contains **Desktop** (Sunshine adds it by default).
3. If you run a firewall, allow TCP 47984, 47989, 48010 and UDP 47998 to 48000 (Tailscale traffic is usually already allowed).
4. Keep the Mesh agent running as in the steps above. The picture and the input use different programs on purpose.

## On the phone

1. Install the newest **mesh-debug** APK (it replaces the old one, your saved computers stay).
2. Tap **Open desktop** on the computer's card. The phone goes landscape.
3. The first time, the phone shows a 4-digit PIN. Tap **Open Sunshine's PIN page** (or open
   `https://<computer address>:47990/pin` on any device), log in, type the PIN and any device name, and send it.
   The phone then continues by itself. This happens once per computer.
4. The desktop appears and the controls tuck away so the picture is full screen. Tap the small **arrow tab** at the top
   centre to bring the bar back (and again to hide it). The bar has **Close**, a status line, **Smooth** (a menu to
   pick Smooth 720p60 / Sharp 1080p60 / Light 720p30) and **Keyboard**. Changing the quality restarts the stream.
   The status line reads like `58 fps · net 12 ms · host 4.1 ms · dec 7 ms · 2 dropped`: frames per second shown,
   network round trip, how long the computer took to encode a frame, how long the phone took to decode it, frames thrown away.
5. **Keyboard** opens a small box that floats over the desktop (it never shrinks the picture). Drag it by its title to
   move it, **Bigger** makes it as large as half the screen (**Smaller** goes back), **Hide** or the **Keyboard** button
   puts it away.

## What to report

- Does a picture appear at all? If not, the message on screen says where it stopped (contacting, pairing, starting, video).
- Is it smooth, and where is the delay? Send me the status line while you move the pointer. `fps` should be near 60;
  a high `net` is the network (try the Wi-Fi 5 GHz band, or **Light**); a high `host` is the computer's encoder
  (Sunshine's settings); a high `dec` is the phone's decoder. Many `dropped` means the phone or network cannot keep up.
- Is there sound? Is the delay when you move the pointer acceptable?
- Green or garbled picture, or a long delay on a cheap phone: tell me the phone model, that is the known weak spot
  (the video decoder is deliberately minimal and has no per-phone workarounds yet).

## What is not done in the desktop view

- Pairing needs you to type the PIN into Sunshine's web page once (automatic PIN entry through the agent comes later).
- H.264 only, stereo sound only, no HDR, no gamepad, no mouse through the streaming engine (input is Mesh's own channel).
- No virtual display: with no monitor attached or a locked screen the picture may be black (Linux specific).
