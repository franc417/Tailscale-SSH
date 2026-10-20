# Mesh input protocol (MIP) v1

How the Mesh phone app sends touches and keys to a computer. Implemented by `mesh_input.py` (host)
and `android/mesh-core` (phone). Both are tested against the same byte vectors, and a test launches the
real agent and drives it with the real phone library.

## Why it exists

Mesh uses Sunshine and moonlight-common-c for video and audio, but input travels on this channel instead
of Sunshine's: no dependence on another project's input quirks, true key down/up (hold-to-repeat,
Shift-click, Ctrl-drag), exact text, and nothing extra to install on the computer (the agent is one
standard-library Python file that talks to `/dev/uinput` directly).

## Security

An input channel is full control of the computer, so it authenticates in two ways.

- **TLS, certificate pinned.** The agent makes a self-signed P-256 certificate once (`openssl` is needed
  that one time). During pairing the phone learns its SHA-256 fingerprint over SSH and refuses any other
  certificate, so nobody can sit in the middle even on an open LAN. TLS 1.2 or newer (Android 8 and 9
  stop at 1.2).
- **Pairing token.** A random 32-byte token, sent inside the encrypted channel in `HELLO` and compared in
  constant time. A wrong token is answered after a one second pause and the connection is closed.
- **One phone at a time.** A newly authenticated phone replaces the old one, which is told `E_REPLACED`.
- **No stuck keys.** Everything the phone had pressed is released when it disconnects, is replaced, or goes
  quiet for 15 seconds.

Secrets live in `~/.config/mesh/agent/` (`key.pem`, `token`, mode 0600). `mesh_input.py reset-token`
revokes every paired phone.

## Pairing (what the phone needs)

`python3 mesh_input.py pair-info` prints `{"version", "port", "fingerprint", "token", "host"}`. The Mesh
app runs it over SSH once per computer, stores the fingerprint and token in the Android Keystore, and never
needs to ask again. Default port: **47600**.

## Framing

```
u16 length | u8 type | payload          (big-endian; length counts type + payload; at most 4096)
```

## Phone to host

| Type | Name | Payload |
|---|---|---|
| 0x01 | HELLO | `u8 version` `u8 n` `token[n]` `u8 m` `name[m]` (UTF-8 device name) |
| 0x02 | MOVE | `f32 dx` `f32 dy` (pixels; fractions are kept and accumulated by the host) |
| 0x03 | SCROLL | `f32 vertical` `f32 horizontal` (wheel notches; fractions allowed) |
| 0x04 | BUTTON | `u8 button` (1 left, 2 middle, 3 right, 4 back, 5 forward) `u8 down` (1 or 0) |
| 0x05 | KEY | `u8 down` `u8 n` `code[n]` (ASCII web `KeyboardEvent.code` name, e.g. `KeyA`, `ArrowUp`) |
| 0x06 | TEXT | UTF-8, at most 1024 bytes (longer text is sent as several frames) |
| 0x07 | PING | `u32 id` |
| 0x08 | RELEASE_ALL | none |
| 0x09 | BYE | none |

The first frame must be HELLO, within 3 seconds. Send a PING at least every 15 seconds (the app sends one
every 5) or the host treats the phone as gone. Unknown types are ignored, so a newer phone can talk to an
older agent. Key names are listed in `KEY_CODES` in `mesh_input.py`; unknown names are skipped.

## Host to phone

| Type | Name | Payload |
|---|---|---|
| 0x81 | HELLO_OK | `u8 version` `u8 features` (1 text, 2 hi-res scroll) `u8 n` `host name[n]` |
| 0x82 | ERROR | `u8 code` `u8 n` `text[n]`, then the connection closes |
| 0x87 | PONG | `u32 id` |

Error codes: 1 bad token, 2 bad version, 3 replaced by another phone, 4 bad frame, 5 no HELLO.

## Behaviour worth knowing

- **Fractional motion** is accumulated on the host, so slow sub-pixel drags still move the cursor.
- **Scroll** uses the hi-res wheel axes (120 units per notch) plus the legacy notch event, for smooth scrolling.
- **Text** is typed as real key presses for ASCII (US layout). Other characters use the Unicode entry
  sequence Ctrl+Shift+U, hex, Space, which needs an input method (IBus, fcitx) in the focused app. This is the
  same compromise Sunshine's own text input makes on Linux.
- **The phone coalesces cursor motion**: if the network write is slower than the touch stream, deltas are
  summed into one message instead of queueing, and any other message drains pending motion first so order is kept.

## Byte vectors (asserted by both test suites)

```
MOVE(1.5, -2.0)            0009 02 3fc00000 c0000000
SCROLL(-0.25, 0)           0009 03 be800000 00000000
BUTTON(left, down)         0003 04 01 01
KEY(KeyA, down)            0007 05 01 04 4b657941
TEXT("hé")                 0004 06 68c3a9
PING(7)                    0005 07 00000007
RELEASE_ALL                0001 08
HELLO(token aabb, "ph")    0008 01 01 02aabb 02 7068
```

## Running the agent

```
python3 mesh_input.py init              # create the certificate and token (once)
python3 mesh_input.py pair-info         # what the phone needs, as JSON
python3 mesh_input.py run               # serve (needs /dev/uinput access, like Sunshine)
python3 mesh_input.py run --dry-run     # print events instead of injecting them
```

`/dev/uinput` needs the same permission setup Sunshine does (a udev rule plus the `input` group); `mesh gui`
already applies it.
