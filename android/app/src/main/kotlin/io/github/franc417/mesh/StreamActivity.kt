package io.github.franc417.mesh

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Build
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
import android.widget.Toast
import io.github.franc417.mesh.core.Reach
import io.github.franc417.mesh.input.FloatingKeyboardSizing
import io.github.franc417.mesh.input.KeyboardView
import io.github.franc417.mesh.input.PointerMode
import io.github.franc417.mesh.input.TrackpadView
import io.github.franc417.mesh.input.VideoArea
import io.github.franc417.mesh.stream.StreamClient
import io.github.franc417.mesh.stream.StreamEvents
import io.github.franc417.mesh.stream.StreamSession
import io.github.franc417.mesh.stream.StreamSettings
import io.github.franc417.mesh.stream.ViewFit
import io.github.franc417.mesh.stream.ViewMode
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
    private lateinit var layoutChip: TextView
    private var keyboardEnlarged = false
    private var enlargedBeforeEditing = false
    private lateinit var messagePanel: LinearLayout
    private lateinit var messageTitle: TextView
    private lateinit var messageBody: TextView
    private lateinit var pinView: TextView
    private lateinit var openPinPage: TextView
    private lateinit var retryButton: TextView
    private lateinit var statusChip: TextView
    private lateinit var qualityChip: TextView
    private lateinit var controlBar: View
    private lateinit var chevron: ChevronView
    private var controlsExpanded = true
    private var wifiLock: WifiManager.WifiLock? = null

    private val streamClient by lazy { StreamClient(this) }
    private var inputLink: InputLink? = null
    private var session: StreamSession? = null
    private var host: String? = null
    private var paired = false
    private var surfaceReady = false
    private var streaming = false
    private var generation = 0 // bumped whenever the screen (re)starts, so reports from an older run are ignored
    private var presetIndex = 0
    private var pointerMode = PointerMode.TRACKPAD
    private var viewMode = ViewMode.FIT
    private lateinit var pointerChip: TextView
    private lateinit var selectChip: TextView
    private lateinit var viewChip: TextView
    private var warnedNoAbsolute = false
    private val viewPrefs by lazy { getSharedPreferences("mesh_stream_view", Context.MODE_PRIVATE) }
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
        loadViewPrefs()
        setContentView(build())
        applyPointerMode(pointerMode)
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

        // The trackpad needs to know where the picture is to turn a touch into a spot on the computer's screen.
        surfaceView.addOnLayoutChangeListener { _, l, t, r, b, _, _, _, _ ->
            trackpad.videoArea = VideoArea(l.toFloat(), t.toFloat(), (r - l).toFloat(), (b - t).toFloat())
        }
        trackpad = TrackpadView(this).apply {
            overlay = true
            onEvent = { inputLink?.bridge?.handle(it) }
        }
        stage.addView(trackpad, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        stage.addView(buildMessagePanel(), FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        stage.addView(buildTopPanel(), FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP))
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
        statusChip = chip("") { copyReport() }.apply {
            visibility = View.INVISIBLE // invisible, not gone: it holds the space that keeps the other chips on the right
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
        }
        bar.addView(statusChip, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        bar.addView(gap())
        pointerChip = chip("Pointer") { showPointerPicker() }
        bar.addView(pointerChip)
        bar.addView(gap())
        selectChip = chip("Select") { toggleSelect() }
        bar.addView(selectChip)
        bar.addView(gap())
        viewChip = chip("View: " + viewLabel(viewMode)) { showViewPicker() }
        bar.addView(viewChip)
        bar.addView(gap())
        qualityChip = chip(StreamSettings.presets[presetIndex].first.substringBefore(" ")) { showQualityPicker() }
        bar.addView(qualityChip)
        bar.addView(gap())
        bar.addView(chip("Keyboard") { setKeyboardVisible(keyboardBox.visibility != View.VISIBLE) })
        return bar
    }

    /**
     * The controls (Close, status, quality, Keyboard) sit in a bar that can be pulled up out of the way by the small
     * arrow tab under it, leaving the desktop truly full screen. The tab itself stays, so the controls are one tap away.
     */
    private fun buildTopPanel(): View {
        val panel = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        controlBar = buildTopBar()
        panel.addView(controlBar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        chevron = ChevronView(this)
        val tab = FrameLayout(this).apply {
            background = Ui.box(this@StreamActivity, fill = Color.argb(150, 11, 11, 12), radiusDp = 12)
            isClickable = true
            setOnClickListener { setControlsExpanded(!controlsExpanded) }
        }
        tab.addView(chevron, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        panel.addView(tab, LinearLayout.LayoutParams(Ui.dp(this, 64), Ui.dp(this, 30)).apply { gravity = Gravity.CENTER_HORIZONTAL })
        setControlsExpandedNow(true)
        return panel
    }

    private fun setControlsExpanded(expanded: Boolean) = setControlsExpandedNow(expanded)

    private fun setControlsExpandedNow(expanded: Boolean) {
        controlsExpanded = expanded
        controlBar.visibility = if (expanded) View.VISIBLE else View.GONE
        chevron.pointingUp = expanded
    }

    /**
     * The keyboard as a small box that floats over the desktop: drag it by its title, make it Bigger (at most half the
     * screen) or Smaller, or Hide it. The keyboard inside keeps its real layout and is only scaled as a whole.
     */
    private fun buildKeyboardBox(): View {
        keyboard = KeyboardView(this).apply { onEvent = { inputLink?.bridge?.handle(it) } }
        // The saved layout (made with the layout editor) is loaded, and every edit is saved for next time.
        KeyboardPrefs.attach(this, keyboard)
        keyFrame = FrameLayout(this)
        keyFrame.addView(keyboard, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        val pad = Ui.dp(this, 6)
        val handle = Ui.text(this, "Drag to move", 12f, Ui.MUTED).apply {
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
        layoutChip = chip("Layout") { keyboard.setEditMode(!keyboard.editMode) }
        keyboard.onEditModeChanged = { editing -> onKeyboardEditChanged(editing) }
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(handle, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        header.addView(layoutChip)
        header.addView(View(this), LinearLayout.LayoutParams(Ui.dp(this, 6), 1))
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
        // Hiding the keyboard while it is being edited ends the editing, so it never comes back half-edited.
        if (!visible && keyboard.editMode) keyboard.setEditMode(false)
        keyboardBox.visibility = if (visible) View.VISIBLE else View.GONE
        if (visible) layoutKeyboard()
    }

    /** Editing needs room, so the box grows to its enlarged size while editing and goes back afterwards. */
    private fun onKeyboardEditChanged(editing: Boolean) {
        layoutChip.text = if (editing) "Done" else "Layout"
        if (editing) {
            enlargedBeforeEditing = keyboardEnlarged
            keyboardEnlarged = true
        } else {
            keyboardEnlarged = enlargedBeforeEditing
        }
        sizeButton.text = if (keyboardEnlarged) "Smaller" else "Bigger"
        // The keyboard's own height changes when its edit bar appears or goes, so measure again once it has been rebuilt.
        keyboard.post { layoutKeyboard() }
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
        setControlsExpanded(true)
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
        val size = ViewFit.size(viewMode, w, h, aspect)
        val cw = size.width
        val ch = size.height
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
        acquireWifiLock()
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
        releaseWifiLock()
        super.onStop()
    }

    /**
     * Phones put the Wi-Fi radio to sleep between bursts to save power, which shows up as stutter and delay in a
     * stream. A low-latency Wi-Fi lock (as Moonlight holds) keeps it awake while this screen is open.
     */
    @Suppress("DEPRECATION")
    private fun acquireWifiLock() {
        try {
            val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            val mode = if (Build.VERSION.SDK_INT >= 29) WifiManager.WIFI_MODE_FULL_LOW_LATENCY else WifiManager.WIFI_MODE_FULL_HIGH_PERF
            val lock = wifi.createWifiLock(mode, "mesh:stream")
            lock.setReferenceCounted(false)
            lock.acquire()
            wifiLock = lock
        } catch (e: Exception) {
            wifiLock = null // not fatal: the stream just runs without the lock
        }
    }

    private fun releaseWifiLock() {
        try {
            wifiLock?.takeIf { it.isHeld }?.release()
        } catch (_: Exception) {
        }
        wifiLock = null
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
                refreshAbsolute()
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
        // The computer's Mesh agent approves this pairing in Sunshine for us when it can, so there is nothing to type.
        // The PIN is shown only if that does not work, and the same PIN is already being waited on below, so typing it
        // into Sunshine's web page still completes the pairing.
        showMessage("Pairing with $hostName", "Asking the computer to approve this phone...")
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
        askAgentToApprove(pin, hostName, g, attemptsLeft = 25)
    }

    /** Sends the PIN to the computer's agent over the secure input channel (waiting a moment for it to connect). */
    private fun askAgentToApprove(pin: String, hostName: String, g: Int, attemptsLeft: Int) {
        if (g != generation || paired) return
        val link = inputLink
        val mip = link?.connected
        when {
            mip != null && mip.canPairSunshine -> mip.pairSunshine(pin, Build.MODEL ?: "Android phone") { result ->
                runOnUiThread {
                    if (g != generation || paired) return@runOnUiThread
                    if (result.ok) {
                        showMessage("Pairing with $hostName", "Approved by the computer. Finishing...")
                    } else {
                        showManualPin(pin, hostName, result.text)
                    }
                }
            }
            mip != null -> showManualPin(pin, hostName, "The Mesh agent on this computer is too old to approve it automatically. Update it with git pull and restart it.")
            link != null && link.connecting && attemptsLeft > 0 ->
                handler.postDelayed({ askAgentToApprove(pin, hostName, g, attemptsLeft - 1) }, 200)
            else -> showManualPin(pin, hostName, "The input connection to the computer is not up, so it could not approve this automatically.")
        }
    }

    private fun showManualPin(pin: String, hostName: String, reason: String) {
        showMessage(
            "Pair with $hostName",
            "$reason\n\nEnter this PIN in Sunshine's web page (Sunshine > PIN), then the desktop appears here. Waiting for the PIN...",
            pin = pin,
        )
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
            setControlsExpanded(false) // full screen: the arrow tab at the top brings the controls back
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
        if (::statusChip.isInitialized) statusChip.visibility = View.INVISIBLE
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

    // --------------------------------------------------- pointer, select and view

    private fun loadViewPrefs() {
        pointerMode = runCatching { PointerMode.valueOf(viewPrefs.getString("pointer", null) ?: "") }.getOrDefault(PointerMode.TRACKPAD)
        viewMode = ViewMode.parse(viewPrefs.getString("view", null))
        presetIndex = viewPrefs.getInt("quality", 0).coerceIn(0, StreamSettings.presets.size - 1)
    }

    private fun pointerLabel(mode: PointerMode) = when (mode) {
        PointerMode.TRACKPAD -> "Trackpad"
        PointerMode.TOUCH -> "Touch"
        PointerMode.AUTO -> "Auto"
    }

    private fun viewLabel(mode: ViewMode) = when (mode) {
        ViewMode.FIT -> "Fit"
        ViewMode.FILL -> "Fill"
        ViewMode.ZOOM -> "Zoom"
    }

    private fun applyPointerMode(mode: PointerMode) {
        pointerMode = mode
        trackpad.pointerPolicy.mode = mode
        pointerChip.text = "Pointer: " + pointerLabel(mode)
        viewPrefs.edit().putString("pointer", mode.name).apply()
        refreshAbsolute()
    }

    /** Touch and Auto place the pointer by position, which only works if the computer's agent can do that. */
    private fun refreshAbsolute() {
        if (!::trackpad.isInitialized) return
        val connected = inputLink?.connected
        val can = connected?.canMoveAbsolute == true
        trackpad.absoluteEnabled = can
        if (connected != null && !can && pointerMode != PointerMode.TRACKPAD && !warnedNoAbsolute) {
            warnedNoAbsolute = true
            Toast.makeText(
                this,
                "This computer's Mesh agent cannot place the pointer yet, so Touch and Auto work like the trackpad. Update it with git pull and restart it.",
                Toast.LENGTH_LONG,
            ).show()
        }
    }

    private fun showPointerPicker() {
        val modes = PointerMode.values()
        val names = arrayOf(
            "Trackpad: slide to move the pointer, tap to click where it is",
            "Touch: tap where you want to click, drag to drag from there",
            "Auto: tap jumps there when you have not just slid the pointer, hold then drag to select",
        )
        AlertDialog.Builder(this)
            .setTitle("Pointer")
            .setSingleChoiceItems(names, modes.indexOf(pointerMode)) { dialog, which ->
                dialog.dismiss()
                applyPointerMode(modes[which])
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun toggleSelect() {
        trackpad.selectMode = !trackpad.selectMode
        val on = trackpad.selectMode
        selectChip.text = if (on) "Select: on" else "Select"
        selectChip.background = Ui.box(this, fill = if (on) Color.argb(240, 74, 74, 80) else Color.argb(150, 11, 11, 12), radiusDp = 10)
    }

    private fun showViewPicker() {
        val modes = ViewMode.values()
        val names = arrayOf(
            "Fit: the whole picture without distortion, black bars where the shapes differ",
            "Fill: the whole picture over the whole screen, nothing cropped (stretched a little)",
            "Zoom: cover the screen without stretching, cropping the edges",
        )
        AlertDialog.Builder(this)
            .setTitle("View")
            .setSingleChoiceItems(names, modes.indexOf(viewMode)) { dialog, which ->
                dialog.dismiss()
                viewMode = modes[which]
                viewChip.text = "View: " + viewLabel(viewMode)
                viewPrefs.edit().putString("view", viewMode.name).apply()
                fitSurface()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /** Tapping the status line copies a report that shows where a delay comes from, ready to paste. */
    private fun copyReport() {
        val running = session
        val text = running?.report(viewLabel(viewMode), pointerLabel(pointerMode)) ?: return
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText("Mesh stream report", text))
        Toast.makeText(this, "Diagnostics copied. Paste them into the chat.", Toast.LENGTH_LONG).show()
    }

    private fun showQualityPicker() {
        val names = StreamSettings.presets.map { it.first }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle("Stream quality")
            .setSingleChoiceItems(names, presetIndex) { dialog, which ->
                dialog.dismiss()
                chooseQuality(which)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun chooseQuality(index: Int) {
        if (index == presetIndex) return
        presetIndex = index
        viewPrefs.edit().putInt("quality", presetIndex).apply()
        qualityChip.text = StreamSettings.presets[presetIndex].first.substringBefore(" ")
        // A running stream is restarted so the new size is really used; before that the choice just applies to the start.
        if (session != null || messagePanel.visibility == View.VISIBLE) {
            showMessage("Switching to ${StreamSettings.presets[presetIndex].first}", "")
            restart()
        }
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
