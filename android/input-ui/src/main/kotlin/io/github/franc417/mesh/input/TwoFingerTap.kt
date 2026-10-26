package io.github.franc417.mesh.input

/**
 * Recognises a two-finger tap (the right click): two fingers go down, barely move, and lift again quickly.
 * Movement beyond [maxMovementPx] means it is a scroll instead, and a third finger means it is neither.
 * No Android API, so it is unit tested; the view feeds it the numbers.
 */
class TwoFingerTap(
    private val maxDurationMs: Long = DEFAULT_MAX_DURATION_MS,
    private val maxMovementPx: Float = DEFAULT_MAX_MOVEMENT_PX,
) {
    private var startedAt = NOT_STARTED
    private var moved = 0f
    private var cancelled = false

    /** The second finger came down. */
    fun begin(timeMs: Long) {
        startedAt = timeMs
        moved = 0f
        cancelled = false
    }

    /** The two fingers moved by [distancePx] together since the last report. */
    fun move(distancePx: Float) {
        moved += kotlin.math.abs(distancePx)
    }

    /** Whether the fingers have moved too much for this to still be a tap (so scrolling may start). */
    val movedTooFar: Boolean get() = moved > maxMovementPx

    /** A third finger came down, or something else made this not a tap. */
    fun cancel() {
        cancelled = true
    }

    /** A finger lifted at [timeMs]. True if the gesture was a two-finger tap; either way the gesture is over. */
    fun end(timeMs: Long): Boolean {
        val isTap = startedAt != NOT_STARTED && !cancelled && moved <= maxMovementPx && timeMs - startedAt in 0..maxDurationMs
        startedAt = NOT_STARTED
        return isTap
    }

    companion object {
        const val DEFAULT_MAX_DURATION_MS = 300L
        const val DEFAULT_MAX_MOVEMENT_PX = 12f
        private const val NOT_STARTED = Long.MIN_VALUE
    }
}
