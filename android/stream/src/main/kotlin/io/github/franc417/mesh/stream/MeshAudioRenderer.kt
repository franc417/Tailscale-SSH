package io.github.franc417.mesh.stream

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import com.limelight.LimeLog
import com.limelight.nvstream.av.audio.AudioRenderer
import com.limelight.nvstream.jni.MoonBridge

/**
 * Plays the PCM that moonlight-common-c decodes from the host's Opus stream.
 *
 * Stereo only: Mesh asks the host for a stereo stream, so there is nothing to downmix. It follows the same policy as
 * Moonlight's own renderer: low-latency track, small buffer first, and never let more than ~40 ms of extra audio
 * queue up (it is dropped instead, so sound cannot drift behind the picture).
 */
class MeshAudioRenderer : AudioRenderer {
    private var track: AudioTrack? = null

    override fun setup(audioConfiguration: MoonBridge.AudioConfiguration, sampleRate: Int, samplesPerFrame: Int): Int {
        if (audioConfiguration.channelCount != 2) {
            LimeLog.severe("MeshAudioRenderer: only stereo is supported, got ${audioConfiguration.channelCount} channels")
            return -1
        }
        val channelMask = AudioFormat.CHANNEL_OUT_STEREO
        val bytesPerFrame = 2 * samplesPerFrame * 2
        val minimum = AudioTrack.getMinBufferSize(sampleRate, channelMask, AudioFormat.ENCODING_PCM_16BIT)

        // 1) small buffer, low latency  2) larger buffer, low latency  3) larger buffer, normal mode
        val attempts = listOf(
            Triple(bytesPerFrame * 2, true, "small buffer, low latency"),
            Triple(maxOf(minimum, bytesPerFrame * 2).roundUpTo(bytesPerFrame), true, "large buffer, low latency"),
            Triple(maxOf(minimum, bytesPerFrame * 2).roundUpTo(bytesPerFrame), false, "large buffer, normal"),
        )
        for ((size, lowLatency, label) in attempts) {
            // A low-latency track only works when the content rate matches the hardware rate.
            if (lowLatency && AudioTrack.getNativeOutputSampleRate(AudioManager.STREAM_MUSIC) != sampleRate) continue
            try {
                val builder = AudioTrack.Builder()
                    .setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_GAME)
                            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                            .build(),
                    )
                    .setAudioFormat(
                        AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(sampleRate)
                            .setChannelMask(channelMask)
                            .build(),
                    )
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .setBufferSizeInBytes(size)
                if (lowLatency) builder.setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
                val t = builder.build()
                if (t.state != AudioTrack.STATE_INITIALIZED) {
                    t.release()
                    continue
                }
                t.play()
                track = t
                LimeLog.info("MeshAudioRenderer: $label ($size bytes)")
                return 0
            } catch (e: Exception) {
                LimeLog.warning("MeshAudioRenderer: $label failed: $e")
            }
        }
        return -2
    }

    override fun start() {}

    override fun stop() {}

    override fun playDecodedAudio(audioData: ShortArray) {
        val t = track ?: return
        // write() blocks until there is room, which can build a backlog; the pending-duration check bounds it.
        if (MoonBridge.getPendingAudioDuration() < 40) {
            t.write(audioData, 0, audioData.size)
        }
    }

    override fun cleanup() {
        val t = track ?: return
        try {
            t.pause()
            t.flush()
            t.release()
        } catch (_: Exception) {
        }
        track = null
    }

    private fun Int.roundUpTo(unit: Int): Int = ((this + unit - 1) / unit) * unit
}
