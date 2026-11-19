# Mesh Guest: show your computer in someone else's browser

Use it when you need to put a screen or an app in front of a person and their computer has nothing installed (a lecturer, a
friend's laptop). You send a link; they open it in any browser; you allow them in; when you are done you revoke it and the
link is dead.

## Step 1 (today): share a web app: `meshd share web PORT`

```
meshd share web 80                 # XAMPP / Apache; phpMyAdmin and your PHP projects are under it
meshd share web 8080 --ttl 20m     # shorter; default 30 minutes, at most 8 hours
meshd share web 80 --read-only     # they can look and click links, not submit forms
meshd share list                   # what is open, who is connected, the code of anyone waiting
meshd share approve [CODE]         # let the waiting visitor in (shows who, asks first)
meshd share revoke                 # end it now; the public address is closed
```

It runs in the terminal where you start it (from the phone, run it in a Termux/`mesh` session on the computer). It prints a link
and a QR code, then shows who opens it. Ctrl+C, closing that terminal, the TTL running out, or `meshd share revoke` from any
terminal all end it the same way.

**What the other person sees:** they open the link, get a black page with a 4 digit code and "waiting for the owner". You see
the same code in your terminal (their browser and system too). You allow them, their page switches to your web app by itself.
Links in your pages that point to `http://localhost/...` (common in student projects) are rewritten so they still work for them.

### One-time Tailscale setup for Funnel

Funnel is off until you approve it once (double opt-in, by Tailscale's design). `meshd share` walks you through it and shows
everything Tailscale says while it waits:

1. The first run prints a link starting `https://login.tailscale.com/...`. Open it on any device and approve. The command carries
   on by itself (it waits up to 5 minutes). If Tailscale asks for HTTPS certificates or MagicDNS, turn them on in its admin console.
2. If Tailscale then says `Access denied: serve config denied`, only root may change Funnel yet. `meshd share` offers to run
   `sudo tailscale set --operator=$USER` for you (answer Y; it asks for your password). Needed once per computer.
3. The first time, the public name of your computer has to appear on the internet: Tailscale says public DNS records can take up to
   10 minutes to show up, and the certificate is made on first use. `meshd share` waits and tells you when the address really answers
   (`--no-wait` skips the wait and prints the link at once). Later shares are immediate.

Funnel limits (Tailscale's, not ours): public ports 443, 8443 or 10000 only (`--https-port`), TCP/TLS only, and a bandwidth
limit they do not publish. TLS ends on your computer; Tailscale's relay cannot read the traffic. The public address exists only
while a share is open.

## Step 2: share the whole screen, with sound: `meshd share screen`

```
meshd share doctor                          # check what this needs and what to install (do this first)
meshd share screen                          # the main monitor, with sound; the guest uses their own keyboard and mouse
meshd share screen --quality high           # up to 1080p at 60 pictures a second; ultra = up to 1440p; text = sharpest small print at 30
meshd share screen --monitor eDP-1 --no-sound --view-only --lock-on-end --ttl 45m --fps 24
meshd share pause | resume                  # freeze their picture and ignore their keys, then undo
meshd share viewonly on | off               # turn their keyboard and mouse off or on while they watch
```

The guest opens the link in a recent Chrome, Edge, Firefox or Safari, you allow them in (same 4 digit code on both screens), they
click **Start**, and they see your screen. Their mouse and keyboard act on your computer. The bar at the top of their page has
Full screen (in Chrome/Edge this also captures Alt+Tab and other shortcuts), Sound on/off, My cursor, **Leave** (they go out; the
link stays open and a new visit needs your approval again) and **Close** (the link is dead for good).

How it works: GNOME's screen-cast (one monitor, no pop-up) -> PipeWire -> GStreamer -> H.264 (VA-API hardware when it works,
x264 software otherwise; picked automatically from what is installed) and the default output's sound -> Opus. They travel over one
WebSocket through the same Funnel address; the browser decodes with its own hardware decoder (WebCodecs). The capture runs only
while a guest is connected (GNOME's recording indicator is on only then), it stops the moment they leave or you revoke, and the
helper processes end with the share even if it is killed.

Typing the link on the other computer: the terminal prints a **short link** first, `https://<name>.ts.net/j/ABCD-EFGH` (the code uses
letters and digits that cannot be mixed up; case and the dash do not matter), and says you can also open just
`https://<name>.ts.net` and type the code in the box that appears. The long link (with the secret in it) is for copy and paste. The
short code is protected the same way: you still approve every visit, wrong codes are limited per address, and a stranger guessing
codes can never close your share.

Picture quality: `--quality auto` (the default) starts at `high` (up to 1920x1080 at 60 pictures a second) with a hardware encoder and
at `fast` (up to 1280x720 at 60) with a software one, then adjusts itself while the guest watches, one step at a time along
`ultra` (up to 2560x1440 at 60), `high`, `fast`, `balanced` (1600x900 at 30), `smooth` (1280x720 at 30) and `low` (960x540 at 30).
It steps down after about 6 seconds when the computer makes fewer pictures than asked or the guest's connection falls behind, and
steps up (after at least 30 comfortable seconds, and only when the encoder has room to spare) towards `ultra`; a step that did not
work is not tried again for 5 minutes. The terminal says each change ("Quality balanced: the computer makes only 20 of 60 pictures
a second."). The picture is never bigger than the monitor or than the guest's window can show. To pin one quality, name it:
`--quality high`, `--quality text` (1920x1080 at 30, the sharpest small print, the most data per picture) and so on; `--fps N` (5 to 60)
overrides the speed. 60 pictures a second at 1080p needs a hardware encoder (Arch: `sudo pacman -Syu`, never `-Sy` alone, then
`sudo pacman -S gst-plugin-va libva-utils`, and `vainfo` should list H264 encoding); a software encoder usually manages 720p at 60.

The picture adapts to the guest's window, in the normal view and in full screen: the page tells the computer how big its window is,
the computer sends a picture of the monitor's own shape that is no bigger than the window can show (no wasted pixels, nothing
stretched), and when the window changes a lot (full screen, a different window size) the encoder restarts with a better size after
about a second. The page itself has **Fit** (the whole picture, bars if the shapes differ), **Fill** (covers the window, crops the
edges) and **Stretch**; the choice is remembered.

What is really happening is shown, not guessed: every 10 seconds the terminal prints a line such as
`stream: 29 fps made, 2860 kbps, guest 0.1 s behind, video 85% of a core`, and says what limits it when the computer makes fewer
pictures than promised (a software encoder that is too slow, or an idle screen: GNOME only sends pictures when something changes).
The page's bar shows fps, kbps, the round trip, and `late` (how far behind real time the pictures are arriving). A guest that falls
behind is never allowed more than about half a second of backlog: pictures are dropped and it jumps to the next key frame.

Watching the screen on the same computer is the worst case and is detected: the picture contains the picture (a tunnel), every
pixel changes every frame so the encoder works at its hardest, the same processor also decodes and draws, and the sound would echo.
The page says so and turns the sound off. Test from another computer.

Hardware encoding is the big saving. On Arch: `sudo pacman -Syu` (a full upgrade, never `-Sy` alone), then
`sudo pacman -S gst-plugin-va intel-media-driver libva-utils`; `vainfo` should list an H.264 encode entry (`VAEntrypointEncSlice`), and
`meshd share doctor` then shows `vah264enc`.

Input rules: only real key names are accepted, rates are limited, Ctrl+Alt+F1..F12 (switch to a text console) and Alt+SysRq are
blocked, held keys are released when the guest leaves, and with `--view-only` or `pause` nothing the guest types reaches the
computer. Without permission for `/dev/uinput` (the Mesh app needs it too) the guest can watch but not type; `doctor` says so.

Needs (Arch): `sudo pacman -S gjs gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugin-pipewire gst-plugin-va`
(add `gst-plugins-ugly` for the software encoder). `meshd share doctor` lists exactly what is missing.

## How the link is protected

| Risk | What stops it |
|---|---|
| Someone guesses or finds the link | 256-bit random secret; only its hash is kept; wrong tries are rate-limited per address (8 per 10 min) and the share closes itself after 24 |
| The link leaks into logs or chat previews | the secret is in the `#` part (a browser never sends it); the page erases it from the address bar; a link preview cannot use it up |
| The wrong person opens it first | nothing opens until **you** approve; the same 4 digit code is on both screens |
| Two people at once | one visitor at a time |
| It stays open by mistake | TTL (default 30 min), idle timeout (15 min), request timeout (5 min); the public address is closed when the share ends |
| After you revoke | the session is deleted, open connections (even a page that is still loading) are cut, the link answers "ended", Funnel is turned off |
| Their browser steals the session | cookie is `HttpOnly`, `Secure`, `SameSite=Strict`, random 256 bits, and is never passed on to your web app |
| Your app is reachable without the gate | only the gate answers on the public port; everything else needs the session |
| Forged addresses | the visitor's address comes from the tunnel hop only; any `X-Forwarded-*` the visitor sends is replaced |

Not protected: **the other person's computer.** Anything you type there can be seen by that computer. Do not type passwords on it.
Never share a web app that has real data and no password (XAMPP's phpMyAdmin often logs in as `root` without one): use
`--read-only`, or set a password first.

## What is and is not tested

Tested by the automatic tests: all the rules above (approval, one visitor, leave, close, TTL, idle, lockout, revoke cutting a page
that is streaming), the gateway in front of a real web app (redirects, cookies, big and chunked pages, headers, uploads, view-only),
the Funnel commands against a fake `tailscale` (including the approval link and an old CLI without `--yes`), the control socket,
and the whole flow in a real Chromium (`tests/test_meshshare_browser.py`, needs Playwright, skipped in CI).

The screen share is tested end to end with ffmpeg standing in for GStreamer (real H.264 and Opus through the gateway into a real
Chromium that decodes, draws and plays them, and whose mouse, keys and wheel arrive at the injector): `tests/test_meshscreen.py`.

**Not tested yet:** the real Funnel on your tailnet (nobody has run it end to end), a real PHP app, other browsers, and for the
screen share the real GNOME capture and GStreamer/VA-API encoder on your hardware (they cannot run where the tests run).
Known limits: WebSocket pages and uploads sent without a length are not supported through the share yet; text pages are sent
uncompressed (so their links can be rewritten).

## Next steps

- **Phone app button** on the home screen: create the link, copy/share it, approve, see who is in, Revoke. The app will use the
  same control socket `meshd share` uses (`share.sock`, owner-only), so no new rules are needed.
- **Guest screen of a virtual monitor** (share only a second, virtual monitor so the main screen is never in the stream).
- **WebRTC + relay** for networks where WebSocket over Funnel is too slow.

## From a phone or tablet

Open the link in Chrome on Android. Tapping Start goes full screen and turns the phone to landscape (Android only lets a page do that from a tap). The screen then works like a laptop touchpad:

- one finger moves the pointer (further when you swipe faster); a tap clicks; a two-finger tap right-clicks; three fingers tap = middle click;
- two fingers sliding scroll (up scrolls down, like on a phone);
- a tap followed at once by a touch that you hold and slide drags; tap twice = double click.

Your own pointer is drawn on the phone at once (a blue arrow), so it follows your finger without waiting for the picture. The Keyboard button (bottom right) opens a floating on-screen keyboard: drag it by its grip out of the way of what you are typing into, make it smaller or bigger with A- and A+ (it remembers where and how big), close it with the cross. It sends the same key names as a real keyboard: Shift, Ctrl, Alt and Win apply to the next key (tap twice to hold, a third time to release), Fn gives F1 to F12, Esc, Home, End, PgUp, PgDn, Ins, Del and the arrows. Menu (top right) holds Full screen, Fit, Sound, Hide my cursor and Leave. A mouse or keyboard connected to the phone works as on a laptop.

The page tells the owner's computer how late the pictures arrive; if they are more than about half a second late, the quality steps down by itself. Sound and replies are sent ahead of waiting pictures, so sound is not delayed by a backlog of video.

If the picture stops, the owner's terminal says why (it now includes the encoder's exit status) and the encoder is started again by itself, twice a minute at most.
