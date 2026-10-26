package io.github.franc417.mesh.input

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TwoFingerTapTest {
    private val tap = TwoFingerTap(maxDurationMs = 300, maxMovementPx = 12f)

    @Test
    fun `two fingers that touch briefly and barely move are a tap`() {
        tap.begin(1000)
        tap.move(3f)
        tap.move(-4f)
        assertTrue(tap.end(1180))
    }

    @Test
    fun `holding too long is not a tap`() {
        tap.begin(1000)
        assertTrue("right at the limit is still a tap", tap.end(1300))
        tap.begin(1000)
        assertFalse(tap.end(1301))
    }

    @Test
    fun `moving too far is a scroll, not a tap`() {
        tap.begin(1000)
        tap.move(7f)
        assertFalse(tap.movedTooFar)
        tap.move(7f)
        assertTrue(tap.movedTooFar)
        assertFalse(tap.end(1100))
    }

    @Test
    fun `movement in either direction counts`() {
        tap.begin(0)
        tap.move(-13f)
        assertTrue(tap.movedTooFar)
        assertFalse(tap.end(50))
    }

    @Test
    fun `a third finger cancels it`() {
        tap.begin(1000)
        tap.cancel()
        assertFalse(tap.end(1100))
    }

    @Test
    fun `lifting without ever starting is not a tap`() {
        assertFalse(tap.end(1000))
    }

    @Test
    fun `a gesture ends with the first lift and cannot be counted twice`() {
        tap.begin(1000)
        assertTrue(tap.end(1100))
        assertFalse("the second finger lifting is not another tap", tap.end(1120))
    }

    @Test
    fun `a new gesture starts clean`() {
        tap.begin(1000)
        tap.move(50f)
        tap.cancel()
        assertFalse(tap.end(1100))
        tap.begin(2000)
        assertFalse(tap.movedTooFar)
        assertTrue(tap.end(2100))
    }

    @Test
    fun `a clock that went backwards is not a tap`() {
        tap.begin(1000)
        assertFalse(tap.end(900))
    }
}
