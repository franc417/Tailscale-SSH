package io.github.franc417.mesh.input

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class FloatingKeyboardSizingTest {
    // The phone the first desktop test ran on: a 1600 x 720 landscape stage and a keyboard about 505 px tall at full width.
    private val stageW = 1600
    private val stageH = 720
    private val naturalW = 1600
    private val naturalH = 505

    @Test
    fun enlargedKeyboardNeverTakesMoreThanHalfTheScreenHeight() {
        val s = FloatingKeyboardSizing.scale(stageW, stageH, naturalW, naturalH, enlarged = true)
        assertTrue("height ${naturalH * s} must be at most ${stageH / 2}", naturalH * s <= stageH / 2f + 0.5f)
        assertTrue(naturalW * s <= stageW + 0.5f)
    }

    @Test
    fun enlargedKeyboardUsesAllOfTheHalfScreenItIsAllowed() {
        val s = FloatingKeyboardSizing.scale(stageW, stageH, naturalW, naturalH, enlarged = true)
        assertEquals(360f, naturalH * s, 0.5f)
    }

    @Test
    fun compactIsClearlySmallerThanEnlarged() {
        val big = FloatingKeyboardSizing.scale(stageW, stageH, naturalW, naturalH, enlarged = true)
        val small = FloatingKeyboardSizing.scale(stageW, stageH, naturalW, naturalH, enlarged = false)
        assertTrue("compact $small should be well under enlarged $big", small <= big * 0.75f)
        assertTrue(small > 0.3f)
    }

    @Test
    fun widthLimitsTheScaleOnATallNarrowStage() {
        // Portrait-like stage: width, not height, is the limit; the keyboard must not be wider than the stage.
        val s = FloatingKeyboardSizing.scale(500, 2000, 1000, 400, enlarged = true)
        assertEquals(0.5f, s, 0.001f)
    }

    @Test
    fun aKeyboardThatAlreadyFitsIsNotMadeBiggerThanItsNaturalWidthAllows() {
        // Natural keyboard much smaller than the stage: the scale may exceed 1 only up to the stage's limits.
        val s = FloatingKeyboardSizing.scale(2000, 2000, 500, 200, enlarged = true)
        assertTrue(500 * s <= 2000.5f)
        assertTrue(200 * s <= 1000.5f)
    }

    @Test
    fun compactNeverGoesBelowTheTapableMinimumUnlessEnlargedIsAlreadySmaller() {
        // A tiny stage: enlarged is already very small, compact must not shrink it into nothing.
        val big = FloatingKeyboardSizing.scale(300, 200, 1600, 505, enlarged = true)
        val small = FloatingKeyboardSizing.scale(300, 200, 1600, 505, enlarged = false)
        assertTrue(small > 0f)
        assertTrue(small <= big + 0.0001f)
        assertTrue(small >= minOf(FloatingKeyboardSizing.MIN_SCALE, big) - 0.0001f)
    }

    @Test
    fun nonsenseSizesGiveScaleOne() {
        assertEquals(1f, FloatingKeyboardSizing.scale(0, 720, 1600, 505, true), 0f)
        assertEquals(1f, FloatingKeyboardSizing.scale(1600, 0, 1600, 505, false), 0f)
        assertEquals(1f, FloatingKeyboardSizing.scale(1600, 720, 0, 505, true), 0f)
        assertEquals(1f, FloatingKeyboardSizing.scale(1600, 720, 1600, -1, true), 0f)
    }

    @Test
    fun clampKeepsTheBoxFullyOnScreen() {
        // Box 400 wide placed at x=600 on a 1600-wide stage: it may move 600 left and 600 right.
        assertEquals(-600f, FloatingKeyboardSizing.clampOffset(-9999f, 600, 400, 1600), 0f)
        assertEquals(600f, FloatingKeyboardSizing.clampOffset(9999f, 600, 400, 1600), 0f)
        assertEquals(123f, FloatingKeyboardSizing.clampOffset(123f, 600, 400, 1600), 0f)
    }

    @Test
    fun aBoxBiggerThanTheStageStaysPutInsteadOfJumping() {
        assertEquals(0f, FloatingKeyboardSizing.clampOffset(50f, 0, 2000, 1600), 0f)
    }

    @Test
    fun aBoxExactlyAsBigAsTheStageCannotMove() {
        assertEquals(0f, FloatingKeyboardSizing.clampOffset(77f, 0, 1600, 1600), 0f)
    }
}
