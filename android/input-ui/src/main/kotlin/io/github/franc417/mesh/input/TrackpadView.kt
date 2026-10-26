package io.github.franc417.mesh.input

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.util.AttributeSet
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import kotlin.math.abs
import kotlin.math.max

private const val TAP_MOVE_THRESHOLD_PX = 12f
private const val TAP_MAX_DURATION_MS = 200L
private const val TOUCH_TAP_MAX_DURATION_MS = 450L   // pointing with a finger is slower than tapping a trackpad
private const val OVERLAY_DOT_ALPHA = 70
private const val OVERLAY_HELD_ALPHA = 150
private const val LONG_PRESS_MS = 400L                // holding a finger still this long (Auto mode) starts a press-and-drag
private const val SCROLL_DIVISOR = 4f
private const val RELEASE_FADE_MS = 220L
private const val RIPPLE_MS = 300L
private const val MAX_TRACKED_POINTERS = 3
private const val DOUBLE_TAP_WINDOW_MS = 300L
private const val DOUBLE_TAP_DISTANCE_PX = 50f
private const val THREE_FINGER_THRESHOLD_PX = 70f

/**
 * Trackpad surface. Gestures:
 * - One-finger drag: move the cursor
 * - One-finger tap: click
 * - One-finger tap, then a second tap-and-hold-drag at roughly the same
 *   spot within 300ms: click-and-drag (press, drag, release) — the
 *   standard trackpad gesture for text selection and drag-and-drop.
 *   The first tap still sends a normal click; the second touch's
 *   down/move/up becomes click(down)/move.../click(up), which is
 *   exactly the raw sequence a real double-click-drag produces, so the
 *   receiving OS's own text-selection heuristics do the rest.
 * - Two-finger drag: scroll
 * - Three-finger horizontal swipe: switch workspace (Ctrl+Alt+Left/
 *   Right) — confirmed as the default binding across GNOME, Cinnamon
 *   (Linux Mint's own default desktop), MATE, and XFCE, not a
 *   GNOME-only convention. KDE Plasma and tiling window managers
 *   (i3, sway) often bind this differently, so it may need adjusting
 *   there.
 * - Three-finger vertical swipe: swipe up sends Super/Meta alone
 *   (opens an app overview/launcher on most desktop environments,
 *   though the exact resulting UI differs by DE); swipe down sends
 *   Escape (back/close). This vertical mapping is more of an
 *   assumption than the horizontal one — easy to change if it's not
 *   the right direction.
 *
 * Visuals (purely cosmetic, never affect what gets sent): a recessed
 * panel with a faint dot grid, a thin ring + centre dot under each
 * finger (a brighter fill while a click-drag is held), and a short
 * ripple on a tap. All hardware-accelerated plain drawing: the earlier
 * glowing-hexagon effect needed a software layer and a blur mask,
 * which re-rasterised the whole view on the CPU every frame while the
 * finger was moving.
 *
 * Emits already protocol-shaped messages (as Map<String, Any?>) via
 * onEvent — this view knows nothing about WebSockets or JSON.
 *
 * Every gesture above is untested beyond compiling — touch feel,
 * timing thresholds, and whether three fingers register cleanly as a
 * single gesture rather than fighting the one/two-finger paths can
 * only be judged on a real touchscreen.
 */
class TrackpadView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    var onEvent: ((Map<String, Any?>) -> Unit)? = null

    /**
     * Mesh change: when true the pad draws no panel or grid, only the finger rings and tap ripples, so it can lie on
     * top of the streamed desktop and be used without hiding it.
     */
    var overlay: Boolean = false
        set(value) {
            field = value
            invalidate()
        }

    /**
     * How one finger is understood: as a trackpad, as a touchscreen, or as whichever fits (see [PointerPolicy]).
     * Touch and Auto place the pointer by position, so they need [absoluteEnabled] and a [videoArea]; without them
     * every finger is treated as a trackpad.
     */
    val pointerPolicy = PointerPolicy()

    /** When on, one finger dragging selects: it holds the left button down while it moves (to highlight text, say). */
    var selectMode: Boolean = false

    /** Where the streamed picture is inside this view, in this view's pixels. Null when there is no picture. */
    var videoArea: VideoArea? = null

    /** True when the computer can place the pointer at an exact spot (the agent advertises it). */
    var absoluteEnabled: Boolean = false

    private enum class OneFinger { RELATIVE, DIRECT, IGNORED }
    private enum class Jump { DONE, OUTSIDE, UNAVAILABLE }

    private val twoFingerTap = TwoFingerTap()
    private var oneFinger = OneFinger.RELATIVE
    private var directPressed = false   // the left button is held by a touch-style drag
    private val longPress = Runnable { onLongPress() }

    private var lastX = 0f
    private var lastY = 0f
    private var lastTwoFingerX = 0f
    private var lastTwoFingerY = 0f
    private var downTime = 0L
    private var totalMovement = 0f
    private var usedMultitouch = false

    private var lastReleaseTime = 0L
    private var lastReleaseX = 0f
    private var lastReleaseY = 0f
    private var dragArmed = false
    private var dragActive = false

    private var threeFingerStartX = 0f
    private var threeFingerStartY = 0f
    private var threeFingerTriggered = false

    private var touching = false
    private var releasedAt = 0L

    // Visual-only copies of where the fingers are (gesture logic above
    // never reads these).
    private val ptX = FloatArray(MAX_TRACKED_POINTERS)
    private val ptY = FloatArray(MAX_TRACKED_POINTERS)
    private var ptCount = 0

    private var rippleX = 0f
    private var rippleY = 0f
    private var rippleStart = 0L

    private val density = resources.displayMetrics.density
    private fun dp(v: Float) = v * density

    private val panelRect = RectF()
    private val panelRadius = dp(18f)
    private val panelInset = dp(8f)

    private val panelFill = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.parseColor("#111113")
    }
    private val panelStroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(1f)
        color = Color.parseColor("#2B2B30")
    }
    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(1.5f)
        color = Color.WHITE
    }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.WHITE
    }
    init {
        // A faint dot grid, built once as a tiny tile and repeated by a
        // shader, so drawing it costs one fill per frame.
        val tilePx = max(1, dp(22f).toInt())
        val tile = Bitmap.createBitmap(tilePx, tilePx, Bitmap.Config.ARGB_8888)
        val tileCanvas = Canvas(tile)
        val dot = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE; alpha = 26 }
        tileCanvas.drawCircle(tilePx / 2f, tilePx / 2f, dp(1f), dot)
        gridPaint.shader = BitmapShader(tile, Shader.TileMode.REPEAT, Shader.TileMode.REPEAT)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        panelRect.set(panelInset, panelInset, w - panelInset, h - panelInset)
    }

    /** Copies finger positions for drawing only; `skip` leaves out a lifting pointer. */
    private fun syncPointers(event: MotionEvent, skip: Int = -1) {
        var n = 0
        for (i in 0 until event.pointerCount) {
            if (i == skip || n >= MAX_TRACKED_POINTERS) continue
            ptX[n] = event.getX(i)
            ptY[n] = event.getY(i)
            n++
        }
        ptCount = n
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                lastX = event.x
                lastY = event.y
                downTime = event.eventTime
                totalMovement = 0f
                usedMultitouch = false
                touching = true
                syncPointers(event)

                val dt = event.eventTime - lastReleaseTime
                val ddx = event.x - lastReleaseX
                val ddy = event.y - lastReleaseY
                dragArmed = dt in 0..DOUBLE_TAP_WINDOW_MS &&
                    (ddx * ddx + ddy * ddy) < DOUBLE_TAP_DISTANCE_PX * DOUBLE_TAP_DISTANCE_PX
                dragActive = false
                directPressed = false

                oneFinger = beginOneFinger(event.x, event.y)
                removeCallbacks(longPress)
                if (oneFinger == OneFinger.RELATIVE && pointerPolicy.longPressStartsDrag() && absoluteEnabled) {
                    postDelayed(longPress, LONG_PRESS_MS)
                }

                postInvalidateOnAnimation()
            }

            MotionEvent.ACTION_POINTER_DOWN -> {
                removeCallbacks(longPress)
                if (directPressed) releaseDirect()   // a second finger ends a one-finger drag
                syncPointers(event)
                usedMultitouch = true
                dragArmed = false
                if (event.pointerCount == 2) {
                    lastTwoFingerX = averageX(event)
                    lastTwoFingerY = averageY(event)
                    twoFingerTap.begin(event.eventTime)
                } else if (event.pointerCount == 3) {
                    twoFingerTap.cancel()
                    threeFingerStartX = averageX(event)
                    threeFingerStartY = averageY(event)
                    threeFingerTriggered = false
                }
            }

            MotionEvent.ACTION_POINTER_UP -> {
                // Two fingers that touched briefly and barely moved are a right click.
                if (event.pointerCount == 2 && twoFingerTap.end(event.eventTime)) {
                    rightClick(averageX(event), averageY(event))
                }
                syncPointers(event, skip = event.actionIndex)
                postInvalidateOnAnimation()
            }

            MotionEvent.ACTION_MOVE -> {
                syncPointers(event)
                when (event.pointerCount) {
                    3 -> handleThreeFingerSwipe(event)
                    2 -> {
                        val x = averageX(event)
                        val y = averageY(event)
                        val dy = y - lastTwoFingerY
                        twoFingerTap.move(abs(x - lastTwoFingerX) + abs(dy))
                        // The first few pixels are held back: they are the wobble of a tap, not a scroll.
                        if (dy != 0f && twoFingerTap.movedTooFar) {
                            onEvent?.invoke(mapOf("t" to "scroll", "dy" to (dy / SCROLL_DIVISOR)))
                        }
                        lastTwoFingerX = x
                        lastTwoFingerY = y
                    }
                    else -> moveOneFinger(event)
                }
                postInvalidateOnAnimation()
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                removeCallbacks(longPress)
                val duration = event.eventTime - downTime
                if (directPressed) {
                    releaseDirect()
                } else if (dragActive) {
                    onEvent?.invoke(mapOf("t" to "click", "button" to "left", "action" to "up"))
                } else if (event.actionMasked == MotionEvent.ACTION_UP && !usedMultitouch &&
                    totalMovement < TAP_MOVE_THRESHOLD_PX &&
                    duration < (if (oneFinger == OneFinger.DIRECT) TOUCH_TAP_MAX_DURATION_MS else TAP_MAX_DURATION_MS)
                ) {
                    if (tap(event.x, event.y)) {
                        rippleX = event.x
                        rippleY = event.y
                        rippleStart = System.currentTimeMillis()
                    }
                }
                if (!usedMultitouch) {
                    lastReleaseTime = event.eventTime
                    lastReleaseX = event.x
                    lastReleaseY = event.y
                }
                touching = false
                releasedAt = System.currentTimeMillis()
                dragArmed = false
                dragActive = false
                postInvalidateOnAnimation()
            }
        }
        return true
    }

    /** Decides at touch-down whether this finger works like a trackpad or like a touchscreen, and for touch moves the pointer under it. */
    private fun beginOneFinger(x: Float, y: Float): OneFinger {
        if (!pointerPolicy.dragIsDirect(selectMode)) return OneFinger.RELATIVE
        return when (jumpTo(x, y, clamped = false)) {
            Jump.DONE -> OneFinger.DIRECT
            Jump.OUTSIDE -> OneFinger.IGNORED       // on the black bars: nothing to point at
            Jump.UNAVAILABLE -> OneFinger.RELATIVE  // cannot place the pointer, so behave as a trackpad
        }
    }

    private fun moveOneFinger(event: MotionEvent) {
        val dx = event.x - lastX
        val dy = event.y - lastY
        totalMovement += abs(dx) + abs(dy)
        val moved = totalMovement > TAP_MOVE_THRESHOLD_PX
        if (moved) removeCallbacks(longPress)

        when (oneFinger) {
            OneFinger.IGNORED -> {}
            OneFinger.DIRECT -> {
                if (!directPressed && moved) {
                    directPressed = true
                    leftButton("down")
                }
                if (directPressed) jumpTo(event.x, event.y, clamped = true)
            }
            OneFinger.RELATIVE -> {
                if ((dragArmed || selectMode) && !dragActive && moved) {
                    dragActive = true
                    leftButton("down")
                }
                if (moved) pointerPolicy.noteTrackpadUse()
                if (dx != 0f || dy != 0f) {
                    onEvent?.invoke(mapOf("t" to "move", "dx" to dx, "dy" to dy))
                }
            }
        }
        lastX = event.x
        lastY = event.y
    }

    /** A finger held still in Auto mode becomes a touch-style press: from here a drag highlights. */
    private fun onLongPress() {
        if (!touching || usedMultitouch || oneFinger != OneFinger.RELATIVE || dragActive ||
            totalMovement > TAP_MOVE_THRESHOLD_PX
        ) return
        if (jumpTo(lastX, lastY, clamped = false) != Jump.DONE) return
        oneFinger = OneFinger.DIRECT
        directPressed = true
        leftButton("down")
        performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
        postInvalidateOnAnimation()
    }

    /** A tap. Returns false if it was ignored (a touch tap on the black bars). */
    private fun tap(x: Float, y: Float): Boolean {
        when (oneFinger) {
            OneFinger.IGNORED -> return false
            OneFinger.DIRECT -> {}   // the pointer already went to the finger when it touched down
            OneFinger.RELATIVE -> if (pointerPolicy.tapAction() == PointerTap.JUMP_AND_CLICK) {
                // Pointing at the screen. If it cannot be done the tap is a plain click where the pointer is.
                if (jumpTo(x, y, clamped = false) == Jump.OUTSIDE) return false
            }
        }
        onEvent?.invoke(mapOf("t" to "click", "button" to "left"))
        return true
    }

    private fun rightClick(x: Float, y: Float) {
        onEvent?.invoke(mapOf("t" to "click", "button" to "right"))
        rippleX = x
        rippleY = y
        rippleStart = System.currentTimeMillis()
    }

    private fun leftButton(action: String) {
        onEvent?.invoke(mapOf("t" to "click", "button" to "left", "action" to action))
    }

    private fun releaseDirect() {
        directPressed = false
        leftButton("up")
    }

    private fun jumpTo(x: Float, y: Float, clamped: Boolean): Jump {
        val area = videoArea
        if (!absoluteEnabled || area == null || !area.valid) return Jump.UNAVAILABLE
        val f = (if (clamped) area.clamped(x, y) else area.fraction(x, y)) ?: return Jump.OUTSIDE
        onEvent?.invoke(mapOf("t" to "moveabs", "x" to f.x, "y" to f.y))
        return Jump.DONE
    }

    override fun onDetachedFromWindow() {
        removeCallbacks(longPress)
        super.onDetachedFromWindow()
    }

    private fun handleThreeFingerSwipe(event: MotionEvent) {
        if (threeFingerTriggered) return
        val dx = averageX(event) - threeFingerStartX
        val dy = averageY(event) - threeFingerStartY
        if (abs(dx) < THREE_FINGER_THRESHOLD_PX && abs(dy) < THREE_FINGER_THRESHOLD_PX) return

        threeFingerTriggered = true
        if (abs(dx) > abs(dy)) {
            // Confirmed default across GNOME, Cinnamon, MATE, and XFCE
            // (not GNOME-specific) — see the class doc comment.
            val code = if (dx < 0) "ArrowLeft" else "ArrowRight"
            sendShortcut(listOf("ctrl", "alt"), code)
        } else if (dy < 0) {
            // Swipe up: app overview/launcher on most desktop environments.
            sendShortcut(emptyList(), "Meta")
        } else {
            // Swipe down: back/close.
            sendShortcut(emptyList(), "Escape")
        }
    }

    private fun sendShortcut(mods: List<String>, code: String) {
        onEvent?.invoke(mapOf("t" to "key", "code" to code, "action" to "down", "mods" to mods))
        onEvent?.invoke(mapOf("t" to "key", "code" to code, "action" to "up", "mods" to mods))
    }

    private fun averageX(event: MotionEvent): Float {
        var sum = 0f
        for (i in 0 until event.pointerCount) sum += event.getX(i)
        return sum / event.pointerCount
    }

    private fun averageY(event: MotionEvent): Float {
        var sum = 0f
        for (i in 0 until event.pointerCount) sum += event.getY(i)
        return sum / event.pointerCount
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val now = System.currentTimeMillis()

        // Recessed panel with the dot grid (skipped in overlay mode so the video underneath stays visible).
        if (!overlay) {
            canvas.drawRoundRect(panelRect, panelRadius, panelRadius, panelFill)
            canvas.drawRoundRect(panelRect, panelRadius, panelRadius, gridPaint)
            canvas.drawRoundRect(panelRect, panelRadius, panelRadius, panelStroke)
        }

        // 1 while a finger is down, then fades to 0 after release.
        val presence = if (touching) {
            1f
        } else {
            val since = now - releasedAt
            if (since >= RELEASE_FADE_MS) 0f else 1f - since.toFloat() / RELEASE_FADE_MS
        }

        // Over the desktop (overlay) the only mark is a small faint dot, a little stronger while the button is held;
        // the ring and the ripple are for the standalone pad, where they are the whole picture.
        if (overlay) {
            if (presence > 0f) {
                val held = dragActive || directPressed
                fillPaint.alpha = ((if (held) OVERLAY_HELD_ALPHA else OVERLAY_DOT_ALPHA) * presence).toInt()
                val radius = dp(if (held) 7f else 5f)
                for (i in 0 until ptCount) canvas.drawCircle(ptX[i], ptY[i], radius, fillPaint)
                postInvalidateOnAnimation()
            }
            return
        }

        // A ring and centre dot under each finger; the fill is brighter
        // while a click-drag is being held so "pressed" is visible.
        if (presence > 0f) {
            val ringRadius = dp(22f)
            val fillAlpha = if (dragActive) 70 else 26
            for (i in 0 until ptCount) {
                fillPaint.alpha = (fillAlpha * presence).toInt()
                canvas.drawCircle(ptX[i], ptY[i], ringRadius, fillPaint)
                ringPaint.alpha = (210 * presence).toInt()
                canvas.drawCircle(ptX[i], ptY[i], ringRadius, ringPaint)
                fillPaint.alpha = (255 * presence).toInt()
                canvas.drawCircle(ptX[i], ptY[i], dp(3f), fillPaint)
            }
        }

        // Tap ripple: a ring that expands and fades.
        val rippleAge = now - rippleStart
        val rippleActive = rippleStart != 0L && rippleAge in 0 until RIPPLE_MS
        if (rippleActive) {
            val p = rippleAge.toFloat() / RIPPLE_MS
            val eased = 1f - (1f - p) * (1f - p)
            ringPaint.alpha = (150 * (1f - p)).toInt()
            canvas.drawCircle(rippleX, rippleY, dp(14f) + dp(30f) * eased, ringPaint)
        }

        // Only keep redrawing while something is actually animating.
        if (touching || presence > 0f || rippleActive) {
            postInvalidateOnAnimation()
        }
    }
}
