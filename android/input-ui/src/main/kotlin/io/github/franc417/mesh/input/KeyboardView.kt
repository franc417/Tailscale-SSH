package io.github.franc417.mesh.input

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.util.AttributeSet
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import kotlin.math.abs

// Palette: near-black surfaces, hairline borders, one light "armed" state.
private val BOARD_COLOR = Color.parseColor("#0B0B0C")
private val DIVIDER_COLOR = Color.parseColor("#1F1F23")
private val KEY_COLOR = Color.parseColor("#1A1A1D")
private val KEY_PRESSED_COLOR = Color.parseColor("#2E2E34")
private val MODIFIER_COLOR = Color.parseColor("#141416")
private val MODIFIER_PRESSED_COLOR = Color.parseColor("#2A2A30")
private val ARMED_COLOR = Color.parseColor("#EDEDED")
private val ARMED_PRESSED_COLOR = Color.parseColor("#D2D2D6")
private val BORDER_COLOR = Color.parseColor("#2B2B30")
private val BORDER_PRESSED_COLOR = Color.parseColor("#55555E")
private val TEXT_COLOR_LIGHT = Color.parseColor("#EDEDED")
private val TEXT_COLOR_DARK = Color.parseColor("#0B0B0C")
// Row height follows the screen (about 7% of its height, between 56 and 72dp) so the
// keys are comfortably tall on a big phone without swallowing the trackpad on a small one.
private const val ROW_HEIGHT_FRACTION = 0.070f
private const val MIN_ROW_DP = 56f
private const val MAX_ROW_DP = 72f
private const val KEY_RADIUS_DP = 12f
private const val KEY_MARGIN_DP = 3f        // sideways: small, so ten keys in a row stay wide
private const val KEY_MARGIN_V_DP = 4f      // up and down: more air between rows
private const val BOARD_PADDING_DP = 6f
private const val KEY_TEXT_SP = 19f
private const val WORD_TEXT_SP = 13f  // multi-character labels: esc, ctrl, space, PgUp, F10...
private const val PRESS_SCALE = 0.95f
private const val PREVIEW_MAX_CHARS = 200

// US QWERTY shift-symbol mapping, for the typing preview only.
private val SHIFT_SYMBOLS = mapOf(
    "Digit1" to "!", "Digit2" to "@", "Digit3" to "#", "Digit4" to "$",
    "Digit5" to "%", "Digit6" to "^", "Digit7" to "&", "Digit8" to "*",
    "Digit9" to "(", "Digit0" to ")", "Minus" to "_", "Equal" to "+",
)

// Punctuation keys — real physical keys, 100% reliable on any
// receiving system. Each maps to (unshifted, shifted) for the preview.
private val PUNCTUATION_CHARS = mapOf(
    "Period" to ("." to ">"),
    "Comma" to ("," to "<"),
    "Semicolon" to (";" to ":"),
    "Quote" to ("'" to "\""),
    "Slash" to ("/" to "?"),
    "Backslash" to ("\\" to "|"),
    "BracketLeft" to ("[" to "{"),
    "BracketRight" to ("]" to "}"),
    "Backquote" to ("`" to "~"),
)

private const val EDIT_BAR_HEIGHT_DP = 46f
private const val RESIZE_ZONE_DP = 26f
private const val WIDTH_STEP = 0.25f
private const val HEIGHT_STEP = 0.1f
private const val EDIT_HINT = "Tap a key to select it. Drag it to move it, drag its right edge to resize."

/**
 * Full on-screen keyboard, built from a LayoutDef (KeyboardLayout.kt): layers
 * (letters and the two symbol pages), rows, keys with widths. Ctrl/Alt/Shift
 * are sticky (tap to arm, next key only, then cleared) and stay armed across
 * layer switches. A key can switch layer (123, ABC, =\<); tapping the
 * toggle for the layer you are on returns to the first layer. fn is not a
 * layer: while it is on, keys change in place (digits become F1..F10, esc F11,
 * prtsc F12, the arrows Home/PgUp/PgDn/End) until it is pressed again.
 *
 * Row height is fixed per row (not stretched to fill available space) — see
 * activity_main.xml, where this view is wrap_content height and the
 * trackpad absorbs the remaining space.
 *
 * EDIT MODE (setEditMode): taps select a key instead of typing. Drag a key
 * to move it (within its row or to another row), drag its right edge to
 * resize it (the other keys in the row give or take the space), or use the
 * bar's buttons for exact steps. Every change goes to onLayoutChanged so the
 * host can save it; Copy/Paste exchange the layout as editable text.
 *
 * Emits key events as protocol-shaped maps via onEvent, and a live
 * local echo of what's been typed via onPreviewTextChanged (a
 * reconstruction from which keys were tapped, not a readback of what
 * landed in a text field on the desktop — this view knows nothing
 * about WebSockets, JSON, or the far end).
 */
class KeyboardView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : LinearLayout(context, attrs) {

    var onEvent: ((Map<String, Any?>) -> Unit)? = null
    var onPreviewTextChanged: ((String) -> Unit)? = null

    /** Called with the new layout after every edit, so the host can save it. */
    var onLayoutChanged: ((LayoutDef) -> Unit)? = null
    var onEditModeChanged: ((Boolean) -> Unit)? = null

    /** Called when the vibration strength is changed from the edit bar, so the host can save it. */
    var onHapticLevelChanged: ((Int) -> Unit)? = null

    /** Vibration for key presses (see Haptics). */
    val haptics = Haptics(context)

    private val previewBuffer = StringBuilder()

    private val density = resources.displayMetrics.density
    private val keyRadiusPx = KEY_RADIUS_DP * density
    private val rowHeightPx = run {
        val screenDp = resources.displayMetrics.heightPixels / density
        ((screenDp * ROW_HEIGHT_FRACTION).coerceIn(MIN_ROW_DP, MAX_ROW_DP) * density).toInt()
    }
    private val keyMarginPx = (KEY_MARGIN_DP * density).toInt()
    private val keyMarginVPx = (KEY_MARGIN_V_DP * density).toInt()
    private val strokePx = maxOf(1, density.toInt())
    private val keyTypeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
    private val touchSlopPx = ViewConfiguration.get(context).scaledTouchSlop

    var layout: LayoutDef = DefaultLayout.build()
        private set
    private var currentLayer: String = layout.defaultLayerName

    var editMode = false
        private set
    private var selRow = -1
    private var selKey = -1

    // fn stays on until pressed again, and applies on whichever layer is showing.
    private var fnActive = false

    private val armedMods = mutableSetOf<String>()
    private data class StickyInfo(val view: TextView, val restColor: Int, val restPressedColor: Int)
    private val modifierButtons = mutableMapOf<String, MutableList<StickyInfo>>()

    // Holds the row views; rebuilt whenever the layer, the layout or the selection changes.
    private val keyHost = LinearLayout(context).apply {
        orientation = VERTICAL
        layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT)
    }
    private val editInfo = TextView(context)
    private lateinit var vibrationChip: TextView
    private val editBar = HorizontalScrollView(context)
    private var resetArmedAt = 0L

    init {
        orientation = VERTICAL
        setBackgroundColor(BOARD_COLOR)
        val pad = (BOARD_PADDING_DP * density).toInt()
        setPadding(pad, 0, pad, pad)
        clipToPadding = false  // lets the divider below run edge to edge through the padding

        // Hairline between the trackpad and the keys.
        addView(View(context).apply {
            setBackgroundColor(DIVIDER_COLOR)
            layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, strokePx).apply {
                setMargins(-pad, 0, -pad, pad)
            }
        })

        buildEditBar()
        addView(editBar)
        addView(keyHost)
        rebuild()
    }

    // ------------------------------------------------------------ public API

    /** Replaces the whole layout (e.g. one loaded from storage or pasted in). */
    fun setLayout(newLayout: LayoutDef, notify: Boolean = false) {
        layout = newLayout
        if (layout.layer(currentLayer) == null) currentLayer = layout.defaultLayerName
        clearSelection()
        rebuild()
        if (notify) onLayoutChanged?.invoke(layout)
    }

    fun setEditMode(on: Boolean) {
        if (editMode == on) return
        editMode = on
        if (on) fnActive = false  // edit the real keys, not their fn versions
        editBar.visibility = if (on) View.VISIBLE else View.GONE
        clearSelection()
        updateEditInfo()
        rebuild()
        onEditModeChanged?.invoke(on)
    }

    // ------------------------------------------------------------ building

    private fun clearSelection() {
        selRow = -1
        selKey = -1
    }

    private fun currentRows(): List<RowDef> = layout.layer(currentLayer)?.rows ?: emptyList()

    private fun rebuild() {
        keyHost.removeAllViews()
        modifierButtons.clear()
        val rows = currentRows()
        for ((r, rowDef) in rows.withIndex()) {
            val row = LinearLayout(context).apply {
                orientation = HORIZONTAL
                layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, (rowHeightPx * rowDef.height).toInt())
            }
            for ((k, key) in rowDef.keys.withIndex()) {
                row.addView(buildKeyView(key, selected = editMode && r == selRow && k == selKey))
            }
            keyHost.addView(row)
        }
    }

    private fun switchLayer(name: String) {
        if (layout.layer(name) == null) return
        currentLayer = name
        clearSelection()
        rebuild()
        updateEditInfo()
    }

    private fun buildKeyView(key: KeyDef, selected: Boolean): View {
        val params = LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, key.weight).apply {
            setMargins(keyMarginPx, keyMarginVPx, keyMarginPx, keyMarginVPx)
        }

        if (key.isSpacer) {
            // Invisible gap. In edit mode it gets an outline so it can be selected and resized.
            return View(context).apply {
                layoutParams = params
                if (editMode) background = outline(selected)
            }
        }

        val toggledOn = key.layerTarget != null && key.layerTarget == currentLayer &&
            key.layerTarget != layout.defaultLayerName
        val armedSticky = key.isSticky && armedMods.contains(key.code)
        val armedFn = key.isFnToggle && fnActive
        val armed = toggledOn || armedSticky || armedFn

        // While fn is on, plain keys show and send their function-key meaning (F1.., Home..).
        val shown = displayedKey(key, fnActive, editMode)
        val restColor = if (key.isModifierLook) MODIFIER_COLOR else KEY_COLOR
        val restPressedColor = if (key.isModifierLook) MODIFIER_PRESSED_COLOR else KEY_PRESSED_COLOR

        val view = TextView(context).apply {
            gravity = Gravity.CENTER
            typeface = keyTypeface
            layoutParams = params
        }
        applyLabel(view, shown.label)
        if (armed) {
            style(view, ARMED_COLOR, ARMED_PRESSED_COLOR, TEXT_COLOR_DARK, selected)
        } else {
            style(view, restColor, restPressedColor, TEXT_COLOR_LIGHT, selected)
        }

        // A small press-in on touch. Returns false so the click below still fires.
        view.setOnTouchListener { v, e ->
            if (!editMode) {
                when (e.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        haptics.tick()  // on touch-down, so it feels instant rather than after release
                        v.animate().scaleX(PRESS_SCALE).scaleY(PRESS_SCALE).setDuration(50).start()
                    }
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL ->
                        v.animate().scaleX(1f).scaleY(1f).setDuration(90).start()
                }
            }
            false
        }

        view.setOnClickListener {
            onKeyTapped(key, view, restColor, restPressedColor)
        }

        if (key.isSticky) {
            modifierButtons.getOrPut(key.code) { mutableListOf() }.add(StickyInfo(view, restColor, restPressedColor))
        }
        return view
    }

    private fun onKeyTapped(key: KeyDef, view: TextView, restColor: Int, restPressedColor: Int) {
        // What the tap means is decided in the model (resolveTap, unit tested); this just does it.
        when (val action = resolveTap(key, fnActive, editMode, currentLayer, layout.defaultLayerName)) {
            is TapAction.ToggleFn -> toggleFn()
            is TapAction.SwitchLayer -> switchLayer(action.name)
            is TapAction.ToggleModifier -> toggleModifier(action.mod, view, restColor, restPressedColor)
            is TapAction.SendUnicode -> sendUnicodeChar(action.hex, action.label)
            is TapAction.SendKey -> sendKey(action.code, action.mods)
        }
    }

    private fun toggleFn() {
        fnActive = !fnActive
        rebuild()  // every key re-labels in place; the fn key itself lights up while on
    }

    /** Single glyphs get the big size; multi-character labels (esc, PgUp, F10) a smaller one. */
    private fun applyLabel(view: TextView, text: String) {
        view.text = text
        view.textSize = if (text.length > 1) WORD_TEXT_SP else KEY_TEXT_SP
    }

    private fun style(view: TextView, restColor: Int, pressedColor: Int, textColor: Int, selected: Boolean = false) {
        view.setTextColor(textColor)
        val armed = restColor == ARMED_COLOR
        val strokeWidth = if (selected) strokePx * 3 else strokePx
        val normal = GradientDrawable().apply {
            setColor(restColor)
            cornerRadius = keyRadiusPx
            setStroke(strokeWidth, if (selected || armed) ARMED_COLOR else BORDER_COLOR)
        }
        val pressed = GradientDrawable().apply {
            setColor(pressedColor)
            cornerRadius = keyRadiusPx
            setStroke(strokeWidth, if (selected) ARMED_COLOR else if (armed) ARMED_PRESSED_COLOR else BORDER_PRESSED_COLOR)
        }
        view.background = StateListDrawable().apply {
            addState(intArrayOf(android.R.attr.state_pressed), pressed)
            addState(intArrayOf(), normal)
        }
    }

    private fun outline(selected: Boolean) = GradientDrawable().apply {
        setColor(Color.TRANSPARENT)
        cornerRadius = keyRadiusPx
        setStroke(if (selected) strokePx * 3 else strokePx, if (selected) ARMED_COLOR else BORDER_COLOR)
    }

    private fun toggleModifier(mod: String, view: TextView, restColor: Int, restPressedColor: Int) {
        if (armedMods.contains(mod)) {
            armedMods.remove(mod)
            for (info in modifierButtons[mod].orEmpty()) {
                style(info.view, info.restColor, info.restPressedColor, TEXT_COLOR_LIGHT)
            }
        } else {
            armedMods.add(mod)
            for (info in modifierButtons[mod].orEmpty()) {
                style(info.view, ARMED_COLOR, ARMED_PRESSED_COLOR, TEXT_COLOR_DARK)
            }
        }
    }

    // ------------------------------------------------------------ edit mode: bar

    private fun chip(text: String, onClick: () -> Unit): TextView = TextView(context).apply {
        this.text = text
        textSize = 12f
        typeface = keyTypeface
        setTextColor(TEXT_COLOR_LIGHT)
        gravity = Gravity.CENTER
        val h = (12 * density).toInt()
        val v = (9 * density).toInt()
        setPadding(h, v, h, v)
        background = GradientDrawable().apply {
            setColor(KEY_COLOR)
            cornerRadius = keyRadiusPx
            setStroke(strokePx, BORDER_COLOR)
        }
        layoutParams = LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply {
            setMargins(keyMarginPx, 0, keyMarginPx, 0)
        }
        setOnClickListener {
            performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
            onClick()
        }
    }

    private fun buildEditBar() {
        editBar.apply {
            visibility = View.GONE
            isHorizontalScrollBarEnabled = false
            layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, (EDIT_BAR_HEIGHT_DP * density).toInt())
        }
        editInfo.apply {
            textSize = 12f
            setTextColor(Color.parseColor("#A1A1A8"))
            gravity = Gravity.CENTER_VERTICAL
            setPadding((8 * density).toInt(), 0, (12 * density).toInt(), 0)
            layoutParams = LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.MATCH_PARENT)
        }
        val strip = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        strip.addView(chip("done") { setEditMode(false) })
        strip.addView(chip("◀ move") { nudgeSelected(-1) })
        strip.addView(chip("move ▶") { nudgeSelected(1) })
        strip.addView(chip("− width") { resizeSelected(-WIDTH_STEP) })
        strip.addView(chip("+ width") { resizeSelected(WIDTH_STEP) })
        strip.addView(chip("− height") { resizeRow(-HEIGHT_STEP) })
        strip.addView(chip("+ height") { resizeRow(HEIGHT_STEP) })
        strip.addView(chip("next layer") { cycleLayer() })
        strip.addView(chip("copy") { copyLayout() })
        strip.addView(chip("paste") { pasteLayout() })
        strip.addView(chip("reset") { resetLayout() })
        vibrationChip = chip(vibrationLabel()) { cycleVibration() }
        strip.addView(vibrationChip)
        strip.addView(editInfo)
        editBar.addView(strip)
        updateEditInfo()
    }

    private fun vibrationLabel() = "vibration: " + listOf("off", "light", "medium", "strong")[haptics.level.coerceIn(0, 3)]

    private fun cycleVibration() {
        haptics.level = (haptics.level + 1) % 4
        vibrationChip.text = vibrationLabel()
        haptics.tick()  // let the new strength be felt straight away
        onHapticLevelChanged?.invoke(haptics.level)
    }

    /** Restores a saved vibration strength (0 off ... 3 strong). */
    fun setVibrationLevel(level: Int) {
        haptics.level = level.coerceIn(0, 3)
        if (::vibrationChip.isInitialized) vibrationChip.text = vibrationLabel()
    }

    private fun selectedKeyDef(): KeyDef? = currentRows().getOrNull(selRow)?.keys?.getOrNull(selKey)

    private fun updateEditInfo() {
        val key = selectedKeyDef()
        editInfo.text = if (key == null) {
            "$currentLayer  ·  $EDIT_HINT"
        } else {
            val row = currentRows()[selRow]
            val name = if (key.isSpacer) "gap" else "\"${key.label}\""
            "$currentLayer  ·  row ${selRow + 1}  ·  $name  ·  width ${"%.2f".format(key.weight)}  ·  row height ${"%.2f".format(row.height)}"
        }
    }

    /** Applies an edit result: saves it, redraws, refreshes the info line. */
    private fun commit(newLayout: LayoutDef) {
        if (newLayout == layout) return
        layout = newLayout
        rebuild()
        updateEditInfo()
        onLayoutChanged?.invoke(layout)
    }

    private fun nudgeSelected(delta: Int) {
        if (selectedKeyDef() == null) return
        val to = selKey + delta
        val moved = LayoutEditor.moveKey(layout, currentLayer, selRow, selKey, to)
        if (moved != layout) selKey = to
        commit(moved)
    }

    private fun resizeSelected(delta: Float) {
        val key = selectedKeyDef() ?: return
        commit(LayoutEditor.resizeKey(layout, currentLayer, selRow, selKey, key.weight + delta))
    }

    private fun resizeRow(delta: Float) {
        if (selRow < 0) return
        val row = currentRows().getOrNull(selRow) ?: return
        commit(LayoutEditor.setRowHeight(layout, currentLayer, selRow, row.height + delta))
    }

    private fun cycleLayer() {
        val names = layout.layers.map { it.name }
        val next = names[(names.indexOf(currentLayer) + 1) % names.size]
        switchLayer(next)
    }

    private fun copyLayout() {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("keyboard layout", LayoutText.serialize(layout)))
        Toast.makeText(context, "Layout copied. Paste it anywhere to edit it.", Toast.LENGTH_SHORT).show()
    }

    private fun pasteLayout() {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = clipboard.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString()
        if (text.isNullOrBlank()) {
            Toast.makeText(context, "Nothing to paste: copy a layout first.", Toast.LENGTH_SHORT).show()
            return
        }
        val result = LayoutText.parse(text)
        val parsed = result.layout
        if (parsed == null) {
            val shown = result.errors.take(4).joinToString("\n")
            val more = if (result.errors.size > 4) "\n…and ${result.errors.size - 4} more" else ""
            Toast.makeText(context, "Layout not applied:\n$shown$more", Toast.LENGTH_LONG).show()
            return
        }
        setLayout(parsed, notify = true)
        updateEditInfo()
        Toast.makeText(context, "Layout applied.", Toast.LENGTH_SHORT).show()
    }

    /** Needs a second tap within 3 seconds, so a stray touch can't wipe the layout. */
    private fun resetLayout() {
        val now = System.currentTimeMillis()
        if (now - resetArmedAt > 3000L) {
            resetArmedAt = now
            Toast.makeText(context, "Tap reset again to restore the default layout.", Toast.LENGTH_SHORT).show()
            return
        }
        resetArmedAt = 0L
        setLayout(DefaultLayout.build(), notify = true)
        updateEditInfo()
    }

    // ------------------------------------------------------------ edit mode: touch

    private enum class Drag { NONE, MOVE, RESIZE }

    private var drag = Drag.NONE
    private var dragDownX = 0f
    private var dragDownY = 0f
    private var dragMoved = false
    private var dragStartLayout: LayoutDef? = null
    private var dragStartWeight = 0f
    private var dragChanged = false

    /** In edit mode every touch on the keys is ours: nothing types, the keys are handled here. */
    override fun onInterceptTouchEvent(ev: MotionEvent): Boolean =
        editMode && ev.y >= keyHost.top

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (!editMode) return super.onTouchEvent(event)
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                val hit = hitTest(event.x, event.y) ?: return true
                selRow = hit.first
                selKey = hit.second
                dragDownX = event.x
                dragDownY = event.y
                dragMoved = false
                dragChanged = false
                dragStartLayout = layout
                dragStartWeight = selectedKeyDef()?.weight ?: 1f
                drag = if (inResizeZone(hit, event.x)) Drag.RESIZE else Drag.MOVE
                rebuild()  // highlights the newly selected key
                updateEditInfo()
            }
            MotionEvent.ACTION_MOVE -> {
                if (drag == Drag.NONE) return true
                if (!dragMoved &&
                    abs(event.x - dragDownX) < touchSlopPx && abs(event.y - dragDownY) < touchSlopPx
                ) return true
                dragMoved = true
                if (drag == Drag.RESIZE) dragResize(event.x) else dragMove(event.x, event.y)
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (dragChanged) onLayoutChanged?.invoke(layout)
                drag = Drag.NONE
                dragStartLayout = null
            }
        }
        return true
    }

    private fun rowViewAt(index: Int): LinearLayout? = keyHost.getChildAt(index) as? LinearLayout

    /** (row, key) under a point in this view's coordinates, snapping to the nearest key in the row. */
    private fun hitTest(x: Float, y: Float): Pair<Int, Int>? {
        val yy = y - keyHost.top
        val xx = x - keyHost.left
        var rowIndex = -1
        for (r in 0 until keyHost.childCount) {
            val rv = keyHost.getChildAt(r)
            if (yy >= rv.top && yy < rv.bottom) { rowIndex = r; break }
        }
        if (rowIndex < 0) {
            // Between rows or past the ends: snap to the closest row so a drag never loses its target.
            if (keyHost.childCount == 0) return null
            rowIndex = if (yy < 0) 0 else keyHost.childCount - 1
        }
        val rowView = rowViewAt(rowIndex) ?: return null
        if (rowView.childCount == 0) return null
        var best = 0
        var bestDistance = Float.MAX_VALUE
        for (k in 0 until rowView.childCount) {
            val kv = rowView.getChildAt(k)
            val centre = (kv.left + kv.right) / 2f
            val distance = abs(centre - xx)
            if (distance < bestDistance) { bestDistance = distance; best = k }
        }
        return rowIndex to best
    }

    private fun inResizeZone(hit: Pair<Int, Int>, x: Float): Boolean {
        val kv = rowViewAt(hit.first)?.getChildAt(hit.second) ?: return false
        val zone = minOf(RESIZE_ZONE_DP * density, kv.width * 0.4f)
        val right = kv.right + keyHost.left
        return x >= right - zone - keyMarginPx && x <= right + keyMarginPx
    }

    private fun dragResize(x: Float) {
        val start = dragStartLayout ?: return
        val rowView = rowViewAt(selRow) ?: return
        val startRow = start.layer(currentLayer)?.rows?.getOrNull(selRow) ?: return
        if (rowView.width <= 0) return
        val weightPerPx = startRow.totalWeight / rowView.width
        val newWeight = dragStartWeight + (x - dragDownX) * weightPerPx
        val resized = LayoutEditor.resizeKey(start, currentLayer, selRow, selKey, newWeight)
        if (resized == layout) return
        layout = resized
        dragChanged = true
        // Update the widths in place: no rebuild, so the drag stays smooth.
        val keys = currentRows()[selRow].keys
        for (k in 0 until rowView.childCount) {
            val lp = rowView.getChildAt(k).layoutParams as LayoutParams
            lp.weight = keys[k].weight
        }
        rowView.requestLayout()
        updateEditInfo()
    }

    private fun dragMove(x: Float, y: Float) {
        val hit = hitTest(x, y) ?: return
        if (hit.first == selRow && hit.second == selKey) return
        val moved = if (hit.first == selRow) {
            LayoutEditor.moveKey(layout, currentLayer, selRow, selKey, hit.second)
        } else {
            LayoutEditor.moveKeyToRow(layout, currentLayer, selRow, selKey, hit.first, hit.second)
        }
        if (moved == layout) return
        layout = moved
        dragChanged = true
        selRow = hit.first
        selKey = hit.second.coerceAtMost(currentRows()[selRow].keys.size - 1)
        rebuild()
        updateEditInfo()
    }

    private fun sendRaw(code: String, mods: List<String>) {
        onEvent?.invoke(mapOf("t" to "key", "code" to code, "action" to "down", "mods" to mods))
        onEvent?.invoke(mapOf("t" to "key", "code" to code, "action" to "up", "mods" to mods))
    }

    private fun sendKey(code: String, extraMods: List<String> = emptyList()) {
        val mods = (armedMods + extraMods).distinct()
        sendRaw(code, mods)
        updatePreview(code, "shift" in mods)
        clearModifiers()
    }

    /**
     * Sends a character with no physical key via GTK/IBus's Unicode
     * entry sequence: Ctrl+Shift+U, the hex codepoint, Enter to commit.
     * Best-effort — needs the receiving desktop's input method to be
     * IBus (GNOME's default) and the focused app to support it; unlike
     * every other key here, this isn't guaranteed to work everywhere.
     * Deliberately ignores any armed Ctrl/Alt/Shift, since combining
     * them with this sequence has no well-defined meaning.
     */
    private fun sendUnicodeChar(hex: String, previewChar: String) {
        clearModifiers()
        sendRaw("KeyU", listOf("ctrl", "shift"))
        for (digit in hex) {
            val digitCode = if (digit.isDigit()) "Digit$digit" else "Key${digit.uppercaseChar()}"
            sendRaw(digitCode, emptyList())
        }
        sendRaw("Enter", emptyList())

        previewBuffer.append(previewChar)
        if (previewBuffer.length > PREVIEW_MAX_CHARS) {
            previewBuffer.delete(0, previewBuffer.length - PREVIEW_MAX_CHARS)
        }
        onPreviewTextChanged?.invoke(previewBuffer.toString())
    }

    private fun updatePreview(code: String, shiftArmed: Boolean) {
        when (code) {
            "Backspace" -> if (previewBuffer.isNotEmpty()) previewBuffer.deleteCharAt(previewBuffer.length - 1)
            "Enter" -> previewBuffer.clear()
            else -> {
                val char = previewCharFor(code, shiftArmed) ?: return
                previewBuffer.append(char)
                if (previewBuffer.length > PREVIEW_MAX_CHARS) {
                    previewBuffer.delete(0, previewBuffer.length - PREVIEW_MAX_CHARS)
                }
            }
        }
        onPreviewTextChanged?.invoke(previewBuffer.toString())
    }

    private fun previewCharFor(code: String, shiftArmed: Boolean): String? = when {
        code.startsWith("Key") && code.length == 4 -> {
            val letter = code.substring(3)
            if (shiftArmed) letter else letter.lowercase()
        }
        code.startsWith("Digit") -> if (shiftArmed) SHIFT_SYMBOLS[code] else code.removePrefix("Digit")
        code == "Minus" -> if (shiftArmed) "_" else "-"
        code == "Equal" -> if (shiftArmed) "+" else "="
        code == "Space" -> " "
        PUNCTUATION_CHARS.containsKey(code) -> {
            val (base, shifted) = PUNCTUATION_CHARS.getValue(code)
            if (shiftArmed) shifted else base
        }
        else -> null
    }

    private fun clearModifiers() {
        armedMods.clear()
        for (infos in modifierButtons.values) {
            for (info in infos) {
                style(info.view, info.restColor, info.restPressedColor, TEXT_COLOR_LIGHT)
            }
        }
    }
}
