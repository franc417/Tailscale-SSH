package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ScanGeometryTest {

    @Test
    fun `a tall phone screen crops the sides of a sensor picture, and the square is sized for what is visible`() {
        // sensor 1280x720 turned 90 degrees is 720x1280 upright; a 1080x2160 screen scales it by max(1.5, 1.6875) = 1.6875
        // the square is 70% of 1080 screen pixels = 756, which is 756 / 1.6875 = 448 picture pixels
        assertEquals(448, ScanGeometry.squareSide(1280, 720, 90, 1080, 2160))
    }

    @Test
    fun `an upright landscape picture on a landscape view`() {
        // 1280x720 upright, 1280x720 view: scale 1, the shorter side is 720, 70% is 504
        assertEquals(504, ScanGeometry.squareSide(1280, 720, 0, 1280, 720))
    }

    @Test
    fun `rotation by 270 and by 90 give the same size, and 180 the same as 0`() {
        assertEquals(ScanGeometry.squareSide(1280, 720, 90, 1080, 2160), ScanGeometry.squareSide(1280, 720, 270, 1080, 2160))
        assertEquals(ScanGeometry.squareSide(1280, 720, 0, 1280, 720), ScanGeometry.squareSide(1280, 720, 180, 1280, 720))
    }

    @Test
    fun `the square never leaves the picture`() {
        for (rotation in listOf(0, 90, 180, 270)) {
            val side = ScanGeometry.squareSide(640, 480, rotation, 300, 300, fraction = 5f)
            assertTrue(side <= 480)
        }
    }

    @Test
    fun `nonsense sizes give a one pixel square instead of a crash`() {
        assertEquals(1, ScanGeometry.squareSide(0, 0, 0, 100, 100))
        assertEquals(1, ScanGeometry.squareSide(640, 480, 90, 0, 0))
    }

    @Test
    fun `a centred square starts halfway in`() {
        assertEquals(136, ScanGeometry.origin(720, 448))
        assertEquals(416, ScanGeometry.origin(1280, 448))
        assertEquals(0, ScanGeometry.origin(448, 448))
    }
}
