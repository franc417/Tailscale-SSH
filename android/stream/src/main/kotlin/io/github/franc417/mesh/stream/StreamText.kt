package io.github.franc417.mesh.stream

/** What the user chose for the stream. Sizes are the resolution the host encodes, not the phone's screen. */
data class StreamSettings(
    val width: Int = 1280,
    val height: Int = 720,
    val fps: Int = 60,
    val bitrateKbps: Int = 10_000,
    /** HEVC is smaller on the wire but many entry-level phones decode H.264 better, so H.264 is the default. */
    val preferHevc: Boolean = false,
) {
    init {
        require(width > 0 && height > 0) { "resolution must be positive" }
        require(fps in 10..240) { "fps out of range" }
        require(bitrateKbps in 500..150_000) { "bitrate out of range" }
    }

    companion object {
        /** The presets offered in the app, best-first for a weak phone. */
        val presets: List<Pair<String, StreamSettings>> = listOf(
            "Smooth · 720p 60 fps" to StreamSettings(1280, 720, 60, 10_000),
            "Sharp · 1080p 60 fps" to StreamSettings(1920, 1080, 60, 20_000),
            "Light · 720p 30 fps" to StreamSettings(1280, 720, 30, 5_000),
        )
    }
}

/** Turns the numeric failure codes of the streaming core into sentences a person can act on. */
object StreamText {
    // Values of MoonBridge.ML_ERROR_* and ML_PORT_FLAG_* (guarded by a test); repeated so this object needs no native library.
    const val ERROR_GRACEFUL_TERMINATION = 0
    const val ERROR_NO_VIDEO_TRAFFIC = -100
    const val ERROR_NO_VIDEO_FRAME = -101
    const val ERROR_UNEXPECTED_EARLY_TERMINATION = -102
    const val ERROR_PROTECTED_CONTENT = -103
    const val ERROR_FRAME_CONVERSION = -104

    private val ports = listOf(
        0x0001 to "TCP 47984",
        0x0002 to "TCP 47989",
        0x0004 to "TCP 48010",
        0x0100 to "UDP 47998",
        0x0200 to "UDP 47999",
        0x0400 to "UDP 48000",
        0x0800 to "UDP 48010",
    )

    fun portList(flags: Int): String = ports.filter { (flag, _) -> flags and flag != 0 }.joinToString(", ") { it.second }

    /** [stage] is the core's name for the step that failed (may be blank), [portFlags] the ports that step needs. */
    fun stageFailure(stage: String, portFlags: Int, errorCode: Int): String {
        val what = if (stage.isBlank()) "starting the stream" else stage
        val sb = StringBuilder("Failed while $what")
        if (errorCode != 0) sb.append(" (error ").append(errorCode).append(')')
        sb.append('.')
        val list = portList(portFlags)
        if (list.isNotEmpty()) {
            sb.append(" Mesh could not use ").append(list)
                .append(" on the computer. Check that Sunshine is running there and that its firewall allows these ports.")
        }
        return sb.toString()
    }

    /** Null means the stream ended normally (the host or the user stopped it), so nothing needs reporting as an error. */
    fun termination(errorCode: Int): String? = when (errorCode) {
        ERROR_GRACEFUL_TERMINATION -> null
        ERROR_NO_VIDEO_TRAFFIC ->
            "No video reached the phone. A firewall on the computer or network is probably blocking UDP 47998–48000."
        ERROR_NO_VIDEO_FRAME ->
            "Video packets arrived but no picture could be built. The network may be dropping too much; try the Light preset."
        ERROR_UNEXPECTED_EARLY_TERMINATION ->
            "The computer ended the stream unexpectedly. Check Sunshine's log on the computer."
        ERROR_PROTECTED_CONTENT ->
            "The computer is showing protected content that cannot be streamed."
        ERROR_FRAME_CONVERSION ->
            "The computer could not convert a frame for streaming."
        else -> "The connection was lost (error $errorCode)."
    }
}
