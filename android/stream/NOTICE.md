# Streaming core (`:stream`)

Mesh shows the computer's desktop by speaking the same streaming protocol as Moonlight and Sunshine. Rather than
re-implement it, this module reuses Moonlight for Android (GPL-3.0, https://github.com/moonlight-stream/moonlight-android),
pinned to version 12.2, commit `b48494cb96bff23d8886c4775cc4f39a1075495d`.

## What is Moonlight's, unchanged

- `src/main/java/com/limelight/**`: the protocol and pairing code (`NvHTTP`, `PairingManager`, `NvConnection`,
  `MoonBridge` and the small classes they need), copied byte for byte. The package names must stay as they are:
  the native glue looks up `com.limelight.nvstream.jni.MoonBridge` by name.
- The native core (moonlight-common-c, ENet, the JNI glue, prebuilt OpenSSL and Opus static libraries) is **not** committed.
  `android/third_party/fetch-moonlight-core.sh` downloads it at the pinned commit into `src/main/jni/moonlight-core`
  (git-ignored); CI runs that script before building.

## What is Mesh's

- `src/main/kotlin/io/github/franc417/mesh/stream/**`: a small low-latency video decoder, an audio renderer, the
  probe/pair/launch client and the session wrapper, plus the pure helpers that have unit tests.
- Input is deliberately **not** sent through Moonlight's input path: the trackpad and keyboard use Mesh's own pinned,
  encrypted channel.

## Licence

Moonlight's code is GPL-3.0 (`LICENSE-moonlight-android.txt`), so an app that includes this module is distributed under
the GPL as well.

## Known differences from Moonlight

- OkHttp 4.12.0 instead of 5.5.0 (5.x needs a newer Kotlin than this project builds with).
- No jcodec, mDNS discovery, wake-on-LAN or controller support: they are not needed yet.
- Only the two ABIs `arm64-v8a` and `armeabi-v7a` are built.
