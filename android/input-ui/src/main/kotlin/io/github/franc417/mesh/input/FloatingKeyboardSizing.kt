package io.github.franc417.mesh.input

/**
 * Size and placement rules for the keyboard when it floats over the streamed desktop. No Android API here, so the
 * rules are unit tested on a plain JVM.
 *
 * The keyboard is laid out at its natural size (full width, rows as tall as KeyboardView makes them) and then
 * scaled as a whole, which keeps every key and its proportions and makes touches land on the right key.
 */
object FloatingKeyboardSizing {
    /** The enlarged keyboard never takes more than this share of the screen's height. */
    const val ENLARGED_MAX_HEIGHT_SHARE = 0.5f

    /** The compact keyboard is this share of the enlarged one. */
    const val COMPACT_SHARE_OF_ENLARGED = 0.7f

    /** Below this the keys are too small to hit, so the compact size stops shrinking here. */
    const val MIN_SCALE = 0.3f

    /**
     * Scale to apply to a keyboard of natural size [naturalW] x [naturalH] shown over a stage of [stageW] x [stageH].
     * Enlarged: as big as fits within the screen's width and half its height. Compact: noticeably smaller.
     * Returns 1 for sizes that make no sense (zero or negative), so a layout pass before the screen is measured is harmless.
     */
    fun scale(stageW: Int, stageH: Int, naturalW: Int, naturalH: Int, enlarged: Boolean): Float {
        if (stageW <= 0 || stageH <= 0 || naturalW <= 0 || naturalH <= 0) return 1f
        val enlargedScale = minOf(stageW.toFloat() / naturalW, stageH * ENLARGED_MAX_HEIGHT_SHARE / naturalH)
        if (enlarged) return enlargedScale
        return maxOf(enlargedScale * COMPACT_SHARE_OF_ENLARGED, minOf(MIN_SCALE, enlargedScale))
    }

    /**
     * The offset a box may be dragged to along one axis so it stays fully on screen.
     * [startPos] is where layout put the box (its left or top edge), [size] its width or height, [stageSize] the
     * stage's width or height, and [wanted] the offset the finger asks for. A box larger than the stage is pinned to its start.
     */
    fun clampOffset(wanted: Float, startPos: Int, size: Int, stageSize: Int): Float {
        val low = -startPos.toFloat()
        val high = (stageSize - startPos - size).toFloat()
        if (high < low) return 0f
        return wanted.coerceIn(low, high)
    }
}
