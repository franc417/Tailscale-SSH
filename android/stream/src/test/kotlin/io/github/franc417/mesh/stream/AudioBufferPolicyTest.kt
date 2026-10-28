package io.github.franc417.mesh.stream

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioBufferPolicyTest {
    private val rate = 48_000
    private val packet = 240   // 5 ms of audio, what Sunshine sends

    @Test
    fun `the first buffer is 40 ms and a whole number of packets`() {
        val frames = AudioBufferPolicy.initialFrames(rate, packet)
        assertEquals(1920, frames)
        assertEquals(0, frames % packet)
        assertEquals(40, AudioBufferPolicy.toMs(frames, rate))
    }

    @Test
    fun `a packet size that does not divide 40 ms rounds up to whole packets`() {
        val frames = AudioBufferPolicy.initialFrames(rate, 480 + 7)   // an odd packet size
        assertEquals(0, frames % (480 + 7))
        assertTrue(frames >= 1920)
    }

    @Test
    fun `the capacity is 160 ms and never below what the system needs`() {
        assertEquals(7680, AudioBufferPolicy.capacityFrames(rate, packet, 1000))
        assertEquals(9000, AudioBufferPolicy.capacityFrames(rate, packet, 9000))
    }

    @Test
    fun `no dry spells means no change`() {
        assertEquals(1920, AudioBufferPolicy.nextFrames(1920, 7680, rate, packet, 0))
        assertEquals(1920, AudioBufferPolicy.nextFrames(1920, 7680, rate, packet, -3))
    }

    @Test
    fun `a dry spell grows the buffer by 20 ms`() {
        assertEquals(2880, AudioBufferPolicy.nextFrames(1920, 7680, rate, packet, 1))
        assertEquals(2880, AudioBufferPolicy.nextFrames(1920, 7680, rate, packet, 25))   // many at once is still one step
    }

    @Test
    fun `it grows step by step up to the capacity and stops there`() {
        var size = AudioBufferPolicy.initialFrames(rate, packet)
        val capacity = AudioBufferPolicy.capacityFrames(rate, packet, 0)
        val seen = mutableListOf(AudioBufferPolicy.toMs(size, rate))
        repeat(20) {
            size = AudioBufferPolicy.nextFrames(size, capacity, rate, packet, 1)
            seen += AudioBufferPolicy.toMs(size, rate)
        }
        assertEquals(listOf(40, 60, 80, 100, 120, 140, 160), seen.distinct())
        assertEquals(capacity, size)
    }

    @Test
    fun `it never shrinks even if the capacity is smaller than the current size`() {
        assertEquals(3000, AudioBufferPolicy.nextFrames(3000, 2000, rate, packet, 1))
    }

    @Test
    fun `nonsense rates give zero milliseconds instead of dividing by zero`() {
        assertEquals(0, AudioBufferPolicy.toMs(1000, 0))
    }
}
