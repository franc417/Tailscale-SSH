package io.github.franc417.mesh

import android.content.Context
import io.github.franc417.mesh.core.AudioChoice

/**
 * Which device the sound comes out of when a desktop is opened: the computer (the default: a song that is playing there keeps
 * playing, and is not moved or paused when the desktop is opened or closed) or the phone. Every computer has its own choice,
 * made on the main page BEFORE opening: tap the computer's card to highlight it, then switch the button at the top.
 * A choice saved before computers had their own is used until a computer gets one.
 */
object AudioPrefs {
    private const val FILE = "mesh_audio"
    private const val LEGACY_KEY = "audio_on_computer"
    private const val SELECTED_KEY = "selected_computer"

    private fun keyFor(computerId: String) = "$LEGACY_KEY:$computerId"

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun onComputer(context: Context, computerId: String): Boolean {
        val p = prefs(context)
        val own = if (p.contains(keyFor(computerId))) p.getBoolean(keyFor(computerId), true) else null
        val legacy = if (p.contains(LEGACY_KEY)) p.getBoolean(LEGACY_KEY, true) else null
        return AudioChoice.resolve(own, legacy)
    }

    fun setOnComputer(context: Context, computerId: String, onComputer: Boolean) {
        prefs(context).edit().putBoolean(keyFor(computerId), onComputer).apply()
    }

    /** The computer highlighted on the main page, if one was chosen before. */
    fun selectedId(context: Context): String? = prefs(context).getString(SELECTED_KEY, null)

    fun setSelectedId(context: Context, computerId: String) {
        prefs(context).edit().putString(SELECTED_KEY, computerId).apply()
    }

    /** Drops what was saved for a computer that was removed. */
    fun forget(context: Context, computerId: String) {
        prefs(context).edit().remove(keyFor(computerId)).apply()
    }
}
