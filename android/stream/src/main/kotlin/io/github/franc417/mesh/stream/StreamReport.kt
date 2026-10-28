package io.github.franc417.mesh.stream

import java.util.Locale

/** What kind of address the computer is reached at, which matters a lot for delay (a direct LAN path or a tunnel). */
object HostKind {
    fun of(address: String): String {
        val parts = address.trim().split('.').mapNotNull { it.toIntOrNull() }
        if (parts.size != 4 || parts.any { it !in 0..255 }) return if (address.contains(':')) "IPv6 or a name" else "a name"
        val (a, b) = parts[0] to parts[1]
        return when {
            a == 100 && b in 64..127 -> "Tailscale (tunnel)"
            a == 10 || (a == 172 && b in 16..31) || (a == 192 && b == 168) -> "home network (LAN)"
            a == 169 && b == 254 -> "link-local (cable)"
            a == 127 -> "this phone itself"
            else -> "other"
        }
    }
}

/**
 * Builds the text the person pastes when the stream feels slow: where the delay is can be read from it. No Android
 * API, so the arithmetic is unit tested.
 */
object StreamReport {

    data class Sample(val snapshot: StreamStats.Snapshot, val rttMs: Int?)

    data class Input(
        val phone: String,
        val android: String,
        val decoder: String,
        val spsPatched: Int,
        val spsPatchFailed: Int,
        val spsPatchError: String?,
        val settings: StreamSettings,
        val view: String,
        val pointer: String,
        val host: String,
        val firstPictureMs: Long?,
        val samples: List<Sample>,
        val totalShown: Long,
        val totalDropped: Long,
        val audioBufferMs: Int = 0,
        val audioUnderruns: Int = 0,
    )

    fun build(i: Input): String {
        val l = Locale.ROOT
        val lines = ArrayList<String>()
        lines += "Mesh stream report"
        lines += "phone: ${i.phone}, Android ${i.android}"
        lines += "decoder: ${i.decoder.ifEmpty { "none yet" }}"
        lines += "video header rewrite: " + when {
            i.spsPatched > 0 && i.spsPatchFailed == 0 -> "applied (${i.spsPatched}x)"
            i.spsPatched > 0 -> "applied ${i.spsPatched}x, failed ${i.spsPatchFailed}x (${i.spsPatchError})"
            i.spsPatchFailed > 0 -> "FAILED ${i.spsPatchFailed}x (${i.spsPatchError}), so the decoder uses its default delay"
            else -> "not seen (H.264 header not received yet, or HEVC)"
        }
        val s = i.settings
        lines += "stream: ${s.width}x${s.height} at ${s.fps} fps, ${s.bitrateKbps} kbps, ${if (s.preferHevc) "HEVC preferred" else "H.264"}; view ${i.view}, pointer ${i.pointer}"
        lines += "computer: ${i.host} = ${HostKind.of(i.host)}"
        lines += "first picture after: ${i.firstPictureMs?.let { String.format(l, "%.1f s", it / 1000.0) } ?: "never"}"

        val n = i.samples.size
        if (n == 0) {
            lines += "no statistics yet (the stream had not been running for a second)"
        } else {
            val snaps = i.samples.map { it.snapshot }
            val rtts = i.samples.mapNotNull { it.rttMs }.filter { it > 0 }
            val hosts = snaps.mapNotNull { it.hostMs }
            lines += "last $n s:"
            lines += String.format(l, "  frames per second: average %.0f, lowest %d", snaps.map { it.fps }.average(), snaps.minOf { it.fps })
            lines += String.format(l, "  phone decode time: average %.0f ms, worst second %.0f ms", snaps.map { it.decodeMs }.average(), snaps.maxOf { it.decodeMs })
            lines += if (hosts.isEmpty()) "  computer encode time: not reported"
            else String.format(l, "  computer encode time: average %.1f ms, worst second %.1f ms", hosts.average(), hosts.max())
            lines += if (rtts.isEmpty()) "  network round trip: unknown"
            else String.format(l, "  network round trip: average %.0f ms, worst %d ms", rtts.average(), rtts.max())
            lines += "  frames dropped: ${snaps.sumOf { it.dropped }}"
        }
        lines += "since the start: ${i.totalShown} frames shown, ${i.totalDropped} dropped"
        lines += if (i.audioBufferMs > 0) "audio: ${i.audioBufferMs} ms buffer, ran dry ${i.audioUnderruns} time(s) (each one is a scratch)" else "audio: not running"
        val findings = verdicts(i)
        lines += "what the numbers say:"
        lines += findings.map { "  - $it" }
        return lines.joinToString("\n")
    }

    /** Plain-language reading of the numbers: the first entry is the biggest problem. */
    fun verdicts(i: Input): List<String> {
        val l = Locale.ROOT
        val out = ArrayList<String>()
        val snaps = i.samples.map { it.snapshot }
        if (snaps.isEmpty()) return listOf("not enough time has passed to say anything yet")
        val fps = i.settings.fps
        val budgetMs = 1000.0 / fps
        val avgFps = snaps.map { it.fps }.average()
        val hosts = snaps.mapNotNull { it.hostMs }
        val rtts = i.samples.mapNotNull { it.rttMs }.filter { it > 0 }
        val avgDecode = snaps.map { it.decodeMs }.average()

        if (hosts.isNotEmpty() && hosts.average() > budgetMs) {
            out += String.format(l, "The computer's encoder is the main delay: %.0f ms to encode each frame, but only %.0f ms are available at %d fps. " +
                "Encoding on the processor instead of the graphics chip is the usual cause. Try the Smooth or Quick preset, and run `python3 mesh_input.py diagnose` on the computer to see which encoder Sunshine uses.",
                hosts.average(), budgetMs, fps)
        }
        if (avgFps < fps * 0.9) {
            out += String.format(l, "Only %.0f of %d frames per second arrive, so motion is choppy and the delay grows.", avgFps, fps)
        }
        if (avgDecode > budgetMs) {
            out += String.format(l, "The phone takes %.0f ms to decode a frame, more than the %.0f ms available: lower the resolution (Smooth or Quick).", avgDecode, budgetMs)
        }
        if (rtts.isNotEmpty() && rtts.average() > 30) {
            out += String.format(l, "The network round trip is %.0f ms, which is slow for a home network: try the 5 GHz Wi-Fi band or move closer to the router.", rtts.average())
        }
        if (i.audioUnderruns > 0) {
            out += "The sound ran dry ${i.audioUnderruns} time(s); the audio buffer grows each time, so it should settle (now ${i.audioBufferMs} ms)."
        }
        if (out.isEmpty()) out += "No bottleneck stands out in these numbers."
        return out
    }
}
