package io.github.franc417.mesh.input

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/**
 * A short vibration for each key press, driven straight through the vibrator
 * service. The earlier version used View.performHapticFeedback, which Android
 * only honours when the system's "touch feedback" / keyboard vibration setting
 * is on, and many phones ship with it off, so keys felt dead. This works
 * regardless of that setting (it needs only the VIBRATE permission).
 */
class Haptics(context: Context) {

    private val vibrator: Vibrator? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            (context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }

    /** 0 off, 1 light, 2 medium, 3 strong. */
    var level = 2

    fun tick() {
        if (level <= 0) return
        val v = vibrator ?: return
        if (!v.hasVibrator()) return
        val millis = when (level) { 1 -> 10L; 2 -> 16L; else -> 26L }
        val amplitude = when (level) { 1 -> 70; 2 -> 150; else -> 255 }
        val effect = if (v.hasAmplitudeControl()) {
            VibrationEffect.createOneShot(millis, amplitude)
        } else {
            VibrationEffect.createOneShot(millis, VibrationEffect.DEFAULT_AMPLITUDE)
        }
        v.vibrate(effect)
    }
}
