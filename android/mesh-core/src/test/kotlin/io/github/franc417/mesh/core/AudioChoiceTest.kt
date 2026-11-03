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
}
