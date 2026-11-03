package io.github.franc417.mesh

import android.content.Context
import io.github.franc417.mesh.core.AudioChoice

/**
 * Which device the sound comes out of when the desktop is opened: the computer (the default: a song that is playing there keeps
 * playing, and is not moved or paused when the desktop is opened or closed) or the phone. Chosen on the main page BEFORE opening.
 */
object AudioPrefs {
    private const val FILE = "mesh_audio"
    private const val KEY = "audio_on_computer"

    fun onComputer(context: Context): Boolean = AudioChoice.fromSaved(read(context))

    fun setOnComputer(context: Context, onComputer: Boolean) {
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putBoolean(KEY, onComputer).apply()
    }

    private fun read(context: Context): Boolean? {
        val prefs = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        return if (prefs.contains(KEY)) prefs.getBoolean(KEY, true) else null
    }
}
