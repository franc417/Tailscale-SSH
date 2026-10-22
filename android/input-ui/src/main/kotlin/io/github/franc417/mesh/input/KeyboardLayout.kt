package io.github.franc417.mesh.input

import kotlin.math.max
import kotlin.math.roundToInt

/*
 * Keyboard layout as DATA, with no Android types, so it can be unit-tested on
 * a plain JVM, saved, copied, pasted and edited.
 *
 * LayoutDef -> layers (letters, symbols, ...) -> rows -> keys.
 *
 * Width: a key's `weight` is its share of the row. Keys always fill the row,
 * so changing one weight changes every other key's width: that is the
 * "others auto-adjust" behaviour (see LayoutEditor.resizeKey, which keeps the
 * row total constant so the other keys shrink or grow proportionally).
 * Height: each row has a `height` multiplier of the base row height.
 */

/** Marks an invisible, untappable gap (used to indent the home row like Android does). */
const val CODE_SPACER = "spacer"

/** `layer:<name>` switches to another layer (123, ABC, fn, =\<). */
const val CODE_LAYER_PREFIX = "layer:"

/**
 * The fn toggle. While it is on, keys that have a function-key meaning change IN PLACE
 * (see FnMap): the digits become F1..F10, esc F11, prtsc F12, the arrows Home/PgUp/PgDn/End.
 * It is not a page of its own. (Older layout text used `layer:fn`; the parser still accepts
 * that and treats it as this toggle.)
 */
const val CODE_FN = "fn"

/** A character with no physical key, sent through the Ctrl+Shift+U sequence; needs `unicode` hex. */
const val CODE_UNICODE = "unicode"

const val MIN_KEY_WEIGHT = 0.5f
const val MAX_KEY_WEIGHT = 12f
const val MIN_ROW_HEIGHT = 0.5f
const val MAX_ROW_HEIGHT = 2.5f

private val STICKY_MODS = setOf("shift", "ctrl", "alt")
private val FORCED_MODS = setOf("shift", "ctrl", "alt", "meta")

/** Wire key codes the desktop understands (must stay in step with KEY_MAP in linux-client/input_backend.py). */
val PHYSICAL_CODES: Set<String> = buildSet {
    for (c in 'A'..'Z') add("Key$c")
    for (d in 0..9) add("Digit$d")
    for (n in 1..12) add("F$n")
    addAll(
        listOf(
            "Enter", "Backspace", "Space", "Tab", "Escape",
            "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
            "Comma", "Period", "Minus", "Equal", "PrintScreen",
            "Home", "End", "PageUp", "PageDown", "Meta",
            "Semicolon", "Quote", "Slash", "Backslash",
            "BracketLeft", "BracketRight", "Backquote",
        ),
    )
}

/** What each key becomes while fn is on. Only plain keys (no forced modifiers) are remapped. */
object FnMap {
    private val codes: Map<String, String> = buildMap {
        for (d in 1..9) put("Digit$d", "F$d")
        put("Digit0", "F10")
        put("Escape", "F11")
        put("PrintScreen", "F12")
        put("ArrowLeft", "Home")
        put("ArrowUp", "PageUp")
        put("ArrowDown", "PageDown")
        put("ArrowRight", "End")
    }

    fun forCode(code: String): String? = codes[code]

    fun label(code: String): String = when (code) {
        "PageUp" -> "PgUp"
        "PageDown" -> "PgDn"
        else -> code
    }
}

data class KeyDef(
    val label: String,
    val code: String,
    val weight: Float = 1f,
    /** Modifiers forced on for this key, e.g. shift for "@" (= Shift+Digit2). */
    val mods: List<String> = emptyList(),
    /** Hex codepoint, for code == CODE_UNICODE. */
    val unicode: String? = null,
) {
    val isSpacer: Boolean get() = code == CODE_SPACER
    val isSticky: Boolean get() = code in STICKY_MODS
    val layerTarget: String? get() = if (code.startsWith(CODE_LAYER_PREFIX)) code.removePrefix(CODE_LAYER_PREFIX) else null
    val isFnToggle: Boolean get() = code == CODE_FN
    val isModifierLook: Boolean get() = code == "ctrl" || code == "alt" || code == "Meta" || layerTarget != null || isFnToggle

    /**
     * This key as it should look and act while fn is on, or null if fn leaves it alone. "@" is
     * Shift+2, so it is left alone (it has a forced modifier); the plain "2" becomes F2.
     */
    fun fnVariant(): KeyDef? {
        if (mods.isNotEmpty() || isSticky || isSpacer || isFnToggle || layerTarget != null || code == CODE_UNICODE) return null
        val fnCode = FnMap.forCode(code) ?: return null
        return KeyDef(FnMap.label(fnCode), fnCode, weight)
    }
}

data class RowDef(val keys: List<KeyDef>, val height: Float = 1f) {
    val totalWeight: Float get() = keys.fold(0f) { acc, k -> acc + k.weight }
}

data class LayerDef(val name: String, val rows: List<RowDef>)

data class LayoutDef(val layers: List<LayerDef>) {
    fun layer(name: String): LayerDef? = layers.firstOrNull { it.name == name }
    val defaultLayerName: String get() = layers.first().name
}

// ---------------------------------------------------------------- what a tap does

/**
 * What tapping a key does right now. Kept free of Android types so the real key
 * behaviour (fn, layers, sticky modifiers, symbols) can be unit tested; the view only
 * carries the action out.
 */
sealed class TapAction {
    object ToggleFn : TapAction()
    data class SwitchLayer(val name: String) : TapAction()
    data class ToggleModifier(val mod: String) : TapAction()
    data class SendUnicode(val hex: String, val label: String) : TapAction()
    data class SendKey(val code: String, val mods: List<String>) : TapAction()
}

/** The key as it currently looks and acts: its fn version while fn is on (and not editing). */
fun displayedKey(key: KeyDef, fnActive: Boolean, editing: Boolean): KeyDef =
    if (fnActive && !editing) key.fnVariant() ?: key else key

fun resolveTap(key: KeyDef, fnActive: Boolean, editing: Boolean, currentLayer: String, defaultLayer: String): TapAction {
    val shown = displayedKey(key, fnActive, editing)
    val target = key.layerTarget
    return when {
        key.isFnToggle -> TapAction.ToggleFn
        // Tapping the toggle for the layer you are already on goes back to the first layer.
        target != null -> TapAction.SwitchLayer(if (target == currentLayer) defaultLayer else target)
        key.isSticky -> TapAction.ToggleModifier(key.code)
        shown.code == CODE_UNICODE && shown.unicode != null -> TapAction.SendUnicode(shown.unicode, shown.label)
        else -> TapAction.SendKey(shown.code, shown.mods)
    }
}

// ---------------------------------------------------------------- editing

/**
 * Pure editing operations. Every function returns a NEW layout and leaves the
 * input untouched; an invalid request (bad index, last key in a row...) returns
 * the layout unchanged rather than throwing, so a stray touch can never crash.
 */
object LayoutEditor {

    private fun round2(v: Float): Float = (v * 100f).roundToInt() / 100f

    private fun withRow(layout: LayoutDef, layerName: String, rowIndex: Int, change: (RowDef) -> RowDef): LayoutDef {
        val layer = layout.layer(layerName) ?: return layout
        if (rowIndex !in layer.rows.indices) return layout
        val newRows = layer.rows.mapIndexed { i, r -> if (i == rowIndex) change(r) else r }
        return LayoutDef(layout.layers.map { if (it.name == layerName) LayerDef(it.name, newRows) else it })
    }

    /**
     * Sets one key's weight and scales ALL the other keys in the row so the row
     * total stays the same, so the row still fills the screen and its neighbours
     * give or take the space. No key can be pushed below MIN_KEY_WEIGHT; the
     * request is clamped to the largest size that still allows that.
     */
    fun resizeKey(layout: LayoutDef, layerName: String, rowIndex: Int, keyIndex: Int, newWeight: Float): LayoutDef =
        withRow(layout, layerName, rowIndex) { row ->
            val n = row.keys.size
            if (n < 2 || keyIndex !in row.keys.indices) return@withRow row
            val total = row.totalWeight
            val maxW = total - MIN_KEY_WEIGHT * (n - 1)
            if (maxW < MIN_KEY_WEIGHT) return@withRow row
            val w = round2(newWeight.coerceIn(MIN_KEY_WEIGHT, maxW))

            // Share the remaining width among the other keys in proportion to
            // their current widths; a key that would fall under the minimum is
            // pinned at the minimum and the rest are re-scaled to absorb that.
            val others = row.keys.indices.filter { it != keyIndex }
            val pinned = mutableSetOf<Int>()
            var scale = 1f
            repeat(others.size + 1) {
                val free = others.filter { it !in pinned }
                if (free.isEmpty()) return@repeat
                val freeOriginal = free.fold(0f) { acc, i -> acc + row.keys[i].weight }
                val remaining = total - w - MIN_KEY_WEIGHT * pinned.size
                scale = if (freeOriginal > 0f) remaining / freeOriginal else 1f
                val newlyPinned = free.filter { row.keys[it].weight * scale < MIN_KEY_WEIGHT }
                if (newlyPinned.isNotEmpty()) pinned.addAll(newlyPinned)
            }
            val newKeys = row.keys.mapIndexed { i, k ->
                when {
                    i == keyIndex -> k.copy(weight = w)
                    i in pinned -> k.copy(weight = MIN_KEY_WEIGHT)
                    else -> k.copy(weight = round2(max(MIN_KEY_WEIGHT, k.weight * scale)))
                }
            }
            row.copy(keys = newKeys)
        }

    /** Moves a key to another position in the SAME row; the others shift over. */
    fun moveKey(layout: LayoutDef, layerName: String, rowIndex: Int, from: Int, to: Int): LayoutDef =
        withRow(layout, layerName, rowIndex) { row ->
            if (from !in row.keys.indices || to !in row.keys.indices || from == to) return@withRow row
            val list = row.keys.toMutableList()
            list.add(to, list.removeAt(from))
            row.copy(keys = list)
        }

    /**
     * Moves a key into a different row. Both rows still fill the screen (their
     * keys share the width by weight). The last key of a row cannot be moved out.
     */
    fun moveKeyToRow(
        layout: LayoutDef, layerName: String,
        fromRow: Int, keyIndex: Int, toRow: Int, toIndex: Int,
    ): LayoutDef {
        if (fromRow == toRow) return moveKey(layout, layerName, fromRow, keyIndex, toIndex)
        val layer = layout.layer(layerName) ?: return layout
        if (fromRow !in layer.rows.indices || toRow !in layer.rows.indices) return layout
        val source = layer.rows[fromRow]
        if (source.keys.size < 2 || keyIndex !in source.keys.indices) return layout
        val key = source.keys[keyIndex]
        val target = layer.rows[toRow]
        val insertAt = toIndex.coerceIn(0, target.keys.size)
        val newRows = layer.rows.mapIndexed { i, r ->
            when (i) {
                fromRow -> r.copy(keys = r.keys.filterIndexed { j, _ -> j != keyIndex })
                toRow -> r.copy(keys = r.keys.toMutableList().apply { add(insertAt, key) })
                else -> r
            }
        }
        return LayoutDef(layout.layers.map { if (it.name == layerName) LayerDef(it.name, newRows) else it })
    }

    fun setRowHeight(layout: LayoutDef, layerName: String, rowIndex: Int, height: Float): LayoutDef =
        withRow(layout, layerName, rowIndex) { it.copy(height = round2(height.coerceIn(MIN_ROW_HEIGHT, MAX_ROW_HEIGHT))) }
}

// ---------------------------------------------------------------- text template

/**
 * The editable text form of a layout, so it can be copied out of the app,
 * changed by hand, and pasted back in.
 *
 *   remote-hid keyboard layout v1
 *   # lines starting with # are comments
 *   layer letters
 *   row 1.0  q|KeyQ  w|KeyW  @|Digit2|1|shift  space|Space|3  €|unicode|1||20ac
 *
 * A row line is `row <height>` followed by keys separated by spaces. A key is
 * `label|code|weight|mods|unicode`; only label and code are required. Inside a
 * label write \| for a pipe, \\ for a backslash and \s for a space. `mods` is a
 * comma list of shift, ctrl, alt, meta. The code is a key name (KeyQ, Comma,
 * Enter...), shift/ctrl/alt (sticky), fn (toggles function keys in place),
 * layer:<name>, spacer, or unicode.
 */
object LayoutText {

    const val HEADER = "remote-hid keyboard layout v1"

    data class ParseResult(val layout: LayoutDef?, val errors: List<String>)

    private class ParsedLayer(val name: String) {
        val rows = mutableListOf<RowDef>()
        var rowLines = 0  // row lines seen, valid or not, so one bad row is not ALSO reported as "no rows"
    }

    fun serialize(layout: LayoutDef): String {
        val sb = StringBuilder()
        sb.append(HEADER).append('\n')
        sb.append("# row <height> then keys:  label|code|weight|mods|unicode   (see the app's help)\n")
        for (layer in layout.layers) {
            sb.append('\n').append("layer ").append(layer.name).append('\n')
            for (row in layer.rows) {
                sb.append("row ").append(fmt(row.height))
                for (key in row.keys) sb.append("  ").append(serializeKey(key))
                sb.append('\n')
            }
        }
        return sb.toString()
    }

    private fun fmt(v: Float): String = ((v * 100f).roundToInt() / 100f).toString()

    private fun escapeLabel(label: String): String =
        label.replace("\\", "\\\\").replace("|", "\\|").replace(" ", "\\s")

    private fun serializeKey(k: KeyDef): String {
        val parts = mutableListOf(escapeLabel(k.label), k.code, fmt(k.weight), k.mods.joinToString(","), k.unicode ?: "")
        while (parts.size > 2 && (parts.last().isEmpty() || (parts.size == 3 && parts.last() == "1.0"))) parts.removeAt(parts.size - 1)
        return parts.joinToString("|")
    }

    private fun unescapeLabel(s: String): String {
        val out = StringBuilder()
        var i = 0
        while (i < s.length) {
            val c = s[i]
            if (c == '\\' && i + 1 < s.length) {
                when (s[i + 1]) {
                    '\\' -> out.append('\\')
                    '|' -> out.append('|')
                    's' -> out.append(' ')
                    else -> out.append(c).append(s[i + 1])
                }
                i += 2
            } else {
                out.append(c)
                i++
            }
        }
        return out.toString()
    }

    /** Splits on `|` that are not escaped with a backslash. */
    private fun splitFields(token: String): List<String> {
        val fields = mutableListOf<String>()
        val cur = StringBuilder()
        var i = 0
        while (i < token.length) {
            val c = token[i]
            if (c == '\\' && i + 1 < token.length) {
                cur.append(c).append(token[i + 1])
                i += 2
            } else if (c == '|') {
                fields.add(cur.toString())
                cur.clear()
                i++
            } else {
                cur.append(c)
                i++
            }
        }
        fields.add(cur.toString())
        return fields
    }

    fun parse(text: String): ParseResult {
        val errors = mutableListOf<String>()
        val layers = mutableListOf<ParsedLayer>()
        var sawHeader = false

        text.lines().forEachIndexed { index, rawLine ->
            val lineNo = index + 1
            val line = rawLine.trim()
            if (line.isEmpty() || line.startsWith("#")) return@forEachIndexed
            if (!sawHeader) {
                if (line != HEADER) errors.add("line $lineNo: first line must be \"$HEADER\"")
                sawHeader = true
                return@forEachIndexed
            }
            val words = line.split(Regex("\\s+"))
            when (words[0]) {
                "layer" -> {
                    val name = words.getOrNull(1)
                    when {
                        name == null || words.size != 2 -> errors.add("line $lineNo: write \"layer <name>\"")
                        layers.any { it.name == name } -> errors.add("line $lineNo: layer \"$name\" is defined twice")
                        else -> layers.add(ParsedLayer(name))
                    }
                }
                "row" -> {
                    val current = layers.lastOrNull()
                    current?.let { it.rowLines++ }
                    val height = words.getOrNull(1)?.toFloatOrNull()
                    when {
                        current == null -> errors.add("line $lineNo: a row must come after a \"layer <name>\" line")
                        height == null -> errors.add("line $lineNo: a row starts with its height, e.g. \"row 1.0\"")
                        height < MIN_ROW_HEIGHT || height > MAX_ROW_HEIGHT ->
                            errors.add("line $lineNo: row height $height must be between $MIN_ROW_HEIGHT and $MAX_ROW_HEIGHT")
                        words.size < 3 -> errors.add("line $lineNo: this row has no keys")
                        else -> {
                            val keys = mutableListOf<KeyDef>()
                            var ok = true
                            for (token in words.drop(2)) {
                                val key = parseKey(token, lineNo, errors)
                                if (key == null) ok = false else keys.add(key)
                            }
                            if (ok) current.rows.add(RowDef(keys, height))
                        }
                    }
                }
                else -> errors.add("line $lineNo: unknown line \"${words[0]}\" (expected layer or row)")
            }
        }

        layers.removeAll { it.name == "fn" }  // the old separate fn page no longer exists

        if (!sawHeader) errors.add("empty: expected \"$HEADER\" on the first line")
        if (sawHeader && layers.isEmpty() && errors.isEmpty()) errors.add("no layers: add a \"layer <name>\" line")
        for (layer in layers) if (layer.rowLines == 0) errors.add("layer \"${layer.name}\" has no rows")

        val names = layers.map { it.name }.toSet()
        for (layer in layers) {
            for (row in layer.rows) for (key in row.keys) {
                val target = key.layerTarget
                if (target != null && target !in names) {
                    errors.add("layer \"${layer.name}\": key \"${key.label}\" points to layer \"$target\" which does not exist")
                }
            }
        }

        if (errors.isNotEmpty()) return ParseResult(null, errors)
        return ParseResult(LayoutDef(layers.map { LayerDef(it.name, it.rows.toList()) }), emptyList())
    }

    private fun parseKey(token: String, lineNo: Int, errors: MutableList<String>): KeyDef? {
        val f = splitFields(token)
        if (f.size < 2 || f[1].isEmpty()) {
            errors.add("line $lineNo: key \"$token\" needs at least label|code")
            return null
        }
        val label = unescapeLabel(f[0])
        // Older layouts used a separate "fn" page: that key is now the in-place fn toggle.
        val code = if (f[1] == "layer:fn") CODE_FN else f[1]
        var ok = true

        val validCode = code in PHYSICAL_CODES || code in STICKY_MODS || code == CODE_SPACER ||
            code == CODE_UNICODE || code == CODE_FN || (code.startsWith(CODE_LAYER_PREFIX) && code.length > CODE_LAYER_PREFIX.length)
        if (!validCode) {
            errors.add("line $lineNo: unknown key code \"$code\" in \"$token\"")
            ok = false
        }

        var weight = 1f
        val weightField = f.getOrNull(2).orEmpty()
        if (weightField.isNotEmpty()) {
            val w = weightField.toFloatOrNull()
            if (w == null || w <= 0f || w > MAX_KEY_WEIGHT) {
                errors.add("line $lineNo: bad width \"$weightField\" in \"$token\" (use a number up to $MAX_KEY_WEIGHT)")
                ok = false
            } else {
                weight = w
            }
        }

        val mods = f.getOrNull(3).orEmpty().split(",").filter { it.isNotEmpty() }
        val badMod = mods.firstOrNull { it !in FORCED_MODS }
        if (badMod != null) {
            errors.add("line $lineNo: unknown modifier \"$badMod\" in \"$token\" (use shift, ctrl, alt, meta)")
            ok = false
        }

        val unicode = f.getOrNull(4)?.takeIf { it.isNotEmpty() }
        if (code == CODE_UNICODE) {
            if (unicode == null || !unicode.all { it.isDigit() || it.lowercaseChar() in 'a'..'f' }) {
                errors.add("line $lineNo: a unicode key needs a hex codepoint as the 5th field, e.g. €|unicode|1||20ac")
                ok = false
            }
        }
        if (f.size > 5) {
            errors.add("line $lineNo: too many fields in \"$token\" (label|code|width|mods|unicode); escape a pipe in the label as \\|")
            ok = false
        }
        return if (ok) KeyDef(label, code, weight, mods, unicode?.lowercase()) else null
    }
}

// ---------------------------------------------------------------- default layout

/**
 * The default layout:
 *
 *  - letters: a thin top row with esc above the 1 key and prtsc above the 0 key, then digits,
 *    qwerty, an indented home row, a wide shift and backspace, a modifier row (tab, win, ctrl, alt,
 *    fn) and a bottom row with the comma and full stop beside a wide space bar. No row has more
 *    than ten keys. The arrow keys are on the 123 page, where they are one tap away and do not
 *    crowd the typing page. (Mesh keeps Tab on the letters page because remote desktops need Tab and
 *    Alt+Tab; remote-hid's default drops it.)
 *  - fn is not a page: pressing it changes keys in place. The digits become F1 to F10, esc
 *    becomes F11 and prtsc F12 (they sit above F1 and F10), and the arrows become Home, PgUp,
 *    PgDn and End. Press fn again to go back.
 *  - symbols1 (123): Android's ?123 page, plus the arrow cluster with the up arrow directly
 *    above the down arrow.
 *  - symbols2 (=\<): Android's second symbols page.
 *
 * Every layer adds up to the same total height (6.7) so the keyboard never changes size when
 * you switch layers: a 0.7 top row plus six rows of 1.0, or five rows of 1.34.
 *
 * Symbols come from real physical keys plus shift wherever one exists (what a physical US
 * keyboard sends); only characters with no physical key (euro, pi, ...) use the best-effort
 * Unicode entry sequence.
 */
object DefaultLayout {

    private fun k(label: String, code: String, w: Float = 1f) = KeyDef(label, code, w)
    private fun s(label: String, code: String, w: Float = 1f) = KeyDef(label, code, w, listOf("shift"))
    private fun u(label: String, hex: String) = KeyDef(label, CODE_UNICODE, 1f, emptyList(), hex)
    private fun gap(w: Float) = KeyDef("", CODE_SPACER, w)

    private fun letters(chars: String) = chars.map { k(it.toString(), "Key${it.uppercaseChar()}") }
    private fun digits() = "1234567890".map { k(it.toString(), "Digit$it") }

    private fun esc() = k("esc", "Escape")
    private fun prtsc() = k("prtsc", "PrintScreen")

    /** win, ctrl, alt, fn */
    private fun modifiers() = listOf(k("win", "Meta"), k("ctrl", "ctrl"), k("alt", "alt"), k("fn", CODE_FN))

    private fun bottom(backLabel: String, backLayer: String, comma: KeyDef = k(",", "Comma"), stop: KeyDef = k(".", "Period")) =
        RowDef(listOf(k(backLabel, "layer:$backLayer", 1.5f), comma, k("space", "Space", 5f), stop, k("enter", "Enter", 1.5f)))

    // Height of one row on the pages that have five rows (see the total in the comment above).
    private const val TALL = 1.34f

    private val lettersRows: List<RowDef> = listOf(
        // esc is one key wide above the "1" column, prtsc one key wide above the "0" column.
        RowDef(listOf(esc(), gap(8f), prtsc()), 0.7f),
        RowDef(digits()),
        RowDef(letters("qwertyuiop")),
        RowDef(listOf(gap(0.5f)) + letters("asdfghjkl") + gap(0.5f)),
        RowDef(listOf(k("\u21E7", "shift", 1.5f)) + letters("zxcvbnm") + k("\u232B", "Backspace", 1.5f)),
        RowDef(listOf(k("tab", "Tab")) + modifiers()),   // Mesh: remote desktops need Tab and Alt+Tab
        bottom("123", "symbols1"),
    )

    // Android's ?123 page with the arrow cluster. Row 3 has nine equal keys and the
    // up arrow sits in row 2 above the down arrow: the widths in row 2 are chosen so
    // both arrows share one column (up arrow's centre = 7.5 / 9 of the row, as the
    // down arrow's is in row 3).
    private val symbols1Rows: List<RowDef> = listOf(
        RowDef(digits(), TALL),
        RowDef(
            listOf(
                s("@", "Digit2"), s("#", "Digit3"), s("$", "Digit4"), s("%", "Digit5"), s("&", "Digit7"),
                k("-", "Minus"), s("+", "Equal"), s("(", "Digit9"), s(")", "Digit0"), k("/", "Slash"),
            ),
            TALL,
        ),
        RowDef(
            listOf(k("=\\<", "layer:symbols2", 1.5f)) + listOf(
                s("*", "Digit8"), s("\"", "Quote"), k("'", "Quote"), s(":", "Semicolon"),
                k(";", "Semicolon"), s("!", "Digit1"), s("?", "Slash"),
            ) + k("\u25B2", "ArrowUp", 1.21f) + k("\u232B", "Backspace", 1.21f),
            TALL,
        ),
        RowDef(
            listOf(esc(), prtsc()) + modifiers() + listOf(k("\u25C0", "ArrowLeft"), k("\u25BC", "ArrowDown"), k("\u25B6", "ArrowRight")),
            TALL,
        ),
        bottom("ABC", "letters").copy(height = TALL),
    )

    // Android's =\< page.
    private val symbols2Rows: List<RowDef> = listOf(
        RowDef(digits(), TALL),
        RowDef(
            listOf(
                s("~", "Backquote"), k("`", "Backquote"), s("|", "Backslash"), u("\u2022", "2022"), u("\u221A", "221a"),
                u("\u03C0", "3c0"), u("\u00F7", "f7"), u("\u00D7", "d7"), u("\u00B6", "b6"), u("\u2206", "2206"),
            ),
            TALL,
        ),
        RowDef(
            listOf(
                u("\u00A3", "a3"), u("\u00A2", "a2"), u("\u20AC", "20ac"), u("\u00A5", "a5"), s("^", "Digit6"),
                u("\u00B0", "b0"), k("=", "Equal"), s("{", "BracketLeft"), s("}", "BracketRight"), k("\\", "Backslash"),
            ),
            TALL,
        ),
        RowDef(
            listOf(k("?123", "layer:symbols1", 1.5f)) + listOf(
                s("%", "Digit5"), u("\u00A9", "a9"), u("\u00AE", "ae"), u("\u2122", "2122"), u("\u2713", "2713"),
                k("[", "BracketLeft"), k("]", "BracketRight"),
            ) + k("\u232B", "Backspace", 1.5f),
            TALL,
        ),
        bottom("ABC", "letters", comma = s("<", "Comma"), stop = s(">", "Period")).copy(height = TALL),
    )

    fun build(): LayoutDef = LayoutDef(
        listOf(
            LayerDef("letters", lettersRows),
            LayerDef("symbols1", symbols1Rows),
            LayerDef("symbols2", symbols2Rows),
        ),
    )
}
