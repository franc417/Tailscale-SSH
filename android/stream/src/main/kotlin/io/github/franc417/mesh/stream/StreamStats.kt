package io.github.franc417.mesh.stream

import java.util.Locale

/**
 * Counts what happens to frames while streaming and turns it into the numbers that explain lag:
 * how many frames a second reach the screen, how long the phone takes from receiving a frame to showing it
 * (decode), how long the computer took to encode it (host), and how many frames were thrown away.
 * No Android API, so it is unit tested. Written by the decoder's threads, read once a second by the screen.
 */
class StreamStats {

    data class Snapshot(
        val fps: Int,
        /** Average milliseconds from the frame being queued to the decoder until it was released to the screen. */
        val decodeMs: Float,
        /** Average milliseconds the computer spent encoding a frame, or null if it does not report that. */
        val hostMs: Float?,
        val dropped: Int,
    )

    private var rendered = 0
    private var decodeSumMs = 0L
    private var hostSumTenths = 0L
    private var hostFrames = 0
    private var dropped = 0

    @Synchronized
    fun onRendered(decodeMs: Long) {
        rendered++
        decodeSumMs += decodeMs.coerceAtLeast(0)
    }

    @Synchronized
    fun onDropped() {
        dropped++
    }

    /** [tenthsOfMs] is the host's processing latency in 0.1 ms units; 0 means the host did not report one. */
    @Synchronized
    fun onHostLatency(tenthsOfMs: Int) {
        if (tenthsOfMs > 0) {
            hostSumTenths += tenthsOfMs
            hostFrames++
        }
    }

    /** Returns what happened over the last [windowMs] milliseconds and starts a new window. */
    @Synchronized
    fun take(windowMs: Long): Snapshot {
        val fps = if (windowMs > 0) Math.round(rendered * 1000.0 / windowMs).toInt() else 0
        val decode = if (rendered > 0) decodeSumMs.toFloat() / rendered else 0f
        val host = if (hostFrames > 0) hostSumTenths / 10f / hostFrames else null
        val snapshot = Snapshot(fps, decode, host, dropped)
        rendered = 0
        decodeSumMs = 0
        hostSumTenths = 0
        hostFrames = 0
        dropped = 0
        return snapshot
    }

    companion object {
        /** For example `58 fps · net 12 ms · host 4.1 ms · dec 7 ms · 2 dropped`. [rttMs] null or 0 means unknown. */
        fun describe(s: Snapshot, rttMs: Int?): String {
            val parts = ArrayList<String>()
            parts += "${s.fps} fps"
            if (rttMs != null && rttMs > 0) parts += "net $rttMs ms"
            if (s.hostMs != null) parts += String.format(Locale.ROOT, "host %.1f ms", s.hostMs)
            parts += String.format(Locale.ROOT, "dec %.0f ms", s.decodeMs)
            if (s.dropped > 0) parts += "${s.dropped} dropped"
            return parts.joinToString("  \u00B7  ")
        }
    }
}
