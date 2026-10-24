package io.github.franc417.mesh

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import io.github.franc417.mesh.core.Reach
import io.github.franc417.mesh.input.KeyboardView
import io.github.franc417.mesh.input.TrackpadView
import io.github.franc417.mesh.stream.StreamClient
import io.github.franc417.mesh.stream.StreamEvents
import io.github.franc417.mesh.stream.StreamSession
import io.github.franc417.mesh.stream.StreamSettings
import java.security.SecureRandom

/**
 * The desktop of one computer, full screen and landscape: video and audio come from Sunshine through the Moonlight
 * streaming core (module :stream), while the trackpad (laid over the picture) and the keyboard send input over
 * Mesh's own pinned, encrypted channel.
 *
 * NOT verified on a real phone yet. In particular the video path (hardware decoder, surface, audio) has only been
 * compiled in CI.
 */
class StreamActivity : Activity() {

    private lateinit var computer: Computer
    private lateinit var surfaceView: SurfaceView
    private lateinit var stage: FrameLayout
    private lateinit var trackpad: TrackpadView
    private lateinit var keyboard: KeyboardView
    private lateinit var messagePanel: LinearLayout
    private lateinit var messageTitle: TextView
    private lateinit var messageBody: TextView
    private lateinit var pinView: TextView
    private lateinit var openPinPage: TextView
    private lateinit var retryButton: TextView
    private lateinit var statusChip: TextView
    private lateinit var qualityChip: TextView

    private val streamClient by lazy { StreamClient(this) }
    private var inputLink: InputLink? = null
    private var session: StreamSession? = null
    private var host: String? = null
    private var paired = false
    private var surfaceReady = false
    private var streaming = false
    private var generation = 0 // bumped whenever the screen (re)starts, so reports from an older run are ignored
    private var presetIndex = 0
    private var inputStatus = ""
    private val handler = Handler(Looper.getMainLooper())

    private val settings: StreamSettings get() = StreamSettings.presets[presetIndex].second

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val found = ComputerStore(this).find(intent.getStringExtra(EXTRA_ID))
        if (found == null) {
            finish()
            return
        }
        computer = found
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(build())
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION)
        }
    }

    // ---------------------------------------------------------------- screen

    private fun build(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK)
        }

        stage = FrameLayout(this)
        surfaceView = SurfaceView(this)
        stage.addView(surfaceView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.CENTER))
        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                surfaceReady = true
                startIfReady()
            }

            override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {}

            override fun surfaceDestroyed(holder: SurfaceHolder) {
                surfaceReady = false
                stopStream() // the decoder must let go before the surface is gone
            }
        })

        trackpad = TrackpadView(this).apply {
            overlay = true
            onEvent = { inputLink?.bridge?.handle(it) }
        }
        stage.addView(trackpad, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        stage.addView(buildMessagePanel(), FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        stage.addView(buildTopBar(), FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP))
        stage.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> fitSurface() }

        root.addView(stage, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        keyboard = KeyboardView(this).apply {
            visibility = View.GONE
            onEvent = { inputLink?.bridge?.handle(it) }
        }
        root.addView(keyboard, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        return root
    }

    private fun chip(label: String, onClick: () -> Unit) = TextView(this).apply {
        text = label
        textSize = 12f
        setTextColor(Ui.TEXT)
        gravity = Gravity.CENTER
        background = Ui.box(this@StreamActivity, fill = Color.argb(150, 11, 11, 12), radiusDp = 10)
        val h = Ui.dp(this@StreamActivity, 12)
        val v = Ui.dp(this@StreamActivity, 6)
        setPadding(h, v, h, v)
        setOnClickListener { onClick() }
    }

    private fun buildTopBar(): View {
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            val p = Ui.dp(this@StreamActivity, 8)
            setPadding(p, p, p, p)
        }
        val gap = { View(this).apply { layoutParams = LinearLayout.LayoutParams(Ui.dp(this@StreamActivity, 6), 1) } }
        bar.addView(chip("\u2715  Close") { finish() })
        bar.addView(gap())
        statusChip = chip("") {}.apply { visibility = View.GONE }
        bar.addView(statusChip)
        bar.addView(View(this), LinearLayout.LayoutParams(0, 1, 1f))
        qualityChip = chip(StreamSettings.presets[presetIndex].first.substringBefore(" ")) { cycleQuality() }
        bar.addView(qualityChip)
        bar.addView(gap())
        bar.addView(chip("Keyboard") {
            keyboard.visibility = if (keyboard.visibility == View.VISIBLE) View.GONE else View.VISIBLE
        })
        return bar
    }

    private fun buildMessagePanel(): View {
        messagePanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            background = Ui.box(this@StreamActivity, fill = Color.argb(225, 20, 20, 22))
            val p = Ui.dp(this@StreamActivity, 20)
            setPadding(p, p, p, p)
        }
        messageTitle = Ui.text(this, "", 17f, Ui.TEXT, bold = true).apply { gravity = Gravity.CENTER }
        messageBody = Ui.text(this, "", 14f, Ui.MUTED).apply {
            gravity = Gravity.CENTER
            maxWidth = Ui.dp(this@StreamActivity, 420)
        }
        pinView = Ui.text(this, "", 40f, Ui.TEXT, bold = true).apply {
            gravity = Gravity.CENTER
            letterSpacing = 0.3f
        }
        openPinPage = Ui.button(this, "Open Sunshine's PIN page") {
            host?.let { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://$it:47990/pin"))) }
        }
        retryButton = Ui.button(this, "Try again") { restart() }
        messagePanel.addView(messageTitle)
        messagePanel.addView(Ui.gap(this, 8))
        messagePanel.addView(messageBody)
        messagePanel.addView(pinView)
        messagePanel.addView(Ui.gap(this, 12))
        messagePanel.addView(openPinPage)
        messagePanel.addView(Ui.gap(this, 8))
        messagePanel.addView(retryButton)
        messagePanel.visibility = View.GONE
        return messagePanel
    }

    private fun showMessage(title: String, body: String, pin: String? = null, retry: Boolean = false) {
        messageTitle.text = title
        messageBody.text = body
        messageBody.visibility = if (body.isEmpty()) View.GONE else View.VISIBLE
        pinView.text = pin ?: ""
        pinView.visibility = if (pin == null) View.GONE else View.VISIBLE
        openPinPage.visibility = if (pin == null) View.GONE else View.VISIBLE
        retryButton.visibility = if (retry) View.VISIBLE else View.GONE
        messagePanel.visibility = View.VISIBLE
    }

    private fun showError(text: String) = showMessage("Could not show the desktop", text, retry = true)

    private fun hideMessage() {
        messagePanel.visibility = View.GONE
    }

    /** Keeps the video at the stream's aspect ratio, centred, in whatever space the keyboard leaves. */
    private fun fitSurface() {
        val w = stage.width
        val h = stage.height
        if (w == 0 || h == 0) return
        val aspect = settings.width.toFloat() / settings.height
        var cw = w
        var ch = (w / aspect).toInt()
        if (ch > h) {
            ch = h
            cw = (h * aspect).toInt()
        }
        val lp = surfaceView.layoutParams as FrameLayout.LayoutParams
        if (lp.width != cw || lp.height != ch) {
            lp.width = cw
            lp.height = ch
            lp.gravity = Gravity.CENTER
            surfaceView.layoutParams = lp
        }
    }

    // ------------------------------------------------------------- lifecycle

    override fun onStart() {
        super.onStart()
        begin()
    }

    override fun onStop() {
        generation++
        stopStream()
        inputLink?.close()
        inputLink = null
        host = null
        paired = false
        handler.removeCallbacksAndMessages(null)
        super.onStop()
    }

    private fun begin() {
        val g = ++generation
        host = null
        paired = false
        showMessage("Looking for ${computer.name}", "")
        if (computer.hosts.isEmpty()) {
            showError("This computer has no usable address saved. Remove it and add it again.")
            return
        }
        // Every saved address is tried at once; the first that answers is used for both input and video.
        Thread({
            val reachable = Reach.firstReachable(computer.hosts, computer.port, 3000)
            runOnUiThread {
                if (g != generation) return@runOnUiThread
                if (reachable == null) {
                    showError("Could not reach ${computer.name} at ${computer.hosts.joinToString(", ")}. Is the Mesh agent running, and are you on the same Wi-Fi or on Tailscale?")
                } else {
                    host = reachable
                    connectInput(reachable)
                    checkPairing(reachable, g)
                }
            }
        }, "mesh-find-host").start()
    }

    private fun connectInput(host: String) {
        inputLink?.close()
        val link = InputLink(computer) { text, ok ->
            runOnUiThread {
                inputStatus = text
                if (!ok && streaming) statusChip.text = text
            }
        }
        inputLink = link
        link.connect(host)
    }

    private fun checkPairing(host: String, g: Int) {
        Thread({
            try {
                val info = streamClient.probe(host)
                runOnUiThread {
                    if (g != generation) return@runOnUiThread
                    if (info.paired) {
                        paired = true
                        startIfReady()
                    } else {
                        pair(host, info.name, g)
                    }
                }
            } catch (e: Exception) {
                runOnUiThread {
                    if (g != generation) return@runOnUiThread
                    showError("Could not reach Sunshine on $host (port 47989). Is Sunshine running on ${computer.name}?" +
                        (e.message?.takeIf { it.isNotBlank() }?.let { "\n($it)" } ?: ""))
                }
            }
        }, "mesh-probe").start()
    }

    private fun pair(host: String, hostName: String, g: Int) {
        val pin = String.format("%04d", SecureRandom().nextInt(10_000))
        showMessage(
            "Pair with $hostName",
            "Enter this PIN in Sunshine's web page (Sunshine > PIN), then the desktop appears here. Waiting for the PIN...",
            pin = pin,
        )
        Thread({
            val result = streamClient.pair(host, pin)
            runOnUiThread {
                if (g != generation) return@runOnUiThread
                when (result) {
                    is StreamClient.PairResult.Paired -> {
                        paired = true
                        startIfReady()
                    }
                    StreamClient.PairResult.WrongPin -> showError("The PIN was wrong. Try again and type the number shown.")
                    StreamClient.PairResult.AlreadyInProgress -> showError("Another device is pairing with ${computer.name} right now. Wait a minute and try again.")
                    is StreamClient.PairResult.Failed -> showError("Pairing failed: ${result.reason}")
                }
            }
        }, "mesh-pair").start()
    }

    // ---------------------------------------------------------------- stream

    private inner class Events(private val g: Int) : StreamEvents {
        private fun ui(block: () -> Unit) = runOnUiThread { if (g == generation) block() }

        override fun onStatus(text: String) = ui { showMessage(text, "") }

        override fun onStarted() = ui { showMessage("Starting video", "") }

        override fun onFirstFrame(width: Int, height: Int) = ui {
            streaming = true
            hideMessage()
            statusChip.visibility = View.VISIBLE
            statusChip.setTextColor(Ui.OK)
            scheduleDiagnostics()
        }

        override fun onFailed(message: String) = ui {
            stopStream()
            showError(message)
        }

        override fun onEnded() = ui {
            stopStream()
            showMessage("The stream ended", "The computer or Sunshine stopped it.", retry = true)
        }

        override fun onConnectionQuality(poor: Boolean) = ui {
            statusChip.setTextColor(if (poor) Ui.ERROR else Ui.OK)
        }
    }

    private fun startIfReady() {
        val h = host ?: return
        if (!paired || !surfaceReady || session != null) return
        fitSurface()
        showMessage("Starting the stream", "")
        session = streamClient.start(h, settings, surfaceView.holder.surface, Events(generation))
    }

    private fun stopStream() {
        streaming = false
        handler.removeCallbacksAndMessages(null)
        session?.stop()
        session = null
        if (::statusChip.isInitialized) statusChip.visibility = View.GONE
    }

    private fun scheduleDiagnostics() {
        handler.postDelayed({
            val s = session
            if (s != null && streaming) {
                statusChip.text = s.diagnostics() + if (inputStatus.isNotEmpty()) "  \u00B7  $inputStatus" else ""
                scheduleDiagnostics()
            }
        }, 1000)
    }

    private fun cycleQuality() {
        presetIndex = (presetIndex + 1) % StreamSettings.presets.size
        qualityChip.text = StreamSettings.presets[presetIndex].first.substringBefore(" ")
        // A running stream is restarted so the new size is really used; before that the choice just applies to the start.
        if (session != null || messagePanel.visibility == View.VISIBLE) restart()
    }

    private fun restart() {
        stopStream()
        inputLink?.close()
        inputLink = null
        begin()
    }

    companion object {
        const val EXTRA_ID = "computer_id"
    }
}
