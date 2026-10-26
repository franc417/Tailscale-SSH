package io.github.franc417.mesh.input

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VideoAreaTest {
    // A 1280x720 picture centred on a 1600x720 screen: 160 pixels of black bar on each side.
    private val fit = VideoArea(160f, 0f, 1280f, 720f)

    @Test
    fun `corners and centre map to fractions of the picture`() {
        assertEquals(VideoArea.Fraction(0f, 0f), fit.fraction(160f, 0f))
        assertEquals(VideoArea.Fraction(1f, 1f), fit.fraction(1440f, 720f))
        val c = fit.fraction(800f, 360f)!!
        assertEquals(0.5f, c.x, 0.0001f)
        assertEquals(0.5f, c.y, 0.0001f)
    }

    @Test
    fun `a touch on the black bars is not a point on the picture`() {
        assertNull(fit.fraction(100f, 300f))
        assertNull(fit.fraction(1500f, 300f))
        assertNull(fit.fraction(800f, -1f))
    }

    @Test
    fun `a drag that runs onto the bars is held at the edge`() {
        assertEquals(VideoArea.Fraction(0f, 0.5f), fit.clamped(20f, 360f))
        assertEquals(VideoArea.Fraction(1f, 0.5f), fit.clamped(1590f, 360f))
        assertEquals(VideoArea.Fraction(0.5f, 1f), fit.clamped(800f, 900f))
    }

    @Test
    fun `a picture zoomed past the screen maps the visible part`() {
        // 1600x900 picture on a 1600x720 screen, centred: 90 pixels cropped above and below.
        val fill = VideoArea(0f, -90f, 1600f, 900f)
        assertEquals(0.1f, fill.fraction(0f, 0f)!!.y, 0.0001f)
        assertEquals(0.9f, fill.fraction(1600f, 720f)!!.y, 0.0001f)
        assertNotNull(fill.fraction(800f, 360f))
    }

    @Test
    fun `no picture means nothing to point at`() {
        for (area in listOf(VideoArea(0f, 0f, 0f, 720f), VideoArea(0f, 0f, 1280f, 0f), VideoArea(0f, 0f, -5f, 10f),
            VideoArea(0f, 0f, Float.NaN, 10f))) {
            assertFalse(area.valid)
            assertNull(area.fraction(10f, 10f))
            assertNull(area.clamped(10f, 10f))
        }
        assertTrue(fit.valid)
    }
}
