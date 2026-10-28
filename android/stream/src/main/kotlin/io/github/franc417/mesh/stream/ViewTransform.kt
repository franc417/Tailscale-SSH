package io.github.franc417.mesh.stream

/**
 * The person's zoom and pan of the streamed picture, on top of the size the [ViewMode] gives it.
 *
 * A picture can only fill a screen of a different shape by being distorted, cropped or barred, so for small text and a
 * big view the answer is the same as in every remote desktop app: zoom in with two fingers and move the view around.
 * Nothing is lost then: whatever is off screen is one pan away, and nothing is stretched. No Android API, so every
 * rule is unit tested.
 *
 * All numbers are in stage pixels. [rect] is where the picture sits; the stage is the area it is shown in.
 */
class ViewTransform {
    data class Rect(val left: Float, val top: Float, val width: Float, val height: Float)

    var zoom = 1f
        private set
    private var panX = 0f
    private var panY = 0f

    val isZoomed: Boolean get() = zoom > 1.0001f || panX != 0f || panY != 0f

    fun reset() {
        zoom = 1f
        panX = 0f
        panY = 0f
    }

    /** Where the picture is: centred, scaled by [zoom], moved by the pan, and never pulled away from the stage's edges. */
    fun rect(stageW: Int, stageH: Int, baseW: Int, baseH: Int): Rect {
        val w = baseW * zoom
        val h = baseH * zoom
        val left = place(stageW.toFloat(), w, panX)
        val top = place(stageH.toFloat(), h, panY)
        // Keep the stored pan consistent with what is shown, so a clamped drag does not build up hidden distance.
        panX = left - (stageW - w) / 2f
        panY = top - (stageH - h) / 2f
        return Rect(left, top, w, h)
    }

    /**
     * Zooms by [factor] keeping the spot under ([focusX], [focusY]) where it is, like pinching a map. The result is held
     * between [MIN_ZOOM] and [MAX_ZOOM].
     */
    fun zoomBy(factor: Float, focusX: Float, focusY: Float, stageW: Int, stageH: Int, baseW: Int, baseH: Int) {
        if (factor.isNaN() || factor <= 0f || factor.isInfinite()) return
        val before = rect(stageW, stageH, baseW, baseH)
        if (before.width <= 0f || before.height <= 0f) return
        val newZoom = (zoom * factor).coerceIn(MIN_ZOOM, MAX_ZOOM)
        if (newZoom == zoom) return
        // The fraction of the picture under the fingers, which has to stay under them.
        val fx = (focusX - before.left) / before.width
        val fy = (focusY - before.top) / before.height
        zoom = newZoom
        val w = baseW * zoom
        val h = baseH * zoom
        panX = (focusX - fx * w) - (stageW - w) / 2f
        panY = (focusY - fy * h) - (stageH - h) / 2f
        rect(stageW, stageH, baseW, baseH)   // clamps
    }

    /** Moves the picture by ([dx], [dy]); a direction in which the picture fits entirely on the stage does not move. */
    fun panBy(dx: Float, dy: Float, stageW: Int, stageH: Int, baseW: Int, baseH: Int) {
        if (dx.isNaN() || dy.isNaN()) return
        rect(stageW, stageH, baseW, baseH)
        panX += dx
        panY += dy
        rect(stageW, stageH, baseW, baseH)   // clamps
    }

    private fun place(stage: Float, size: Float, pan: Float): Float {
        val centred = (stage - size) / 2f
        if (size <= stage) return centred                       // it fits: always centred, panning has no effect
        return (centred + pan).coerceIn(stage - size, 0f)       // bigger than the stage: the edges cannot leave it
    }

    companion object {
        const val MIN_ZOOM = 1f
        const val MAX_ZOOM = 5f
    }
}
