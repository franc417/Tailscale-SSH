package io.github.franc417.mesh.core

/** The words the main page shows for the choice, kept apart from Android so they are tested on a plain JVM. */
object AudioChoice {
    /** Nothing saved yet means the computer. */
    fun fromSaved(saved: Boolean?): Boolean = saved ?: true

    /** One computer's own choice wins; else the old single choice from before each computer had its own; else the computer. */
    fun resolve(own: Boolean?, legacy: Boolean?): Boolean = own ?: legacy ?: true

    /** Which computer is highlighted on the main page: the saved one if it still exists, else the first, else none. */
    fun pickSelected(ids: List<String>, saved: String?): String? = if (saved != null && saved in ids) saved else ids.firstOrNull()

    /** The same button, naming the computer it belongs to, so two computers can each have their own. */
    fun buttonLabel(computerName: String, onComputer: Boolean): String =
        "Sound for $computerName:  " + if (onComputer) "Computer" else "Phone"

    fun buttonLabel(onComputer: Boolean): String = "Sound plays on:  " + if (onComputer) "Computer" else "Phone"

    fun explanation(onComputer: Boolean): String =
        if (onComputer) {
            "The sound stays on the computer: a song playing there keeps playing, and it is not paused when you open or close the desktop. The phone is silent."
        } else {
            "The sound is taken off the computer and plays on the phone while the desktop is open."
        }
}
