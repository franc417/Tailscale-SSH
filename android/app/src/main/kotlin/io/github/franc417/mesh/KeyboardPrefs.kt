package io.github.franc417.mesh

import android.content.Context
import io.github.franc417.mesh.input.KeyboardView
import io.github.franc417.mesh.input.LayoutText

/**
 * Remembers the keyboard layout the person made with the layout editor, and the vibration strength, and gives them back
 * to every keyboard in the app, so the input-only screen and the desktop view always show the same keyboard.
 */
object KeyboardPrefs {
    private const val FILE = "mesh_keyboard"

    // Bump the number when the default layout changes in a way people should see: a saved layout from an older default
    // is then not loaded (the editor's copy/paste can bring an old one back).
    private const val LAYOUT_KEY = "layout_v1"
    private const val HAPTIC_KEY = "haptic_level"
    private const val DEFAULT_HAPTIC_LEVEL = 2

    /** Loads the saved layout and strength into [keyboard] and saves again whenever either is changed there. */
    fun attach(context: Context, keyboard: KeyboardView) {
        val prefs = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        prefs.getString(LAYOUT_KEY, null)?.let { saved ->
            // A saved layout that no longer parses is ignored, which leaves the default in place.
            LayoutText.parse(saved).layout?.let { keyboard.setLayout(it) }
        }
        keyboard.onLayoutChanged = { layout ->
            prefs.edit().putString(LAYOUT_KEY, LayoutText.serialize(layout)).apply()
        }
        keyboard.setVibrationLevel(prefs.getInt(HAPTIC_KEY, DEFAULT_HAPTIC_LEVEL))
        keyboard.onHapticLevelChanged = { level -> prefs.edit().putInt(HAPTIC_KEY, level).apply() }
    }
}
