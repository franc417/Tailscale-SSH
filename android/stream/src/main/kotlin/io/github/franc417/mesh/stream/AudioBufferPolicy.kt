package io.github.franc417.mesh.stream

/**
 * How big the audio buffer should be, and when to make it bigger.
 *
 * A tiny buffer (10 ms, what Moonlight asks for) gives the lowest delay but runs dry whenever the phone stalls for
 * a moment, and a dry buffer is the scratch or crackle in the sound. The picture already arrives about a tenth of a
 * second after the event, so sound that is a little later than a few milliseconds is not noticed; a steadier buffer is
 * worth it. It starts at [INITIAL_MS] and grows by [STEP_MS] each time it ran dry, up to [MAX_MS]. No Android API.
 */
object AudioBufferPolicy {
    const val INITIAL_MS = 40
    const val STEP_MS = 20
    const val MAX_MS = 160

    private fun roundUpToPackets(frames: Int, samplesPerPacket: Int): Int =
        if (samplesPerPacket <= 0) frames else ((frames + samplesPerPacket - 1) / samplesPerPacket) * samplesPerPacket

    /** The buffer size, in sample frames, to start with: [INITIAL_MS], a whole number of packets. */
    fun initialFrames(sampleRate: Int, samplesPerPacket: Int): Int =
        roundUpToPackets(sampleRate * INITIAL_MS / 1000, samplesPerPacket)

    /** The most the buffer may ever grow to, in sample frames: [MAX_MS], a whole number of packets, never below the system's minimum. */
    fun capacityFrames(sampleRate: Int, samplesPerPacket: Int, systemMinimumFrames: Int): Int =
        maxOf(roundUpToPackets(sampleRate * MAX_MS / 1000, samplesPerPacket), systemMinimumFrames)

    /**
     * The size to use next, given the current one and how many times the buffer ran dry since the last look.
     * It only ever grows, one step at a time, and never past [capacityFrames].
     */
    fun nextFrames(currentFrames: Int, capacityFrames: Int, sampleRate: Int, samplesPerPacket: Int, underrunsSinceLastLook: Int): Int {
        if (underrunsSinceLastLook <= 0) return currentFrames
        val grown = roundUpToPackets(currentFrames + sampleRate * STEP_MS / 1000, samplesPerPacket)
        return minOf(grown, capacityFrames).coerceAtLeast(currentFrames)
    }

    fun toMs(frames: Int, sampleRate: Int): Int = if (sampleRate <= 0) 0 else frames * 1000 / sampleRate
}
