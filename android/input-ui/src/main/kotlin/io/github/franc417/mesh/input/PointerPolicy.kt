package io.github.franc417.mesh.input

/** How a finger on the desktop view is understood. */
enum class PointerMode {
    /** Like a laptop trackpad: dragging moves the pointer by the amount you drag, a tap clicks where the pointer already is. */
    TRACKPAD,

    /** Like a touchscreen: the pointer goes to where the finger is; a tap clicks there and a drag drags from there. */
    TOUCH,

    /** Trackpad while you are using it, touch when you are not (see [PointerPolicy.tapAction]). */
    AUTO,
}

enum class PointerTap {
    /** Click where the pointer already is. */
    CLICK_IN_PLACE,

    /** Move the pointer to the finger, then click. */
    JUMP_AND_CLICK,
}

/**
 * Decides what a tap means in each [PointerMode], so a trackpad and a touchscreen can share one surface without
 * getting in each other's way. No Android API, so every rule is unit tested.
 *
 * The one subtle rule is [PointerMode.AUTO]. Say you slide the pointer onto a small close button and then tap
 * anywhere to click it: that tap must click the button, not jump to wherever your finger is. So a tap soon after
 * trackpad-style movement clicks in place ("you are using it as a trackpad"). A tap after a pause jumps to the finger
 * ("you are pointing at the screen"). Each click in place renews the window, so a double click stays put too.
 */
class PointerPolicy(private val now: () -> Long = { System.currentTimeMillis() }) {

    var mode: PointerMode = PointerMode.TRACKPAD
        set(value) {
            field = value
            lastTrackpadUseAt = NEVER   // switching modes starts clean: no leftover "just used the trackpad"
        }

    /** True when moving by touch is on the table at all, which needs the computer to support absolute positions. */
    val usesAbsolute: Boolean get() = mode != PointerMode.TRACKPAD

    private var lastTrackpadUseAt = NEVER

    /** The finger slid the pointer around, trackpad style. Call it when real movement happens, not for finger jitter. */
    fun noteTrackpadUse() {
        lastTrackpadUseAt = now()
    }

    fun tapAction(): PointerTap = when (mode) {
        PointerMode.TRACKPAD -> PointerTap.CLICK_IN_PLACE
        PointerMode.TOUCH -> PointerTap.JUMP_AND_CLICK
        PointerMode.AUTO ->
            if (now() - lastTrackpadUseAt <= TRACKPAD_GRACE_MS) {
                lastTrackpadUseAt = now()
                PointerTap.CLICK_IN_PLACE
            } else {
                PointerTap.JUMP_AND_CLICK
            }
    }

    /** Whether a drag that starts now is touch-style (starts under the finger) rather than trackpad-style. */
    fun dragIsDirect(selecting: Boolean): Boolean = when (mode) {
        PointerMode.TRACKPAD -> false
        PointerMode.TOUCH -> true
        PointerMode.AUTO -> selecting   // plain dragging in Auto moves the pointer; selecting starts under the finger
    }

    /** Whether holding a finger still turns into a touch-style press-and-drag (the way to select text in Auto). */
    fun longPressStartsDrag(): Boolean = mode == PointerMode.AUTO

    companion object {
        /** How long after trackpad-style movement a tap still counts as "I am using the trackpad". */
        const val TRACKPAD_GRACE_MS = 2500L
        private const val NEVER = Long.MIN_VALUE / 2
    }
}
