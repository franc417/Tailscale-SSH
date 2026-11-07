package io.github.franc417.mesh

import android.os.Build
import io.github.franc417.mesh.core.InputBridge
import io.github.franc417.mesh.core.MipClient
import io.github.franc417.mesh.core.MipException
import io.github.franc417.mesh.core.hexToBytes

/**
 * The pinned, encrypted input channel to one computer, for screens that show the desktop and send input to it.
 * [report] is called from background threads with a short status and whether it is good news.
 */
class InputLink(
    computer: Computer,
    private val report: (text: String, ok: Boolean) -> Unit,
    /** Called once, from a background thread, with the certificate fingerprint learned at the first connection to a Tailscale computer. */
    private val onLearned: (fingerprint: String) -> Unit = {},
) {
    @Volatile private var computer: Computer = computer

    @Volatile private var client: MipClient? = null

    /** Null until connected; view events go through it to the computer. */
    @Volatile var bridge: InputBridge? = null
        private set

    private inner class Listener(val owner: () -> MipClient?) : MipClient.Listener {
        private fun current() = owner() != null && owner() === client
        override fun onRtt(millis: Long) { if (current()) report("Input $millis ms", true) }
        override fun onError(code: Int, text: String) { if (current()) report(text, false) }
        override fun onDisconnected(reason: String) {
            if (current()) {
                bridge = null
                report("Input disconnected: $reason", false)
            }
        }
    }

    /** Connects to [host]; returns at once. A new call replaces the previous connection. */
    fun connect(host: String) {
        close()
        var mine: MipClient? = null
        val mip = MipClient(
            MipClient.Config(host, computer.port, computer.tokenHex.hexToBytes(), computer.fingerprint, Build.MODEL ?: "Android phone"),
            Listener { mine },
        )
        mine = mip
        client = mip
        val bridgeForThis = InputBridge(mip)
        connecting = true
        Thread({
            try {
                mip.connect()
                mip.learnedFingerprint?.let { learned ->
                    computer = computer.copy(fingerprint = learned)   // from now on this connection is pinned like any other
                    onLearned(learned)
                }
                if (client === mip) {
                    bridge = bridgeForThis
                    report("Input connected", true)
                }
            } catch (e: MipException) {
                if (client === mip) report(e.message ?: "Input could not connect", false)
            } finally {
                if (client === mip) connecting = false
            }
        }, "mesh-input-connect").start()
    }

    /** The channel once it is connected (null while connecting and after it ended); use it for requests beyond plain input. */
    val connected: MipClient? get() = if (bridge != null) client else null

    /** True from the moment connect() is called until the channel is up or has failed. */
    @Volatile var connecting: Boolean = false
        private set

    /** Closing tells the computer to release any key still held down. */
    fun close() {
        connecting = false
        bridge = null
        client?.closeInBackground()   // this runs on the main thread, where network writes are forbidden
        client = null
    }
}
