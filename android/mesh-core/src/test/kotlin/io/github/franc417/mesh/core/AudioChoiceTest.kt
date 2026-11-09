package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioChoiceTest {

    @Test
    fun nothingSavedMeansTheSoundStaysOnTheComputer() {
        assertTrue(AudioChoice.fromSaved(null))
    }

    @Test
    fun aSavedChoiceIsKept() {
        assertTrue(AudioChoice.fromSaved(true))
        assertFalse(AudioChoice.fromSaved(false))
    }

    @Test
    fun theButtonNamesTheDeviceTheSoundPlaysOn() {
        assertEquals("Sound plays on:  Computer", AudioChoice.buttonLabel(true))
        assertEquals("Sound plays on:  Phone", AudioChoice.buttonLabel(false))
    }

    @Test
    fun theExplanationSaysWhatHappensToASongThatIsPlaying() {
        assertTrue(AudioChoice.explanation(true).contains("keeps playing"))
        assertTrue(AudioChoice.explanation(true).contains("phone is silent"))
        assertTrue(AudioChoice.explanation(false).contains("plays on the phone"))
    }

    @Test
    fun aComputersOwnChoiceBeatsTheOldSingleChoice() {
        assertFalse(AudioChoice.resolve(own = false, legacy = true))
        assertTrue(AudioChoice.resolve(own = true, legacy = false))
    }

    @Test
    fun theOldSingleChoiceIsUsedUntilAComputerGetsItsOwn() {
        assertFalse(AudioChoice.resolve(own = null, legacy = false))
        assertTrue(AudioChoice.resolve(own = null, legacy = true))
        assertTrue(AudioChoice.resolve(own = null, legacy = null))
    }

    @Test
    fun theButtonNamesTheComputerItBelongsTo() {
        assertEquals("Sound for mkz:  Computer", AudioChoice.buttonLabel("mkz", true))
        assertEquals("Sound for Office PC:  Phone", AudioChoice.buttonLabel("Office PC", false))
    }

    @Test
    fun theHighlightedComputerIsTheSavedOneWhileItExists() {
        assertEquals("b", AudioChoice.pickSelected(listOf("a", "b"), "b"))
    }

    @Test
    fun aRemovedOrUnknownSelectionFallsBackToTheFirstComputer() {
        assertEquals("a", AudioChoice.pickSelected(listOf("a", "b"), "gone"))
        assertEquals("a", AudioChoice.pickSelected(listOf("a", "b"), null))
        assertEquals(null, AudioChoice.pickSelected(emptyList(), "a"))
    }
}
