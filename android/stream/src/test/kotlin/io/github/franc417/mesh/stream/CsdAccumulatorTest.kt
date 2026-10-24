package io.github.franc417.mesh.stream

import com.limelight.nvstream.jni.MoonBridge
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CsdAccumulatorTest {
    private fun nal(vararg b: Int) = ByteArray(b.size) { b[it].toByte() }

    @Test
    fun constantsMatchMoonBridge() {
        // MoonBridge's constants are compile-time constants, so reading them does not load the native library.
        assertEquals(MoonBridge.BUFFER_TYPE_PICDATA, CsdAccumulator.BUFFER_TYPE_PICDATA)
        assertEquals(MoonBridge.BUFFER_TYPE_SPS, CsdAccumulator.BUFFER_TYPE_SPS)
        assertEquals(MoonBridge.BUFFER_TYPE_PPS, CsdAccumulator.BUFFER_TYPE_PPS)
        assertEquals(MoonBridge.BUFFER_TYPE_VPS, CsdAccumulator.BUFFER_TYPE_VPS)
    }

    @Test
    fun joinsParameterSetsInVpsSpsPpsOrderWhateverTheArrivalOrder() {
        val a = CsdAccumulator()
        a.beginFrame(7)
        assertTrue(a.add(CsdAccumulator.BUFFER_TYPE_PPS, nal(0, 0, 0, 1, 0x68), 5))
        assertTrue(a.add(CsdAccumulator.BUFFER_TYPE_SPS, nal(0, 0, 0, 1, 0x67), 5))
        assertTrue(a.add(CsdAccumulator.BUFFER_TYPE_VPS, nal(0, 0, 0, 1, 0x40), 5))
        assertArrayEquals(
            nal(0, 0, 0, 1, 0x40, 0, 0, 0, 1, 0x67, 0, 0, 0, 1, 0x68),
            a.toByteArray(),
        )
    }

    @Test
    fun onlyTheFirstLengthBytesAreKept() {
        val a = CsdAccumulator()
        a.beginFrame(1)
        a.add(CsdAccumulator.BUFFER_TYPE_SPS, nal(1, 2, 3, 9, 9, 9), 3)
        assertArrayEquals(nal(1, 2, 3), a.toByteArray())
    }

    @Test
    fun pictureDataIsNotAParameterSet() {
        val a = CsdAccumulator()
        a.beginFrame(1)
        assertFalse(a.add(CsdAccumulator.BUFFER_TYPE_PICDATA, nal(1, 2, 3), 3))
        assertTrue(a.isEmpty)
    }

    @Test
    fun aNewFrameNumberDropsTheOldSets() {
        val a = CsdAccumulator()
        a.beginFrame(1)
        a.add(CsdAccumulator.BUFFER_TYPE_SPS, nal(1), 1)
        a.beginFrame(2)
        assertTrue(a.isEmpty)
        a.add(CsdAccumulator.BUFFER_TYPE_SPS, nal(2), 1)
        assertArrayEquals(nal(2), a.toByteArray())
    }

    @Test
    fun theSameFrameNumberKeepsCollecting() {
        val a = CsdAccumulator()
        a.beginFrame(5)
        a.add(CsdAccumulator.BUFFER_TYPE_SPS, nal(1), 1)
        a.beginFrame(5)
        a.add(CsdAccumulator.BUFFER_TYPE_PPS, nal(2), 1)
        assertArrayEquals(nal(1, 2), a.toByteArray())
    }

    @Test
    fun clearEmptiesAndAllowsTheSameFrameNumberAgain() {
        val a = CsdAccumulator()
        a.beginFrame(3)
        a.add(CsdAccumulator.BUFFER_TYPE_SPS, nal(1), 1)
        a.clear()
        assertTrue(a.isEmpty)
        a.beginFrame(3)
        a.add(CsdAccumulator.BUFFER_TYPE_SPS, nal(4), 1)
        assertArrayEquals(nal(4), a.toByteArray())
    }
}
