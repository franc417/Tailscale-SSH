package io.github.franc417.mesh.stream

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ViewTransformTest {
    // The phone is 1600 x 720; the Fit picture is 1280 x 720 (bars of 160 on each side).
    private val sw = 1600
    private val sh = 720
    private val bw = 1280
    private val bh = 720

    private fun rect(t: ViewTransform) = t.rect(sw, sh, bw, bh)

    @Test
    fun `unzoomed the picture is centred exactly as the mode draws it`() {
        val r = rect(ViewTransform())
        assertEquals(ViewTransform.Rect(160f, 0f, 1280f, 720f), r)
    }

    @Test
    fun `zooming about the centre keeps the centre in place and grows evenly`() {
        val t = ViewTransform()
        t.zoomBy(2f, 800f, 360f, sw, sh, bw, bh)
        val r = rect(t)
        assertEquals(2560f, r.width, 0.01f)
        assertEquals(1440f, r.height, 0.01f)
        assertEquals(r.left + r.width / 2, 800f, 0.01f)
        assertEquals(r.top + r.height / 2, 360f, 0.01f)
    }

    @Test
    fun `the spot under the fingers stays under the fingers while zooming`() {
        val t = ViewTransform()
        val focusX = 1000f   // chosen so the zoomed picture still covers the stage and no edge has to be pulled back
        val focusY = 200f
        val before = rect(t)
        val fx = (focusX - before.left) / before.width
        val fy = (focusY - before.top) / before.height
        t.zoomBy(2f, focusX, focusY, sw, sh, bw, bh)
        val after = rect(t)
        assertEquals(focusX, after.left + fx * after.width, 0.5f)
        assertEquals(focusY, after.top + fy * after.height, 0.5f)
    }

    @Test
    fun `zooming near the edge of a picture with bars pulls the edge back to the stage instead of leaving a gap`() {
        val t = ViewTransform()
        t.zoomBy(1.6f, 1200f, 150f, sw, sh, bw, bh)   // 2048 wide: keeping the spot fixed would leave 16 px of gap on the right
        val r = rect(t)
        assertEquals(sw.toFloat(), r.left + r.width, 0.01f)
        assertTrue(r.left <= 0f && r.top <= 0f)
    }

    @Test
    fun `the picture's edges can never be pulled inside the stage once it is bigger`() {
        val t = ViewTransform()
        t.zoomBy(2f, 800f, 360f, sw, sh, bw, bh)
        t.panBy(100_000f, 100_000f, sw, sh, bw, bh)
        var r = rect(t)
        assertEquals(0f, r.left, 0.01f)
        assertEquals(0f, r.top, 0.01f)
        t.panBy(-100_000f, -100_000f, sw, sh, bw, bh)
        r = rect(t)
        assertEquals(sw - r.width, r.left, 0.01f)
        assertEquals(sh - r.height, r.top, 0.01f)
    }

    @Test
    fun `a clamped drag does not store hidden distance`() {
        val t = ViewTransform()
        t.zoomBy(2f, 800f, 360f, sw, sh, bw, bh)
        t.panBy(100_000f, 0f, sw, sh, bw, bh)       // pinned at the left edge
        t.panBy(-50f, 0f, sw, sh, bw, bh)           // the first drag back moves at once
        assertEquals(-50f, rect(t).left, 0.01f)
    }

    @Test
    fun `a direction in which the picture fits does not pan`() {
        val t = ViewTransform()
        t.panBy(300f, 300f, sw, sh, bw, bh)         // the unzoomed picture fits both ways
        assertEquals(ViewTransform.Rect(160f, 0f, 1280f, 720f), rect(t))
        t.zoomBy(1.2f, 800f, 360f, sw, sh, bw, bh)  // 1536 x 864: fits across (1600) but not down
        t.panBy(500f, -40f, sw, sh, bw, bh)
        val r = rect(t)
        assertEquals((sw - r.width) / 2f, r.left, 0.01f)   // still centred across
        assertEquals(-40f + (sh - r.height) / 2f, r.top, 0.01f)
    }

    @Test
    fun `zoom is held between one and five`() {
        val t = ViewTransform()
        t.zoomBy(0.2f, 800f, 360f, sw, sh, bw, bh)
        assertEquals(1f, t.zoom, 0f)
        t.zoomBy(100f, 800f, 360f, sw, sh, bw, bh)
        assertEquals(ViewTransform.MAX_ZOOM, t.zoom, 0f)
        t.zoomBy(1.5f, 800f, 360f, sw, sh, bw, bh)
        assertEquals(ViewTransform.MAX_ZOOM, t.zoom, 0f)
    }

    @Test
    fun `zooming back out to one centres the picture again`() {
        val t = ViewTransform()
        t.zoomBy(3f, 1400f, 100f, sw, sh, bw, bh)
        t.panBy(-400f, 200f, sw, sh, bw, bh)
        t.zoomBy(0.1f, 800f, 360f, sw, sh, bw, bh)
        assertEquals(1f, t.zoom, 0f)
        assertEquals(ViewTransform.Rect(160f, 0f, 1280f, 720f), rect(t))
        assertFalse(t.isZoomed)
    }

    @Test
    fun `reset puts everything back`() {
        val t = ViewTransform()
        t.zoomBy(2.5f, 300f, 300f, sw, sh, bw, bh)
        t.panBy(-70f, -20f, sw, sh, bw, bh)
        assertTrue(t.isZoomed)
        t.reset()
        assertFalse(t.isZoomed)
        assertEquals(ViewTransform.Rect(160f, 0f, 1280f, 720f), rect(t))
    }

    @Test
    fun `zoom works on a picture that already fills the stage`() {
        // The Fill or Zoom view modes give a picture of at least the stage's size.
        val t = ViewTransform()
        t.zoomBy(2f, 0f, 0f, sw, sh, sw, sh)
        val r = t.rect(sw, sh, sw, sh)
        assertEquals(0f, r.left, 0.01f)   // zooming about the top left keeps the top left in place
        assertEquals(0f, r.top, 0.01f)
        assertEquals(3200f, r.width, 0.01f)
    }

    @Test
    fun `nonsense input changes nothing`() {
        val t = ViewTransform()
        t.zoomBy(Float.NaN, 0f, 0f, sw, sh, bw, bh)
        t.zoomBy(-2f, 0f, 0f, sw, sh, bw, bh)
        t.zoomBy(Float.POSITIVE_INFINITY, 0f, 0f, sw, sh, bw, bh)
        t.panBy(Float.NaN, 0f, sw, sh, bw, bh)
        t.zoomBy(2f, 0f, 0f, sw, sh, 0, 0)   // no picture
        assertEquals(1f, t.zoom, 0f)
        assertFalse(t.isZoomed)
    }
}
