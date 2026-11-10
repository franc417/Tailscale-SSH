package io.github.franc417.mesh.core

import kotlin.math.max
import kotlin.math.min

/**
 * The square on the scanning screen. The person is told to put the QR code inside it, so the decoder looks ONLY inside it: the square
 * drawn on the screen and the part of the camera picture that is decoded are the same thing, which is what makes "keep it in the
 * square" true. Plain arithmetic, so it is unit tested on an ordinary JVM.
 *
 * The camera preview fills the screen and crops what does not fit, around the centre (PreviewView's fill-centre). A square centred
 * on the screen is therefore centred in the camera picture too, whatever the rotation; only its size in picture pixels has to be
 * worked out.
 */
object ScanGeometry {
    /** The square's side as a share of the shorter side of the screen. */
    const val FRACTION = 0.7f

    /**
     * Side, in pixels of the camera picture as delivered (before rotating it upright), of the square the person sees.
     * [rotationDegrees] is how far the picture must be turned to be upright (0, 90, 180 or 270); the picture is upright on screen.
     */
    fun squareSide(imageWidth: Int, imageHeight: Int, rotationDegrees: Int, viewWidth: Int, viewHeight: Int, fraction: Float = FRACTION): Int {
        if (imageWidth <= 0 || imageHeight <= 0 || viewWidth <= 0 || viewHeight <= 0) return 1
        val sideways = rotationDegrees % 180 != 0
        val uprightWidth = if (sideways) imageHeight else imageWidth
        val uprightHeight = if (sideways) imageWidth else imageHeight
        val scale = max(viewWidth.toFloat() / uprightWidth, viewHeight.toFloat() / uprightHeight)
        val side = (fraction * min(viewWidth, viewHeight) / scale).toInt()
        return side.coerceIn(1, min(imageWidth, imageHeight))
    }

    /** Where a centred square of [side] starts along a picture dimension of [size] pixels. */
    fun origin(size: Int, side: Int): Int = (size - side) / 2
}
