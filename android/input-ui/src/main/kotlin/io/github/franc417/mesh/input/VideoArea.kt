package io.github.franc417.mesh.input

/**
 * Where the streamed picture sits inside the touch surface, in the surface's own pixels, so a touch can be turned into
 * "this far across and down the picture" (what the computer needs to place the pointer). The picture may be smaller
 * than the surface (black bars) or bigger (zoomed to fill, with its edges cropped). No Android API.
 */
class VideoArea(val left: Float, val top: Float, val width: Float, val height: Float) {

    /** True when there is a picture to point at. */
    val valid: Boolean get() = width > 0f && height > 0f && !width.isNaN() && !height.isNaN()

    /** The point as a fraction of the picture (0..1 from the top left), or null if it is on the bars or there is no picture. */
    fun fraction(x: Float, y: Float): Fraction? {
        if (!valid) return null
        val fx = (x - left) / width
        val fy = (y - top) / height
        if (fx < 0f || fx > 1f || fy < 0f || fy > 1f || fx.isNaN() || fy.isNaN()) return null
        return Fraction(fx, fy)
    }

    /** Like [fraction] but a point outside the picture becomes the nearest edge, so a drag can run off the edge and still reach it. */
    fun clamped(x: Float, y: Float): Fraction? {
        if (!valid) return null
        return Fraction(((x - left) / width).coerceIn(0f, 1f), ((y - top) / height).coerceIn(0f, 1f))
    }

    data class Fraction(val x: Float, val y: Float)
}
