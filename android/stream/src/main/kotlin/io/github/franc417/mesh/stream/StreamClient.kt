package io.github.franc417.mesh.stream

import android.content.Context
import android.view.Surface
import com.limelight.LimeLog
import com.limelight.binding.crypto.AndroidCryptoProvider
import com.limelight.nvstream.NvConnection
import com.limelight.nvstream.NvConnectionListener
import com.limelight.nvstream.StreamConfiguration
import com.limelight.nvstream.http.ComputerDetails
import com.limelight.nvstream.http.NvApp
import com.limelight.nvstream.http.NvHTTP
import com.limelight.nvstream.http.PairingManager
import com.limelight.nvstream.jni.MoonBridge

/** What the UI hears about a stream. Called from background threads: post to the main thread before touching views. */
interface StreamEvents {
    /** Progress text such as "Starting Desktop" or "RTSP handshake". */
    fun onStatus(text: String)

    /** The connection to the computer is up; the first picture is still to come. */
    fun onStarted()

    fun onFirstFrame(width: Int, height: Int)

    /** The stream could not start or broke. [message] is meant to be shown to the person. */
    fun onFailed(message: String)

    /** The stream ended normally (the computer or the person stopped it). */
    fun onEnded()

    fun onConnectionQuality(poor: Boolean)
}

/**
 * Talks to Sunshine (or GeForce Experience) the way Moonlight does: serverinfo, pairing, the app list, then launch
 * and the streaming core. The protocol work is Moonlight's own code, vendored unchanged; this class is the small
 * Mesh-specific layer on top. Everything here blocks, so call it from a background thread.
 */
class StreamClient(context: Context) {
    private val appContext = context.applicationContext
    internal val crypto: AndroidCryptoProvider by lazy { AndroidCryptoProvider(appContext) }
    val certs = ServerCertStore(appContext)

    data class HostInfo(
        val name: String,
        /** The computer's own unique id; stable across addresses. */
        val id: String,
        val paired: Boolean,
        val httpsPort: Int,
        /** True when a stream is already running on the computer (it will be resumed or replaced). */
        val streamRunning: Boolean,
    )

    sealed class PairResult {
        data class Paired(val info: HostInfo) : PairResult()
        object WrongPin : PairResult()
        object AlreadyInProgress : PairResult()
        data class Failed(val reason: String) : PairResult()
    }

    private fun tuple(host: String) = ComputerDetails.AddressTuple(host, NvHTTP.DEFAULT_HTTP_PORT)

    /** Asks the computer who it is and whether this phone is already paired with it. Throws when unreachable. */
    fun probe(host: String): HostInfo {
        val plain = NvHTTP(tuple(host), 0, UNIQUE_ID, null, crypto)
        val first = plain.getComputerDetails(plain.getServerInfo(true))
        val cert = certs.get(first.uuid)
        if (cert == null) {
            return HostInfo(first.name, first.uuid, false, first.httpsPort, first.runningGameId != 0)
        }
        // "Paired" is only reported over HTTPS with our pinned certificate; if that fails the computer forgot us.
        return try {
            val pinned = NvHTTP(tuple(host), first.httpsPort, UNIQUE_ID, cert, crypto)
            val d = pinned.getComputerDetails(pinned.getServerInfo(true))
            HostInfo(d.name, d.uuid, d.pairState == PairingManager.PairState.PAIRED, d.httpsPort, d.runningGameId != 0)
        } catch (e: Exception) {
            LimeLog.info("StreamClient: pinned probe failed ($e), treating as not paired")
            HostInfo(first.name, first.uuid, false, first.httpsPort, first.runningGameId != 0)
        }
    }

    /**
     * Pairs this phone with the computer. This call WAITS until [pin] has been entered on the computer (Sunshine's web
     * page, https://<computer>:47990/pin), exactly like Moonlight; there is no way to cancel it from here.
     */
    fun pair(host: String, pin: String): PairResult {
        return try {
            val http = NvHTTP(tuple(host), 0, UNIQUE_ID, null, crypto)
            val serverInfo = http.getServerInfo(true)
            val details = http.getComputerDetails(serverInfo)
            when (http.pairingManager.pair(serverInfo, pin)) {
                PairingManager.PairState.PAIRED -> {
                    certs.put(details.uuid, http.pairingManager.pairedCert)
                    PairResult.Paired(
                        HostInfo(details.name, details.uuid, true, details.httpsPort, details.runningGameId != 0),
                    )
                }
                PairingManager.PairState.PIN_WRONG -> PairResult.WrongPin
                PairingManager.PairState.ALREADY_IN_PROGRESS -> PairResult.AlreadyInProgress
                else -> PairResult.Failed("The computer refused to pair.")
            }
        } catch (e: Exception) {
            PairResult.Failed(e.message?.takeIf { it.isNotBlank() } ?: e.javaClass.simpleName)
        }
    }

    /** Starts streaming the computer's desktop onto [surface]. Returns at once; progress arrives through [events]. */
    fun start(host: String, settings: StreamSettings, surface: Surface, events: StreamEvents): StreamSession {
        val session = StreamSession(appContext, this, host, settings, surface, events)
        session.begin()
        return session
    }

    internal companion object {
        // NvHTTP ignores this and always sends the same id, so every Moonlight-family client can end each other's sessions.
        const val UNIQUE_ID = "0123456789ABCDEF"
    }
}

class StreamSession internal constructor(
    private val appContext: Context,
    private val client: StreamClient,
    private val host: String,
    private val settings: StreamSettings,
    surface: Surface,
    private val events: StreamEvents,
) {
    @Volatile private var stopped = false
    @Volatile private var connection: NvConnection? = null
    @Volatile private var lastMessage: String? = null

    private val video = MeshVideoDecoder(
        surface,
        object : MeshVideoDecoder.Events {
            override fun onFirstFrame(width: Int, height: Int) {
                if (!stopped) events.onFirstFrame(width, height)
            }

            override fun onDecoderError(message: String) {
                if (!stopped) events.onFailed("The video decoder failed: $message")
            }
        },
    )
    private val audio = MeshAudioRenderer()

    /** One line for a diagnostics overlay. */
    fun diagnostics(): String =
        "${video.decoderName} · ${video.framesRendered} shown, ${video.framesDropped} dropped"

    internal fun begin() {
        Thread({ run() }, "mesh-stream-start").start()
    }

    private fun run() {
        try {
            events.onStatus("Contacting the computer")
            val info = client.probe(host)
            if (stopped) return
            if (!info.paired) {
                events.onFailed("This phone is not paired with ${info.name} yet.")
                return
            }
            val cert = client.certs.get(info.id)
            if (cert == null) {
                events.onFailed("This phone is not paired with ${info.name} yet.")
                return
            }

            events.onStatus("Finding the desktop")
            val addr = ComputerDetails.AddressTuple(host, NvHTTP.DEFAULT_HTTP_PORT)
            val http = NvHTTP(addr, info.httpsPort, StreamClient.UNIQUE_ID, cert, client.crypto)
            val apps = http.getAppList()
            val app: NvApp? = apps.firstOrNull { it.appName.equals("Desktop", ignoreCase = true) } ?: apps.firstOrNull()
            if (app == null) {
                events.onFailed("Sunshine on ${info.name} has no applications. Add a \"Desktop\" application in its web page.")
                return
            }
            if (stopped) return

            var formats = MoonBridge.VIDEO_FORMAT_H264
            if (settings.preferHevc) formats = formats or MoonBridge.VIDEO_FORMAT_H265

            val config = StreamConfiguration.Builder()
                .setApp(app)
                .setResolution(settings.width, settings.height)
                .setRefreshRate(settings.fps)
                .setLaunchRefreshRate(settings.fps)
                .setClientRefreshRateX100(settings.fps * 100)
                .setBitrate(settings.bitrateKbps)
                .setEnableSops(false) // that is for games; the desktop should stream at exactly the size asked for
                .enableLocalAudioPlayback(false)
                .setMaxPacketSize(1392)
                .setRemoteConfiguration(StreamConfiguration.STREAM_CFG_AUTO)
                .setAudioConfiguration(MoonBridge.AUDIO_CONFIGURATION_STEREO)
                .setSupportedVideoFormats(formats)
                .setColorSpace(MoonBridge.COLORSPACE_REC_709)
                .setColorRange(MoonBridge.COLOR_RANGE_LIMITED)
                .setAttachedGamepadMask(0)
                .build()

            events.onStatus("Starting ${app.appName}")
            val c = NvConnection(appContext, addr, info.httpsPort, StreamClient.UNIQUE_ID, config, client.crypto, cert)
            connection = c
            if (stopped) {
                c.stop()
                return
            }
            c.start(audio, video, Listener())
        } catch (e: Exception) {
            if (!stopped) {
                LimeLog.severe("StreamSession: start failed: $e")
                events.onFailed(describe(e))
            }
        }
    }

    private fun describe(e: Exception): String {
        val detail = e.message?.takeIf { it.isNotBlank() }
        return when (e) {
            is java.net.ConnectException, is java.net.SocketTimeoutException, is java.net.UnknownHostException ->
                "Could not reach the computer at $host." + (detail?.let { " ($it)" } ?: "")
            else -> detail ?: ("Could not start the stream: " + e.javaClass.simpleName)
        }
    }

    /** Stops the stream and frees the decoder and audio. Safe to call more than once and from any thread. */
    fun stop() {
        if (stopped) return
        stopped = true
        video.prepareForStop() // synchronous and cheap, so the surface can go away right after this returns
        val c = connection
        if (c != null) {
            Thread({
                try {
                    c.stop()
                } catch (e: Exception) {
                    LimeLog.warning("StreamSession: stop failed: $e")
                }
            }, "mesh-stream-stop").start()
        }
    }

    private inner class Listener : NvConnectionListener {
        override fun stageStarting(stage: String) {
            if (!stopped) events.onStatus(stage.replaceFirstChar { it.uppercase() })
        }

        override fun stageComplete(stage: String) {}

        override fun stageFailed(stage: String, portFlags: Int, errorCode: Int) {
            if (stopped) return
            events.onFailed(lastMessage ?: StreamText.stageFailure(stage, portFlags, errorCode))
        }

        override fun connectionStarted() {
            if (!stopped) events.onStarted()
        }

        override fun connectionTerminated(errorCode: Int) {
            if (stopped) return
            val message = StreamText.termination(errorCode)
            if (message == null) events.onEnded() else events.onFailed(message)
        }

        override fun connectionStatusUpdate(connectionStatus: Int) {
            if (!stopped) events.onConnectionQuality(connectionStatus == MoonBridge.CONN_STATUS_POOR)
        }

        override fun displayMessage(message: String?) {
            // The core reports the real reason here just before a stage fails; keep it for stageFailed.
            if (!message.isNullOrBlank()) lastMessage = message
        }

        override fun displayTransientMessage(message: String?) {}

        override fun rumble(controllerNumber: Short, lowFreqMotor: Short, highFreqMotor: Short) {}

        override fun rumbleTriggers(controllerNumber: Short, leftTrigger: Short, rightTrigger: Short) {}

        override fun setHdrMode(enabled: Boolean, hdrMetadata: ByteArray?) {}

        override fun setMotionEventState(controllerNumber: Short, motionType: Byte, reportRateHz: Short) {}

        override fun setControllerLED(controllerNumber: Short, r: Byte, g: Byte, b: Byte) {}
    }
}
