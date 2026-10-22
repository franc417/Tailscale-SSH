# input-ui: provenance

The trackpad, the on-screen keyboard, its layout model and the key-press vibration were copied from
https://github.com/franc417/remote-hid (MIT licence, `android-app/app/src/main/java/com/remotehid/server/input`)
at commit `1d8377c`, with the package renamed to `io.github.franc417.mesh.input`.

They are a copy, not a dependency, on purpose: Mesh needs changes that remote-hid should not have
(true key down/up, a floating keyboard and a shortcut strip, Tab in the default layout, text events), and
sharing one module would make each project's changes risk the other's. Fixes that matter to both are
ported by hand.

Differences from the original are listed in `CHANGES.md`.
