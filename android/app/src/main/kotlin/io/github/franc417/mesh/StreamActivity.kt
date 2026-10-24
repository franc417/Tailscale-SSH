package io.github.franc417.mesh

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.MotionEvent
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import io.github.franc417.mesh.core.Reach
import io.github.franc417.mesh.input.FloatingKeyboardSizing
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
    private lateinit var keyboardBox: LinearLayout
    private lateinit var keyFrame: FrameLayout
    private lateinit var sizeButton: TextView
    private var keyboardEnlarged = false
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
        stage.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            fitSurface()
            layoutKeyboard()
        }

        // The keyboard floats over the picture (it never shrinks the desktop), so it is added to the stage itself.
        stage.addView(buildKeyboardBox(), FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL))
        root.addView(stage, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
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
        bar.addView(chip("Keyboard") { setKeyboardVisible(keyboardBox.visibility != View.VISIBLE) })
        return bar
    }

    /**
     * The keyboard as a small box that floats over the desktop: drag it by its title, make it Bigger (at most half the
     * screen) or Smaller, or Hide it. The keyboard inside keeps its real layout and is only scaled as a whole.
     */
    private fun buildKeyboardBox(): View {
        keyboard = KeyboardView(this).apply { onEvent = { inputLink?.bridge?.handle(it) } }
        keyFrame = FrameLayout(this)
        keyFrame.addView(keyboard, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        val pad = Ui.dp(this, 6)
        val handle = Ui.text(this, "Keyboard  \u00B7  drag here to move", 12f, Ui.MUTED).apply {
            setPadding(pad, pad, pad, pad)
        }
        var downX = 0f
        var downY = 0f
        var startTx = 0f
        var startTy = 0f
        handle.setOnTouchListener { _, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = e.rawX
                    downY = e.rawY
                    startTx = keyboardBox.translationX
                    startTy = keyboardBox.translationY
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    keyboardBox.translationX = FloatingKeyboardSizing.clampOffset(startTx + (e.rawX - downX), keyboardBox.left, keyboardBox.width, stage.width)
                    keyboardBox.translationY = FloatingKeyboardSizing.clampOffset(startTy + (e.rawY - downY), keyboardBox.top, keyboardBox.height, stage.height)
                    true
                }
                else -> true
            }
        }

        sizeButton = chip("Bigger") { toggleKeyboardSize() }
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(handle, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        header.addView(sizeButton)
        header.addView(View(this), LinearLayout.LayoutParams(Ui.dp(this, 6), 1))
        header.addView(chip("Hide") { setKeyboardVisible(false) })

        keyboardBox = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = Ui.box(this@StreamActivity, fill = Color.argb(238, 17, 17, 19), radiusDp = 14)
            setPadding(pad, pad, pad, pad)
            isClickable = true // swallows touches in the gaps so they never reach the trackpad underneath
            visibility = View.GONE
        }
        keyboardBox.addView(header)
        keyboardBox.addView(keyFrame, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        return keyboardBox
    }

    private fun setKeyboardVisible(visible: Boolean) {
        keyboardBox.visibility = if (visible) View.VISIBLE else View.GONE
        if (visible) layoutKeyboard()
    }

    private fun toggleKeyboardSize() {
        keyboardEnlarged = !keyboardEnlarged
        sizeButton.text = if (keyboardEnlarged) "Smaller" else "Bigger"
        layoutKeyboard()
    }

    /** Lays the keyboard out at its natural full-width size, then scales it down to the compact or enlarged box. */
    private fun layoutKeyboard() {
        if (!::keyboardBox.isInitialized) return
        val sw = stage.width
        val sh = stage.height
        if (sw == 0 || sh == 0) return
        val naturalW = sw
        keyboard.measure(
            View.MeasureSpec.makeMeasureSpec(naturalW, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
        )
        val naturalH = keyboard.measuredHeight
        if (naturalH <= 0) return
        val scale = FloatingKeyboardSizing.scale(sw, sh, naturalW, naturalH, keyboardEnlarged)
        val klp = keyboard.layoutParams
        if (klp.width != naturalW || klp.height != naturalH) {
            keyboard.layoutParams = FrameLayout.LayoutParams(naturalW, naturalH)
        }
        keyboard.pivotX = 0f
        keyboard.pivotY = 0f
        keyboard.scaleX = scale
        keyboard.scaleY = scale
        val fw = (naturalW * scale).toInt()
        val fh = (naturalH * scale).toInt()
        val flp = keyFrame.layoutParams
        if (flp.width != fw || flp.height != fh) {
            keyFrame.layoutParams = LinearLayout.LayoutParams(fw, fh)
        }
        // The box changed size, so where it sits may now be partly off screen.
        keyboardBox.post {
            keyboardBox.translationX = FloatingKeyboardSizing.clampOffset(keyboardBox.translationX, keyboardBox.left, keyboardBox.width, stage.width)
            keyboardBox.translationY = FloatingKeyboardSizing.clampOffset(keyboardBox.translationY, keyboardBox.top, keyboardBox.height, stage.height)
        }
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

    /** Keeps the video at the stream's aspect ratio, centred in the stage (the floating keyboard hovers over it). */
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

        override fun onStatus(text: String) = ui { if (!streaming) showMessage(text, "") }

        // Progress reports can arrive after the first picture; once video is showing they must not bring the box back.
        override fun onStarted() = ui { if (!streaming) showMessage("Starting video", "") }

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
