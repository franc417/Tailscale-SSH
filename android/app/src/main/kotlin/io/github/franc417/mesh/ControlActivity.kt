package io.github.franc417.mesh

import android.app.Activity
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import io.github.franc417.mesh.core.HostMessage
import io.github.franc417.mesh.core.InputBridge
import io.github.franc417.mesh.core.MipClient
import io.github.franc417.mesh.core.MipException
import io.github.franc417.mesh.core.hexToBytes
import io.github.franc417.mesh.input.KeyboardView
import io.github.franc417.mesh.input.TrackpadView

/**
 * Controls one computer: a trackpad and a keyboard sending input over the encrypted, certificate-pinned channel.
 * This is the input-channel test bench; the streamed desktop view joins it later.
 */
class ControlActivity : Activity() {

    private var computer: Computer? = null
    private var client: MipClient? = null
    private var bridge: InputBridge? = null
    private lateinit var status: TextView
    private lateinit var retry: TextView

    /** Reports for ONE connection attempt; ignores anything from a connection that has since been replaced. */
    private inner class Session : MipClient.Listener {
        var owner: MipClient? = null
        private fun current() = owner != null && owner === client

        override fun onRtt(millis: Long) {
            runOnUiThread { if (current()) show("Connected  \u00B7  $millis ms", Ui.OK) }
        }

        override fun onError(code: Int, text: String) {
            runOnUiThread { if (current()) show(text, Ui.ERROR) }
        }

        override fun onDisconnected(reason: String) {
            runOnUiThread {
                if (current()) {
                    show("Disconnected: $reason", Ui.ERROR)
                    retry.visibility = View.VISIBLE
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        computer = ComputerStore(this).find(intent.getStringExtra(EXTRA_ID))
        if (computer == null) {
            finish()
            return
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(build())
    }

    private fun build(): View {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            val p = Ui.dp(this@ControlActivity, 14)
            setPadding(p, p, p, Ui.dp(this@ControlActivity, 6))
        }
        val titles = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        titles.addView(Ui.text(this, computer!!.name, 16f, Ui.TEXT, bold = true))
        status = Ui.text(this, "", 13f, Ui.MUTED)
        titles.addView(status)
        bar.addView(titles, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        retry = Ui.button(this, "Reconnect") { connect() }.apply { visibility = View.GONE }
        bar.addView(retry)
        root.addView(bar)

        val trackpad = TrackpadView(this)
        val keyboard = KeyboardView(this)
        trackpad.onEvent = { bridge?.handle(it) }
        keyboard.onEvent = { bridge?.handle(it) }
        root.addView(trackpad, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        root.addView(keyboard, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        return root
    }

    override fun onStart() {
        super.onStart()
        if (computer != null) connect()
    }

    override fun onStop() {
        // Closing tells the computer to let go of any key still held down.
        client?.close()
        client = null
        bridge = null
        super.onStop()
    }

    private fun show(message: String, color: Int) {
        status.text = message
        status.setTextColor(color)
    }

    private fun connect() {
        val c = computer ?: return
        client?.close()
        retry.visibility = View.GONE
        show("Connecting to ${c.host}...", Ui.MUTED)

        val session = Session()
        val mip = MipClient(
            MipClient.Config(c.host, c.port, c.tokenHex.hexToBytes(), c.fingerprint, Build.MODEL ?: "Android phone"),
            session,
        )
        session.owner = mip
        client = mip
        bridge = InputBridge(mip)

        Thread {
            try {
                val hello: HostMessage.HelloOk = mip.connect()
                runOnUiThread {
                    if (client === mip) show("Connected to ${hello.hostName.ifEmpty { c.name }}", Ui.OK)
                }
            } catch (e: MipException) {
                runOnUiThread {
                    if (client === mip) {
                        show(e.message ?: "Could not connect", Ui.ERROR)
                        retry.visibility = View.VISIBLE
                    }
                }
            }
        }.start()
    }

    companion object {
        const val EXTRA_ID = "computer_id"
    }
}
