package io.github.franc417.mesh.core

/** The words the main page shows for the choice, kept apart from Android so they are tested on a plain JVM. */
object AudioChoice {
    /** Nothing saved yet means the computer. */
    fun fromSaved(saved: Boolean?): Boolean = saved ?: true

    fun buttonLabel(onComputer: Boolean): String = "Sound plays on:  " + if (onComputer) "Computer" else "Phone"

    fun explanation(onComputer: Boolean): String =
        if (onComputer) {
            "The sound stays on the computer: a song playing there keeps playing, and it is not paused when you open or close the desktop. The phone is silent."
        } else {
            "The sound is taken off the computer and plays on the phone while the desktop is open."
        }
}
