package io.github.franc417.mesh.core

import java.io.DataInputStream
import java.io.IOException
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.SSLException
import javax.net.ssl.SSLSocket

/**
 * The phone's end of the Mesh input channel: one authenticated, certificate-pinned TLS
 * connection to the computer's agent. Thread-safe; call the send methods from any thread.
 *
 *  - Cursor and scroll deltas are additive, so when the network write is slower than the
 *    touch stream they are summed and sent as one message instead of queueing up (a queue
 *    would add its whole length to the cursor's lag). When the link keeps up there is one
 *    pending delta per message, so this adds no delay. Any other message drains pending
 *    motion first, so the order the user produced events in is preserved.
 *  - A ping every few seconds measures round-trip time and proves the host is alive; the
 *    host closing or going silent ends the session and is reported once.
 */
class MipClient(private val config: Config, private val listener: Listener) {

    class Config(
        val host: String,
        val port: Int,
        val token: ByteArray,
        /** SHA-256 of the computer's certificate, hex. */
        val pinSha256: String,
        val deviceName: String,
        val connectTimeoutMs: Int = 4000,
        val pingIntervalMs: Long = 5000,
        val idleTimeoutMs: Int = 15000,
    )

    interface Listener {
        fun onDisconnected(reason: String) {}
        fun onError(code: Int, text: String) {}
        fun onRtt(millis: Long) {}
    }

    private var socket: SSLSocket? = null
    private var input: DataInputStream? = null
    private var output: OutputStream? = null

    private val writer = Executors.newSingleThreadExecutor { r -> Thread(r, "mesh-mip-writer").apply { isDaemon = true } }
    private val scheduler: ScheduledExecutorService =
        Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "mesh-mip-ping").apply { isDaemon = true } }

    private val motionLock = Any()
    private var pendingDx = 0f
    private var pendingDy = 0f
    private var pendingScrollV = 0f
    private var pendingScrollH = 0f
    private var motionQueued = false

    private val finished = AtomicBoolean(false)
    @Volatile private var pingId = 0
    private val pingSentAt = java.util.concurrent.ConcurrentHashMap<Int, Long>()

    @Volatile var hello: HostMessage.HelloOk? = null
        private set

    val isConnected: Boolean get() = hello != null && !finished.get()

    /** Opens the connection and authenticates. Blocks; throws MipException with a plain reason on failure. */
    fun connect(): HostMessage.HelloOk {
        val raw = Socket()
        try {
            raw.tcpNoDelay = true
            raw.connect(InetSocketAddress(config.host, config.port), config.connectTimeoutMs)
            val tls = PinnedTls.context(config.pinSha256).socketFactory.createSocket(raw, config.host, config.port, true) as SSLSocket
            tls.useClientMode = true
            val wanted = arrayOf("TLSv1.3", "TLSv1.2").filter { it in tls.supportedProtocols }
            tls.enabledProtocols = wanted.toTypedArray()
            tls.soTimeout = config.connectTimeoutMs
            tls.startHandshake()  // the certificate pin is checked here

            val out = tls.outputStream
            val inp = DataInputStream(tls.inputStream)
            out.write(MipCodec.hello(config.token, config.deviceName))
            out.flush()

            val (type, payload) = readFrame(inp)
            when (val reply = MipCodec.decodeHost(type, payload)) {
                is HostMessage.HelloOk -> {
                    socket = tls
                    input = inp
                    output = out
                    hello = reply
                }
                is HostMessage.Error -> throw MipException(reply.code, describe(reply.code, reply.text))
                else -> throw MipException(0, "the computer sent an unexpected reply")
            }
            tls.soTimeout = config.idleTimeoutMs
        } catch (e: MipException) {
            closeQuietly(raw)
            throw e
        } catch (e: SSLException) {
            closeQuietly(raw)
            throw MipException(0, e.message?.takeIf { "pinned" in it || "paired" in it } ?: "secure connection failed: ${e.message}", e)
        } catch (e: SocketTimeoutException) {
            closeQuietly(raw)
            throw MipException(0, "the computer did not answer (is the Mesh agent running?)", e)
        } catch (e: IOException) {
            closeQuietly(raw)
            throw MipException(0, "could not reach the computer: ${e.message}", e)
        }

        startReader()
        scheduler.scheduleWithFixedDelay({ sendPing() }, config.pingIntervalMs, config.pingIntervalMs, TimeUnit.MILLISECONDS)
        return hello!!
    }

    private fun describe(code: Int, text: String) = when (code) {
        Mip.E_BAD_TOKEN -> "the computer rejected this phone's pairing token (pair again)"
        Mip.E_BAD_VERSION -> "the computer and this app use different protocol versions (update the Mesh agent or the app)"
        else -> text
    }

    private fun readFrame(inp: DataInputStream): Pair<Int, ByteArray> {
        val size = inp.readUnsignedShort()
        if (size < 1 || size > Mip.MAX_FRAME) throw IOException("bad frame size $size")
        val body = ByteArray(size)
        inp.readFully(body)
        return (body[0].toInt() and 0xFF) to body.copyOfRange(1, size)
    }

    private fun startReader() {
        Thread({
            try {
                val inp = input!!
                while (!finished.get()) {
                    val (type, payload) = readFrame(inp)
                    when (val msg = MipCodec.decodeHost(type, payload)) {
                        is HostMessage.Pong -> pingSentAt.remove(msg.id)?.let { listener.onRtt((System.nanoTime() - it) / 1_000_000) }
                        is HostMessage.Error -> {
                            listener.onError(msg.code, msg.text)
                            finish(if (msg.code == Mip.E_REPLACED) "another phone took over this computer" else msg.text)
                            return@Thread
                        }
                        else -> {}
                    }
                }
            } catch (e: SocketTimeoutException) {
                finish("the computer stopped answering")
            } catch (e: IOException) {
                finish(if (finished.get()) "closed" else "connection lost")
            } catch (e: IllegalArgumentException) {
                finish("the computer sent a damaged message")
            }
        }, "mesh-mip-reader").apply { isDaemon = true }.start()
    }

    private fun sendPing() {
        val id = ++pingId
        pingSentAt[id] = System.nanoTime()
        if (pingSentAt.size > 8) pingSentAt.keys.minOrNull()?.let { pingSentAt.remove(it) }
        send(MipCodec.ping(id))
    }

    // ------------------------------------------------------------------ sending

    fun move(dx: Float, dy: Float) {
        synchronized(motionLock) {
            pendingDx += dx
            pendingDy += dy
            if (motionQueued) return
            motionQueued = true
        }
        post { flushMotion() }
    }

    fun scroll(vertical: Float, horizontal: Float = 0f) {
        synchronized(motionLock) {
            pendingScrollV += vertical
            pendingScrollH += horizontal
            if (motionQueued) return
            motionQueued = true
        }
        post { flushMotion() }
    }

    fun button(button: Int, down: Boolean) = send(MipCodec.button(button, down))

    fun key(code: String, down: Boolean) = send(MipCodec.key(code, down))

    /** Types text on the computer (any length; sent in pieces that fit a frame). */
    fun text(text: String) {
        for (chunk in MipCodec.textChunks(text)) send(MipCodec.text(chunk))
    }

    fun releaseAll() = send(MipCodec.releaseAll())

    private fun send(frame: ByteArray) = post {
        flushMotion()
        write(frame)
    }

    /** Runs on the writer thread only. */
    private fun flushMotion() {
        var dx = 0f
        var dy = 0f
        var sv = 0f
        var sh = 0f
        synchronized(motionLock) {
            dx = pendingDx; dy = pendingDy; sv = pendingScrollV; sh = pendingScrollH
            pendingDx = 0f; pendingDy = 0f; pendingScrollV = 0f; pendingScrollH = 0f
            motionQueued = false
        }
        if (dx != 0f || dy != 0f) write(MipCodec.move(dx, dy))
        if (sv != 0f || sh != 0f) write(MipCodec.scroll(sv, sh))
    }

    private fun write(frame: ByteArray) {
        val out = output ?: return
        out.write(frame)
        out.flush()
    }

    private fun post(task: () -> Unit) {
        if (finished.get()) return
        try {
            writer.execute {
                try {
                    task()
                } catch (e: IOException) {
                    finish("connection lost")
                }
            }
        } catch (e: RejectedExecutionException) {
            // shutting down
        }
    }

    // ------------------------------------------------------------------ ending

    /** Ends the session politely; the host releases anything still held. */
    fun close() {
        if (finished.get()) return
        try {
            synchronized(this) {
                output?.write(MipCodec.bye())
                output?.flush()
            }
        } catch (_: IOException) {
        }
        finish("closed")
    }

    private fun finish(reason: String) {
        if (!finished.compareAndSet(false, true)) return
        closeQuietly(socket)
        scheduler.shutdownNow()
        writer.shutdownNow()
        listener.onDisconnected(reason)
    }

    private fun closeQuietly(s: Socket?) {
        try {
            s?.close()
        } catch (_: IOException) {
        }
    }
}
