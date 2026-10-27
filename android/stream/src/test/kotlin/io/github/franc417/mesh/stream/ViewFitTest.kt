package io.github.franc417.mesh.stream

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ViewFitTest {
    private val wide = 1280f / 720f   // the picture
    // The phone: 1600 x 720, a wider shape than the picture.

    @Test
    fun `fit keeps the whole picture and leaves bars at the sides on a wider screen`() {
        val s = ViewFit.size(ViewMode.FIT, 1600, 720, wide)
        assertEquals(ViewFit.Size(1280, 720), s)
    }

    @Test
    fun `fit leaves bars above and below on a taller screen`() {
        val s = ViewFit.size(ViewMode.FIT, 1000, 1000, wide)
        assertEquals(1000, s.width)
        assertEquals(563, s.height)   // 562.5 rounds up
    }

    @Test
    fun `zoom covers the whole screen and crops the overhang`() {
        val s = ViewFit.size(ViewMode.ZOOM, 1600, 720, wide)
        assertEquals(1600, s.width)
        assertEquals(900, s.height)   // 90 pixels cropped above and below
        assertTrue(s.width >= 1600 && s.height >= 720)
    }

    @Test
    fun `zoom on a taller screen is as tall as the screen and crops the sides`() {
        val s = ViewFit.size(ViewMode.ZOOM, 1000, 1000, wide)
        assertEquals(1000, s.height)
        assertEquals(1778, s.width)
    }

    @Test
    fun `fill is exactly the screen with nothing cropped`() {
        assertEquals(ViewFit.Size(1600, 720), ViewFit.size(ViewMode.FILL, 1600, 720, wide))
    }

    @Test
    fun `when the shapes match all three agree`() {
        for (mode in ViewMode.values()) {
            assertEquals("$mode", ViewFit.Size(1280, 720), ViewFit.size(mode, 1280, 720, wide))
        }
    }

    @Test
    fun `fit never exceeds the stage and zoom never falls short of it`() {
        for ((w, h) in listOf(1600 to 720, 720 to 1600, 1080 to 1920, 640 to 480, 333 to 777)) {
            for (aspect in listOf(1f, 4f / 3f, 16f / 9f, 21f / 9f)) {
                val fit = ViewFit.size(ViewMode.FIT, w, h, aspect)
                val zoom = ViewFit.size(ViewMode.ZOOM, w, h, aspect)
                assertTrue("fit $w x $h @ $aspect -> $fit", fit.width <= w + 1 && fit.height <= h + 1)
                assertTrue("zoom $w x $h @ $aspect -> $zoom", zoom.width >= w - 1 && zoom.height >= h - 1)
            }
        }
    }

    @Test
    fun `nonsense sizes give the stage back instead of dividing by zero`() {
        assertEquals(ViewFit.Size(0, 0), ViewFit.size(ViewMode.FIT, 0, 0, wide))
        assertEquals(ViewFit.Size(800, 600), ViewFit.size(ViewMode.ZOOM, 800, 600, 0f))
        assertEquals(ViewFit.Size(800, 600), ViewFit.size(ViewMode.FIT, 800, 600, Float.NaN))
        assertEquals(ViewFit.Size(0, 600), ViewFit.size(ViewMode.FIT, -5, 600, wide))
    }

    @Test
    fun `fill never crops and never leaves bars on any screen shape`() {
        for ((w, h) in listOf(1600 to 720, 720 to 1600, 1000 to 1000)) {
            assertEquals(ViewFit.Size(w, h), ViewFit.size(ViewMode.FILL, w, h, wide))
        }
    }

    @Test
    fun `saved view names are read back, including the old name for fill`() {
        assertEquals(ViewMode.FIT, ViewMode.parse("FIT"))
        assertEquals(ViewMode.FILL, ViewMode.parse("FILL"))
        assertEquals(ViewMode.FILL, ViewMode.parse("STRETCH"))   // an earlier version's name for what is now FILL
        assertEquals(ViewMode.ZOOM, ViewMode.parse("ZOOM"))
        assertEquals(ViewMode.FIT, ViewMode.parse(null))
        assertEquals(ViewMode.FIT, ViewMode.parse("nonsense"))
    }
}
