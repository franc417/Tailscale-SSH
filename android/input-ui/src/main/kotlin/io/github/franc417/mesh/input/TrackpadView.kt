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
import android.view.MotionEvent
import android.view.View
import kotlin.math.abs
import kotlin.math.max

private const val TAP_MOVE_THRESHOLD_PX = 12f
private const val TAP_MAX_DURATION_MS = 200L
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

    private var lastX = 0f
    private var lastY = 0f
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

                postInvalidateOnAnimation()
            }

            MotionEvent.ACTION_POINTER_DOWN -> {
                syncPointers(event)
                usedMultitouch = true
                dragArmed = false
                if (event.pointerCount == 2) {
                    lastTwoFingerY = averageY(event)
                } else if (event.pointerCount == 3) {
                    threeFingerStartX = averageX(event)
                    threeFingerStartY = averageY(event)
                    threeFingerTriggered = false
                }
            }

            MotionEvent.ACTION_POINTER_UP -> {
                syncPointers(event, skip = event.actionIndex)
                postInvalidateOnAnimation()
            }

            MotionEvent.ACTION_MOVE -> {
                syncPointers(event)
                when (event.pointerCount) {
                    3 -> handleThreeFingerSwipe(event)
                    2 -> {
                        val y = averageY(event)
                        val dy = y - lastTwoFingerY
                        if (dy != 0f) {
                            onEvent?.invoke(mapOf("t" to "scroll", "dy" to (dy / SCROLL_DIVISOR)))
                        }
                        lastTwoFingerY = y
                    }
                    else -> {
                        val dx = event.x - lastX
                        val dy = event.y - lastY
                        totalMovement += abs(dx) + abs(dy)

                        if (dragArmed && !dragActive && totalMovement > TAP_MOVE_THRESHOLD_PX) {
                            dragActive = true
                            onEvent?.invoke(mapOf("t" to "click", "button" to "left", "action" to "down"))
                        }

                        if (dx != 0f || dy != 0f) {
                            onEvent?.invoke(mapOf("t" to "move", "dx" to dx, "dy" to dy))
                        }
                        lastX = event.x
                        lastY = event.y
                    }
                }
                postInvalidateOnAnimation()
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                val duration = event.eventTime - downTime
                if (dragActive) {
                    onEvent?.invoke(mapOf("t" to "click", "button" to "left", "action" to "up"))
                } else if (event.actionMasked == MotionEvent.ACTION_UP &&
                    !usedMultitouch && totalMovement < TAP_MOVE_THRESHOLD_PX && duration < TAP_MAX_DURATION_MS
                ) {
                    onEvent?.invoke(mapOf("t" to "click", "button" to "left"))
                    rippleX = event.x
                    rippleY = event.y
                    rippleStart = System.currentTimeMillis()
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
