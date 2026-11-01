package io.github.franc417.mesh.stream

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PhoneShapedTest {

    private fun size(w: Int, h: Int): Pair<Int, Int> {
        val settings = StreamSettings.phoneShaped(w, h)!!.second
        return settings.width to settings.height
    }

    @Test
    fun aTwentyByNinePhoneGetsAStreamOfTheSameShape() {
        assertEquals(1920 to 864, size(2400, 1080))
    }

    @Test
    fun theStreamHasTheSameShapeWhicheverWayThePhoneIsHeld() {
        assertEquals(size(2400, 1080), size(1080, 2400))
    }

    @Test
    fun aNineteenPointFiveByNinePhoneGetsAnEvenHeight() {
        val (w, h) = size(2340, 1080)
        assertEquals(1920, w)
        assertEquals(886, h)
    }

    @Test
    fun aSixteenByNinePhoneGetsTheUsualFullHdSize() {
        assertEquals(1920 to 1080, size(1920, 1080))
    }

    @Test
    fun bothSidesAreAlwaysEven() {
        for ((w, h) in listOf(2412 to 1084, 2772 to 1272, 3200 to 1440, 2160 to 1080, 2640 to 1200)) {
            val (sw, sh) = size(w, h)
            assertTrue("$w x $h gave $sw x $sh", sw % 2 == 0 && sh % 2 == 0)
        }
    }

    @Test
    fun theNameSaysTheSizeSoTheComputerCanBeToldTheSame() {
        val (name, settings) = StreamSettings.phoneShaped(2400, 1080)!!
        assertTrue(name.startsWith("Phone screen"))
        assertTrue(name.contains("${settings.width}x${settings.height}"))
    }

    @Test
    fun anUnknownScreenGivesNoPreset() {
        assertNull(StreamSettings.phoneShaped(0, 1080))
        assertNull(StreamSettings.phoneShaped(2400, -1))
        assertNotNull(StreamSettings.phoneShaped(2400, 1080))
    }
}
