package io.github.franc417.mesh.stream

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Build
import android.os.Process
import com.limelight.LimeLog
import com.limelight.nvstream.av.audio.AudioRenderer
import com.limelight.nvstream.jni.MoonBridge

/**
 * Plays the PCM that moonlight-common-c decodes from the host's Opus stream.
 *
 * Stereo only: Mesh asks the host for a stereo stream, so there is nothing to downmix. Unlike Moonlight's own renderer
 * (a 10 ms buffer in low-latency mode) this one starts with a steadier 40 ms buffer in normal mode and grows it by
 * 20 ms every time it ran dry (see [AudioBufferPolicy]): a dry buffer is the occasional scratch, and on a phone that is
 * also busy decoding video it happens. The thread that feeds it runs at audio priority.
 */
class MeshAudioRenderer : AudioRenderer {
    private var track: AudioTrack? = null
    private var sampleRate = 48_000
    private var samplesPerPacket = 240
    private var capacityFrames = 0
    private var sizeFrames = 0
    private var lastUnderruns = 0
    private var writesSinceCheck = 0
    private var prioritySet = false

    /** The buffer size now, in milliseconds (it grows when the buffer ran dry). */
    @Volatile var bufferMs = 0; private set

    /** How many times the buffer has run dry since the stream started (each is an audible gap). */
    @Volatile var underruns = 0; private set

    override fun setup(audioConfiguration: MoonBridge.AudioConfiguration, sampleRate: Int, samplesPerFrame: Int): Int {
        if (audioConfiguration.channelCount != 2) {
            LimeLog.severe("MeshAudioRenderer: only stereo is supported, got ${audioConfiguration.channelCount} channels")
            return -1
        }
        this.sampleRate = sampleRate
        this.samplesPerPacket = samplesPerFrame
        val channelMask = AudioFormat.CHANNEL_OUT_STEREO
        val bytesPerSampleFrame = 2 * 2   // two channels, 16 bits
        val minimumFrames = AudioTrack.getMinBufferSize(sampleRate, channelMask, AudioFormat.ENCODING_PCM_16BIT) / bytesPerSampleFrame
        val steadyCapacity = AudioBufferPolicy.capacityFrames(sampleRate, samplesPerFrame, minimumFrames)
        val steadyStart = AudioBufferPolicy.initialFrames(sampleRate, samplesPerFrame)
        val packetFrames = samplesPerFrame

        // 1) steady buffer that can grow, normal mode  2) minimum-size buffer, normal mode
        // 3) small buffer, low-latency mode (Moonlight's own choice, if nothing else will start)
        data class Attempt(val capacity: Int, val start: Int, val lowLatency: Boolean, val label: String)
        val attempts = listOf(
            Attempt(steadyCapacity, steadyStart, false, "steady 40 ms buffer that can grow"),
            Attempt(maxOf(minimumFrames, steadyStart), maxOf(minimumFrames, steadyStart), false, "system minimum buffer"),
            Attempt(packetFrames * 2, packetFrames * 2, true, "small buffer, low latency"),
        )
        for (attempt in attempts) {
            if (attempt.lowLatency && AudioTrack.getNativeOutputSampleRate(AudioManager.STREAM_MUSIC) != sampleRate) continue
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
                    .setBufferSizeInBytes(attempt.capacity * bytesPerSampleFrame)
                if (attempt.lowLatency) builder.setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
                val t = builder.build()
                if (t.state != AudioTrack.STATE_INITIALIZED) {
                    t.release()
                    continue
                }
                // The track may have been given more room than asked for; use only the starting size for now.
                val set = t.setBufferSizeInFrames(attempt.start)
                capacityFrames = t.bufferCapacityInFrames
                sizeFrames = if (set > 0) set else t.bufferSizeInFrames
                bufferMs = AudioBufferPolicy.toMs(sizeFrames, sampleRate)
                t.play()
                track = t
                LimeLog.info("MeshAudioRenderer: ${attempt.label}: $bufferMs ms of ${AudioBufferPolicy.toMs(capacityFrames, sampleRate)} ms")
                return 0
            } catch (e: Exception) {
                LimeLog.warning("MeshAudioRenderer: ${attempt.label} failed: $e")
            }
        }
        return -2
    }

    override fun start() {}

    override fun stop() {}

    override fun playDecodedAudio(audioData: ShortArray) {
        val t = track ?: return
        if (!prioritySet) {
            prioritySet = true
            try {
                // This is the thread that keeps the buffer full, so it must not be starved by video work.
                Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
            } catch (_: Exception) {
            }
        }
        // write() blocks until there is room, which can build a backlog; the pending-duration check bounds it.
        if (MoonBridge.getPendingAudioDuration() < PENDING_LIMIT_MS) {
            t.write(audioData, 0, audioData.size)
        }
        if (++writesSinceCheck >= CHECK_EVERY_WRITES) {
            writesSinceCheck = 0
            growIfItRanDry(t)
        }
    }

    /** About once a second: if the buffer ran dry since the last look, make it a little bigger. */
    private fun growIfItRanDry(t: AudioTrack) {
        if (Build.VERSION.SDK_INT < 24) return
        val total = t.underrunCount
        val since = total - lastUnderruns
        lastUnderruns = total
        underruns = total
        val next = AudioBufferPolicy.nextFrames(sizeFrames, capacityFrames, sampleRate, samplesPerPacket, since)
        if (next != sizeFrames) {
            val set = t.setBufferSizeInFrames(next)
            if (set > 0) {
                sizeFrames = set
                bufferMs = AudioBufferPolicy.toMs(set, sampleRate)
                LimeLog.info("MeshAudioRenderer: buffer ran dry $since time(s); now $bufferMs ms")
            }
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

    private companion object {
        const val PENDING_LIMIT_MS = 40
        const val CHECK_EVERY_WRITES = 200   // 200 packets of 5 ms is about a second
    }
}
