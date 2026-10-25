package io.github.franc417.mesh.stream

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StreamStatsTest {

    @Test
    fun fpsIsFramesPerSecondOverTheWindow() {
        val s = StreamStats()
        repeat(60) { s.onRendered(8) }
        assertEquals(60, s.take(1000).fps)
        repeat(30) { s.onRendered(8) }
        assertEquals(60, s.take(500).fps) // 30 frames in half a second
    }

    @Test
    fun decodeTimeIsTheAverage() {
        val s = StreamStats()
        s.onRendered(10)
        s.onRendered(20)
        s.onRendered(30)
        assertEquals(20f, s.take(1000).decodeMs, 0.001f)
    }

    @Test
    fun aNegativeDecodeTimeFromClockSkewCountsAsZero() {
        val s = StreamStats()
        s.onRendered(-5)
        s.onRendered(10)
        assertEquals(5f, s.take(1000).decodeMs, 0.001f)
    }

    @Test
    fun hostLatencyIsAveragedInTenthsOfAMillisecondAndIgnoresZero() {
        val s = StreamStats()
        s.onHostLatency(40)
        s.onHostLatency(0) // the host did not report this one
        s.onHostLatency(60)
        assertEquals(5.0f, s.take(1000).hostMs!!, 0.001f)
    }

    @Test
    fun hostLatencyIsNullWhenNeverReported() {
        val s = StreamStats()
        s.onRendered(5)
        s.onHostLatency(0)
        assertNull(s.take(1000).hostMs)
    }

    @Test
    fun takingStartsAFreshWindow() {
        val s = StreamStats()
        repeat(10) { s.onRendered(5) }
        s.onDropped()
        s.onHostLatency(20)
        s.take(1000)
        val second = s.take(1000)
        assertEquals(0, second.fps)
        assertEquals(0, second.dropped)
        assertEquals(0f, second.decodeMs, 0f)
        assertNull(second.hostMs)
    }

    @Test
    fun droppedFramesAreCounted() {
        val s = StreamStats()
        s.onDropped()
        s.onDropped()
        assertEquals(2, s.take(1000).dropped)
    }

    @Test
    fun aZeroWindowGivesZeroFpsInsteadOfDividingByZero() {
        val s = StreamStats()
        s.onRendered(5)
        assertEquals(0, s.take(0).fps)
    }

    @Test
    fun describeShowsEverythingKnown() {
        val text = StreamStats.describe(StreamStats.Snapshot(58, 7.2f, 4.14f, 2), 12)
        assertEquals("58 fps  \u00B7  net 12 ms  \u00B7  host 4.1 ms  \u00B7  dec 7 ms  \u00B7  2 dropped", text)
    }

    @Test
    fun describeLeavesOutWhatIsUnknownOrZero() {
        val text = StreamStats.describe(StreamStats.Snapshot(60, 3f, null, 0), null)
        assertEquals("60 fps  \u00B7  dec 3 ms", text)
        assertTrue(!StreamStats.describe(StreamStats.Snapshot(60, 3f, null, 0), 0).contains("net"))
    }
}
