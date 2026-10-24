package io.github.franc417.mesh.stream

import android.media.MediaCodec
import android.media.MediaFormat
import android.os.Build
import android.os.SystemClock
import android.view.Surface
import com.limelight.LimeLog
import com.limelight.nvstream.av.video.VideoDecoderRenderer
import com.limelight.nvstream.jni.MoonBridge

/**
 * Decodes the video that moonlight-common-c delivers and draws it on [surface].
 *
 * This is deliberately much smaller than Moonlight's own MediaCodecDecoderRenderer (which carries years of per-device
 * workarounds). It does what every device needs: configure a hardware decoder for low latency, give it the
 * parameter sets of each IDR as codec config, queue frames in order, and show the newest decoded frame, dropping any
 * older one that is still waiting (so a hiccup never turns into a growing delay).
 *
 * NOT verified on a real device yet (it can only be compiled in CI). If a particular phone shows green/garbled video or
 * a long delay, the likely next step is Moonlight's per-decoder workarounds, not a rewrite.
 */
class MeshVideoDecoder(
    private val surface: Surface,
    private val events: Events,
) : VideoDecoderRenderer() {

    interface Events {
        /** First picture was handed to the screen. Called from the decoder's output thread. */
        fun onFirstFrame(width: Int, height: Int)

        /** The decoder failed. Called at most once, from a decoder thread. */
        fun onDecoderError(message: String)
    }

    @Volatile private var stopping = false
    private var codec: MediaCodec? = null
    private var outputThread: Thread? = null
    private val csd = CsdAccumulator()
    private var lastTimestampUs = 0L
    private var width = 0
    private var height = 0
    @Volatile private var reportedFirstFrame = false
    @Volatile private var failed = false

    // Counters for a diagnostics line; written on the decoder threads, read on the UI thread.
    @Volatile var framesSubmitted = 0L; private set
    @Volatile var framesRendered = 0L; private set
    @Volatile var framesDropped = 0L; private set
    @Volatile var decoderName: String = ""; private set

    override fun setup(format: Int, width: Int, height: Int, redrawRate: Int): Int {
        val mime = when {
            (format and MoonBridge.VIDEO_FORMAT_MASK_H264) != 0 -> MediaFormat.MIMETYPE_VIDEO_AVC
            (format and MoonBridge.VIDEO_FORMAT_MASK_H265) != 0 -> MediaFormat.MIMETYPE_VIDEO_HEVC
            else -> {
                LimeLog.severe("MeshVideoDecoder: unsupported video format 0x" + Integer.toHexString(format))
                return -1
            }
        }
        this.width = width
        this.height = height
        return try {
            val mf = MediaFormat.createVideoFormat(mime, width, height)
            if (Build.VERSION.SDK_INT >= 30) mf.setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
            if (Build.VERSION.SDK_INT >= 23) {
                mf.setInteger(MediaFormat.KEY_PRIORITY, 0) // real-time
                mf.setInteger(MediaFormat.KEY_OPERATING_RATE, Short.MAX_VALUE.toInt())
            }
            val c = MediaCodec.createDecoderByType(mime)
            decoderName = c.name
            c.configure(mf, surface, null, 0)
            codec = c
            LimeLog.info("MeshVideoDecoder: $mime ${width}x$height using ${c.name}")
            MoonBridge.DR_OK
        } catch (e: Exception) {
            LimeLog.severe("MeshVideoDecoder: setup failed: $e")
            -2
        }
    }

    override fun start() {
        val c = codec ?: return
        try {
            c.start()
        } catch (e: Exception) {
            fail("could not start the decoder: $e")
            return
        }
        val t = Thread({ outputLoop(c) }, "mesh-video-output")
        t.priority = Thread.MAX_PRIORITY
        outputThread = t
        t.start()
    }

    /** Makes every later call return at once. Cheap and synchronous; the real teardown happens in [stop]/[cleanup]. */
    fun prepareForStop() {
        stopping = true
    }

    override fun stop() {
        stopping = true
        outputThread?.let {
            it.interrupt()
            try {
                it.join(500)
            } catch (_: InterruptedException) {
            }
        }
        outputThread = null
        try {
            codec?.stop()
        } catch (_: Exception) {
        }
    }

    override fun cleanup() {
        try {
            codec?.release()
        } catch (_: Exception) {
        }
        codec = null
    }

    override fun getCapabilities(): Int = 0

    override fun setHdrMode(enabled: Boolean, hdrMetadata: ByteArray?) {
        // SDR only for now.
    }

    override fun submitDecodeUnit(
        decodeUnitData: ByteArray,
        decodeUnitLength: Int,
        decodeUnitType: Int,
        frameNumber: Int,
        frameType: Int,
        frameHostProcessingLatency: Char,
        receiveTimeUs: Long,
        enqueueTimeUs: Long,
    ): Int {
        if (stopping || failed) return MoonBridge.DR_OK

        if (frameType == MoonBridge.FRAME_TYPE_IDR) {
            csd.beginFrame(frameNumber)
            if (csd.add(decodeUnitType, decodeUnitData, decodeUnitLength)) {
                return MoonBridge.DR_OK // parameter set: held until the picture data of this IDR arrives
            }
            if (!csd.isEmpty) {
                val config = csd.toByteArray()
                if (!queue(config, config.size, 0L, MediaCodec.BUFFER_FLAG_CODEC_CONFIG)) return MoonBridge.DR_NEED_IDR
            }
        }

        // The timestamps from the C side have an undefined epoch; MediaCodec only needs them to increase.
        var ts = enqueueTimeUs
        if (ts <= lastTimestampUs) ts = lastTimestampUs + 1
        lastTimestampUs = ts

        val flags = if (frameType == MoonBridge.FRAME_TYPE_IDR) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0
        if (!queue(decodeUnitData, decodeUnitLength, ts, flags)) return MoonBridge.DR_NEED_IDR
        framesSubmitted++
        return MoonBridge.DR_OK
    }

    /** Copies [data] into the next free input buffer. Gives up after ~100 ms (the caller then asks for a new IDR). */
    private fun queue(data: ByteArray, length: Int, timestampUs: Long, flags: Int): Boolean {
        val c = codec ?: return false
        val deadline = SystemClock.uptimeMillis() + 100
        try {
            while (!stopping) {
                val index = c.dequeueInputBuffer(10_000)
                if (index >= 0) {
                    val buf = c.getInputBuffer(index) ?: return false
                    if (length > buf.capacity()) {
                        // Hand the buffer back empty so the decoder does not wait for it, then ask for a fresh IDR.
                        c.queueInputBuffer(index, 0, 0, timestampUs, 0)
                        LimeLog.warning("MeshVideoDecoder: unit of $length bytes does not fit the ${buf.capacity()}-byte input buffer")
                        return false
                    }
                    buf.clear()
                    buf.put(data, 0, length)
                    c.queueInputBuffer(index, 0, length, timestampUs, flags)
                    return true
                }
                if (SystemClock.uptimeMillis() > deadline) {
                    framesDropped++
                    return false
                }
            }
        } catch (e: Exception) {
            if (!stopping) fail("decoder input failed: $e")
        }
        return false
    }

    private fun outputLoop(c: MediaCodec) {
        val info = MediaCodec.BufferInfo()
        try {
            while (!stopping) {
                val index = c.dequeueOutputBuffer(info, 10_000)
                if (index < 0) continue // timeout, format change or buffers changed: nothing to draw

                // Newest frame wins: if more frames are already waiting, the older ones are released unseen.
                var newest = index
                while (true) {
                    val more = c.dequeueOutputBuffer(info, 0)
                    if (more < 0) break
                    c.releaseOutputBuffer(newest, false)
                    framesDropped++
                    newest = more
                }
                c.releaseOutputBuffer(newest, true)
                framesRendered++
                if (!reportedFirstFrame) {
                    reportedFirstFrame = true
                    events.onFirstFrame(width, height)
                }
            }
        } catch (e: Exception) {
            if (!stopping) fail("decoder output failed: $e")
        }
    }

    private fun fail(message: String) {
        if (failed) return
        failed = true
        LimeLog.severe("MeshVideoDecoder: $message")
        events.onDecoderError(message)
    }
}
