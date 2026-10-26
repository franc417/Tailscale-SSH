package io.github.franc417.mesh.input

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PointerPolicyTest {
    private var clock = 1_000_000L
    private val policy = PointerPolicy { clock }

    private fun at(ms: Long) {
        clock = 1_000_000L + ms
    }

    @Test
    fun `trackpad mode always clicks where the pointer is`() {
        policy.mode = PointerMode.TRACKPAD
        assertEquals(PointerTap.CLICK_IN_PLACE, policy.tapAction())
        policy.noteTrackpadUse()
        assertEquals(PointerTap.CLICK_IN_PLACE, policy.tapAction())
        assertFalse(policy.usesAbsolute)
    }

    @Test
    fun `touch mode always jumps to the finger`() {
        policy.mode = PointerMode.TOUCH
        policy.noteTrackpadUse()
        assertEquals(PointerTap.JUMP_AND_CLICK, policy.tapAction())
        assertTrue(policy.usesAbsolute)
    }

    @Test
    fun `auto mode jumps when the trackpad has not been used`() {
        policy.mode = PointerMode.AUTO
        assertEquals(PointerTap.JUMP_AND_CLICK, policy.tapAction())
    }

    @Test
    fun `auto mode clicks in place right after the pointer was slid onto its target`() {
        // The case that matters: slide the pointer to the small close button, then tap anywhere to press it.
        policy.mode = PointerMode.AUTO
        at(0)
        policy.noteTrackpadUse()
        at(900)
        assertEquals(PointerTap.CLICK_IN_PLACE, policy.tapAction())
    }

    @Test
    fun `auto mode jumps again once the trackpad has been left alone for a while`() {
        policy.mode = PointerMode.AUTO
        at(0)
        policy.noteTrackpadUse()
        at(PointerPolicy.TRACKPAD_GRACE_MS)
        assertEquals("exactly at the limit still counts", PointerTap.CLICK_IN_PLACE, policy.tapAction())
        at(PointerPolicy.TRACKPAD_GRACE_MS * 2 + 1)
        assertEquals(PointerTap.JUMP_AND_CLICK, policy.tapAction())
    }

    @Test
    fun `just past the limit is already touch`() {
        policy.mode = PointerMode.AUTO
        at(0)
        policy.noteTrackpadUse()
        at(PointerPolicy.TRACKPAD_GRACE_MS + 1)
        assertEquals(PointerTap.JUMP_AND_CLICK, policy.tapAction())
    }

    @Test
    fun `each click in place renews the window so a double click and repeated clicks stay put`() {
        policy.mode = PointerMode.AUTO
        at(0)
        policy.noteTrackpadUse()
        at(2000)
        assertEquals(PointerTap.CLICK_IN_PLACE, policy.tapAction())   // renews: window now runs to 4500
        at(4400)
        assertEquals(PointerTap.CLICK_IN_PLACE, policy.tapAction())
        at(4400 + PointerPolicy.TRACKPAD_GRACE_MS + 1)
        assertEquals(PointerTap.JUMP_AND_CLICK, policy.tapAction())
    }

    @Test
    fun `a jump does not start a trackpad window`() {
        policy.mode = PointerMode.AUTO
        assertEquals(PointerTap.JUMP_AND_CLICK, policy.tapAction())
        at(100)
        assertEquals("tapping elsewhere right after a touch tap is a new touch tap", PointerTap.JUMP_AND_CLICK, policy.tapAction())
    }

    @Test
    fun `changing the mode forgets recent trackpad use`() {
        policy.mode = PointerMode.AUTO
        policy.noteTrackpadUse()
        policy.mode = PointerMode.TOUCH
        policy.mode = PointerMode.AUTO
        assertEquals(PointerTap.JUMP_AND_CLICK, policy.tapAction())
    }

    @Test
    fun `drags start under the finger in touch mode, and in auto only when selecting`() {
        policy.mode = PointerMode.TRACKPAD
        assertFalse(policy.dragIsDirect(selecting = false))
        assertFalse(policy.dragIsDirect(selecting = true))
        policy.mode = PointerMode.TOUCH
        assertTrue(policy.dragIsDirect(selecting = false))
        policy.mode = PointerMode.AUTO
        assertFalse(policy.dragIsDirect(selecting = false))
        assertTrue(policy.dragIsDirect(selecting = true))
    }

    @Test
    fun `only auto mode turns a held finger into a press and drag`() {
        policy.mode = PointerMode.TRACKPAD
        assertFalse(policy.longPressStartsDrag())
        policy.mode = PointerMode.TOUCH
        assertFalse(policy.longPressStartsDrag())
        policy.mode = PointerMode.AUTO
        assertTrue(policy.longPressStartsDrag())
    }
}
